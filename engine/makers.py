"""Maker orders (Nov 17): limit orders to rest on an exchange that are worth a lot more than the sharp fair if they fill.
Suggestions only for now (Vince places them by hand); the plan is to place, reprice and cancel them through the
exchanges' own APIs later.

How a maker order works on a two-outcome exchange: the best price a taker can buy side X at (qX, cents per $1) is some
maker's bid on the other side Y at 1 - qX. We bid Y one tick better (1 - qX + tick), so the next taker who wants X gets
our order first. If it fills we hold Y at that price (+ any maker fee). The make-take gap is the venue's spread
(qX + qY - 1): where it is wide, a bid can sit far under the fair.

Where (Vince, Nov 17: "10%+ if it fills; attack early markets and fringe sports"): every two-way market on Kalshi,
Polymarket US, Novig and ProphetX that the board prices off the market (game lines incl. alternates, periods and team
totals; player props), any sport, from AHEAD_H 96 h out to the start, bids 25-75c, EV after the maker fee >= MIN_EDGE
(5%; the site's bar defaults to 10%) and cheaper than taking Y. Only two-sided venue quotes with a spread up to MAX_GAP
25c, an ask within MAX_OFF 15 pts of the fair, and an edge under MAX_EV 60%: past those it is a stale order or a
mismatched number (the first unfiltered pass showed "+150%" bids on alt spreads that were exactly that). The fair has to be a market fair: lines with fairSource
sharp (no model-shaped or one-sided fairs), props with sharp fairs or 3+ books, since on thin alts and props a big gap
is as often our fair being off as the venue being wide.

The Nov 8-17 study (10 days of raw pulls, two-sided exchange quotes passing the checks below): the make-take gap widens
the earlier and the more fringe the market: main lines 1.0c median inside 6 h, 1.6c at 6-24 h, 2.4-2.9c a day or more
out; alts / periods 2.4c inside 6 h, 3-5c earlier; props 4.4c inside 6 h, ~8c earlier. Resting levels already 10%+
under the fair: unique ones over 10 days ~6,300 main-line (mostly 6 h+ out), ~16,000 alt / period, ~15,800 prop; they
held their value to the close (main +15% then / +14-16% at the close, alts +18% / +18%, props +16% / +16%). Fill
evidence is the weak part: the size behind shrinking or the level vanishing ran ~1-2 times an hour inside 6 h and
0.2-0.5 an hour earlier, and on thin books much of that is makers moving orders, not takers. So fill chances are a
rough ceiling until the paper record (and real orders) say otherwise. What decides a fill is who would take it: our order
sells the other side at 1 - bid, and when every sportsbook charges more for that side ("competitive"), a taker who wants it
can't do better anywhere; when a sportsbook is cheaper, only takers who don't shop fill it. Competitive ones rank first
(Nov 17 board: 324 of 2,437 bids at 10%+).

Paper record: each suggestion is followed as if posted (track/makers/open.json). Filled when the level behind ours is
eaten or vanishes; cancelled when the fair moves the edge under CANCEL_EDGE; expired at the start; locked when the other
side can be taken at the arb bar. Graded into the Methods board (track/methods/graded.jsonl, m maker) and the fill log
(track/makers/log/<day>.jsonl, with each order's edge so the fill rate can be read by band).

Policy (Vince, Nov 17), the same one the automation will follow: an arb is always locked; otherwise a leg is only ever
bet when it is +EV on its own. Post one side, or both sides when both clear the bar (if both fill, that's an arb). A
filled order is hedged only when the other side can be taken for a guaranteed profit at the arb bar (LOCK_MIN 3%) or the
other side is itself +EV; a thinner lock through a leg below its fair gives up more than it saves, so the order is held."""
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
MIN_EDGE, CANCEL_EDGE, AHEAD_H, LOCK_MIN = 0.05, 0.005, 96, 0.03
SHOW_EDGE = 0.10    # the site's default bar (Vince: 10%+ if it fills)
# eats per hour of a resting level by kind and hours to start (upper bound), Nov 8-17 study; venue multipliers vs average
# (levels already 10%+ under the fair; two-sided quotes that pass the checks below)
RATE = {"main": [(6, 1.01), (24, 0.38), (72, 0.47), (1e9, 0.22)],
        "alt": [(6, 1.05), (1e9, 0.30)],
        "prop": [(6, 1.94), (1e9, 0.30)]}
MAX_GAP, MAX_OFF, MAX_EV = 0.25, 0.15, 0.60   # a venue spread over 25c, an ask 15+ pts off the fair, or a 60%+ "edge" is a
                                             # stale order or a mismatched number, not a price anyone will take
VMULT = {"kalshi": 1.23, "polymarket_us": 1.26, "novig": 1.15, "prophetx": 0.65}
MAIN = ("h2h", "spreads", "totals")
TOL = 5e-4
P_FILL_CAP = 0.8
TAKE = {b for b, v in C.BOOKS.items() if v.get("venue") and v.get("kind") != "dfs" and b not in ("draftkings", "fanduel")}


def _iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _kind(mk, player):
    return "prop" if player else "main" if mk in MAIN else "alt"


def p_fill(venue, hours, kind="main"):
    """rough ceiling on the chance some of it fills before the start: 1 - exp(-integrated eat rate)"""
    lam, h, tab = 0.0, 0.0, RATE.get(kind, RATE["main"])
    while h < hours:
        step = min(0.5, hours - h)
        r = next(v for ub, v in tab if (hours - h) <= ub)
        lam += r * step * VMULT.get(venue, 1.0)
        h += step
    return min(P_FILL_CAP, 1 - math.exp(-lam))     # days of resting don't make a fill certain: fairs move, orders get repriced


def _cost(venue, bid):
    return bid + MAKER_FEE_K.get(venue, 0) * bid * (1 - bid)


def _market_fair(r, prop):
    if not r.get("fair") or r.get("modelFair") or r.get("oneSided") or r.get("est") or len(r.get("outcomes") or []) != 2:
        return False
    if prop:
        return r.get("fairSource") == "sharp" or (r.get("nBooks") or 0) >= 3
    return r.get("fairSource") == "sharp"


def _markets(game_docs, prop_docs):
    """every market-priced two-way market: (doc, row, locator)"""
    for d in game_docs:
        for l in d.get("lines") or []:
            if _market_fair(l, False):
                yield d, l, {"src": "line", "market": l["market"], "point": l.get("point"), "team": l.get("team")}
    for d in prop_docs:
        for r in d.get("rows") or []:
            if r.get("player") and _market_fair(r, True):
                yield d, r, {"src": "prop", "market": d["market"], "point": r.get("point"), "player": r["player"]}


def _side_point(doc, l, side, loc):
    pt = loc.get("point")
    if "spreads" in loc["market"] and pt is not None and side != doc.get("home"):
        return -pt
    return pt


def _best_take(r, side):
    best = None
    for q in r.get("quotes") or []:
        if q.get("book") not in TAKE or q.get("stale"):
            continue
        c = ((q.get("prices") or {}).get(side) or {}).get("cost")
        if c and 0.01 < c < 0.99 and (best is None or c < best[1]):
            best = (q["book"], c)
    return best


NOT_BOOK = {b for b, v in C.BOOKS.items() if v.get("kind") in ("exchange", "dfs")} | {"betfair_ex_eu", "betfair_ex_uk", "matchbook"}


def _elsewhere(r, side):
    """the cheapest price a taker could get `side` at any sportsbook on the board: (book, cost) or None"""
    best = None
    for q in r.get("quotes") or []:
        if q.get("book") in NOT_BOOK or q.get("stale"):
            continue
        c = ((q.get("prices") or {}).get(side) or {}).get("cost")
        if c and 0.01 < c < 0.99 and (best is None or c < best[1]):
            best = (q["book"], c)
    return best


def _label(loc, side, pt):
    mk = loc["market"]
    per = next((p for p in ("_q1", "_q2", "_q3", "_q4", "_h1", "_h2", "_p1", "_p2", "_p3") if mk.endswith(p)), "")
    tag = {"_q1": "1Q ", "_q2": "2Q ", "_q3": "3Q ", "_q4": "4Q ", "_h1": "1H ", "_h2": "2H ", "_p1": "P1 ", "_p2": "P2 ", "_p3": "P3 "}.get(per, "")
    if loc["src"] == "prop":
        stat = mk.replace("player_", "").replace("batter_", "").replace("pitcher_", "").replace("_alternate", "").replace("_", " ")
        return f"{loc['player']} {side} {pt} {stat}"
    if "team_totals" in mk:
        return f"{tag}{loc.get('team') or ''} {side} {pt}".strip()
    if "totals" in mk:
        return f"{tag}{side} {pt}"
    if "spreads" in mk:
        return f"{tag}{side} {'+' if pt and pt > 0 else ''}{pt}"
    return f"{tag}{side} to win"


def suggest(game_docs, prop_docs, now):
    items = []
    for d, r, loc in _markets(game_docs, prop_docs):
        if not d.get("commence"):
            continue
        hrs = (_iso(d["commence"]) - now).total_seconds() / 3600
        if not 0.05 < hrs <= AHEAD_H:
            continue
        kind = _kind(loc["market"], loc.get("player"))
        oc = r["outcomes"]
        for q in r.get("quotes") or []:
            v = q.get("book")
            if v not in VENUES or q.get("stale"):
                continue
            pr = q.get("prices") or {}
            qa, qb = (pr.get(oc[0]) or {}).get("dec"), (pr.get(oc[1]) or {}).get("dec")
            if not (qa and qb):
                continue          # one-sided at this venue: no make-take gap to read, and the most common mismatches
            spread = 1 / qa + 1 / qb - 1
            if not -0.02 <= spread <= MAX_GAP:
                continue
            for X in oc:
                Y = oc[1] if X == oc[0] else oc[0]
                px, py = pr.get(X) or {}, pr.get(Y) or {}
                if not px.get("dec"):
                    continue          # nobody bids Y there yet: no level to stand in front of (no fill evidence)
                qx = 1 / px["dec"]
                tick = TICK[v]
                bid = round((1 - qx) / tick) * tick + tick
                if not 0.25 <= bid <= 0.75:
                    continue
                cost = _cost(v, bid)
                fy, fx = r["fair"].get(Y), r["fair"].get(X)
                if fy is None or fx is None or qx - fx > MAX_OFF:
                    continue
                ev = fy / cost - 1
                if ev > MAX_EV:
                    continue
                take_y = py.get("cost")
                if ev < MIN_EDGE or (take_y is not None and take_y <= cost):
                    continue
                pf = p_fill(v, hrs, kind)
                bx = _best_take(r, X)
                # who takes it: our order sells X at 1 - bid. When no sportsbook offers X cheaper, a taker who wants X
                # can't do better anywhere (the realistic fills); when one does, only takers who don't shop will fill it
                el = _elsewhere(r, X)
                comp = el is not None and (1 - bid) <= el[1] + 0.005
                if not comp:
                    pf *= 0.25        # a sportsbook sells X cheaper: only takers who don't shop (a guess until the record says)
                pt = _side_point(d, r, Y, loc)
                items.append({
                    "key": f"{v}|{d['id']}|{loc['market']}|{loc.get('player') or ''}|{loc.get('point')}|{Y}",
                    "gameId": d["id"], "league": d.get("league"), "sport": d.get("sport"),
                    "matchup": f"{d.get('away')} @ {d.get('home')}", "commence": d["commence"], "market": loc["market"],
                    "kind": kind, "loc": loc, "side": Y, "point": pt, "player": loc.get("player"),
                    "team": (loc.get("team") or (Y if "totals" not in loc["market"] and kind != "prop" else None)),
                    "label": _label(loc, Y, pt), "fringe": bool(d.get("fringe")), "venue": v, "venueName": C.BOOKS.get(v, {}).get("name", v),
                    "bid": round(bid, 4), "cost": round(cost, 4), "dec": round(1 / cost, 4), "am": M.american_from_prob(cost),
                    "fair": round(fy, 4), "ev": round(ev, 4), "spread": round(spread, 4) if spread is not None else None,
                    "pFill": round(pf, 3), "exp100": round(ev * pf * 100, 2), "hrs": round(hrs, 2),
                    "cancelBelow": round(cost * (1 + CANCEL_EDGE), 4),
                    "behind": {"side": X, "price": round(qx, 4), "avail": px.get("avail")},
                    "takeY": take_y, "fee": MAKER_FEE_K.get(v, 0) > 0, "books": r.get("nBooks"), "competitive": comp,
                    "elsewhere": {"book": el[0], "name": C.BOOKS.get(el[0], {}).get("name", el[0]), "cost": round(el[1], 4)} if el else None,
                    "other": {"venue": bx[0], "venueName": C.BOOKS.get(bx[0], {}).get("name", bx[0]), "side": X,
                              "cost": round(bx[1], 4), "ev": round(fx / bx[1] - 1, 4) if fx else None,
                              "lock": round(1 / (cost + bx[1]) - 1, 4)} if bx else None,
                })
    best = {}
    for it in items:     # one venue per market side: the most expected dollars
        k = it["key"].split("|", 1)[1]
        if k not in best or it["exp100"] > best[k]["exp100"]:
            best[k] = it
    out = sorted(best.values(), key=lambda x: (not x["competitive"], -x["ev"] * (0.5 + x["pFill"])))
    side_of = {o["key"].split("|", 1)[1].rsplit("|", 1)[0] + "|" + o["side"]: o for o in out}
    for o in out:
        p = side_of.get(o["key"].split("|", 1)[1].rsplit("|", 1)[0] + "|" + o["behind"]["side"])
        if p:
            o["pair"] = {"venue": p["venue"], "venueName": p["venueName"], "bid": p["bid"], "cost": p["cost"],
                         "ev": p["ev"], "lock": round(1 / (o["cost"] + p["cost"]) - 1, 4)}
    return out


def plan(o):
    """the policy for one order: arbs are always locked; otherwise only +EV legs are ever bet"""
    oth = o.get("other") or {}
    now = "post both sides (both clear the bar)" if o.get("pair") else "post this side only"
    name = oth.get("venueName") or oth.get("venue")
    if oth and (oth.get("ev") or -1) >= 0.02:
        fill = f"if it fills, also take the other side at {name}: it is +EV on its own" + (
            f" and the two lock {oth['lock'] * 100:.1f}%" if (oth.get("lock") or 0) > 0 else "")
    elif oth and (oth.get("lock") or 0) >= LOCK_MIN:
        fill = f"if it fills, take the other side at {name} right away: locks {oth['lock'] * 100:.1f}%"
    elif oth and (oth.get("lock") or 0) > 0:
        fill = (f"if it fills, hold it: taking the other side at {name} would lock only {oth['lock'] * 100:.1f}%, "
                f"under the {LOCK_MIN * 100:.0f}% arb bar, through a leg below its fair")
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


def _find(by_game, by_prop, gid, loc):
    """the current row for a locator (old entries without one: the main line of that market)"""
    if not loc or loc.get("src") == "line":
        d = by_game.get(gid)
        if not d:
            return None
        mk = (loc or {}).get("market")
        cands = [l for l in d.get("lines") or [] if l.get("market") == mk and _market_fair(l, False)]
        if loc and loc.get("point") is not None:
            cands = [l for l in cands if l.get("point") is not None and abs(l["point"] - loc["point"]) < 1e-6 and (l.get("team") == loc.get("team"))]
        return max(cands, key=lambda l: l.get("fairWeight") or 0) if cands else None
    d = by_prop.get((gid, loc["market"]))
    if not d:
        return None
    return next((r for r in d.get("rows") or [] if r.get("player") == loc.get("player") and r.get("point") == loc.get("point")), None)


def _venue_q(r, venue, side):
    for q in (r or {}).get("quotes") or []:
        if q.get("book") == venue:
            px = (q.get("prices") or {}).get(side) or {}
            if px.get("dec"):
                return 1 / px["dec"], px.get("avail")
    return None, None


def track(data_dir, game_docs, prop_docs, items, now, log=print):
    os.makedirs(_p(data_dir), exist_ok=True)
    op = _load(_p(data_dir, "open.json"), {})
    by_game = {d["id"]: d for d in game_docs}
    by_prop = {(d["id"], d["market"]): d for d in prop_docs}
    n_new, fresh = 0, set()
    for it in sorted(items, key=lambda x: -x["ev"]):
        if it["key"] in op or it["pFill"] < 0.1 or n_new >= 400:
            continue
        op[it["key"]] = {**{k: it.get(k) for k in ("key", "gameId", "league", "sport", "matchup", "commence", "market", "kind", "loc",
                                                    "side", "point", "player", "team", "label", "venue", "bid", "cost", "dec", "fair",
                                                    "ev", "spread", "pFill", "hrs", "cancelBelow", "competitive", "fringe")},
                         "behindSide": it["behind"]["side"], "ourX": round(1 - it["bid"], 4), "lastX": it["behind"]["price"],
                         "lastAvail": it["behind"]["avail"], "at": now.isoformat(), "status": "resting", "outbid": 0}
        fresh.add(it["key"])
        n_new += 1
    for k, e in list(op.items()):
        r = _find(by_game, by_prop, e["gameId"], e.get("loc"))
        if e["status"] == "filled" and _iso(e["commence"]) > now and r:
            bx = _best_take(r, e["behindSide"])
            fx = (r.get("fair") or {}).get(e["behindSide"])
            if bx and e["cost"] + bx[1] < 1 - 1e-9 and (1 / (e["cost"] + bx[1]) - 1 >= LOCK_MIN or (fx and fx / bx[1] - 1 >= 0.02)):
                e["status"], e["lockAt"], e["lockVenue"], e["lockCost"] = "locked", now.isoformat(), bx[0], round(bx[1], 4)
            continue
        if e["status"] != "resting" or k in fresh:
            continue
        if _iso(e["commence"]) <= now:
            e["status"] = "expired"
            continue
        if not r:
            continue
        fy = (r.get("fair") or {}).get(e["side"])
        if fy is not None and fy < e["cancelBelow"]:
            e["status"], e["cancelAt"], e["fairAtCancel"] = "cancelled", now.isoformat(), round(fy, 4)
            continue
        qx, av = _venue_q(r, e["venue"], e["behindSide"])
        if qx is None:
            continue
        if qx < e["ourX"] - TOL:
            e["outbid"] += 1
        elif abs(qx - e["lastX"]) < TOL and e.get("lastAvail") and av is not None and av < e["lastAvail"] * 0.98:
            e["status"], e["fillAt"], e["fairAtFill"] = "filled", now.isoformat(), round(fy, 4) if fy is not None else None
        elif qx > e["lastX"] + TOL:
            e["status"], e["fillAt"], e["fairAtFill"] = "filled", now.isoformat(), round(fy, 4) if fy is not None else None
        if e["status"] == "resting":
            e["lastX"], e["lastAvail"] = qx, av
    json.dump(op, open(_p(data_dir, "open.json"), "w"), separators=(",", ":"))
    if n_new:
        log(f"makers: {n_new} maker orders paper-posted")
    return op


def grade(data_dir, fin_by_gid, now, log=print):
    import closemkt
    import ledger
    op = _load(_p(data_dir, "open.json"), {})
    rows, logs = [], []
    for k, e in list(op.items()):
        st = e["status"]
        start = _iso(e["commence"])
        day = (start - timedelta(hours=8)).strftime("%Y-%m-%d")
        if st == "resting":
            continue
        if st in ("expired", "cancelled"):
            logs.append({**e, "d": day})
            op.pop(k)
            continue
        leg = {"gameId": e["gameId"], "market": e["market"], "side": e["side"], "point": e.get("point"), "team": e.get("team"),
               "player": e.get("player")}
        if e["gameId"] not in fin_by_gid and now - start < timedelta(hours=4):
            continue
        try:
            g = ledger.grade_leg(data_dir, leg, fin_by_gid)
        except Exception:
            g = None
        if g is None:
            if now - start > timedelta(days=4):
                logs.append({**e, "d": day, "ungraded": True})     # no final for it (fringe leagues): still a fill
                op.pop(k)
            continue
        y = None if g.get("void") else g.get("y")
        base = {"d": day, "m": "maker", "gid": e["gameId"], "sport": e["sport"], "league": e.get("league"), "mk": e["market"],
                "kind": e.get("kind"), "side": e["side"], "pt": e.get("point"), "player": e.get("player"), "venue": e["venue"],
                "evAtPost": e.get("ev")}
        if st == "locked":
            pc = e["cost"] + e["lockCost"]
            r = {**base, "dec": round(1 / pc, 4), "fair": None, "close": None, "clv": None, "y": 1.0 if y is not None else None,
                 "pnl": round(1 / pc - 1, 4) if y is not None else 0.0, "locked": True, "lockVenue": e["lockVenue"]}
        else:
            try:
                c = closemkt.leg_close(data_dir, leg, e["sport"], e["commence"])
            except Exception:
                c = {}
            close = c.get("p")
            r = {**base, "dec": e["dec"], "fair": e["fair"], "close": close, "ca": c.get("age"),
                 "clv": round(close * e["dec"] - 1, 4) if close else None,
                 "y": y, "pnl": 0.0 if y is None else round(y * e["dec"] - 1, 4), "fairAtFill": e.get("fairAtFill")}
        rows.append(r)
        logs.append({**e, "d": day, "y": y, "pnl": r["pnl"], "clv": r.get("clv")})
        op.pop(k)
    if rows:
        os.makedirs(os.path.join(data_dir, "track", "methods"), exist_ok=True)
        with open(os.path.join(data_dir, "track", "methods", "graded.jsonl"), "a") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    if logs:
        write_log(data_dir, logs)
    json.dump(op, open(_p(data_dir, "open.json"), "w"), separators=(",", ":"))
    if rows:
        log(f"makers: {len(rows)} filled paper orders graded")
    return len(rows)


def write_log(data_dir, logs):
    """the paper record, one file per Pacific day (track/makers/log/<d>.jsonl). Nov 24 audit: the single log.jsonl reached
    GitHub's 100 MB file limit and every main-lane snapshot push was refused from 7 PM PT on, grading included."""
    by = {}
    for r in logs:
        by.setdefault(str(r.get("d") or "undated"), []).append(r)
    os.makedirs(_p(data_dir, "log"), exist_ok=True)
    for d, rs in by.items():
        with open(_p(data_dir, "log", f"{d}.jsonl"), "a") as f:
            for r in rs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")


def log_rows(data_dir):
    """every logged order: the day files, plus a legacy log.jsonl if one is still there"""
    import glob
    fns = sorted(glob.glob(_p(data_dir, "log", "*.jsonl")))
    if os.path.exists(_p(data_dir, "log.jsonl")):
        fns = [_p(data_dir, "log.jsonl")] + fns
    rows = []
    for fn in fns:
        try:
            with open(fn) as f:
                for line in f:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    return rows


def fill_summary(data_dir):
    rows = log_rows(data_dir)
    op = _load(_p(data_dir, "open.json"), {})

    def summ(rs, opn):
        out = {"done": len(rs), "resting": sum(1 for e in opn if e["status"] == "resting"),
               "filledOpen": sum(1 for e in opn if e["status"] in ("filled", "locked")),
               "lockedOpen": sum(1 for e in opn if e["status"] == "locked")}
        for s in ("filled", "locked", "cancelled", "expired"):
            out[s] = sum(1 for r in rs if r.get("status") == s)
        n = out["filled"] + out["locked"] + out["cancelled"] + out["expired"]
        out["fillRate"] = round((out["filled"] + out["locked"]) / n, 3) if n else None
        exp = [r["pFill"] for r in rs if r.get("pFill") is not None]
        out["predicted"] = round(sum(exp) / len(exp), 3) if exp else None
        return out
    out = summ(rows, list(op.values()))
    hi = lambda x: (x.get("ev") or 0) >= SHOW_EDGE
    out["bar"] = summ([r for r in rows if hi(r)], [e for e in op.values() if hi(e)])
    hc = lambda x: hi(x) and x.get("competitive")
    out["barComp"] = summ([r for r in rows if hc(r)], [e for e in op.values() if hc(e)])
    return out


def run(data_dir, game_docs, prop_docs, fin_by_gid, now, log=print):
    scan = {}
    try:        # early markets and fringe sports from the maker scan (engine/makerscan.py), merged into the board's games
        import makerscan
        try:
            lines_at = json.load(open(os.path.join(data_dir, "latest", "refresh_state.json"))).get("lines") or {}
        except Exception:
            lines_at = {}
        game_docs = makerscan.docs(data_dir, now, game_docs, lines_at)
        scan = makerscan.load(data_dir)
    except Exception as e:
        log(f"makers: scan merge failed ({type(e).__name__}: {str(e)[:80]})")
    items = suggest(game_docs, prop_docs, now)
    for o in items:
        o["now"], o["ifFilled"] = plan(o)
    try:            # the census: every 5%+ order seen today (engine/oppreport.py)
        import oppreport
        oppreport.maker_census(data_dir, items, now)
    except Exception as e:
        log(f"makers: census failed ({type(e).__name__})")
    track(data_dir, game_docs, prop_docs, items, now, log)
    run.graded = grade(data_dir, fin_by_gid, now, log)
    shown = [o for o in items if o["ev"] >= SHOW_EDGE][:120] + [o for o in items if o["ev"] < SHOW_EDGE][:30]
    drop = ("loc", "sport", "key", "cancelBelow")
    shown = [{**{k: v for k, v in o.items() if k not in drop}, "cancelBelow": o["cancelBelow"]} for o in shown]
    return {"at": now.isoformat(), "items": shown, "n": len(items), "nBar": sum(1 for o in items if o["ev"] >= SHOW_EDGE),
            "nBarComp": sum(1 for o in items if o["ev"] >= SHOW_EDGE and o["competitive"]),
            "paper": fill_summary(data_dir),
            "scan": {"at": scan.get("at"), **(scan.get("last") or {}), "fringeGames": sum(1 for d in game_docs if d.get("fringe"))},
            "nFringe": sum(1 for o in items if o.get("fringe") and o["ev"] >= SHOW_EDGE),
            "nEarly": sum(1 for o in items if o["hrs"] > 36 and o["ev"] >= SHOW_EDGE),
            "rules": {"minEdge": MIN_EDGE, "showEdge": SHOW_EDGE, "cancelEdge": CANCEL_EDGE, "aheadH": AHEAD_H, "feeK": MAKER_FEE_K,
                      "lockMin": LOCK_MIN}}
