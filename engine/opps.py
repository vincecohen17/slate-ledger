"""Arbitrage and middles across the venues Vince can use (all-in prices: Kalshi/Polymarket fees included).

Arb: both sides of the same line at two venues cost less than 1 together (1 / sum - 1 is locked profit, whatever
happens). Middle: one side at one number and the other side at a better number elsewhere, e.g. Over 220.5 at A and
Under 223.5 at B: both win when the result lands between. Each middle is priced with the sharp fair: the chance the
result lands in the window is fair(Over 220.5) - fair(Over 223.5) (from our alternate-line fairs), and the stakes are
split so either single win returns the same, so

    EV = (1 - S) + P(middle)        S = 1/d1 + 1/d2 (per unit returned)

Only quotes that are not stale; pick'em apps are left out (their payouts are per entry, not per pick).
"""


def _side_quotes(line, side):
    """[(cost, book, name, am)] for one side at Vince's venues, cheapest first"""
    out = []
    for q in line.get("quotes") or []:
        if not q.get("venue") or q.get("stale"):
            continue
        pe = (q.get("prices") or {}).get(side) or {}
        c = pe.get("cost")
        if c and 0 < c < 1:
            out.append((c, q["book"], q["name"], pe.get("allInAm", pe.get("am"))))
    return sorted(out)


def _leg(book, name, am, cost, label, fair=None, spec=None):
    """spec: what the ledger grades from (market, side, that side's own point, team for team totals), so a logged
    arb or middle settles itself leg by leg"""
    return dict({"book": book, "name": name, "am": am, "dec": round(1 / cost, 4), "label": label,
                 "fair": fair, "off": None if fair is None else round(fair - cost, 4)}, **(spec or {}))


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
                if best is None or ca + cb < best[0]:
                    best = (ca + cb, (ca, ba, na, aa), (cb, bb, nb, ab))
        if best and best[0] < 0.995:
            s, (ca, ba, na, aa), (cb, bb, nb, ab) = best
            out.append(dict(meta, kind="arb", market=l.get("market") or meta.get("market"), point=l.get("point"), profit=round(1 / s - 1, 4),
                            legs=[_leg(ba, na, aa, ca, labels(l, outs[0]), (l.get("fair") or {}).get(outs[0]), _spec(l, outs[0], home, meta.get("market"))),
                                  _leg(bb, nb, ab, cb, labels(l, outs[1]), (l.get("fair") or {}).get(outs[1]), _spec(l, outs[1], home, meta.get("market")))],
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
                out.append(dict(meta, kind="middle", market=l1.get("market") or meta.get("market"), window=[key(l1), key(l2)], pMiddle=round(pm, 4),
                                ev=round(ev, 4), worst=round(1 - S, 4),
                                legs=[_leg(b1, n1, a1, c1, labels(l1, up(l1)), (l1.get("fair") or {}).get(up(l1)), _spec(l1, up(l1), home, meta.get("market"))),
                                      _leg(b2, n2, a2, c2, labels(l2, down(l2)), (l2.get("fair") or {}).get(down(l2)), _spec(l2, down(l2), home, meta.get("market")))],
                                stakes=[round(c1 / S, 4), round(c2 / S, 4)]))
    out.sort(key=lambda x: -x["ev"])
    return out[:3]          # the best few windows per market


def find(game_docs, prop_docs, now_iso=None):
    out = []
    for g in game_docs:
        if now_iso and g["commence"] <= now_iso:
            continue
        meta = {"gameId": g["id"], "league": g["league"], "commence": g["commence"], "matchup": f"{g['away']} @ {g['home']}"}

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
    out.sort(key=lambda x: -x["score"])
    return out[:150]
