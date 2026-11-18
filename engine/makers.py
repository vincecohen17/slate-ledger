"""Maker orders (Nov 17): limit orders to rest on an exchange that are worth more than the sharp fair if they fill, and
have a real chance to fill before the start. Suggestions only for now (Vince places them by hand); later the plan is to
place, reprice and cancel them through the exchanges' own APIs.

How a maker order works on a two-outcome exchange: the best price a taker can buy side X at (qX, cents per $1) is some
maker's bid on the other side Y at 1 - qX. We bid Y one tick better (1 - qX + tick), so the next taker who wants X gets
our order first, at a tick better than anything else on the venue. If it fills we hold Y at that price (+ any maker fee).

Which ones: main game lines (h2h, spreads, totals at the main number) on Vince's exchanges (Kalshi, Polymarket US, Novig,
ProphetX) for games starting within AHEAD_H hours, bids between 25c and 75c, where our price is at least MIN_EDGE better than the board's sharp fair
for Y and cheaper than just taking Y. Fill chance: the rate at which resting orders 2-4% in their maker's favor were
eaten on these venues, Nov 12-17 (6 days of raw pulls, ~420k observed intervals; size at the best price shrinking with
the price unchanged), by time to start and venue, integrated to the start. After an eat the sharp fair moved only
0.05 points toward the taker, so fills weren't mostly informed money. Measured on main lines only.

Paper record: each suggestion is followed as if posted (track/makers/open.json). Filled when the level behind ours is
eaten (takers would have hit our better price first) or vanishes; cancelled when the sharp fair moves so the edge drops
under CANCEL_EDGE (what the automation would do); expired at the start. Filled ones are graded after the game into the
Methods board (track/methods/graded.jsonl, m = "maker": result at our price, CLV against the market consensus at the
close); every outcome goes to track/makers/log.jsonl for the fill rate.

Policy (Vince, Nov 17), the same one the automation will follow: an arb is always locked; otherwise a leg is only ever
bet when it is +EV on its own. So: post one side, or both sides when both clear MIN_EDGE (if both fill, that's an arb).
A filled order is hedged only when the other side can be taken for a guaranteed profit at Vince's arb bar (LOCK_MIN 3%;
status "locked", graded as the pair) or when the other side is itself +EV; a thinner lock through a leg that is below its
fair gives up more expected value than it saves, so the order is held.
Taker arbs and +EV taker edges stay with the Arbs and Edges boards."""
import json
import math
import os
from datetime import datetime, timedelta

import config as C
import oddsmath as M

VENUES = ("kalshi", "polymarket_us", "novig", "prophetx")
TICK = {"kalshi": 0.01, "polymarket_us": 0.01, "novig": 0.005, "prophetx": 0.005}
# maker fee per contract = k x p x (1 - p); assumptions to verify against each venue's fee page
MAKER_FEE_K = {"kalshi": 0.0175, "polymarket_us": 0.0, "novig": 0.0, "prophetx": 0.0}
MIN_EDGE, CANCEL_EDGE, AHEAD_H = 0.02, 0.005, 6
LOCK_MIN = 0.03     # lock a filled order with the other side only at Vince's arb bar (3%+), or when that side is +EV
# eats per hour of a resting +2-4% order, by hours to start (upper bound), Nov 12-17; venue multipliers vs the average
RATE = [(1, 2.11), (3, 1.37), (6, 1.01), (24, 0.69), (1e9, 0.46)]
VMULT = {"kalshi": 1.23, "polymarket_us": 1.26, "novig": 1.15, "prophetx": 0.65}
MAIN = ("h2h", "spreads", "totals")
TOL = 5e-4          # prices are compared to the nearest 0.05c (the stored ones are rounded)
# where the other leg can be TAKEN to lock a filled maker order: Vince's venues, not pick'em apps, not DK Predictions /
# FanDuel Predicts (their prices are borrowed from the sportsbooks and the market may not be listed)
TAKE = {b for b, v in C.BOOKS.items() if v.get("venue") and v.get("kind") != "dfs" and b not in ("draftkings", "fanduel")}


def _iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def p_fill(venue, hours):
    """chance at least part of it fills before the start: 1 - exp(-integrated eat rate)"""
    lam, h = 0.0, 0.0
    while h < hours:
        step = min(0.25, hours - h)
        r = next(v for ub, v in RATE if (hours - h) <= ub)
        lam += r * step * VMULT.get(venue, 1.0)
        h += step
    return 1 - math.exp(-lam)


def _cost(venue, bid):
    return bid + MAKER_FEE_K.get(venue, 0) * bid * (1 - bid)


def _main_lines(doc):
    out = {}
    for l in doc.get("lines") or []:
        mk = l.get("market")
        if mk not in MAIN or not l.get("fair") or l.get("fairSource") != "sharp" or len(l.get("outcomes") or []) != 2:
            continue
        if mk not in out or (l.get("fairWeight") or 0) > (out[mk].get("fairWeight") or 0):
            out[mk] = l
    return out.values()


def _side_point(doc, l, side):
    pt = l.get("point")
    if l["market"] == "spreads" and pt is not None and side != doc["home"]:
        return -pt
    return pt


def _best_take(l, side):
    """the cheapest all-in price to take `side` right now on Vince's venues: (venue, cost) or None"""
    best = None
    for q in l.get("quotes") or []:
        if q.get("book") not in TAKE or q.get("stale"):
            continue
        c = ((q.get("prices") or {}).get(side) or {}).get("cost")
        if c and 0.01 < c < 0.99 and (best is None or c < best[1]):
            best = (q["book"], c)
    return best


def suggest(game_docs, now):
    items = []
    for d in game_docs:
        if not d.get("commence"):
            continue
        hrs = (_iso(d["commence"]) - now).total_seconds() / 3600
        if not 0.05 < hrs <= AHEAD_H:
            continue
        for l in _main_lines(d):
            oc = l["outcomes"]
            for q in l.get("quotes") or []:
                v = q.get("book")
                if v not in VENUES or q.get("stale"):
                    continue
                pr = q.get("prices") or {}
                for X in oc:
                    Y = oc[1] if X == oc[0] else oc[0]
                    px, py = pr.get(X) or {}, pr.get(Y) or {}
                    if not px.get("dec"):
                        continue
                    qx = 1 / px["dec"]
                    tick = TICK[v]
                    bid = round((1 - qx) / tick) * tick + tick
                    if not 0.25 <= bid <= 0.75:
                        continue        # longshots and heavy favorites: the no-vig fair is least reliable there, and the
                                        # fill rates were measured on lines near even
                    cost = _cost(v, bid)
                    fy = l["fair"].get(Y)
                    if fy is None:
                        continue
                    ev = fy / cost - 1
                    take_y = py.get("cost")
                    if ev < MIN_EDGE or (take_y is not None and take_y <= cost):
                        continue        # not enough edge, or taking Y now is as cheap: that's an edge, not a maker order
                    pf = p_fill(v, hrs)
                    bx = _best_take(l, X)
                    fx = l["fair"].get(X)
                    items.append({
                        "key": f"{v}|{d['id']}|{l['market']}|{Y}", "gameId": d["id"], "league": d.get("league"), "sport": d.get("sport"),
                        "matchup": f"{d['away']} @ {d['home']}", "commence": d["commence"], "market": l["market"],
                        "side": Y, "point": _side_point(d, l, Y), "team": Y if l["market"] != "totals" else None,
                        "venue": v, "venueName": C.BOOKS.get(v, {}).get("name", v), "bid": round(bid, 4), "cost": round(cost, 4),
                        "dec": round(1 / cost, 4), "am": M.american_from_prob(cost), "fair": round(fy, 4), "ev": round(ev, 4),
                        "pFill": round(pf, 3), "exp100": round(ev * pf * 100, 2), "hrs": round(hrs, 2),
                        "cancelBelow": round(cost * (1 + CANCEL_EDGE), 4),
                        "behind": {"side": X, "price": round(qx, 4), "avail": px.get("avail")},
                        "takeY": take_y, "fee": MAKER_FEE_K.get(v, 0) > 0,
                        # if it fills: the other side taken now at the best venue; an arb when the two cost under $1
                        "other": {"venue": bx[0], "venueName": C.BOOKS.get(bx[0], {}).get("name", bx[0]), "side": X, "cost": round(bx[1], 4), "ev": round(fx / bx[1] - 1, 4) if fx else None,
                                  "lock": round(1 / (cost + bx[1]) - 1, 4)} if bx else None,
                    })
    best = {}
    for it in items:     # one venue per game x market x side: the most expected dollars
        k = (it["gameId"], it["market"], it["side"])
        if k not in best or it["exp100"] > best[k]["exp100"]:
            best[k] = it
    out = sorted(best.values(), key=lambda x: -x["exp100"])
    # both sides of one line worth a maker order (each 2%+ over the fair): post both; if both fill, that's an arb
    side_of = {(o["gameId"], o["market"], o["side"]): o for o in out}
    for o in out:
        p = side_of.get((o["gameId"], o["market"], o["behind"]["side"]))
        if p:
            o["pair"] = {"venue": p["venue"], "venueName": p["venueName"], "bid": p["bid"], "cost": p["cost"], "lock": round(1 / (o["cost"] + p["cost"]) - 1, 4)}
    return out


def plan(o):
    """the policy for one order (Vince, Nov 17): arbs are always locked; otherwise only +EV legs are ever bet.
    -> what to do now and once it fills"""
    oth = o.get("other") or {}
    if o.get("pair"):
        now = "post both sides (both 2%+ over the fair)"
    else:
        now = "post this side only"
    name = oth.get("venueName") or oth.get("venue")
    if oth and (oth.get("ev") or -1) >= MIN_EDGE:
        fill = f"if it fills, also take the other side at {name}: it is +EV on its own" + (
            f" and the two lock {oth['lock'] * 100:.1f}%" if (oth.get("lock") or 0) > 0 else "")
    elif oth and (oth.get("lock") or 0) >= LOCK_MIN:
        fill = f"if it fills, take the other side at {name} right away: locks {oth['lock'] * 100:.1f}%"
    elif oth and (oth.get("lock") or 0) > 0:
        fill = (f"if it fills, hold it: taking the other side at {name} would lock only {oth['lock'] * 100:.1f}%, "
                f"under the {LOCK_MIN * 100:.0f}% arb bar, and that side is below its fair; lock it if the gap widens")
    else:
        fill = "if it fills, hold it: the other side costs more than it's worth"
    return now, fill


def _p(data_dir, *a):
    return os.path.join(data_dir, "track", "makers", *a)


def _load(fn, default):
    try:
        return json.load(open(fn))
    except Exception:
        return default


def _quote(doc, it):
    for l in _main_lines(doc):
        if l["market"] != it["market"]:
            continue
        for q in l.get("quotes") or []:
            if q.get("book") == it["venue"]:
                px = (q.get("prices") or {}).get(it["behind"]["side"]) or {}
                if px.get("dec"):
                    return l, 1 / px["dec"], px.get("avail")
        return l, None, None
    return None, None, None


def track(data_dir, game_docs, items, now, log=print):
    """follow every suggestion as if posted: fills, cancels, expiry"""
    os.makedirs(_p(data_dir), exist_ok=True)
    op = _load(_p(data_dir, "open.json"), {})
    by = {d["id"]: d for d in game_docs}
    done, n_new, fresh = [], 0, set()
    for it in items:
        if it["key"] in op or it["pFill"] < 0.2:
            continue
        op[it["key"]] = {**{k: it[k] for k in ("key", "gameId", "league", "sport", "matchup", "commence", "market", "side", "point",
                                                "team", "venue", "bid", "cost", "dec", "fair", "ev", "pFill", "cancelBelow")},
                         "behindSide": it["behind"]["side"], "ourX": round(1 - it["bid"], 4), "lastX": it["behind"]["price"],
                         "lastAvail": it["behind"]["avail"], "at": now.isoformat(), "status": "resting", "outbid": 0}
        fresh.add(it["key"])
        n_new += 1
    for k, e in list(op.items()):
        if e["status"] == "filled" and _iso(e["commence"]) > now and by.get(e["gameId"]):
            # a filled order whose other side can now be taken for a guaranteed profit: the policy locks it
            l = next((x for x in _main_lines(by[e["gameId"]]) if x["market"] == e["market"]), None)
            bx = _best_take(l, e["behindSide"]) if l else None
            fx = (l.get("fair") or {}).get(e["behindSide"]) if l else None
            if bx and e["cost"] + bx[1] < 1 - 1e-9 and (1 / (e["cost"] + bx[1]) - 1 >= LOCK_MIN or (fx and fx / bx[1] - 1 >= MIN_EDGE)):
                e["status"], e["lockAt"], e["lockVenue"], e["lockCost"] = "locked", now.isoformat(), bx[0], round(bx[1], 4)
            continue
        if e["status"] != "resting" or k in fresh:
            continue
        d = by.get(e["gameId"])
        if _iso(e["commence"]) <= now:
            e["status"] = "expired"
            continue
        if not d:
            continue
        l, qx, av = _quote(d, {"market": e["market"], "venue": e["venue"], "behind": {"side": e["behindSide"]}})
        if l is None:
            continue
        fy = (l.get("fair") or {}).get(e["side"])
        if fy is not None and fy < e["cancelBelow"]:
            e["status"], e["cancelAt"], e["fairAtCancel"] = "cancelled", now.isoformat(), round(fy, 4)
            continue
        if qx is None:
            continue
        if qx < e["ourX"] - TOL:
            e["outbid"] += 1                # someone else now offers takers a better price than ours
        elif abs(qx - e["lastX"]) < TOL and e.get("lastAvail") and av is not None and av < e["lastAvail"] * 0.98:
            e["status"], e["fillAt"], e["fairAtFill"] = "filled", now.isoformat(), round(fy, 4) if fy is not None else None
        elif qx > e["lastX"] + TOL:
            # the level behind ours is gone and takers now pay more: eaten (or pulled); counted as a fill, the
            # pessimistic read, since that's also when the market tends to move against a maker
            e["status"], e["fillAt"], e["fairAtFill"] = "filled", now.isoformat(), round(fy, 4) if fy is not None else None
        if e["status"] == "resting":
            e["lastX"], e["lastAvail"] = qx, av
    json.dump(op, open(_p(data_dir, "open.json"), "w"), separators=(",", ":"))
    if n_new:
        log(f"makers: {n_new} maker orders paper-posted")
    return op


def grade(data_dir, fin_by_gid, now, log=print):
    """filled paper orders whose game is over -> Methods board rows; every finished order -> the fill log"""
    import closemkt
    import ledger
    op = _load(_p(data_dir, "open.json"), {})
    rows, logs = [], []
    for k, e in list(op.items()):
        st = e["status"]
        start = _iso(e["commence"])
        if st == "resting":
            continue
        if st in ("expired", "cancelled"):
            logs.append({**e, "d": (start - timedelta(hours=8)).strftime("%Y-%m-%d")})
            op.pop(k)
            continue
        leg = {"gameId": e["gameId"], "market": e["market"], "side": e["side"], "point": e.get("point"), "team": e.get("team")}
        if e["gameId"] not in fin_by_gid and now - start < timedelta(hours=4):
            continue
        try:
            g = ledger.grade_leg(data_dir, leg, fin_by_gid)
        except Exception:
            g = None
        if g is None:
            if now - start > timedelta(days=4):
                op.pop(k)
            continue
        y = None if g.get("void") else g.get("y")
        if st == "locked":     # both sides held: the pair pays $1 whatever happens (a push or void returns both stakes)
            pc = e["cost"] + e["lockCost"]
            r = {"d": (start - timedelta(hours=8)).strftime("%Y-%m-%d"), "m": "maker", "gid": e["gameId"], "sport": e["sport"],
                 "league": e.get("league"), "mk": e["market"], "side": e["side"], "pt": e.get("point"), "venue": e["venue"],
                 "dec": round(1 / pc, 4), "fair": None, "close": None, "clv": None, "y": 1.0 if y is not None else None,
                 "pnl": round(1 / pc - 1, 4) if y is not None else 0.0, "locked": True, "lockVenue": e["lockVenue"]}
            rows.append(r)
            logs.append({**e, "d": r["d"], "y": y, "pnl": r["pnl"]})
            op.pop(k)
            continue
        try:
            c = closemkt.leg_close(data_dir, leg, e["sport"], e["commence"])
        except Exception:
            c = {}
        close = c.get("p")
        r = {"d": (start - timedelta(hours=8)).strftime("%Y-%m-%d"), "m": "maker", "gid": e["gameId"], "sport": e["sport"],
             "league": e.get("league"), "mk": e["market"], "side": e["side"], "pt": e.get("point"), "venue": e["venue"],
             "dec": e["dec"], "fair": e["fair"], "close": close, "ca": c.get("age"),
             "clv": round(close * e["dec"] - 1, 4) if close else None,
             "y": y, "pnl": 0.0 if y is None else round(y * e["dec"] - 1, 4),
             "fairAtFill": e.get("fairAtFill")}
        rows.append(r)
        logs.append({**e, "d": r["d"], "y": y, "pnl": r["pnl"], "clv": r["clv"]})
        op.pop(k)
    if rows:
        os.makedirs(os.path.join(data_dir, "track", "methods"), exist_ok=True)
        with open(os.path.join(data_dir, "track", "methods", "graded.jsonl"), "a") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    if logs:
        with open(_p(data_dir, "log.jsonl"), "a") as f:
            for r in logs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    json.dump(op, open(_p(data_dir, "open.json"), "w"), separators=(",", ":"))
    if rows:
        log(f"makers: {len(rows)} filled paper orders graded")
    return len(rows)


def fill_summary(data_dir):
    rows = []
    try:
        for line in open(_p(data_dir, "log.jsonl")):
            rows.append(json.loads(line))
    except Exception:
        pass
    op = _load(_p(data_dir, "open.json"), {})
    out = {"done": len(rows), "resting": sum(1 for e in op.values() if e["status"] == "resting"),
           "filledOpen": sum(1 for e in op.values() if e["status"] in ("filled", "locked")),
           "lockedOpen": sum(1 for e in op.values() if e["status"] == "locked")}
    for s in ("filled", "locked", "cancelled", "expired"):
        out[s] = sum(1 for r in rows if r.get("status") == s)
    n = out["filled"] + out["locked"] + out["cancelled"] + out["expired"]
    out["fillRate"] = round((out["filled"] + out["locked"]) / n, 3) if n else None
    exp = [r["pFill"] for r in rows if r.get("pFill") is not None]
    out["predicted"] = round(sum(exp) / len(exp), 3) if exp else None
    return out


def run(data_dir, game_docs, fin_by_gid, now, log=print):
    items = suggest(game_docs, now)
    for o in items:
        o["now"], o["ifFilled"] = plan(o)
    track(data_dir, game_docs, items, now, log)
    grade(data_dir, fin_by_gid, now, log)
    return {"at": now.isoformat(), "items": items[:40], "n": len(items), "paper": fill_summary(data_dir),
            "rules": {"minEdge": MIN_EDGE, "cancelEdge": CANCEL_EDGE, "aheadH": AHEAD_H, "feeK": MAKER_FEE_K}}
