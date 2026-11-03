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


def muting(data_dir, min_n=100, z_cut=-2.0):
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
        km = [r for r in today if r["k"] in bad or r["vk"] in bad]
        kk = [r for r in today if not (r["k"] in bad or r["vk"] in bad)]
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


def run(data_dir, log=print, ms=None):
    if ms is None:
        ms, _ = MS.compute(data_dir, log=log)
    res = {"muting": muting(data_dir), "blending": blending(data_dir, ms)}
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(res, open(os.path.join(data_dir, "stats", "policytest.json"), "w"), indent=1)
    m, b = res["muting"], res["blending"].get("all") or {}
    log(f"policytest: kept {m['kept'].get('n')} edges gap {m['kept'].get('gap')} roi {m['kept'].get('roi')}; "
        f"muted {m['muted'].get('n')} gap {m['muted'].get('gap')} roi {m['muted'].get('roi')}; blend new vs old {b.get('newVsOld')}")
    return res
