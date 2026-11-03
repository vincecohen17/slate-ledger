"""Which books move first, and do stale soft prices after a sharp move beat the close? (mode leadlag ->
stats/leadlag.json). Rebuilt from the raw payloads saved each pull (snapshots/<date>/): every main game line and main
prop row at every saved time, with the sharp no-vig fair and each venue's all-in price.

1. Lead-lag: for each book, how well its price change between two pulls predicts the consensus fair's change over the
   next pull (correlation and slope), and how often it moves before vs after the consensus.
2. Stale after steam: at each pull, every venue price that beats the fair by 2+ points is classified by what happened in
   the previous ~hour: the fair moved 2+ points toward that side while the venue's price didn't follow ("steam"), or the
   fair barely moved ("static gap"). Each is scored against the closing fair (CLV at that price) and, where the game has
   been graded, the result (won minus the fair at the flag, flat ROI).
"""
import glob
import gzip
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta

import build as B
import config as C

STEAM = 0.02          # fair move toward the side over the look-back
LOOK_MIN = 75         # look-back window (minutes)
EDGE = 0.02           # venue price beats the fair by this much


def _ts(day, fn):
    return datetime.strptime(f"{day} {fn[:6]}", "%Y-%m-%d %H%M%S")


def _series(data_dir, days=None, log=print):
    """{(gid, market, line id, outcome): [(t, fair, {book: cost}, commence, meta)]} from the raw payloads"""
    S = defaultdict(list)
    dirs = sorted(glob.glob(os.path.join(data_dir, "snapshots", "*")))
    if days:
        dirs = dirs[-days:]
    nfiles = 0
    for d in dirs:
        day = os.path.basename(d)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".json.gz"):
                continue
            try:
                data = json.load(gzip.open(os.path.join(d, fn)))
            except Exception:
                continue
            t = _ts(day, fn)
            sport = fn[7:].split("_lines")[0].split("_event")[0]
            label = C.label(sport) if sport in C.SPORTS else sport
            evs = data if isinstance(data, list) else [data]
            for ev in evs:
                if not ev.get("bookmakers"):
                    continue
                try:
                    com = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                if t >= com:
                    continue
                lines = []
                try:
                    if "_lines" in fn:
                        lines = [(l, None) for l in B.build_game(ev, label)["lines"] if l.get("isMain")]
                    else:
                        for doc in B.build_props(ev, label).values():
                            lines += [(r, doc["market"]) for r in doc["rows"] if r.get("isMain")]
                except Exception:
                    continue
                for l, mk in lines:
                    fair = l.get("fair") or {}
                    for o, f in fair.items():
                        costs = {q["book"]: (q["prices"].get(o) or {}).get("cost") for q in l.get("quotes") or [] if (q["prices"].get(o) or {}).get("cost")}
                        key = (ev["id"], mk or l.get("market"), l.get("player") or "", l.get("point"), o)
                        S[key].append((t, f, costs, com, {"sport": sport, "league": label}))
            nfiles += 1
    log(f"leadlag: {nfiles} payloads, {len(S)} line sides")
    return S


def _grades(data_dir):
    """(gid, market, player, point, side) -> y from the graded edge-free path: ledger.grade_leg on closed snapshots"""
    try:
        import ledger
        fin = {}
        for fn in sorted(glob.glob(os.path.join(data_dir, "finals", "20*.json")))[-14:]:
            for x in json.load(open(fn)).values():
                if x.get("gameId"):
                    fin[x["gameId"]] = x
        return ledger, fin
    except Exception:
        return None, {}


def run(data_dir, log=print, days=None):
    S = _series(data_dir, days, log)
    venues = {k for k, b in C.BOOKS.items() if b.get("venue")}
    # ---- 1. lead-lag: book's change t-1 -> t vs consensus change t -> t+1
    lead = defaultdict(lambda: {"n": 0, "sxy": 0.0, "sxx": 0.0, "syy": 0.0, "first": 0, "after": 0})
    for key, seq in S.items():
        seq.sort(key=lambda x: x[0])
        for i in range(1, len(seq) - 1):
            t0, f0, c0, _, _ = seq[i - 1]
            t1, f1, c1, _, _ = seq[i]
            t2, f2, _, _, _ = seq[i + 1]
            if (t1 - t0) > timedelta(minutes=30) or (t2 - t1) > timedelta(minutes=30):
                continue
            dnext = f2 - f1
            for b in set(c0) & set(c1):
                x = (1 / c0[b] - 1 / c1[b])        # the book's implied chance change (cost up = chance up): use cost directly
                x = c1[b] - c0[b]
                if abs(x) < 1e-9 and abs(dnext) < 1e-9:
                    continue
                L = lead[b]
                L["n"] += 1
                L["sxy"] += x * dnext
                L["sxx"] += x * x
                L["syy"] += dnext * dnext
                if abs(x) >= 0.01 and abs(f1 - f0) < 0.005:
                    L["first"] += 1                 # moved before the consensus did
                if abs(x) >= 0.01 and abs(f1 - f0) >= 0.01 and abs(c1[b] - c0[b]) > 0:
                    L["after"] += 1
    leaders = []
    for b, L in lead.items():
        if L["n"] < 200 or L["sxx"] <= 0 or L["syy"] <= 0:
            continue
        leaders.append({"book": b, "name": C.book(b)["name"], "n": L["n"], "corr": round(L["sxy"] / math.sqrt(L["sxx"] * L["syy"]), 4),
                        "slope": round(L["sxy"] / L["sxx"], 4), "movesFirst": L["first"], "venue": b in venues})
    leaders.sort(key=lambda r: -r["corr"])
    # ---- 2. stale after steam
    ledger, fin = _grades(data_dir)
    flags = []
    for key, seq in S.items():
        gid, mk, player, point, side = key
        seq.sort(key=lambda x: x[0])
        close = seq[-1][1]
        seen = set()
        for i, (t, f, costs, com, meta) in enumerate(seq):
            prior = [x for x in seq[:i] if t - x[0] <= timedelta(minutes=LOOK_MIN)]
            if not prior:
                continue
            f0 = prior[0][1]
            move = f - f0
            for b, c in costs.items():
                if b not in venues or (b, ) in seen:
                    continue
                if f - c < EDGE:
                    continue
                c0 = prior[0][2].get(b)
                followed = c0 is not None and (c - c0) >= move * 0.5 if move > 0 else True
                kind = "steam" if move >= STEAM and c0 is not None and not followed else "static" if abs(move) < 0.01 else "other"
                seen.add((b, ))
                flags.append({"key": key, "book": b, "t": t, "fair": f, "cost": c, "close": close, "kind": kind, "hrs": (com - t).total_seconds() / 3600,
                              "league": meta["league"], "prop": bool(player)})
    # results where graded
    out = defaultdict(list)
    for fl in flags:
        gid, mk, player, point, side = fl["key"]
        y = None
        if ledger:
            try:
                g = ledger.grade_leg(data_dir, {"gameId": gid, "market": mk, "side": side, "point": point if not mk.startswith("h2h") else None,
                                                "player": player or None}, fin)
                y = None if not g else g.get("y")
            except Exception:
                y = None
        fl["y"] = y
        fl["clv"] = fl["close"] / fl["cost"] - 1
        out[fl["kind"]].append(fl)

    def summ(rs):
        n = len(rs)
        if not n:
            return {"n": 0}
        clv = sum(r["clv"] for r in rs) / n
        g = [r for r in rs if r["y"] is not None]
        res = {"n": n, "clv": round(clv, 4), "beatClose": round(sum(r["close"] > r["cost"] for r in rs) / n, 3), "edge": round(sum(r["fair"] - r["cost"] for r in rs) / n, 4)}
        if g:
            w = sum(r["y"] for r in g)
            e = sum(r["fair"] for r in g)
            v = sum(r["fair"] * (1 - r["fair"]) for r in g)
            res.update({"graded": len(g), "gap": round((w - e) / len(g), 4), "z": round((w - e) / math.sqrt(v), 2) if v else None,
                        "roi": round(sum(r["y"] / r["cost"] - 1 for r in g) / len(g), 4)})
        return res
    res = {"leaders": leaders, "flags": {k: summ(v) for k, v in out.items()},
           "byVenue": {k: {b: summ([r for r in v if r["book"] == b]) for b in sorted({r["book"] for r in v})} for k, v in out.items()},
           "byKind": {k: {"props": summ([r for r in v if r["prop"]]), "lines": summ([r for r in v if not r["prop"]])} for k, v in out.items()},
           "params": {"steam": STEAM, "lookMin": LOOK_MIN, "edge": EDGE}}
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(res, open(os.path.join(data_dir, "stats", "leadlag.json"), "w"), indent=1, default=str)
    log(f"leadlag: {len(flags)} stale-price flags; steam {res['flags'].get('steam', {})}; static {res['flags'].get('static', {})}")
    return res
