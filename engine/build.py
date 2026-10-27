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
    complete = [q for q in quotes if all(o in q["probs"] for o in outcomes)
                and C.MIN_BOOK <= sum(q["probs"][o] for o in outcomes) <= C.MAX_BOOK]
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


def _reprice(line, fair, source, weight):
    """Replace a line's fair (e.g. with a model fair) and recompute every quote's edge."""
    line["fair"] = {o: _r(p) for o, p in fair.items()}
    line["fairAm"] = {o: M.american_from_prob(p) for o, p in fair.items()}
    line["fairSource"], line["fairWeight"] = source, round(weight, 2)
    best, best_venue = {}, {}
    for q in line["quotes"]:
        for o, pe in q["prices"].items():
            if o in fair:
                pe["edge"] = _r(fair[o] - pe["cost"])
                pe["ev"] = _r(M.ev_per_dollar(fair[o], pe["cost"]))
            if q["stale"]:
                continue
            for bucket, ok in ((best, True), (best_venue, q["venue"])):
                if ok and (o not in bucket or pe["cost"] < bucket[o]["cost"]):
                    bucket[o] = {"book": q["book"], "name": q["name"], "am": pe.get("allInAm", pe["am"]),
                                 "cost": pe["cost"], "edge": pe.get("edge")}
    line["best"], line["bestVenue"] = best, best_venue


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
            if not o.get("price") or o["price"] < C.MIN_DEC_PRICE:  # 99c+ = placeholder, no liquidity
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
    # Some books quote a 3-way (regulation, with a draw) moneyline under the same key.
    # Keep 2-way and 3-way quotes apart: mixing them corrupts the fair.
    ml = _collect(bms, "h2h", lambda o: ("h2h", o["name"], None))
    if "h2h" in ml:
        quotes = list(ml["h2h"]["quotes"].values())
        two = [q for q in quotes if set(q["dec"]) <= {home, away}]
        three = [q for q in quotes if "Draw" in q["dec"]]
        soccer = ev["sport_key"].startswith("soccer")
        if soccer and three:
            lines.append(_finish_line({"market": "h2h", "id": "h2h", "point": None}, three, [away, home, "Draw"]))
        elif two:
            lines.append(_finish_line({"market": "h2h", "id": "h2h", "point": None}, two, [away, home]))
            if three:
                lines.append(_finish_line({"market": "h2h_3way", "id": "h2h_3way", "point": None}, three, [away, home, "Draw"]))

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
# Extra game markets from the event endpoint (alternates, team totals, periods, specials)

PLAYER_PREFIX = ("player_", "batter_", "pitcher_")


def is_player_market(mk):
    return mk.startswith(PLAYER_PREFIX)


def base_market(mk):
    return mk[:-len("_alternate")] if mk.endswith("_alternate") else mk


def add_extra_lines(ev, game):
    """Append every non-player market of an event payload to the game doc's lines."""
    home, away = ev["home_team"], ev["away_team"]
    bms = ev.get("bookmakers", [])
    markets = sorted({m["key"] for bm in bms for m in bm.get("markets", []) if not is_player_market(m["key"])})
    have = {(l["market"], l["id"]) for l in game["lines"]}
    for mk in markets:
        if mk in ("h2h", "spreads", "totals"):
            continue
        if "spreads" in mk:
            def kf(o, mk=mk):
                pt = o.get("point") or 0
                hp = pt if o["name"] == home else -pt
                return (f"{mk}:{hp:+g}", o["name"], hp)
            for lid, L in _collect(bms, mk, kf).items():
                if (mk, lid) not in have:
                    game["lines"].append(_finish_line({"market": mk, "id": lid, "point": L["point"]}, list(L["quotes"].values()), [away, home]))
        elif "team_totals" in mk:
            def kf(o, mk=mk):
                team = o.get("description") or "?"
                return (f"{mk}:{team}:{o.get('point')}", o["name"], o.get("point"))
            for lid, L in _collect(bms, mk, kf).items():
                team = lid.split(":")[1]
                line = _finish_line({"market": mk, "id": lid, "point": L["point"], "team": team}, list(L["quotes"].values()), ["Over", "Under"])
                game["lines"].append(line)
        elif "totals" in mk:
            def kf(o, mk=mk):
                return (f"{mk}:{o.get('point')}", o["name"], o.get("point"))
            for lid, L in _collect(bms, mk, kf).items():
                game["lines"].append(_finish_line({"market": mk, "id": lid, "point": L["point"]}, list(L["quotes"].values()), ["Over", "Under"]))
        else:   # h2h_q1, h2h_3_way, btts, draw_no_bet, double_chance, ...
            def kf(o, mk=mk):
                pt = o.get("point")
                return (f"{mk}" + (f":{pt}" if pt is not None else ""), o["name"] + (f" ({o['description']})" if o.get("description") else ""), pt)
            for lid, L in _collect(bms, mk, kf).items():
                names = []
                for q in L["quotes"].values():
                    for n in q["dec"]:
                        if n not in names:
                            names.append(n)
                order = [n for n in (away, home, "Draw", "Yes", "No") if n in names] + [n for n in names if n not in (away, home, "Draw", "Yes", "No")]
                if len(order) > 6:
                    continue   # correct score style markets: too many outcomes to de-vig sensibly here
                game["lines"].append(_finish_line({"market": mk, "id": lid, "point": L["point"]}, list(L["quotes"].values()), order))
    # main flags for per-period markets: most books, closest to a coin flip
    groups = {}
    for l in game["lines"]:
        if l["market"] not in ("h2h", "spreads", "totals") and l.get("fair") and not l["market"].startswith("alternate"):
            groups.setdefault((l["market"], l.get("team")), []).append(l)
    for ls in groups.values():
        best = max(ls, key=lambda l: (l["nBooks"], -abs(list(l["fair"].values())[0] - 0.5)))
        best["isMain"] = True
    return game


# ---------------------------------------------------------------------------
# Player props

# Distribution of a stat, used only to move a sharp fair from the main line to
# another line (alt ladders, pick'em lines). Counting stats use a negative
# binomial with a per-stat dispersion k (variance = k * mean); yardage uses a normal.
DISPERSION = {
    "basketball_nba": {"player_points": 2.0, "player_rebounds": 1.5, "player_assists": 1.5,
                       "player_threes": 1.3, "player_points_rebounds_assists": 2.5,
                       "player_points_rebounds": 2.2, "player_points_assists": 2.2,
                       "player_rebounds_assists": 1.7, "player_blocks": 1.2, "player_steals": 1.2,
                       "player_blocks_steals": 1.25, "player_turnovers": 1.2},
    "icehockey_nhl": {"player_points": 1.05, "player_goals": 1.03, "player_assists": 1.05,
                      "player_shots_on_goal": 1.15, "player_blocked_shots": 1.2,
                      "player_power_play_points": 1.03, "player_total_saves": 1.6},
    "baseball_mlb": {"batter_hits": 1.0, "batter_total_bases": 1.6, "batter_rbis": 1.3,
                     "batter_runs_scored": 1.1, "batter_hits_runs_rbis": 1.6, "batter_walks": 1.05,
                     "batter_strikeouts": 1.05, "pitcher_strikeouts": 1.3, "pitcher_outs": 1.5,
                     "pitcher_hits_allowed": 1.2, "pitcher_earned_runs": 1.4, "pitcher_walks": 1.1},
    "americanfootball_nfl": {"player_receptions": 1.2, "player_pass_completions": 1.3,
                             "player_pass_attempts": 1.4, "player_rush_attempts": 1.3,
                             "player_pass_tds": 1.1, "player_pass_interceptions": 1.05,
                             "player_sacks": 1.2, "player_tackles_assists": 1.3},
}
DISPERSION["basketball_wnba"] = DISPERSION["basketball_ncaab"] = DISPERSION["basketball_nba"]
DISPERSION["americanfootball_ncaaf"] = DISPERSION["americanfootball_nfl"]


def _is_normal(market):
    return market.endswith("_yds") or "longest" in market


def _sd_normal(market, m):
    if market == "player_pass_yds":
        return 0.16 * m + 18
    if market == "player_rush_reception_yds":
        return 0.42 * m + 9
    return 0.45 * m + 8


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _nb_cdf_tail(mean, k, t):
    """P(X >= t) for a negative binomial with mean `mean` and variance k*mean (Poisson when k=1)."""
    if t <= 0:
        return 1.0
    mean = max(mean, 1e-6)
    cdf = 0.0
    if k <= 1.0001:
        p = math.exp(-mean)
        for x in range(t):
            cdf += p
            p *= mean / (x + 1)
    else:
        r, q = mean / (k - 1), 1.0 / k          # successes, success prob
        logp = r * math.log(q)                   # P(X=0)
        for x in range(t):
            cdf += math.exp(logp)
            logp += math.log((x + r) / (x + 1)) + math.log(1 - q)
    return max(0.0, min(1.0, 1 - cdf))


def p_over(market, mean, line, sport=""):
    """Fair P(over) at `line`, pushes removed on whole-number lines."""
    if _is_normal(market):
        return 1 - _phi((line - mean) / _sd_normal(market, mean))
    k = DISPERSION.get(sport, {}).get(market, 1.3)
    if line == math.floor(line):
        over = _nb_cdf_tail(mean, k, int(line) + 1)
        push = _nb_cdf_tail(mean, k, int(line)) - over
        return over / max(1e-9, 1 - push)
    return _nb_cdf_tail(mean, k, int(math.floor(line)) + 1)


def mean_from_line(market, line, fair_over, sport=""):
    lo, hi = 0.0, max(line * 4 + 10, 10)
    for _ in range(60):
        mid = (lo + hi) / 2
        if p_over(market, mid, line, sport) < fair_over:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def build_props(ev, label):
    """Returns {market: doc}. One Firestore doc per (event, market)."""
    bms = ev.get("bookmakers", [])
    # fold alternate ladders into their base market so every line of a stat lives together
    merged = []
    for bm in bms:
        mk_map = {}
        for m in bm.get("markets", []):
            if not is_player_market(m["key"]):
                continue
            base = base_market(m["key"])
            if base in mk_map:
                mk_map[base]["outcomes"] = mk_map[base]["outcomes"] + m["outcomes"]
            else:
                mk_map[base] = {**m, "key": base}
        merged.append({**bm, "markets": list(mk_map.values())})
    bms = merged
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
                mean = mean_from_line(mk, main["point"], main["fair"]["Over"], ev["sport_key"])
                main["isMain"] = True
            for l in plines:
                # judge every pick'em line and every thin alt line from the main line's sharp fair
                if mean is not None and l["point"] is not None:
                    po = p_over(mk, mean, l["point"], ev["sport_key"])
                    thin = l is not main and l.get("fairWeight", 0) < 0.6 * main["fairWeight"]
                    if (thin or not l.get("fair")) and l.get("quotes") and l["outcomes"] == ["Over", "Under"]:
                        _reprice(l, {"Over": po, "Under": 1 - po}, "model from main line", main["fairWeight"])
                        l["modelFair"] = True
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

_PERIODS = {"q1": "1Q", "q2": "2Q", "q3": "3Q", "q4": "4Q", "h1": "1H", "h2": "2H", "p1": "1P", "p2": "2P", "p3": "3P",
            "1st_1_innings": "1st inn", "1st_3_innings": "F3", "1st_5_innings": "F5", "1st_7_innings": "F7",
            "s1": "1st set", "s2": "2nd set"}


def market_qual(mk):
    """unit qualifier for markets that are not counted in points/goals/runs"""
    return " corners" if mk.endswith("_corners") else " cards" if mk.endswith("_cards") else " sets" if "_set_" in mk else ""


def market_period(mk):
    for k, v in _PERIODS.items():
        if mk.endswith("_" + k):
            return v + " "
    return ""


def edges(game_docs, prop_docs, min_edge=C.EDGE_LEAN, min_weight=2.0):
    out = []

    def scan(doc, line, label):
        if not line.get("fair") or line.get("fairWeight", 0) < min_weight:
            return
        if line.get("modelFair") and min(line["fair"].values()) < 0.15:
            return  # far-tail alt lines: the stat model is not trusted that far out
        for q in line["quotes"]:
            if not q["venue"] or q["stale"]:
                continue
            for o, pe in q["prices"].items():
                e = pe.get("edge")
                if e is not None and e >= min_edge:
                    mp = (line.get("model") or {}).get(o)
                    logic = None if mp is None else "agrees" if mp >= pe["cost"] + 0.01 else "disagrees" if mp < pe["cost"] - 0.02 else "neutral"
                    out.append({"modelP": mp, "logic": logic,
                        "gameId": doc["id"], "league": doc["league"], "commence": doc["commence"],
                        "matchup": f"{doc['away']} @ {doc['home']}", "market": line.get("market") or doc.get("market"),
                        "label": label(o), "book": q["book"], "bookName": q["name"],
                        "am": pe.get("allInAm", pe["am"]), "fair": line["fair"][o],
                        "fairAm": line["fairAm"][o], "edge": e, "ev": pe.get("ev"),
                        "fairWeight": line["fairWeight"], "model": bool(line.get("modelFair")),
                    })

    for g in game_docs:
        for l in g["lines"]:
            mk = l["market"]
            pre = market_period(mk)
            if mk == "h2h":
                scan(g, l, lambda o: f"{o} ML")
            elif mk == "h2h_3way":
                scan(g, l, lambda o: f"{o} (regulation)")
            elif "spreads" in mk:
                scan(g, l, lambda o, l=l, g=g, pre=pre, q=market_qual(mk): f"{pre}{o} {(l['point'] if o == g['home'] else -l['point']):+g}{q}")
            elif "team_totals" in mk:
                scan(g, l, lambda o, l=l, pre=pre, q=market_qual(mk): f"{pre}{l.get('team')} {o} {l['point']:g}{q}")
            elif "totals" in mk:
                scan(g, l, lambda o, l=l, pre=pre, q=market_qual(mk): f"{pre}{o} {l['point']:g}{q}")
            else:
                scan(g, l, lambda o, l=l, pre=pre, mk=mk: f"{pre}{mk.replace('_', ' ')}: {o}" + (f" {l['point']:g}" if l.get("point") is not None else ""))
    for p in prop_docs:
        for l in p["rows"]:
            n0 = len(out)
            scan(p, l, lambda o, l=l, p=p: f"{l['player']} {o} {l['point']} {p['market'].replace('player_', '').replace('_', ' ')}")
            for x in out[n0:]:
                x["player"], x["headshot"], x["last10"], x["point"] = l["player"], l.get("headshot"), l.get("last10"), l.get("point")
            for d in l.get("dfs", []):
                if d.get("edge") is not None and d["edge"] >= 0.02:
                    mp = (l.get("model") or {}).get(d["side"])
                    logic = None if mp is None else "agrees" if mp >= C.DFS_BREAKEVEN + 0.01 else "disagrees" if mp < C.DFS_BREAKEVEN - 0.02 else "neutral"
                    out.append({"modelP": mp, "logic": logic,
                        "gameId": p["id"], "league": p["league"], "commence": p["commence"],
                        "matchup": f"{p['away']} @ {p['home']}", "market": p["market"],
                        "label": f"{l['player']} {d['side']} {d['point']} {p['market'].replace('player_', '').replace('_', ' ')}",
                        "book": d["book"], "bookName": d["name"], "am": None, "pickem": True,
                        "player": l["player"], "headshot": l.get("headshot"), "last10": l.get("last10"), "point": d["point"],
                        "fair": d["p"], "edge": d["edge"], "fairWeight": l.get("fairWeight"),
                    })
    # rank: model-derived fairs (alt lines, pick'em lines) count half until backtests earn them more trust
    for x in out:
        w = 0.5 if x.get("model") or x.get("pickem") else 1.0
        w *= {"agrees": 1.5, "disagrees": 0.4}.get(x.get("logic"), 1.0)
        x["score"] = round(x["edge"] * w, 4)
    out.sort(key=lambda x: -x["score"])
    return out
