"""Arbitrage and middles across the venues Vince can use (all-in prices: Kalshi/Polymarket fees included).

Arb: both sides of the same line at two venues cost less than 1 together (1 / sum - 1 is locked profit, whatever
happens). Middle: one side at one number and the other side at a better number elsewhere, e.g. Over 220.5 at A and
Under 223.5 at B: both win when the result lands between. Each middle is priced with the sharp fair: the chance the
result lands in the window is fair(Over 220.5) - fair(Over 223.5) (from our alternate-line fairs), and the stakes are
split so either single win returns the same, so

    EV = (1 - S) + P(middle)        S = 1/d1 + 1/d2 (per unit returned)

Only quotes that are not stale; pick'em apps are left out (their payouts are per entry, not per pick).
"""
from datetime import datetime, timezone

import config as C


MAX_AGE_MIN = 45          # prices we pulled longer ago than this are often a prop that's been taken down or a price that's moved
_NOW = [None]
_CUR = [(None, None, None)]      # (minutes since we pulled this game's main lines, minutes since we pulled its event markets)
_META = {}                 # (book, name, am) -> {"age": minutes, "exch": bool} for the legs built from the last scan


def _age(upd):
    if not upd or not _NOW[0]:
        return None
    try:
        return (_NOW[0] - datetime.fromisoformat(upd.replace("Z", "+00:00"))).total_seconds() / 60
    except Exception:
        return None


def _side_quotes(line, side):
    """[(cost, book, name, am)] for one side at Vince's venues, cheapest first (prices we pulled recently only)"""
    out = []
    # how long ago WE pulled this market: main game lines come with the sport's pull, alternates and props with the game's
    # event pull (a book's own last_update can be hours old on a quiet market that's still live, so it isn't the test)
    main = (line.get("market") or "") in C.GAME_MARKETS
    age = _CUR[0][0] if main else _CUR[0][1]
    # older than one and a half of its normal refresh cycles (5 min near the start, hours for tomorrow's props) means
    # the engine hasn't been refreshing it: often a pulled prop or a moved price, so it's left out
    hrs = _CUR[0][2]
    cyc = next((m for h, m in (C.LINES_STALE if main else C.PROPS_STALE) if hrs is not None and hrs <= h), 240)
    if age is not None and age > max(MAX_AGE_MIN, 1.5 * cyc):
        return out
    for q in line.get("quotes") or []:
        if not q.get("venue") or q.get("stale"):
            continue
        pe = (q.get("prices") or {}).get(side) or {}
        c = pe.get("cost")
        if c and 0 < c < 1:
            am = pe.get("allInAm", pe.get("am"))
            _META[(q["book"], c)] = {"age": None if age is None else round(age, 1), "exch": q.get("kind") == "exchange", "avail": pe.get("avail")}
            out.append((c, q["book"], q["name"], am))
    return sorted(out)


def _am_of_cost(c):
    """American odds for an all-in cost (implied probability)"""
    if not c or c <= 0 or c >= 1:
        return None
    d = 1 / c
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def _leg(book, name, am, cost, label, fair=None, spec=None, limit=None):
    """spec: what the ledger grades from (market, side, that side's own point, team for team totals), so a logged
    arb or middle settles itself leg by leg"""
    m = _META.get((book, cost)) or {}
    # holdTo: the worst price this leg can take (the other leg's price as it is) before the arb, the risk-free middle
    # or the middle's edge is gone. Exchanges (ProphetX, Novig, Kalshi, Polymarket) can move against you with size.
    return dict({"book": book, "name": name, "am": am, "dec": round(1 / cost, 4), "label": label,
                 "fair": fair, "off": None if fair is None else round(fair - cost, 4),
                 "age": m.get("age"), "exch": bool(m.get("exch")), "avail": m.get("avail"),
                 "holdTo": _am_of_cost(limit) if limit else None}, **(spec or {}))


THIN = 25      # dollars: a pair that can't take $50 in total (about $25 a leg) at these prices isn't worth chasing


def _max_pay(pairs):
    """pairs: [(cost, avail)]. The biggest payout X both legs fill at their prices (a leg's stake is X x cost), or None
    when no leg says how much is on offer (sportsbooks; their limits aren't published)"""
    caps = [a / c for c, a in pairs if a is not None and c]
    return min(caps) if caps else None


def _thin(*legs):
    mp = _max_pay([(c, _META.get((b, c), {}).get("avail")) for c, b in legs])
    return mp is not None and mp * sum(c for c, _ in legs) < 2 * THIN


def _spec(l, side, home=None, mk=None):
    mk = l.get("market") or mk or ""
    pt = l.get("point")
    if "spreads" in mk and pt is not None and home is not None:
        pt = pt if side == home else -pt
    out = {"market": mk, "side": side, "point": None if mk.startswith("h2h") else pt}
    if "team_totals" in mk:
        out["team"] = l.get("team")
    return out


def _arbs_in(lines, meta, labels, home=None):
    out = []
    for l in lines:
        outs = l.get("outcomes") or []
        if len(outs) != 2:
            continue
        a, b = (_side_quotes(l, o) for o in outs)
        if not a or not b:
            continue
        # the cheapest pair from two different venues
        best = None
        for ca, ba, na, aa in a[:3]:
            for cb, bb, nb, ab in b[:3]:
                if ba == bb:
                    continue
                # a pair with real money on both sides beats a slightly better one that only fills a few dollars
                rank = (_thin((ca, ba), (cb, bb)), ca + cb)
                if ca + cb < 0.995 and (best is None or rank < best[0]):
                    best = (rank, (ca, ba, na, aa), (cb, bb, nb, ab))
        if best:
            (_, s), (ca, ba, na, aa), (cb, bb, nb, ab) = best
            out.append(dict(meta, kind="arb", market=l.get("market") or meta.get("market"), point=l.get("point"), profit=round(1 / s - 1, 4),
                            legs=[_leg(ba, na, aa, ca, labels(l, outs[0]), (l.get("fair") or {}).get(outs[0]), _spec(l, outs[0], home, meta.get("market")), 1 - cb),
                                  _leg(bb, nb, ab, cb, labels(l, outs[1]), (l.get("fair") or {}).get(outs[1]), _spec(l, outs[1], home, meta.get("market")), 1 - ca)],
                            stakes=[round(ca / s, 4), round(cb / s, 4)]))
    return out


def _middles_in(lines, meta, labels, up, down, fair_up, key, home=None):
    """lines of one market family; up(l) / down(l) name the side that wins when the result is high / low; fair_up(l)
    is the fair of the high side; key(l) the number on the high side's terms (higher number = harder for the high side)"""
    out = []
    ls = [l for l in lines if l.get("fair") and len(l.get("outcomes") or []) == 2]
    for l1 in ls:          # high side here: wins when result > k1
        q1 = _side_quotes(l1, up(l1))
        if not q1:
            continue
        for l2 in ls:      # low side here: wins when result < k2; a middle needs k2 > k1
            if key(l2) <= key(l1):
                continue
            q2 = _side_quotes(l2, down(l2))
            if not q2:
                continue
            c1, b1, n1, a1 = q1[0]
            c2, b2, n2, a2 = q2[0]
            if b1 == b2 and len(q2) > 1:
                c2, b2, n2, a2 = q2[1]
            if b1 == b2:
                continue
            S = c1 + c2
            pm = (fair_up(l1) or 0) - (fair_up(l2) or 0)
            if pm <= 0:
                continue
            ev = (1 - S) + pm
            if ev >= 0.005 and 1 - S >= -0.06:
                free = 1 - S >= 0           # no loss whichever side wins, and both win in the window
                lim1, lim2 = ((1 - c2), (1 - c1)) if free else ((1 + pm - c2), (1 + pm - c1))
                out.append(dict(meta, kind="middle", free=free, market=l1.get("market") or meta.get("market"), window=[key(l1), key(l2)], pMiddle=round(pm, 4),
                                ev=round(ev, 4), worst=round(1 - S, 4),
                                legs=[_leg(b1, n1, a1, c1, labels(l1, up(l1)), (l1.get("fair") or {}).get(up(l1)), _spec(l1, up(l1), home, meta.get("market")), min(lim1, 0.999)),
                                      _leg(b2, n2, a2, c2, labels(l2, down(l2)), (l2.get("fair") or {}).get(down(l2)), _spec(l2, down(l2), home, meta.get("market")), min(lim2, 0.999))],
                                stakes=[round(c1 / S, 4), round(c2 / S, 4)]))
    out.sort(key=lambda x: -x["ev"])
    return out[:3]          # the best few windows per market


def find(game_docs, prop_docs, now_iso=None, pulled=None):
    """pulled: the engine's refresh_state ({lines: {sport: at}, events: {gameId: at}}) for how fresh each price is"""
    out = []
    _NOW[0] = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) if now_iso else datetime.now(timezone.utc)
    _META.clear()
    pulled = pulled or {}
    sport_of = {g["id"]: g.get("sport") for g in game_docs}
    def ages(gid, sport, commence):
        hrs = (datetime.fromisoformat(commence.replace("Z", "+00:00")) - _NOW[0]).total_seconds() / 3600
        return (_age((pulled.get("lines") or {}).get(sport)), _age((pulled.get("events") or {}).get(gid)), hrs)
    for g in game_docs:
        if now_iso and g["commence"] <= now_iso:
            continue
        meta = {"gameId": g["id"], "league": g["league"], "commence": g["commence"], "matchup": f"{g['away']} @ {g['home']}"}
        _CUR[0] = ages(g["id"], g.get("sport"), g["commence"])

        def lab(l, o, g=g):
            mk = l.get("market") or ""
            if "spreads" in mk:
                pt = l["point"] if o == g["home"] else -l["point"]
                return f"{o} {pt:+g}"
            if "team_totals" in mk:
                return f"{l.get('team')} {o} {l['point']:g}"
            if "totals" in mk:
                return f"{o} {l['point']:g}"
            return f"{o} ML" if mk == "h2h" else f"{o} ({mk.replace('_', ' ')})"
        lines = [l for l in g.get("lines") or [] if "_q" not in l.get("market", "") and "_h" not in l.get("market", "")[-3:]
                 and "_p" not in l.get("market", "")[-3:]]
        out += _arbs_in(lines, meta, lab, g["home"])
        tot = [l for l in lines if (l.get("market") or "").replace("alternate_", "") == "totals"]
        out += _middles_in(tot, meta, lab, lambda l: "Over", lambda l: "Under", lambda l: (l.get("fair") or {}).get("Over"), lambda l: l["point"], g["home"])
        sp = [l for l in lines if (l.get("market") or "").replace("alternate_", "") == "spreads" and l.get("point") is not None]
        # home side covers when margin > -point: the home "high" side at line h wins above -h
        out += _middles_in(sp, meta, lab, lambda l: g["home"], lambda l: g["away"], lambda l: (l.get("fair") or {}).get(g["home"]), lambda l: -l["point"], g["home"])
    for p in prop_docs:
        if now_iso and p["commence"] <= now_iso:
            continue
        meta = {"gameId": p["id"], "league": p["league"], "commence": p["commence"], "matchup": f"{p['away']} @ {p['home']}"}
        _CUR[0] = ages(p["id"], p.get("sport") or sport_of.get(p["id"]), p["commence"])
        mname = p["market"].split("_", 1)[-1].replace("_", " ")
        by = {}
        for r in p.get("rows") or []:
            by.setdefault(r.get("player"), []).append(r)
        for player, rows in by.items():
            lab = lambda l, o, player=player: f"{player} {o} {l['point']:g} {mname}" if l.get("point") is not None else f"{player} {mname} {o}"
            m = dict(meta, player=player)
            out += _arbs_in(rows, dict(m, market=p["market"]), lab)
            ou = [r for r in rows if r.get("point") is not None and "Over" in (r.get("outcomes") or [])]
            out += _middles_in(ou, dict(m, market=p["market"]), lab, lambda l: "Over", lambda l: "Under",
                               lambda l: (l.get("fair") or {}).get("Over"), lambda l: l["point"])
    for o in out:
        o.setdefault("market", None)
        # an "arb" or middle 8%+ is usually a stale quote, a palpable error or different rules (void on DNP, OT):
        # flagged so it is checked before anything is placed, and never alerted
        o["suspect"] = (o.get("profit") or 0) > 0.08 or any(abs(x.get("off") or 0) > 0.12 for x in o["legs"])
        o["score"] = o["profit"] if o["kind"] == "arb" else o["ev"]
        if o.get("free") and not o["suspect"]:
            o["score"] += 1         # a middle that can't lose goes first
        o["oldest"] = max((x.get("age") or 0) for x in o["legs"])
        # size: the most each leg fills at its price (exchanges publish it; sportsbooks don't) -> the payout that fits,
        # the total stake and what it locks in. Thin ones (under $50 in all) stay listed but drop down and never alert.
        mp = _max_pay([(1 / x["dec"], x.get("avail")) for x in o["legs"]])
        if mp is not None:
            S = sum(1 / x["dec"] for x in o["legs"])
            o["maxPay"] = round(mp, 2)
            o["maxStake"] = round(mp * S, 2)
            o["maxProfit"] = round(mp * (1 - S), 2) if o["kind"] == "arb" else round(mp * o.get("ev", 0), 2)
            o["thin"] = mp * S < 2 * THIN
            if o["thin"]:
                o["score"] -= 0.5
    out.sort(key=lambda x: -x["score"])
    return out[:150]
