"""What changed, and what happened after: every model, decision-rule, pick-rule and measurement change with its effect.

The change log is hand-kept in the data repo (stats/change_log.json, plus stats/model_changes.json for model fixes with
backtest numbers): {date, kind: model | logic | picks | measure, area, change, sports?: [sport keys or league labels],
markets?: [market prefixes], groups?: [{g, before, after}]}. For each change this compares the WINDOW days before with
the days after (up to WINDOW) on three records, each restricted to the change's sports and markets when it names them:
  edges: every graded edge (track/edgecard): CLV at the close (fresh closes only), results against the closing fair
         ("gap", won minus expected), flat ROI
  picks: our graded picks (picks/*.json): CLV (fresh closes), flat 1u ROI
  model: graded model rows (track/graded, live only): skill = log loss of the closing fair minus log loss of the model
         (positive = the model beat the close)
with the difference after - before, its standard error and t (independent-sample; edges on the same game are not
independent, so read t below about 3 with care). Written to stats/changes.json and published as mstats.changes.
"""
import glob
import json
import math
import os
from datetime import date, timedelta

WINDOW = 14
CLOSE_OK_MIN = 30


def _ms(xs):
    n = len(xs)
    if not n:
        return None
    m = sum(xs) / n
    v = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else None
    return {"n": n, "m": round(m, 4), "se": round(math.sqrt(v / n), 4) if v is not None else None}


def _diff(a, b):
    if not a or not b or a.get("se") is None or b.get("se") is None:
        return None
    d = b["m"] - a["m"]
    se = math.sqrt(a["se"] ** 2 + b["se"] ** 2)
    return {"d": round(d, 4), "se": round(se, 4), "t": round(d / se, 2) if se > 0 else None}


def _ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _load_log(data_dir):
    out = []
    for fn in ("change_log.json", "model_changes.json"):
        try:
            for c in json.load(open(os.path.join(data_dir, "stats", fn))):
                c = dict(c)
                c.setdefault("kind", "model" if fn == "model_changes.json" else "logic")
                if not any(x["date"] == c["date"] and x.get("area") == c.get("area") for x in out):
                    out.append(c)
        except Exception:
            pass
    return sorted(out, key=lambda c: c["date"])


def _in_scope(c, sport, league, market):
    sp = c.get("sports")
    if sp and sport not in sp and league not in sp:
        return False
    mk = c.get("markets")
    if mk and not any((market or "").startswith(m) for m in mk):
        return False
    return True


def compute(data_dir, today=None, log=print):
    today = today or date.today()
    changes = _load_log(data_dir)
    if not changes:
        return []
    edges = []
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("close") is not None and r.get("fair") is not None and abs(r["close"] - r["fair"]) > 0.20:
                continue        # mismatched close (closecal.MISMATCH)
            edges.append(r)
    model = []
    for fn in glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl")):
        if os.path.basename(fn).startswith("bt_"):
            continue
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("p") is not None and r.get("f") is not None and r.get("y") is not None:
                model.append(r)
    picks = []
    for fn in glob.glob(os.path.join(data_dir, "picks", "*.json")):
        try:
            picks += [p for p in json.load(open(fn)) if p.get("status") in ("won", "lost", "push")]
        except Exception:
            pass
    from config import label as _label
    for c in changes:
        d0 = date.fromisoformat(c["date"][:10])
        lo, hi = (d0 - timedelta(days=WINDOW)).isoformat(), min(d0 + timedelta(days=WINDOW), today).isoformat()
        d0s = d0.isoformat()
        eff = {}
        # edges
        bef, aft = [r for r in edges if lo <= r["d"] < d0s], [r for r in edges if d0s <= r["d"] <= hi]
        bef = [r for r in bef if _in_scope(c, r.get("sport"), r.get("league"), r.get("mk"))]
        aft = [r for r in aft if _in_scope(c, r.get("sport"), r.get("league"), r.get("mk"))]
        fresh = lambda rs: [r["clv"] for r in rs if not (r.get("ca") is not None and r["ca"] > CLOSE_OK_MIN)]
        gap = lambda rs: [r["y"] - r["close"] for r in rs if r.get("y") is not None]
        roi = lambda rs: [r["pnl"] for r in rs if r.get("pnl") is not None]
        e = {}
        import closecal
        closecal.load(data_dir)
        def fresh_cal(rs):
            return [closecal.cal(r["close"], r.get("kind") or "prop", data_dir, closecal.typ(r)) * r["dec"] - 1 for r in rs
                    if not (r.get("ca") is not None and r["ca"] > CLOSE_OK_MIN)]
        for k, f in (("clv", fresh), ("clvCal", fresh_cal), ("gap", gap), ("roi", roi)):
            a, b = _ms(f(bef)), _ms(f(aft))
            e[k] = {"before": a, "after": b, "diff": _diff(a, b)}
        eff["edges"] = e
        # picks
        pd_ = lambda p: (p.get("commence") or p.get("date") or "")[:10]
        pb = [p for p in picks if lo <= pd_(p) < d0s and _in_scope(c, None, p.get("league"), p.get("market"))]
        pa = [p for p in picks if d0s <= pd_(p) <= hi and _in_scope(c, None, p.get("league"), p.get("market"))]
        def flat(p):
            if p.get("flat") is not None:
                return p["flat"]
            a = p.get("gradedAt")
            dec = (1 + a / 100 if a > 0 else 1 + 100 / -a) if isinstance(a, (int, float)) and a else 2.0
            return dec - 1 if p["status"] == "won" else -1.0 if p["status"] == "lost" else 0.0
        pclv = lambda ps: [p["clv"] for p in ps if p.get("clv") is not None and not (p.get("closeAge") is not None and p["closeAge"] > CLOSE_OK_MIN)]
        eff["picks"] = {k: {"before": _ms(f(pb)), "after": _ms(f(pa)), "diff": _diff(_ms(f(pb)), _ms(f(pa)))}
                        for k, f in (("clv", pclv), ("flat", lambda ps: [flat(p) for p in ps]))}
        # model skill
        def sk(rs):
            return [_ll(r["f"], r["y"]) - _ll(r["p"], r["y"]) for r in rs]
        grp = [g["g"] for g in c.get("groups") or []]
        def mscope(r):
            if grp:
                return r["g"] in grp
            sport, kind, mk = (r["g"].split("|") + ["", "", ""])[:3]
            return _in_scope(c, sport, _label(sport) if sport else None, mk)
        mb = [r for r in model if lo <= r["d"] < d0s and mscope(r)]
        ma = [r for r in model if d0s <= r["d"] <= hi and mscope(r)]
        a, b = _ms(sk(mb)), _ms(sk(ma))
        eff["model"] = {"skill": {"before": a, "after": b, "diff": _diff(a, b)}}
        c["effects"], c["window"], c["through"] = eff, WINDOW, hi
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(changes, open(os.path.join(data_dir, "stats", "changes.json"), "w"), indent=1)
    return changes


def daily(data_dir, days=45):
    """daily series for the timeline: edges' fresh-close CLV and results vs fair, picks' CLV, per game day"""
    import closecal
    closecal.load(data_dir)
    today = date.today()
    lo = (today - timedelta(days=days)).isoformat()
    by = {}
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r["d"] < lo:
                continue
            if r.get("close") is not None and r.get("fair") is not None and abs(r["close"] - r["fair"]) > 0.20:
                continue
            x = by.setdefault(r["d"], {"clv": [], "gap": [], "pclv": [], "cal": []})
            if not (r.get("ca") is not None and r["ca"] > CLOSE_OK_MIN):
                x["clv"].append(r["clv"])
                x["cal"].append(closecal.cal(r["close"], r.get("kind") or "prop", data_dir, closecal.typ(r)) * r["dec"] - 1)
            if r.get("y") is not None:
                x["gap"].append(r["y"] - r["close"])
    for fn in glob.glob(os.path.join(data_dir, "picks", "*.json")):
        try:
            for p in json.load(open(fn)):
                d = (p.get("commence") or p.get("date") or "")[:10]
                if d >= lo and p.get("clv") is not None and not (p.get("closeAge") is not None and p["closeAge"] > CLOSE_OK_MIN):
                    by.setdefault(d, {"clv": [], "gap": [], "pclv": [], "cal": []})["pclv"].append(p.get("clvCal", p["clv"]))
        except Exception:
            pass
    out = []
    for d in sorted(by):
        x = by[d]
        out.append({"d": d, "clv": round(sum(x["clv"]) / len(x["clv"]), 4) if x["clv"] else None, "nc": len(x["clv"]),
                    "clvCal": round(sum(x["cal"]) / len(x["cal"]), 4) if x["cal"] else None,
                    "gap": round(sum(x["gap"]) / len(x["gap"]), 4) if x["gap"] else None, "ng": len(x["gap"]),
                    "pclv": round(sum(x["pclv"]) / len(x["pclv"]), 4) if x["pclv"] else None, "np": len(x["pclv"])})
    return out
