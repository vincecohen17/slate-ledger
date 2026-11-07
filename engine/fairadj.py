"""Self-correcting sharp fair (calib/fairadj.json, applied in build._finish_line).

Our no-vig fair splits each book's margin evenly between the two sides. The graded record says that isn't how books
charge: NBA prop overs won about 1.1 points less than their closing fair over the whole 2025-26 backtest (every month
negative, 89k main lines) and 2.6 points less on the live record. So the fair gets a shift on the logit scale for the
side the tracker grades (Over / Yes / home), fit per sport|kind and per market (shrunk toward the sport|kind), weighting
recent days most (half-life HALF_LIFE days; backtest rows count a little, as a stable prior).

It only goes live where it has earned it: a walk-forward test on the live record (each day's shift fit only on earlier
days, scored on that day by log loss against the raw fair, standard error clustered by game) must show a gain 2+ SE
above zero for that sport|kind. Refit and re-tested every time graded rows change, so it keeps up with the market.
"""
import datetime as dt
import glob
import gzip
import json
import math
import os
from collections import defaultdict

HALF_LIFE, BT_W, ALT_W = 14.0, 0.15, 0.4
LAM = (400.0, 800.0)          # shrink (in units of Fisher information) for the sport|kind level and the market level
GATE_T, MIN_SHIFTED = 2.0, 200


def _lg(p):
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def _sg(x):
    return 1 / (1 + math.exp(-max(-30.0, min(30.0, x))))


def _top(g):
    sp, kind, _ = g.split("|")
    return f"{sp}|{kind}|*"


def _load(data_dir):
    live, bt = [], []
    for fn in glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl*")):
        isbt = os.path.basename(fn).startswith("bt_")
        fh = gzip.open(fn, "rt") if fn.endswith(".gz") else open(fn)
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("f") is None or r.get("y") is None:
                continue
            (bt if isbt else live).append(r)
    return live, bt


def _bt_sums(bt):
    """backtest rows as fixed prior sums per group (first-order at shift 0)"""
    S = defaultdict(lambda: [0.0, 0.0])
    for r in bt:
        w = BT_W * (ALT_W if r.get("main") is False else 1.0)
        f = r["f"]
        for k in (_top(r["g"]), r["g"]):
            S[k][0] += w * (r["y"] - f)
            S[k][1] += w * f * (1 - f)
    return S


def fit(live, btS, asof):
    """shifts {group: s} as of the start of day `asof` (rows from earlier days only)"""
    t0 = dt.date.fromisoformat(asof)
    rows = []
    for r in live:
        age = (t0 - dt.date.fromisoformat(r["d"])).days
        if age <= 0:
            continue
        rows.append((r, 0.5 ** (age / HALF_LIFE) * (ALT_W if r.get("main") is False else 1.0)))
    out = {}
    for lvl, lam in ((0, LAM[0]), (1, LAM[1])):
        S = defaultdict(lambda: [0.0, 0.0])
        for r, w in rows:
            k = _top(r["g"]) if lvl == 0 else r["g"]
            base = out.get(_top(r["g"]), 0.0) if lvl == 1 else 0.0
            p = _sg(_lg(r["f"]) + base)
            S[k][0] += w * (r["y"] - p)
            S[k][1] += w * p * (1 - p)
        for k in set(S) | {k for k in btS if (k.endswith("|*") if lvl == 0 else not k.endswith("|*"))}:
            num, den = S[k] if k in S else (0.0, 0.0)
            bnum, bden = btS.get(k, (0.0, 0.0))
            base = out.get(_top(k), 0.0) if lvl == 1 else 0.0
            if lvl == 1:      # the backtest prior was summed at shift 0: move it to the sport level first
                bnum -= base * bden
            out[k] = base + (num + bnum) / (den + bden + lam)
    return {k: round(v, 4) for k, v in out.items() if abs(v) >= 0.002}


def shift_for(shifts, g):
    return shifts.get(g, shifts.get(_top(g), 0.0))


def _ll(p, y):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _cluster_t(vals, clusters):
    n = len(vals)
    if n < 50:
        return None, None
    m = sum(vals) / n
    S = defaultdict(float)
    for v, c in zip(vals, clusters):
        S[c] += v - m
    k = len(S)
    if k < 6:
        return m, None
    se = math.sqrt(sum(s * s for s in S.values()) * k / (k - 1)) / n
    return m, se


def compute(data_dir, log=print):
    live, bt = _load(data_dir)
    if not live:
        return None
    btS = _bt_sums(bt)
    days = sorted({r["d"] for r in live})
    by_day = defaultdict(list)
    for r in live:
        by_day[r["d"]].append(r)
    gains = defaultdict(lambda: ([], []))
    shifted = defaultdict(int)
    for d in days[1:]:
        sh = fit(live, btS, d)
        for r in by_day[d]:
            g = r["g"]
            k = _top(g)
            s = shift_for(sh, g)
            gain = _ll(r["f"], r["y"]) - _ll(_sg(_lg(r["f"]) + s), r["y"]) if s else 0.0
            gains[k][0].append(gain)
            gains[k][1].append(r.get("gid") or r["d"])
            shifted[k] += bool(s)
    ev, on = {}, set()
    for k, (vals, cl) in gains.items():
        m, se = _cluster_t(vals, cl)
        if m is None:
            continue
        t = None if not se else m / se
        # Nov 7 2026 lab: a sport scored only on days with no shift fit yet (UEL, one day of rows) had gains of pure float
        # rounding (~1e-17) whose t came out 2.6 and turned an untested shift on. Gains are now exactly 0 where no shift was
        # applied, and the gate needs MIN_SHIFTED walk-forward rows that actually carried a shift.
        ev[k] = {"n": len(vals), "shifted": shifted[k], "gain": round(m, 6), "se": None if se is None else round(se, 6),
                 "t": None if t is None else round(t, 2),
                 "on": bool(t is not None and t >= GATE_T and m > 0 and shifted[k] >= MIN_SHIFTED)}
        if ev[k]["on"]:
            on.add(k)
    today = (dt.datetime.utcnow() + dt.timedelta(days=1)).date().isoformat()
    full = fit(live, btS, today)
    applied = {k: v for k, v in full.items() if _top(k) in on}
    res = {"at": dt.datetime.utcnow().isoformat() + "Z", "halfLife": HALF_LIFE, "shifts": applied, "fitted": full, "eval": ev}
    os.makedirs(os.path.join(data_dir, "calib"), exist_ok=True)
    json.dump(res, open(os.path.join(data_dir, "calib", "fairadj.json"), "w"), indent=0)
    log(f"fairadj: {len(applied)} shifts live in {len(on)} sport groups of {len(ev)} tested")
    return res


def load(data_dir):
    try:
        return json.load(open(os.path.join(data_dir, "calib", "fairadj.json"))).get("shifts") or {}
    except Exception:
        return {}
