"""Where each posted pick can be played right now. The pick runs write playTo once; prices and pick'em listings move
all day (a Pick6 line gets pulled, a price drifts out of range), so every engine run re-checks each open pick against
the board: each venue's current price, whether a pick'em app still lists the line, and whether it is still worth
taking (at or better than the pick's goodAt). Published to the Realtime Database as pre/pickslive.

Pick'em payouts: Underdog and Dabble prices are real per-pick payouts; PrizePicks standard lines pay the fixed table
(1.732 per leg on a 2-pick); DK Pick6 multipliers are estimated from DraftKings' price (see build.py) and marked so.
"""
from datetime import datetime

import edgecard as EC

STD_LEG = 1 / 0.5774


def _dec(am):
    if am is None:
        return None
    return 1 + am / 100 if am > 0 else 1 + 100 / -am


def _am(dec):
    if not dec or dec <= 1:
        return None
    return round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))


def check(picks, game_docs, prop_docs, now):
    idx = EC._index(game_docs, prop_docs)
    rows_by = {}
    for p in prop_docs:
        for r in p.get("rows") or []:
            rows_by.setdefault((p["id"], p["market"], (r.get("player") or "").lower()), []).append(r)
    out = {}
    for pk in picks:
        if pk.get("status") in ("void", "won", "lost", "push") or not pk.get("gameId") or not pk.get("market"):
            continue
        try:
            if datetime.fromisoformat(pk["commence"].replace("Z", "+00:00")) <= now:
                continue
        except Exception:
            continue
        good = _dec(pk.get("goodAt"))
        side, pt = pk.get("side"), pk.get("point")
        hit = idx.get(EC._key(pk["gameId"], pk["market"], side, pt, pk.get("player"), pk.get("team") if "team_totals" in (pk.get("market") or "") else None))
        line = hit[0] if hit else None
        venues = []
        for v in pk.get("playTo") or []:
            ven = v.get("venue")
            cur = {"venue": ven, "name": v.get("name") or ven, "line": v.get("line", pt)}
            dec = None
            if ven in ("prizepicks", "underdog", "pick6", "dabble_us_dfs", "betr_us_dfs", "sleeper"):
                # pick'em: is the line still listed, at the pick's number, on the pick's side?
                want_pt = v.get("line", pt)
                rows = rows_by.get((pk["gameId"], pk["market"], (pk.get("player") or "").lower()), [])
                d = next((d for r in rows for d in r.get("dfs") or [] if d.get("book") == ven and side in (d.get("dec") or {})
                          and (want_pt is None or d.get("point") == want_pt)), None)
                cur["listed"] = bool(d)
                if d:
                    if ven == "pick6":
                        m = (d.get("estMult") or {}).get(side)
                        cur["mult"] = m
                        dec = STD_LEG * m if m else None
                        cur["note"] = f"~{m:.2f}x est. from DraftKings" if m else "multiplier unknown"
                    elif ven == "prizepicks":
                        dec = STD_LEG if {"Over", "Under"} <= set(d.get("dec") or {}) else None
                        cur["note"] = "standard" if dec else "goblin/demon: payout varies"
                    else:
                        dec = (d.get("dec") or {}).get(side)
            elif line is not None:
                am = EC._venue_price(line, side, ven)
                dec = _dec(am)
                cur["listed"] = am is not None
                av = next((((q.get("prices") or {}).get(side) or {}).get("avail") for q in line.get("quotes") or [] if q.get("book") == ven), None)
                if av is not None:
                    cur["avail"] = av          # exchanges: dollars on offer at that price
            else:
                cur["listed"] = False
            cur["now"] = _am(dec) if dec else None
            cur["inRange"] = bool(dec and good and dec >= good - 1e-9)
            venues.append(cur)
        fair_now = ((line or {}).get("fair") or {}).get(side) if line else None
        # an audit that doesn't take the pick run's word: is our model really on the pick's side of the market, is a
        # usable price in range now, and is it a heavy favorite (little upside, minutes and blowout risk)
        mp, mk = pk.get("modelP"), pk.get("marketFair")
        audit = {"model": None if mp is None or mk is None else mp > mk, "price": any(x["inRange"] for x in venues),
                 "fav": (pk.get("fair") or 0) >= 0.7}
        claims = pk.get("checks") or {}
        audit["mismatch"] = [k for k in ("model", "price") if claims.get(k) is True and audit[k] is False]
        out[pk["id"]] = {"at": now.isoformat().replace("+00:00", "Z"), "venues": venues, "fairNow": fair_now,
                         "anyInRange": any(x["inRange"] for x in venues), "audit": audit}
    return out
