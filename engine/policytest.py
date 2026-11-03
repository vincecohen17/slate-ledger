"""Walk-forward test of the two decision changes on the live record (mode policytest; writes stats/policytest.json):

1. Muting edge kinds: for each day, the kinds (and venue x kind cells) are judged only from edges graded on earlier
   days (300+ graded, 2.5+ SE under their fair); that day's edges are split into kept and muted and both are scored by
   results: win rate minus the fair's expectation and flat ROI at the flagged price.
2. Blend weights: each live graded row's fair is the blend of the calibrated model and the closing market. Old: the
   in-sample blend weight fit on everything before that day. New: the same weight only where the walk-forward backtest
   (data before the live period) found a significant out-of-sample gain, else 0. Both are scored by log loss on the
   live days, against the market alone, with the difference's standard error clustered by day.
"""
import glob
import json
import math
import os
from collections import defaultdict

import numpy as np

import modelstats as MS


def _kind(r):
    if r.get("kind") == "pick'em":
        return "Pick'em"
    if r.get("est"):
        return "One-sided (est. fair)"
    if r.get("model"):
        return "Alt line (model fair)"
    base = "Prop" if r.get("kind") == "prop" else "Game line"
    return f"{base}, main" if r.get("main") else f"{base}, alt"


def _stat(rows):
    n = len(rows)
    if not n:
        return {"n": 0}
    w = sum(r["y"] for r in rows)
    e = sum(r["fair"] for r in rows)
    v = sum(r["fair"] * (1 - r["fair"]) for r in rows)
    return {"n": n, "win": round(w / n, 4), "exp": round(e / n, 4), "gap": round((w - e) / n, 4),
            "z": round((w - e) / math.sqrt(v), 2) if v else None, "roi": round(sum(r["pnl"] for r in rows) / n, 4)}


def _ls(r):
    """league x model logic x price-edge size: the cell the logic override is judged in"""
    lg = {"agrees": "agrees", "disagrees": "disagrees"}.get(r.get("logic"), "other")
    return f"{r.get('league')} · {lg} · {'5%+' if (r.get('edge') or 0) >= 0.05 else 'under 5%'}"


def logic_rule(prior, r, bad, min_n=100):
    """muted after the logic override: a 5%+ price edge where the model agrees comes back when that league's cell has held
    its fair (gap >= 0 over min_n); one where the model disagrees is muted when its cell has lost (z <= -2)"""
    if (r.get("edge") or 0) < 0.05 or r.get("logic") not in ("agrees", "disagrees"):
        return r["k"] in bad or r["vk"] in bad
    s = prior.get(_ls(r))
    if r["logic"] == "agrees":
        if s and s["n"] >= min_n and s["gap"] >= 0:
            return False
        return r["k"] in bad or r["vk"] in bad
    if s and s["n"] >= min_n and s["z"] is not None and s["z"] <= -2:
        return True
    return r["k"] in bad or r["vk"] in bad


def support_rule(prior_sl, prior_ls, r, bad, min_n=100):
    """the support override (edgecard.rerank): with a model number, a 5%+ edge whose model sits between price and fair comes
    back where its league x band x size cell held its fair; one under the price or far past the fair is muted where its
    cell lost. Without one, the logic override."""
    import edgecard as EC
    if (r.get("edge") or 0) >= 0.05 and r.get("sup") is not None:
        c = prior_sl.get(EC._sup_size(r.get("league"), r["sup"], r["edge"]))
        if c and c["n"] >= min_n:
            band = EC._band(r["sup"])
            if band in ("between", "near") and c["gap"] >= 0:
                return False
            if band in ("below", "beyond") and c["z"] is not None and c["z"] <= -2:
                return True
            return r["k"] in bad or r["vk"] in bad
    return logic_rule(prior_ls, r, bad, min_n)


def muting(data_dir, min_n=100, z_cut=-2.0, logic=False, support=False):
    rows = []
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y") is None or (r.get("hrs") or 0) < 0 or r.get("fair") is None or r.get("pnl") is None:
                continue
            r["k"] = _kind(r)
            r["vk"] = f"{r.get('venue')} · {r['k']}"
            rows.append(r)
    days = sorted({r["d"] for r in rows})
    kept, muted, per_day = [], [], []
    for d in days:
        prior = [r for r in rows if r["d"] < d]
        by_k, by_vk = defaultdict(list), defaultdict(list)
        for r in prior:
            by_k[r["k"]].append(r)
            by_vk[r["vk"]].append(r)
        bad = set()
        for grp in (by_k, by_vk):
            for k, rs in grp.items():
                s = _stat(rs)
                if s["n"] >= min_n and s["z"] is not None and s["z"] <= z_cut and s["gap"] < 0:
                    bad.add(k)
        today = [r for r in rows if r["d"] == d]
        if logic or support:
            by_ls = defaultdict(list)
            for r in prior:
                by_ls[_ls(r)].append(r)
            cells = {k: _stat(v) for k, v in by_ls.items()}
            if support:
                import edgecard as EC
                by_sl = defaultdict(list)
                for r in prior:
                    by_sl[EC._sup_size(r.get("league"), r.get("sup"), r.get("edge"))].append(r)
                scells = {k: _stat(v) for k, v in by_sl.items()}
                mut = [support_rule(scells, cells, r, bad) for r in today]
            else:
                mut = [logic_rule(cells, r, bad) for r in today]
        else:
            mut = [r["k"] in bad or r["vk"] in bad for r in today]
        km = [r for r, m in zip(today, mut) if m]
        kk = [r for r, m in zip(today, mut) if not m]
        kept += kk
        muted += km
        per_day.append({"d": d, "kept": _stat(kk), "muted": _stat(km), "rule": sorted(bad)})
    return {"all": _stat(rows), "kept": _stat(kept), "muted": _stat(muted), "days": per_day}


def blending(data_dir, ms):
    live, _ = MS._load(data_dir)
    if not live:
        return {}
    # the gate, decided from the backtests (which end before the live record starts)
    bt_t = {s["group"]: s.get("blendGainT") for s in (ms.get("sources") or {}).get("bt", [])}
    def gate(g):
        sp, kind, _ = g.split("|")
        t = bt_t.get(g)
        if t is None:
            t = bt_t.get(f"{sp}|{kind}|*")
        return t is not None and t >= 2
    by = defaultdict(list)
    for r in live:
        by[r["g"]].append(r)
    out_rows = []
    for g, rs in by.items():
        dstr = [r["d"] for r in rs]
        names = sorted(set(dstr))
        if len(names) < 3 or len(rs) < 100:
            continue
        code = {d: i for i, d in enumerate(names)}
        days = np.array([code[d] for d in dstr])
        p = np.clip(np.array([r["p"] for r in rs], float), 1e-6, 1 - 1e-6)
        f = np.clip(np.array([r["f"] for r in rs], float), 1e-6, 1 - 1e-6)
        y = np.array([r["y"] for r in rs], float)
        x = MS._lg(p)
        on = gate(g)
        for di in range(2, len(names)):
            tr, te = days < di, days == di
            if tr.sum() < 100 or not te.sum():
                continue
            a, b = MS._fit_ab(x[tr], y[tr])
            ctr = MS._sig(a + b * x[tr])
            grid = np.linspace(0, 1, 21)
            w = float(grid[int(np.argmin([MS._ll_rows(MS._blend(ctr, f[tr], w_), y[tr]).mean() for w_ in grid]))])
            cte = MS._sig(a + b * x[te])
            old = MS._blend(cte, f[te], w)
            new = MS._blend(cte, f[te], w if on else 0.0)
            for i, j in enumerate(np.where(te)[0]):
                out_rows.append((g, names[di], float(MS._ll_rows(f[j:j + 1], y[j:j + 1])[0]), float(MS._ll_rows(old[i:i + 1], y[j:j + 1])[0]),
                                 float(MS._ll_rows(new[i:i + 1], y[j:j + 1])[0]), on))
    if not out_rows:
        return {}
    def summ(rs):
        mk = np.array([r[2] for r in rs]); od = np.array([r[3] for r in rs]); nw = np.array([r[4] for r in rs]); cl = np.array([r[1] for r in rs])
        res = {"n": len(rs), "llMarket": round(float(mk.mean()), 5), "llOld": round(float(od.mean()), 5), "llNew": round(float(nw.mean()), 5)}
        for k, dv in (("newVsOld", od - nw), ("newVsMarket", mk - nw), ("oldVsMarket", mk - od)):
            se = MS._cluster_se(dv, cl)
            res[k] = {"gain": round(float(dv.mean()), 6), "se": None if se is None else round(se, 6), "t": None if not se else round(float(dv.mean()) / se, 2)}
        return res
    sports = defaultdict(list)
    for r in out_rows:
        sports[r[0].split("|")[0]].append(r)
    return {"all": summ(out_rows), "gated": sorted({r[0] for r in out_rows if r[5]}), "bySport": {k: summ(v) for k, v in sports.items()}}


FEATS = ("gap", "move", "disp", "depth", "meangap")


def _xrow(r):
    """features on the logit scale relative to the closing fair: the model's gap, the move since open (toward this side
    > 0), books' disagreement, market depth (books x weight) and the model-vs-market mean gap in market sd units"""
    x = r.get("x") or {}
    f = min(max(r["f"], 1e-4), 1 - 1e-4)
    lf = math.log(f / (1 - f))
    p = min(max(r["p"], 1e-4), 1 - 1e-4)
    op = x.get("op")
    mv = 0.0 if op is None or x.get("opPt") is not None else lf - math.log(min(max(op, 1e-4), 1 - 1e-4) / (1 - min(max(op, 1e-4), 1 - 1e-4)))
    mg = 0.0
    if x.get("mm") and x.get("mr") is not None:
        mg = max(-3.0, min(3.0, (x["mr"] - x["mm"]) / max(0.5, abs(x["mm"]) ** 0.5)))
    return lf, [math.log(p / (1 - p)) - lf, mv, x.get("disp") or 0.0, math.log(1 + (x.get("nb") or 0) * (x.get("fw") or 0)) / 5, mg]


def residual(data_dir, min_rows=1500):
    """walk-forward residual model on live rows that carry features: logit(P) = logit(close) + b . features, fit on
    earlier days per sport|kind, scored on each new day against the close alone (log loss, day-clustered SE).
    Wired into fairs only if it beats the close (t >= 2)."""
    live, _ = MS._load(data_dir)
    rows = [r for r in live if r.get("x")]
    by = defaultdict(list)
    for r in rows:
        sp, kind, _ = r["g"].split("|")
        by[f"{sp}|{kind}"].append(r)
    out = {"rows": len(rows), "groups": {}}
    for g, rs in by.items():
        if len(rs) < min_rows:
            out["groups"][g] = {"n": len(rs), "status": f"collecting ({len(rs)} of {min_rows} rows)"}
            continue
        days = sorted({r["d"] for r in rs})
        X = [_xrow(r) for r in rs]
        lf = np.array([a for a, _ in X]); F = np.array([b for _, b in X]); y = np.array([r["y"] for r in rs], float)
        dd = np.array([r["d"] for r in rs])
        gains, cl, coefs = [], [], None
        for d in days[3:]:
            tr, te = dd < d, dd == d
            if tr.sum() < 500 or not te.sum():
                continue
            b = np.zeros(F.shape[1])
            for _ in range(25):        # Newton with a small ridge, offset = logit(close)
                z = lf[tr] + F[tr] @ b
                pz = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
                gr = F[tr].T @ (y[tr] - pz) - 2.0 * b
                Hs = (F[tr] * (pz * (1 - pz))[:, None]).T @ F[tr] + 2.0 * np.eye(F.shape[1])
                b = b + np.linalg.solve(Hs, gr)
            coefs = b
            pm = 1 / (1 + np.exp(-lf[te])); pn = 1 / (1 + np.exp(-np.clip(lf[te] + F[te] @ b, -30, 30)))
            gains += list(MS._ll_rows(pm, y[te]) - MS._ll_rows(pn, y[te]))
            cl += [d] * int(te.sum())
        if not gains:
            continue
        gv = np.array(gains)
        se = MS._cluster_se(gv, np.array([days.index(c) for c in cl]))
        out["groups"][g] = {"n": len(rs), "scored": len(gv), "gain": round(float(gv.mean()), 6), "se": None if se is None else round(se, 6),
                            "t": None if not se else round(float(gv.mean()) / se, 2), "coef": dict(zip(FEATS, [round(float(c), 4) for c in coefs]))}
    return out


def _edge_rows(data_dir):
    rows = []
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y") is None or (r.get("hrs") or 0) < 0 or r.get("fair") is None or r.get("pnl") is None or r.get("close") is None:
                continue
            r["k"] = _kind(r)
            rows.append(r)
    return rows


def _dec_stat(rows):
    """won minus the flagged fair, split into how far the fair moved by the close and how the result beat the close"""
    n = len(rows)
    if not n:
        return {"n": 0}
    s = _stat(rows)
    s.update(move=round(sum(r["close"] - r["fair"] for r in rows) / n, 4), beatClose=round(sum(r["y"] - r["close"] for r in rows) / n, 4),
             clv=round(sum(r.get("clv") or 0 for r in rows) / n, 4), edge=round(sum(r["edge"] for r in rows) / n, 4))
    if n >= 2:
        pn = [r["pnl"] for r in rows]
        m = sum(pn) / n
        s["roiSE"] = round(math.sqrt(sum((x - m) ** 2 for x in pn) / (n - 1) / n), 4)
    return s


SUP_BINS = ((-1e9, 0, "under the price (< 0)"), (0, 0.5, "0 – 0.5"), (0.5, 1.0, "0.5 – 1"), (1.0, 1.5, "1 – 1.5"),
            (1.5, 2.5, "1.5 – 2.5"), (2.5, 1e9, "past 2.5x"))


def logic_study(data_dir):
    """how the model's logic relates to results on every graded edge (Reports > Model logic)"""
    import edgecard as EC
    rows = _edge_rows(data_dir)
    size = lambda r: "5%+" if r["edge"] >= 0.05 else "under 5%"
    lg = lambda r: r.get("logic") or "none"
    out = {"n": len(rows), "since": min((r["d"] for r in rows), default=None),
           "supRows": sum(r.get("sup") is not None for r in rows), "supFlag": sum(r.get("supSrc") == "flag" for r in rows)}
    def table(keyf, rs, order=None):
        by = defaultdict(list)
        for r in rs:
            k = keyf(r)
            if k is not None:
                by[k].append(r)
        keys = order or sorted(by, key=lambda k: -len(by[k]))
        return [{"k": k, **_dec_stat(by[k])} for k in keys if by.get(k)]
    out["logicSize"] = [{"size": sz, "rows": table(lg, [r for r in rows if sz == "all" or size(r) == sz], ["agrees", "neutral", "disagrees", "none"])}
                        for sz in ("all", "under 5%", "5%+")]
    sup = [r for r in rows if r.get("sup") is not None]
    binf = lambda r: next(lbl for lo, hi, lbl in SUP_BINS if lo <= r["sup"] < hi)
    out["supCurve"] = [{"size": sz, "rows": table(binf, [r for r in sup if size(r) == sz], [b[2] for b in SUP_BINS])} for sz in ("5%+", "under 5%")]
    big = [r for r in rows if r["edge"] >= 0.05]
    out["leagueLogic"] = table(lambda r: f"{r.get('league')} · {lg(r)}", [r for r in big if lg(r) in ("agrees", "disagrees")])
    out["leagueBand"] = table(lambda r: f"{r.get('league')} · {EC.SUP_NAME[EC._band(r['sup'])]}", [r for r in big if r.get("sup") is not None])
    out["kindBand"] = table(lambda r: f"{r['k']} · {EC.SUP_NAME[EC._band(r['sup'])]}", [r for r in big if r.get("sup") is not None])
    out["srcBand"] = table(lambda r: f"{'at the flag' if r.get('supSrc') == 'flag' else 'closing model (backfill)'} · {EC.SUP_NAME[EC._band(r['sup'])]}",
                           [r for r in big if r.get("sup") is not None])
    # agrees vs disagrees inside the same league x edge kind x size (so mix can't explain it)
    strata = defaultdict(lambda: {"agrees": [], "disagrees": []})
    for r in rows:
        if lg(r) in ("agrees", "disagrees"):
            strata[(r.get("league"), r["k"], size(r))][lg(r)].append(r)
    num = den = var = 0.0
    for st in strata.values():
        a, d = st["agrees"], st["disagrees"]
        if len(a) < 10 or len(d) < 10:
            continue
        w = len(a) * len(d) / (len(a) + len(d))
        ga = sum(r["y"] - r["fair"] for r in a) / len(a)
        gd = sum(r["y"] - r["fair"] for r in d) / len(d)
        va = sum(r["fair"] * (1 - r["fair"]) for r in a) / len(a) ** 2
        vd = sum(r["fair"] * (1 - r["fair"]) for r in d) / len(d) ** 2
        num += w * (ga - gd); den += w; var += w * w * (va + vd)
    if den:
        out["matched"] = {"diff": round(num / den, 4), "z": round(num / math.sqrt(var), 2) if var else None, "strata": sum(1 for st in strata.values() if len(st["agrees"]) >= 10 and len(st["disagrees"]) >= 10)}
    # daily, 5%+ edges
    days = defaultdict(list)
    for r in big:
        days[r["d"]].append(r)
    out["daily"] = []
    for d in sorted(days):
        rs = days[d]
        grp = lambda f: [r for r in rs if f(r)]
        ag, di = grp(lambda r: lg(r) == "agrees"), grp(lambda r: lg(r) == "disagrees")
        bt = grp(lambda r: r.get("sup") is not None and 0 <= r["sup"] < 1.5)
        ot = grp(lambda r: r.get("sup") is not None and not (0 <= r["sup"] < 1.5))
        g = lambda x: None if len(x) < 5 else round(sum(r["y"] - r["fair"] for r in x) / len(x), 4)
        out["daily"].append({"d": d, "agrees": g(ag), "disagrees": g(di), "support": g(bt), "against": g(ot), "nA": len(ag), "nD": len(di), "nS": len(bt), "nO": len(ot)})
    return out


def run(data_dir, log=print, ms=None):
    if ms is None:
        ms, _ = MS.compute(data_dir, log=log)
    res = {"muting": muting(data_dir), "mutingLogic": muting(data_dir, logic=True), "mutingSupport": muting(data_dir, support=True),
           "blending": blending(data_dir, ms)}
    try:
        res["logic"] = logic_study(data_dir)
    except Exception as e:
        log(f"logic study failed: {type(e).__name__}: {str(e)[:80]}")
    try:
        res["residual"] = residual(data_dir)
    except Exception as e:
        log(f"residual study failed: {type(e).__name__}")
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(res, open(os.path.join(data_dir, "stats", "policytest.json"), "w"), indent=1)
    m, b = res["muting"], res["blending"].get("all") or {}
    log(f"policytest: kept {m['kept'].get('n')} edges gap {m['kept'].get('gap')} roi {m['kept'].get('roi')}; "
        f"muted {m['muted'].get('n')} gap {m['muted'].get('gap')} roi {m['muted'].get('roi')}; blend new vs old {b.get('newVsOld')}")
    return res
