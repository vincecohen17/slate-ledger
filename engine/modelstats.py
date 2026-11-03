"""Deep model statistics from the graded tracking rows (track/graded): how our model's probabilities compare with the
closing sharp market, per market, per sport and overall, for the live record (sport.jsonl) and the walk-forward
backtests (bt_*.jsonl.gz) separately. Written to stats/modelstats.json and the Realtime Database pre/mstats (loaded by
the Reports page only). Recomputed when the graded files change.

Per group (sport|kind|market, plus pooled sport|kind|* and sport|*|*, and *|kind|*):
  n, days, base rate; log loss and Brier of the model, the market, and (out of sample) the calibrated model and the
  model/market blend. Calibration and blend are walk-forward: each test day is scored with a fit (ridge logistic a, b on
  logit(model), then the blend weight w on a 0..1 grid) on the days before it only, so the gain is honest.
  Skill = mean per-row log-loss difference (market minus model, and market minus blend), with a cluster-robust standard
  error: rows of one game (all a player's alternate lines) are strongly correlated, so they are clustered by game when
  the row carries it (gid, new rows) and otherwise by day, which is conservative.
  Reliability: 10 equal-width bins for the model and for the market (n, mean forecast, observed rate, Wilson 95%).
  Murphy decomposition of the Brier score: reliability, resolution, uncertainty.
  Disagreement buckets on model minus market: rows, mean model, mean market, observed rate, log loss of each, and the
  flat return of taking the model's side at the closing no-vig price (mean and cluster SE), which is what a bet on the
  model's opinion would have earned against a fair close.
  A daily series of the skill and the OOS blend gain.
"""
import glob
import gzip
import json
import math
import os

import numpy as np

EPS = 1e-6
BINS = np.linspace(0, 1, 11)
DIS = [-1.0, -0.10, -0.05, -0.02, 0.02, 0.05, 0.10, 1.0]
DIS_K = ["model 10+ pts lower", "5-10 lower", "2-5 lower", "within 2 pts", "2-5 higher", "5-10 higher", "model 10+ pts higher"]
MIN_N = 200


def _lg(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _sig(x):
    return 1 / (1 + np.exp(-np.clip(x, -30, 30)))


def _ll_rows(p, y):
    p = np.clip(p, EPS, 1 - EPS)
    return -(y * np.log(p) + (1 - y) * np.log(1 - p))


def _fit_ab(x, y, lam=20.0):
    """ridge logistic y ~ sigmoid(a + b x), shrunk to a=0, b=1 (the same fit the tracker uses)"""
    a, b = 0.0, 1.0
    for _ in range(25):
        p = _sig(a + b * x)
        r, w = p - y, p * (1 - p)
        ga, gb = r.sum() + lam * a, (r * x).sum() + lam * (b - 1)
        haa, hab, hbb = w.sum() + lam, (w * x).sum(), (w * x * x).sum() + lam
        det = haa * hbb - hab * hab
        if abs(det) < 1e-9:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-7:
            break
    return a, float(min(1.5, max(0.2, b)))


def _blend(cal, f, w):
    return _sig(w * _lg(cal) + (1 - w) * _lg(f))


def _cluster_se(v, cl):
    """standard error of mean(v) with rows clustered (sum over clusters of squared residual sums)"""
    n = len(v)
    if n < 2:
        return None
    m = v.mean()
    _, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=v - m)
    k = len(s)
    if k < 6:                   # too few clusters for a cluster-robust standard error to mean anything
        return None
    return float(math.sqrt((s ** 2).sum() * k / (k - 1)) / n)


def _wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    ph = k / n
    d = 1 + z * z / n
    c = (ph + z * z / (2 * n)) / d
    h = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return round(c - h, 4), round(c + h, 4)


def _rel(pred, y):
    idx = np.clip(np.digitize(pred, BINS) - 1, 0, 9)
    out, rel, res = [], 0.0, 0.0
    ybar, n = y.mean(), len(y)
    for i in range(10):
        m = idx == i
        k = int(m.sum())
        if not k:
            out.append(None)
            continue
        pm, o = float(pred[m].mean()), float(y[m].mean())
        rel += k * (pm - o) ** 2
        res += k * (o - ybar) ** 2
        lo, hi = _wilson(float(y[m].sum()), k)
        out.append({"n": k, "p": round(pm, 4), "o": round(o, 4), "lo": lo, "hi": hi})
    return out, {"rel": round(rel / n, 5), "res": round(res / n, 5), "unc": round(float(ybar * (1 - ybar)), 5)}


def _oos(days, p, f, y):
    """walk-forward calibrated model and blend: each day scored with a fit on the days before it (warm-up: the first 40%
    of days, at least 2). Returns (mask of scored rows, calibrated p, blended p, the last fit)"""
    ud = np.unique(days)
    warm = max(2, int(len(ud) * 0.4))
    cal, bl, mask = np.full(len(p), np.nan), np.full(len(p), np.nan), np.zeros(len(p), bool)
    x, last = _lg(p), None
    grid = np.linspace(0, 1, 21)
    # refit once per block of days (a week when there are many days), scoring the block with the fit before it
    step = 7 if len(ud) > 60 else 1
    for j in range(warm, len(ud), step):
        d0, d1 = ud[j], ud[min(j + step, len(ud)) - 1]
        tr, te = days < d0, (days >= d0) & (days <= d1)
        if tr.sum() < 100:
            continue
        a, b = _fit_ab(x[tr], y[tr])
        ctr = _sig(a + b * x[tr])
        lls = [_ll_rows(_blend(ctr, f[tr], w), y[tr]).mean() for w in grid]
        w = float(grid[int(np.argmin(lls))])
        cal[te] = _sig(a + b * x[te])
        bl[te] = _blend(cal[te], f[te], w)
        mask |= te
        last = {"a": round(float(a), 3), "b": round(float(b), 3), "w": round(w, 2)}
    return mask, cal, bl, last


def _group_stats(rows):
    dstr = [r["d"] for r in rows]
    names = sorted(set(dstr))
    code = {d: i for i, d in enumerate(names)}
    days = np.array([code[d] for d in dstr])           # day index (string arrays don't order in numpy ufuncs)
    p = np.clip(np.array([r["p"] for r in rows], float), EPS, 1 - EPS)
    f = np.clip(np.array([r["f"] for r in rows], float), EPS, 1 - EPS)
    y = np.array([r["y"] for r in rows], float)
    cl = np.array([r.get("gid") or r["d"] for r in rows])
    by_game = any(r.get("gid") for r in rows)
    llm, llf = _ll_rows(p, y), _ll_rows(f, y)
    dv = llf - llm                                   # > 0: the model beat the market on that row
    se = _cluster_se(dv, cl)
    out = {"n": len(rows), "days": int(len(np.unique(days))), "first": names[0], "last": names[-1],
           "base": round(float(y.mean()), 4), "cluster": "game" if by_game else "day",
           "llModel": round(float(llm.mean()), 5), "llMarket": round(float(llf.mean()), 5),
           "brModel": round(float(((p - y) ** 2).mean()), 5), "brMarket": round(float(((f - y) ** 2).mean()), 5),
           "skill": round(float(dv.mean()), 5), "skillSE": None if se is None else round(se, 5),
           "corr": round(float(np.corrcoef(_lg(p), _lg(f))[0, 1]), 3) if len(rows) > 2 else None,
           "sdGap": round(float((p - f).std()), 4)}
    out["skillT"] = round(out["skill"] / se, 2) if se else None
    mask, cal, bl, last = _oos(days, p, f, y)
    if mask.sum() >= 50:
        lc, lb, lfm = _ll_rows(cal[mask], y[mask]), _ll_rows(bl[mask], y[mask]), llf[mask]
        g = lfm - lb
        se2 = _cluster_se(g, cl[mask])
        out.update({"oosN": int(mask.sum()), "llCalOOS": round(float(lc.mean()), 5), "llBlendOOS": round(float(lb.mean()), 5),
                    "llMarketOOS": round(float(lfm.mean()), 5), "blendGain": round(float(g.mean()), 5),
                    "blendGainSE": None if se2 is None else round(se2, 5), "fit": last})
        out["blendGainT"] = round(out["blendGain"] / se2, 2) if se2 else None
    out["relModel"], out["murphyModel"] = _rel(p, y)
    out["relMarket"], out["murphyMarket"] = _rel(f, y)
    gap = p - f
    gi = np.clip(np.digitize(gap, DIS) - 1, 0, len(DIS_K) - 1)
    bk = []
    for i, k in enumerate(DIS_K):
        m = gi == i
        n = int(m.sum())
        if not n:
            bk.append({"k": k, "n": 0})
            continue
        # the model's side at the closing no-vig price: model higher -> the row's side at f, lower -> the other side
        side_yes = gap[m] > 0
        ret = np.where(side_yes, y[m] / f[m] - 1, (1 - y[m]) / (1 - f[m]) - 1)
        rse = _cluster_se(ret, cl[m]) if n > 2 else None
        bk.append({"k": k, "n": n, "p": round(float(p[m].mean()), 4), "f": round(float(f[m].mean()), 4), "o": round(float(y[m].mean()), 4),
                   "llModel": round(float(llm[m].mean()), 4), "llMarket": round(float(llf[m].mean()), 4),
                   "roi": round(float(ret.mean()), 4), "roiSE": None if rse is None else round(rse, 4)})
    out["buckets"] = bk
    daily = []
    for d in np.unique(days):
        m = days == d
        e = {"d": names[int(d)], "n": int(m.sum()), "skill": round(float(dv[m].mean()), 5)}
        mm = m & mask
        if mm.sum():
            e["blendGain"] = round(float((llf[mm] - _ll_rows(bl[mm], y[mm])).mean()), 5)
        daily.append(e)
    out["daily"] = daily[-120:]
    return out


def _load(data_dir):
    live, bt = [], []
    for fn in glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl*")):
        name = os.path.basename(fn)
        sport = name.split(".")[0]
        is_bt = sport.startswith("bt_")
        sport = sport[3:] if is_bt else sport
        fh = gzip.open(fn, "rt") if fn.endswith(".gz") else open(fn)
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("p") is None or r.get("f") is None or r.get("y") is None:
                continue
            r["sport"] = sport
            (bt if is_bt else live).append(r)
    return live, bt


def _groups(rows):
    g = {}
    for r in rows:
        sport, kind, mk = r["g"].split("|")
        for k in (r["g"], f"{sport}|{kind}|*", f"{sport}|*|*", f"*|{kind}|*", "*|*|*"):
            g.setdefault(k, []).append(r)
    return g


def compute(data_dir, log=print, force=False):
    files = sorted(glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl*")))
    sig = [[os.path.basename(f), os.path.getsize(f)] for f in files]
    fn = os.path.join(data_dir, "stats", "modelstats.json")
    sfn = os.path.join(data_dir, "stats", "modelstats_sig.json")
    try:
        if not force and json.load(open(sfn)) == sig and os.path.exists(fn):
            return json.load(open(fn)), False
    except Exception:
        pass
    live, bt = _load(data_dir)
    out = {"at": None, "sources": {}}
    for name, rows in (("live", live), ("bt", bt)):
        res = []
        for k, rs in _groups(rows).items():
            if len(rs) < MIN_N:
                continue
            try:
                s = _group_stats(rs)
            except Exception as e:
                log(f"modelstats: {name} group skipped ({type(e).__name__})")
                continue
            s["group"] = k
            res.append(s)
        res.sort(key=lambda s: (-s["n"]))
        out["sources"][name] = res
    from datetime import datetime, timezone
    out["at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(out, open(fn, "w"), separators=(",", ":"))
    json.dump(sig, open(sfn, "w"))
    log(f"modelstats: {len(out['sources'].get('live', []))} live groups, {len(out['sources'].get('bt', []))} backtest groups")
    return out, True


def oos_weights(data_dir, params, ms, log=print):
    """the blend weight the pick runs use (calib/params.json w) only where the walk-forward test says the model helps:
    w is kept (capped at the walk-forward fit's w) when the out-of-sample blend gain is 2+ standard errors above zero in
    the live record or the backtests, else set to 0 (fair = the market). The in-sample fit alone overstates the model."""
    if not params or not ms:
        return 0
    best = {}
    for src in ("live", "bt"):
        for s in (ms.get("sources") or {}).get(src, []):
            t = s.get("blendGainT")
            if t is None:
                continue
            g = s["group"]
            if g not in best or t > best[g][0]:
                best[g] = (t, (s.get("fit") or {}).get("w"))
    n = 0
    for g, pr in params.items():
        t, w_oos = best.get(g, (None, None))
        w0 = pr.get("w", 0)
        w = (min(w0, w_oos) if w_oos is not None else w0) if t is not None and t >= 2 else 0.0
        if pr.get("wInSample") is None:
            pr["wInSample"] = w0
        pr["oosT"] = t
        if w != w0:
            pr["w"] = w
            n += 1
    json.dump(params, open(os.path.join(data_dir, "calib", "params.json"), "w"), indent=0)
    if n:
        log(f"modelstats: blend weight set from walk-forward results on {n} markets")
    return n
