"""Calibrated CLV: how much the closing fair has actually been worth.

Raw CLV values a price at our closing sharp fair. On the sides we flag, that fair has been too generous: the flagged
side wins less often than the close says (the edge card's "gap"), so raw CLV overstates every edge. Fit on every graded
edge (track/edgecard), per bet kind (line, prop, pick'em):  P(win) = sigmoid(a + b * logit(close)), shrunk toward no
correction (a = 0, b = 1) by n / (n + SHRINK). Calibrated CLV = calibrated close x decimal price - 1. A walk-forward
check (fit on earlier days, score the next) is stored with the fit: raw CLV, calibrated CLV and realized ROI on the same
out-of-sample rows, so anyone can see which one tells the truth.

Rows whose close is 20+ points from the fair at the flag are mismatches (feed errors or the wrong side) and are left
out of everything (MISMATCH).
"""
import glob
import json
import math
import os

SHRINK = 500
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


def compute(data_dir, log=print):
    rows = _rows(data_dir)
    if len(rows) < 200:
        return None
    params = {"_all": _shrunk(rows)}
    for k in KINDS:
        rk = [r for r in rows if r.get("kind") == k]
        if rk:
            params[k] = _shrunk(rk)
    # walk-forward: fit on the days before, score each day
    days = sorted({r["d"] for r in rows})
    raw = cal = roi = 0.0
    n = 0
    for d in days[3:]:
        tr = [r for r in rows if r["d"] < d]
        te = [r for r in rows if r["d"] == d]
        pk = {"_all": _shrunk(tr)}
        for k in KINDS:
            rk = [r for r in tr if r.get("kind") == k]
            if rk:
                pk[k] = _shrunk(rk)
        for r in te:
            q = pk.get(r.get("kind")) or pk["_all"]
            raw += r["clv"]
            cal += _sg(q["a"] + q["b"] * _lg(r["close"])) * r["dec"] - 1
            roi += r["pnl"]
            n += 1
    out = {"params": params, "wf": {"n": n, "raw": round(raw / n, 4) if n else None, "cal": round(cal / n, 4) if n else None,
                                    "roi": round(roi / n, 4) if n else None}, "mismatch": MISMATCH}
    os.makedirs(os.path.join(data_dir, "calib"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "calib", "closecal.json"), "w"), indent=1)
    global _P
    _P = out
    log(f"closecal: {len(rows)} edges; walk-forward raw CLV {out['wf']['raw']}, calibrated {out['wf']['cal']}, realized ROI {out['wf']['roi']}")
    return out


def load(data_dir):
    global _P
    if _P is None or (not _P and data_dir):
        try:
            _P = json.load(open(os.path.join(data_dir, "calib", "closecal.json")))
        except Exception:
            _P = {}
    return _P


def cal(p, kind, data_dir):
    """calibrated chance for a closing fair p on a bet of this kind (line / prop / pick'em)"""
    if p is None:
        return None
    P = (load(data_dir) or {}).get("params") or {}
    q = P.get(kind) or P.get("_all")
    if not q:
        return p
    return _sg(q["a"] + q["b"] * _lg(p))
