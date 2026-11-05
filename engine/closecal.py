"""Calibrated CLV: how much the closing fair has actually been worth.

Raw CLV values a price at our closing sharp fair. On some of the sides we flag, that fair has been too generous: the
flagged side wins less often than the close says, so raw CLV overstates those edges. Fit on every graded edge
(track/edgecard) per bet kind and line type (TYPES: main = the books' main number, alt = an alternate number books
quote, model = a fair moved from the main line by our stat model or estimated from one side):
P(win) = sigmoid(a + b * logit(close)), shrunk toward no correction by games / (games + SHRINK_G) (rows from one game
move together, so games, not rows, are the sample).

A group's correction is used only where it has earned it, judged on what CLV is for (predicting return): walk-forward
(fit on earlier days, score each next day), the group's raw CLV must overstate its realized ROI by GATE_T+ game-clustered
standard errors, and the calibrated CLV must land closer to the realized ROI than the raw one. Nov 5: main props raw +5.1%
vs ROI +3.9% (honest, stays raw; the old per-kind fit pulled it to -5.2%), model-priced props raw +8.1% vs ROI -3.9%
(t 3.4, corrected). Main lines have too few graded edges to say, so they stay raw. Fits are shrunk by n / (n + SHRINK).
The walk-forward raw / calibrated CLV / realized ROI and each group's gate numbers are stored with the fit.

Rows whose close is 20+ points from the fair at the flag are mismatches (feed errors or the wrong side) and are left
out of everything (MISMATCH).
"""
import glob
import json
import math
import os

SHRINK = 500          # (old row-based shrink, kept for _shrunk callers)
GATE_T = 2.0
VERSION = 2         # bets and picks recompute clvCal when this changes (2: per line type, gated on return bias)
TYPES = ("main", "alt", "model")
MISMATCH = 0.20
KINDS = ("line", "prop", "pick'em")
_P = None


def _lg(p):
    p = min(1 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sg(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def ok_row(r):
    return r.get("close") is not None and r.get("fair") is not None and abs(r["close"] - r["fair"]) <= MISMATCH


def fit(rows, iters=25):
    a, b = 0.0, 1.0
    for _ in range(iters):
        ga = gb = haa = hab = hbb = 0.0
        for r in rows:
            x = _lg(r["close"])
            p = _sg(a + b * x)
            w, e = p * (1 - p), r["y"] - p
            ga += e
            gb += e * x
            haa += w
            hab += w * x
            hbb += w * x * x
        det = haa * hbb - hab * hab
        if det <= 0:
            break
        a += (hbb * ga - hab * gb) / det
        b += (-hab * ga + haa * gb) / det
    return a, b


def _shrunk(rows):
    a, b = fit(rows) if len(rows) >= 50 else (0.0, 1.0)
    k = len(rows) / (len(rows) + SHRINK)
    return {"a": round(k * a, 4), "b": round(1 + k * (b - 1), 4), "n": len(rows)}


def typ(r):
    """main / alt / model for a graded edge row"""
    return "model" if (r.get("model") or r.get("est")) else ("main" if r.get("main") else "alt")


def _gkey(r):
    return f"{r.get('kind') or 'prop'}|{typ(r)}"


def _ll(p, y):
    p = min(1 - 1e-6, max(1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def _rows(data_dir):
    out = []
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("y") is not None and ok_row(r) and 0.005 < r["close"] < 0.995:
                out.append(r)
    return out


def _params(rows):
    by = {}
    for r in rows:
        by.setdefault(_gkey(r), []).append(r)
    return {k: _shrunk(v) for k, v in by.items()}


def compute(data_dir, log=print):
    rows = _rows(data_dir)
    if len(rows) < 200:
        return None
    # walk-forward: fit on the days before, score each day; per group and game, raw CLV - realized and calibrated - realized
    days = sorted({r["d"] for r in rows})
    err = {}                       # group -> game -> [raw - pnl, cal - pnl]
    wf_rows = []
    for d in days[3:]:
        pk = _params([r for r in rows if r["d"] < d])
        for r in (x for x in rows if x["d"] == d):
            q = pk.get(_gkey(r))
            pc = _sg(q["a"] + q["b"] * _lg(r["close"])) if q else r["close"]
            e = err.setdefault(_gkey(r), {}).setdefault(r.get("gid") or (r["d"], r.get("sport")), [0.0, 0.0, 0])
            e[0] += r["clv"] - r["pnl"]
            e[1] += pc * r["dec"] - 1 - r["pnl"]
            e[2] += 1
            wf_rows.append((r, pc))
    gate = {}
    for k, g in err.items():
        n = sum(v[2] for v in g.values())
        br, bc = sum(v[0] for v in g.values()) / n, sum(v[1] for v in g.values()) / n
        G = len(g)
        se = (G / max(1, G - 1) * sum((v[0] - br * v[2]) ** 2 for v in g.values())) ** 0.5 / n if G > 1 else None
        t = br / se if se else 0.0
        gate[k] = {"rawBias": round(br, 4), "calBias": round(bc, 4), "t": round(t, 2), "games": G,
                   "on": G >= 20 and t >= GATE_T and abs(bc) < abs(br)}
    raw = cal = roi = 0.0
    n = 0
    for r, pc in wf_rows:
        use = pc if gate.get(_gkey(r), {}).get("on") else r["close"]
        raw += r["clv"]
        cal += use * r["dec"] - 1
        roi += r["pnl"]
        n += 1
    params = _params(rows)
    for k, q in params.items():
        q.update(gate.get(k) or {"on": False})
    out = {"params": params, "wf": {"n": n, "raw": round(raw / n, 4) if n else None, "cal": round(cal / n, 4) if n else None,
                                    "roi": round(roi / n, 4) if n else None}, "mismatch": MISMATCH, "v": 2}
    os.makedirs(os.path.join(data_dir, "calib"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "calib", "closecal.json"), "w"), indent=1)
    global _P
    _P = out
    on = [k for k, q in params.items() if q.get("on")]
    log(f"closecal: {len(rows)} edges; corrections on for {', '.join(on) or 'nothing'}; walk-forward raw CLV {out['wf']['raw']}, "
        f"calibrated {out['wf']['cal']}, realized ROI {out['wf']['roi']}")
    return out


def load(data_dir):
    global _P
    if _P is None or (not _P and data_dir):
        try:
            _P = json.load(open(os.path.join(data_dir, "calib", "closecal.json")))
        except Exception:
            _P = {}
        # a fit from an older version (other groups, no gates): refit now, before anything is calibrated with it
        if data_dir and _P.get("v") != VERSION:
            try:
                compute(data_dir, log=lambda *a, **k: None)
            except Exception:
                pass
    return _P


def cal(p, kind, data_dir, t="main"):
    """calibrated chance for a closing fair p on a bet of this kind (line / prop / pick'em) and line type t (main / alt /
    model); the raw close where that group's correction hasn't beaten it out of sample"""
    if p is None:
        return None
    P = (load(data_dir) or {}).get("params") or {}
    q = P.get(f"{kind}|{t}")
    if not q or not q.get("on") or "a" not in q:
        return p
    return _sg(q["a"] + q["b"] * _lg(p))
