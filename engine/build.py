"""Turn raw Odds API events into board documents.

For every line (market + point) we compute:
  * the sharp no-vig fair probability of each side (weighted by book sharpness),
  * every book's all-in cost (fees included) and its edge over fair,
  * the best price overall and the best price at Vince's venues,
  * stale flags for books that have not updated with the market.
Player props get the same treatment, plus a line-shift model so pick'em lines
that differ from the books' line can still be judged.
"""
import math
from datetime import datetime, timezone

import config as C
import oddsmath as M


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _r(x, n=4):
    return None if x is None else round(x, n)


# ---------------------------------------------------------------------------
# Generic line builder

def _consensus(quotes, outcomes):
    """quotes: list of dicts with 'book' (config dict) and 'probs' {outcome: implied (vig-in or mid)}.
    Returns (fair dict, source label, total weight)."""
    complete = [q for q in quotes if all(o in q["probs"] for o in outcomes)]
    if not complete:
        return None, None, 0.0
    acc = {o: 0.0 for o in outcomes}
    wsum = 0.0
    for q in complete:
        w = q["book"]["weight"]
        if w <= 0:
            continue
        nv = M.no_vig([q["probs"][o] for o in outcomes])
        for o, p in zip(outcomes, nv):
            acc[o] += w * p
        wsum += w
    source = "sharp"
    if wsum == 0:
        # no sharp book on this line: equal-weight every complete book, flagged
        source = "all books"
        for q in complete:
            nv = M.no_vig([q["probs"][o] for o in outcomes])
            for o, p in zip(outcomes, nv):
                acc[o] += p
            wsum += 1.0
    fair = M.no_vig([acc[o] / wsum for o in outcomes])
    return dict(zip(outcomes, fair)), source, wsum


def _price_entry(dec, fee, fair_p):
    raw = M.implied_from_decimal(dec)
    cost = M.all_in_prob(raw, fee)
    e = {"dec": round(dec, 3), "am": M.american_from_decimal(dec), "cost": _r(cost)}
    if fee:
        e["allInAm"] = M.american_from_prob(cost)
    if fair_p is not None:
        e["edge"] = _r(fair_p - cost)
        e["ev"] = _r(M.ev_per_dollar(fair_p, cost))
    return e


def _finish_line(line, quotes, outcomes):
    fair, source, wsum = _consensus(quotes, outcomes)
    newest = max((q["updated"] for q in quotes if q["updated"]), default=None)
    out_quotes, best, best_venue = [], {}, {}
    for q in quotes:
        b = q["book"]
        stale = bool(newest and q["updated"] and
                     (newest - q["updated"]).total_seconds() > C.STALE_MINUTES * 60)
        prices = {}
        for o in outcomes:
            if o not in q["dec"]:
                continue
            fp = fair[o] if fair else None
            pe = _price_entry(q["dec"][o], b.get("fee"), fp)
            prices[o] = pe
            if stale:
                continue
            for bucket, ok in ((best, True), (best_venue, b.get("venue"))):
                if ok and (o not in bucket or pe["cost"] < bucket[o]["cost"]):
                    bucket[o] = {"book": b["key"], "name": b["name"], "am": pe.get("allInAm", pe["am"]),
                                 "cost": pe["cost"], "edge": pe.get("edge")}
        out_quotes.append({
            "book": b["key"], "name": b["name"], "kind": b["kind"],
            "venue": bool(b.get("venue")), "stale": stale,
            "updated": q["updated"].isoformat() if q["updated"] else None,
            "prices": prices,
        })
    kind_order = {"sharp": 0, "exchange": 1, "book": 2, "dfs": 3, "other": 4}
    out_quotes.sort(key=lambda x: (not x["venue"], kind_order.get(x["kind"], 9), x["name"]))
    line.update({
        "outcomes": outcomes,
        "fair": {o: _r(p) for o, p in fair.items()} if fair else None,
        "fairAm": {o: M.american_from_prob(p) for o, p in fair.items()} if fair else None,
        "fairSource": source, "fairWeight": round(wsum, 2),
        "nBooks": len(quotes), "quotes": out_quotes,
        "best": best, "bestVenue": best_venue,
    })
    return line


def _collect(bookmakers, market_key, keyfn):
    """Group a market's outcomes into lines. keyfn(outcome) -> (line_id, outcome_label, point)."""
    lines = {}
    for bm in bookmakers:
        b = C.book(bm["key"], bm.get("title"))
        mk = {m["key"]: m for m in bm.get("markets", [])}
        m = mk.get(market_key)
        if not m:
            continue
        lay = mk.get(market_key + "_lay")
        lay_px = {}
        if lay:
            for o in lay["outcomes"]:
                lid, lab, _ = keyfn(o)
                lay_px[(lid, lab)] = o["price"]
        upd = _ts(m.get("last_update") or bm.get("last_update"))
        for o in m["outcomes"]:
            lid, lab, pt = keyfn(o)
            if b["kind"] == "dfs":  # pick'em: the line matters, the price usually does not exist
                L = lines.setdefault(lid, {"point": pt, "quotes": {}})
                q = L["quotes"].setdefault(b["key"], {"book": b, "dec": {}, "probs": {}, "updated": upd})
                q["dec"][lab] = o.get("price")
                continue
            if not o.get("price") or o["price"] <= 1:
                continue
            L = lines.setdefault(lid, {"point": pt, "quotes": {}})
            q = L["quotes"].setdefault(b["key"], {"book": b, "dec": {}, "probs": {}, "updated": upd})
            q["dec"][lab] = o["price"]
            back_p = M.implied_from_decimal(o["price"])
            lp = lay_px.get((lid, lab))
            # exchange mid-price when a lay price exists
            q["probs"][lab] = (back_p + M.implied_from_decimal(lp)) / 2 if lp else back_p
    return lines


# ---------------------------------------------------------------------------
# Game lines

def build_game(ev, label):
    home, away = ev["home_team"], ev["away_team"]
    bms = ev.get("bookmakers", [])
    lines = []

    # moneyline (2- or 3-way)
    ml = _collect(bms, "h2h", lambda o: ("h2h", o["name"], None))
    if "h2h" in ml:
        names = set()
        for q in ml["h2h"]["quotes"].values():
            names.update(q["dec"].keys())
        outcomes = [away, home] + sorted(n for n in names if n not in (home, away))
        lines.append(_finish_line({"market": "h2h", "id": "h2h", "point": None},
                                  list(ml["h2h"]["quotes"].values()), outcomes))

    # spreads keyed by the home team's point
    def sp_key(o):
        pt = o.get("point")
        hp = pt if o["name"] == home else -pt
        return (f"spreads:{hp:+g}", o["name"], hp)
    for lid, L in _collect(bms, "spreads", sp_key).items():
        lines.append(_finish_line({"market": "spreads", "id": lid, "point": L["point"]},
                                  list(L["quotes"].values()), [away, home]))

    def tot_key(o):
        return (f"totals:{o.get('point'):g}", o["name"], o.get("point"))
    for lid, L in _collect(bms, "totals", tot_key).items():
        lines.append(_finish_line({"market": "totals", "id": lid, "point": L["point"]},
                                  list(L["quotes"].values()), ["Over", "Under"]))

    # flag the main spread/total: most books, then closest to a coin flip
    for mkt in ("spreads", "totals"):
        cands = [l for l in lines if l["market"] == mkt and l["fair"]]
        if cands:
            main = max(cands, key=lambda l: (l["nBooks"], -abs(list(l["fair"].values())[0] - 0.5)))
            main["isMain"] = True
    for l in lines:
        if l["market"] == "h2h":
            l["isMain"] = True

    return {
        "id": ev["id"], "sport": ev["sport_key"], "league": label,
        "home": home, "away": away, "commence": ev["commence_time"],
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Player props

# Rough standard deviation of a stat given its mean, used only to move a fair
# probability from one line to a nearby line (e.g. a pick'em line 1 point off).
def stat_sd(market, mean):
    m = max(mean, 0.1)
    if market in ("player_points",):
        return 0.22 * m + 3.0
    if market in ("player_points_rebounds_assists",):
        return 0.20 * m + 4.0
    if market in ("player_points_rebounds", "player_points_assists"):
        return 0.21 * m + 3.5
    if market.endswith("_yds"):
        return {"player_pass_yds": 0.16 * m + 18}.get(market, 0.45 * m + 8)
    if market in ("player_rush_reception_yds",):
        return 0.42 * m + 9
    # counting stats: over-dispersed Poisson
    return math.sqrt(1.3 * m)


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def p_over(market, mean, line):
    sd = stat_sd(market, mean)
    thresh = math.floor(line) + 0.5 if line != math.floor(line) else line  # integer stats
    return 1 - _phi((thresh - mean) / sd)


def mean_from_line(market, line, fair_over):
    lo, hi = 0.0, max(line * 3 + 10, 10)
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_over(market, mid, line) < fair_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def build_props(ev, label):
    """Returns {market: doc}. One Firestore doc per (event, market)."""
    bms = ev.get("bookmakers", [])
    markets = sorted({m["key"] for bm in bms for m in bm.get("markets", [])})
    docs = {}
    for mk in markets:
        def keyfn(o):
            player = o.get("description") or "?"
            pt = o.get("point")
            return (f"{player}|{pt}", o["name"], pt)
        grouped = _collect(bms, mk, keyfn)
        by_player = {}
        for lid, L in grouped.items():
            player = lid.split("|")[0]
            names = set()
            for q in L["quotes"].values():
                names.update(q["dec"].keys())
            if {"Over", "Under"} & names:
                outcomes = ["Over", "Under"]
            elif {"Yes", "No"} >= names:
                outcomes = ["Yes", "No"] if "No" in names else ["Yes"]
            else:
                outcomes = sorted(names)
            quotes = [q for q in L["quotes"].values() if q["book"]["kind"] != "dfs"]
            dfs = [q for q in L["quotes"].values() if q["book"]["kind"] == "dfs"]
            line = {"player": player, "point": L["point"], "dfs": [
                {"book": q["book"]["key"], "name": q["book"]["name"], "point": L["point"],
                 "dec": q["dec"]} for q in dfs]}
            if quotes:
                _finish_line(line, quotes, outcomes)
                if len(outcomes) == 1:
                    line["fair"] = None  # one-sided market: no honest de-vig
                    line["fairAm"] = None
            else:
                line.update({"outcomes": outcomes, "fair": None, "quotes": [], "nBooks": 0,
                             "best": {}, "bestVenue": {}})
            by_player.setdefault(player, []).append(line)

        rows = []
        for player, plines in by_player.items():
            two_sided = [l for l in plines if l.get("fair") and "Over" in l["fair"]]
            main = max(two_sided, key=lambda l: (l["fairWeight"], l["nBooks"]), default=None)
            mean = None
            if main and main["point"] is not None:
                mean = mean_from_line(mk, main["point"], main["fair"]["Over"])
                main["isMain"] = True
            for l in plines:
                # judge every pick'em line (and books' off lines) from the main line
                if mean is not None and l["point"] is not None:
                    po = p_over(mk, mean, l["point"])
                    if not l.get("fair") or l is not main and l.get("fairWeight", 0) < (main or {}).get("fairWeight", 0):
                        l["modelFair"] = {"Over": _r(po), "Under": _r(1 - po)}
                    for d in l["dfs"]:
                        d["fairOver"], d["fairUnder"] = _r(po), _r(1 - po)
                        side = "Over" if po >= 0.5 else "Under"
                        pside = max(po, 1 - po)
                        d["side"], d["p"] = side, _r(pside)
                        d["edge"] = _r(pside - C.DFS_BREAKEVEN)
                rows.append(l)
            if mean is not None:
                for l in plines:
                    l["mean"] = round(mean, 2)
        if rows:
            docs[mk] = {"id": ev["id"], "sport": ev["sport_key"], "league": label,
                        "home": ev["home_team"], "away": ev["away_team"],
                        "commence": ev["commence_time"], "market": mk, "rows": rows}
    return docs


# ---------------------------------------------------------------------------
# Edge list across everything

def edges(game_docs, prop_docs, min_edge=C.EDGE_LEAN, min_weight=2.0):
    out = []

    def scan(doc, line, label):
        if not line.get("fair") or line.get("fairWeight", 0) < min_weight:
            return
        for q in line["quotes"]:
            if not q["venue"] or q["stale"]:
                continue
            for o, pe in q["prices"].items():
                e = pe.get("edge")
                if e is not None and e >= min_edge:
                    out.append({
                        "gameId": doc["id"], "league": doc["league"], "commence": doc["commence"],
                        "matchup": f"{doc['away']} @ {doc['home']}", "market": line.get("market") or doc.get("market"),
                        "label": label(o), "book": q["book"], "bookName": q["name"],
                        "am": pe.get("allInAm", pe["am"]), "fair": line["fair"][o],
                        "fairAm": line["fairAm"][o], "edge": e, "ev": pe.get("ev"),
                        "fairWeight": line["fairWeight"],
                    })

    for g in game_docs:
        for l in g["lines"]:
            if l["market"] == "h2h":
                scan(g, l, lambda o: f"{o} ML")
            elif l["market"] == "spreads":
                scan(g, l, lambda o, l=l, g=g: f"{o} {(l['point'] if o == g['home'] else -l['point']):+g}")
            else:
                scan(g, l, lambda o, l=l: f"{o} {l['point']:g}")
    for p in prop_docs:
        for l in p["rows"]:
            scan(p, l, lambda o, l=l, p=p: f"{l['player']} {o} {l['point']} {p['market'].replace('player_', '').replace('_', ' ')}")
            for d in l.get("dfs", []):
                if d.get("edge") is not None and d["edge"] >= 0.02:
                    out.append({
                        "gameId": p["id"], "league": p["league"], "commence": p["commence"],
                        "matchup": f"{p['away']} @ {p['home']}", "market": p["market"],
                        "label": f"{l['player']} {d['side']} {d['point']} {p['market'].replace('player_', '').replace('_', ' ')}",
                        "book": d["book"], "bookName": d["name"], "am": None, "pickem": True,
                        "fair": d["p"], "edge": d["edge"], "fairWeight": l.get("fairWeight"),
                    })
    out.sort(key=lambda x: -x["edge"])
    return out
