"""Grades Vince's logged bets and Claude's picks after the games: the result (from the same box scores the tracker uses,
or the ESPN final for game lines), and closing line value against the sharp no-vig fair at the close (the last pre-game
snapshot, track/open or track/closed).

Bets (Firestore bets/{id}, written by the site) get an `auto` field {result, clv, closeFair, at, legs}; the site applies it
(the promo math lives there). Picks (data repo picks/<date>.json) get status won/lost/push, profit in units and clv.

A bet or pick names its bet with gameId + market + side (+ point, player, team). Older picks only have the selection text;
those are matched to the snapshot's rows by player, number and words.
"""
import closemkt as CMK
import closecal
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

import build as B
import livebets as LB
import refs as R
import tracker as T

PROP_WORDS = [("points rebounds assists", "player_points_rebounds_assists"), ("pra", "player_points_rebounds_assists"),
              ("rebounds", "player_rebounds"), ("assists", "player_assists"), ("threes", "player_threes"), ("points", "player_points"),
              ("shots on goal", "player_shots_on_goal"), ("saves", "player_total_saves"), ("receiving yards", "player_reception_yds"),
              ("reception yds", "player_reception_yds"), ("rushing yards", "player_rush_yds"), ("rush yds", "player_rush_yds"),
              ("passing yards", "player_pass_yds"), ("pass yds", "player_pass_yds"), ("receptions", "player_receptions"),
              ("strikeouts", "pitcher_strikeouts"), ("total bases", "batter_total_bases"), ("hits", "batter_hits")]


def _snap(data_dir, gid):
    for sub in ("open", "closed"):
        fn = os.path.join(data_dir, "track", sub, f"{gid}.json")
        if os.path.exists(fn):
            try:
                return json.load(open(fn))
            except Exception:
                return None
    return None


_res_cache = {}


def _result(data_dir, snap, fin_by_gid, need_players):
    """final scores (home/away of our game) and, for props, the box score"""
    key = (snap["id"], need_players)
    if key in _res_cache:
        return _res_cache[key]
    res = None
    try:
        res = T._mlb_game(data_dir, snap) if snap["sport"] == "baseball_mlb" else T._espn_game(data_dir, snap)
    except Exception:
        res = None
    f = fin_by_gid.get(snap["id"])
    if f and need_players and (not res or not res.get("players")) and f.get("espnId") and f["home"].get("score") is not None:
        # finished tonight, before the daily box-score collection: read ESPN's final box score directly
        try:
            import live as LV
            bx, _ = LV.box(snap["sport"], f["espnId"])
        except Exception:
            bx = {}
        if bx:
            swap = T._norm(f["home"].get("name") or "") != T._norm(snap["home"]) and T._norm(f["away"].get("name") or "") == T._norm(snap["home"])
            hs, as_ = f["home"]["score"], f["away"]["score"]
            res = {"hs": as_ if swap else hs, "as": hs if swap else as_, "players": bx, "espn": True}
    if not res and not need_players:
        if snap.get("result"):
            res = dict(snap["result"], players={})
        else:
            f = fin_by_gid.get(snap["id"])
            if f and f["home"].get("score") is not None:
                swap = T._norm(f["home"].get("name") or "") != T._norm(snap["home"]) and T._norm(f["away"].get("name") or "") == T._norm(snap["home"])
                hs, as_ = f["home"]["score"], f["away"]["score"]
                res = {"hs": as_ if swap else hs, "as": hs if swap else as_, "players": {}}
    _res_cache[key] = res
    return res


def _canon(snap, leg):
    """the snapshot row for this bet and whether the bet is the row's side"""
    mk, pt, player = leg.get("market"), leg.get("point"), leg.get("player")
    side = leg.get("side")
    best = None
    for r in snap.get("rows", []):
        if r["mk"] != mk:
            continue
        if player and T._norm(r.get("player") or "") != T._norm(player):
            continue
        if not player and r.get("player"):
            continue
        rpt = r.get("pt")
        if "spreads" in mk and rpt is not None and pt is not None:
            # rows are the home side; an away bet at +x is the home row at -x
            want = pt if T._norm(side or "") == T._norm(snap["home"]) else -pt
            if abs(rpt - want) > 1e-6:
                continue
        elif pt is not None and rpt is not None and abs(rpt - pt) > 1e-6:
            continue
        if leg.get("team") and r.get("team") and T._norm(leg["team"]) != T._norm(r["team"]):
            continue
        best = r
        break
    if not best:
        return None, None
    same = side in (best["side"],) or (side is not None and T._norm(side) == T._norm(best["side"]))
    return best, same


def _close_est(snap, leg):
    """closing chance for a prop leg on a number no book priced (a pick'em app's own line): the player's sharp mean at the
    close, from his most balanced priced row, at this number"""
    mk, side, pt = leg.get("market"), leg.get("side"), leg.get("point")
    if side not in ("Over", "Under") or pt is None:
        return None
    rows = [r for r in snap.get("rows", []) if r["mk"] == mk and r.get("f") is not None and r.get("pt") is not None
            and T._norm(r.get("player") or "") == T._norm(leg.get("player") or "")]
    if not rows:
        return None
    r = min(rows, key=lambda x: abs(x["f"] - 0.5))
    f_over = r["f"] if r.get("side") == "Over" else 1 - r["f"]
    try:
        mean = B.mean_from_line(mk, r["pt"], f_over, snap.get("sport") or "")
        po = B.p_over(mk, mean, pt, snap.get("sport") or "")
    except Exception:
        return None
    if po is None:
        return None
    return round(po if side == "Over" else 1 - po, 4)


FOOTBALL = ("americanfootball_nfl", "americanfootball_ncaaf")


class _Zeros(dict):
    def get(self, k, d=None):
        return 0


def _football_counting(sport, mk):
    """a football player market the box score reads (so a player with no row in its category had zero)"""
    return T.stat(sport, mk, _Zeros()) is not None


def _priced_at_close(snap, player, max_age=60, min_books=3):
    """the books still had this player's props up in a pull within max_age minutes of the start"""
    age = T.close_age(snap, True)
    if age is None or age > max_age:
        return False
    n = T._norm(player)
    return any(T._norm(r.get("player") or "") == n and ((r.get("x") or {}).get("nb") or 0) >= min_books
               for r in snap.get("rows", []))


def _scratched(snap, res, leg, fin_by_gid, max_age=60, min_rows=10):
    """a non-football player who never appeared: the game is final with a full box score (ESPN leaves inactive players
    out of it entirely, so there's no dnp row), the close is fresh, and the books had pulled his props by then (fewer
    than 3 still quoted him). Such a leg is void, as the books settle it; a name the box spells differently while the
    books still priced him at the close is left ungraded, never voided."""
    if leg.get("gameId") not in fin_by_gid or len((res or {}).get("players") or {}) < min_rows:
        return False
    age = T.close_age(snap, True)
    if age is None or age > max_age:
        return False
    return not _priced_at_close(snap, leg["player"], max_age=max_age)


def grade_leg(data_dir, leg, fin_by_gid):
    """{y: 1/0/None(push), close: sharp fair of this side at the close} or None while not final"""
    snap = _snap(data_dir, leg.get("gameId"))
    if not snap:
        return None
    start = datetime.fromisoformat(snap["commence"].replace("Z", "+00:00"))
    # graded as soon as the game is final (finals.collect only lists finished games), else 3 hours after the start
    if leg.get("gameId") not in fin_by_gid and datetime.now(timezone.utc) - start < timedelta(hours=3):
        return None
    row, same = _canon(snap, leg)
    mk, side = leg.get("market") or "", leg.get("side")
    is_prop = bool(leg.get("player"))
    res = _result(data_dir, snap, fin_by_gid, is_prop)
    if not res:
        return None
    close, est = None, False
    t = "main"
    if row is not None:
        fc = row.get("fa", row["f"])          # our fair at the close (the self-corrected one where fairadj applies)
        close = fc if same else 1 - fc
        # main = the number most books quote for this market (the row with the most weight behind it); else alt
        peers = [r for r in snap.get("rows", []) if r["mk"] == row["mk"] and r.get("player") == row.get("player") and r.get("team") == row.get("team")]
        if "alternate" in mk or (peers and (row.get("w") or 0) < max((r.get("w") or 0) for r in peers)):
            t = "alt"
    elif is_prop:
        close = _close_est(snap, leg)
        est = close is not None
    if is_prop:
        p = R.find_player(res["players"], leg["player"], leg.get("team"))
        football = snap["sport"] in FOOTBALL
        if not p:
            # football box scores list only players with a stat in some category: a player the books still priced at a
            # fresh close (inactives are out by then) and who is missing from the final box played and recorded nothing
            if football and leg.get("gameId") in fin_by_gid and _priced_at_close(snap, leg["player"]):
                p = {"name": leg["player"], "absent": True}
            elif not football and _scratched(snap, res, leg, fin_by_gid):
                return {"y": None, "close": close, "void": True, "scratch": True}
            else:
                return None
        if p.get("dnp"):
            return {"y": None, "close": close, "void": True}
        if LB.first_period(mk):            # a first-period prop: the value locked live at the period break (exact only)
            lk = LB.period_lock(data_dir, leg.get("gameId"), mk, leg["player"])
            v = lk["v"] if lk and lk.get("exact") else None
        else:
            v = LB.stat(snap["sport"], mk, p) if res.get("espn") else T.stat(snap["sport"], mk, p)
            if v is None and football and leg.get("gameId") in fin_by_gid and _football_counting(snap["sport"], mk):
                v = 0.0             # no row in that category (no catches, no carries): zero, not unknown
        if v is None:
            return None
        pt = leg.get("point")
        if side in ("Yes", "No"):
            y = float(v >= (1 if pt is None else pt + 0.5))
            y = y if side == "Yes" else 1 - y
        elif pt is None or v == pt:
            y = None
        else:
            y = float(v > pt) if side == "Over" else float(v < pt)
        return {"y": y, "close": close, "stat": v, "age": T.close_age(snap, True), "t": "model" if est else t, **({"est": True} if est else {})}
    base = mk.replace("alternate_", "")
    pt = leg.get("point")
    per = _period_of(base)
    if per:             # a period line (1Q, 1H, P1, first 5 innings): graded from the final's line score
        base = base[: -len(per[0])]
        res = _period_score(snap, per, fin_by_gid.get(snap["id"]))
        if res is None:
            return None
        if base == "h2h_3_way":
            if side == "Draw":
                y = float(res["hs"] == res["as"])
            else:
                mine_home = T._norm(side or "") == T._norm(snap["home"])
                y = float((res["hs"] > res["as"]) if mine_home else (res["as"] > res["hs"]))
            return {"y": y, "close": close, "score": [res["hs"], res["as"]], "age": T.close_age(snap, True), "t": t}
        if base not in ("h2h", "spreads", "totals", "team_totals"):
            return None
    if not per and base in ("spreads", "totals") and str(snap.get("sport") or "").startswith("tennis"):
        # tennis spreads and totals are in games (game handicap, total games), not sets: graded from the set line score
        res = _tennis_games(snap, fin_by_gid.get(snap["id"]))
        if res is None:
            return None
    canon_side = snap["home"] if base in ("h2h", "spreads") else "Over"
    row_pt = pt
    if base == "spreads" and pt is not None and T._norm(side or "") != T._norm(snap["home"]):
        row_pt = -pt
    y = T._grade_line(snap, {"mk": base if per else mk, "pt": row_pt, "team": leg.get("team")}, res)
    if y is None and base not in ("h2h", "spreads", "totals", "team_totals"):
        return None
    if y is not None:
        mine = (T._norm(side or "") == T._norm(canon_side)) if base in ("h2h", "spreads") else side == "Over"
        y = y if mine else 1 - y
    return {"y": y, "close": close, "score": [res["hs"], res["as"]], "age": T.close_age(snap, bool(per) or base not in ("h2h", "spreads", "totals")), "t": t}


_PERIOD_RE = re.compile(r"_(q[1-4]|h[12]|p[1-3]|1st_[1-9]_innings)$")


def _period_of(base):
    """('_q1', 'q1') for a period market key (totals_q1, spreads_h1, h2h_3_way_h1, totals_1st_5_innings), else None"""
    m = _PERIOD_RE.search(base or "")
    return (m.group(0), m.group(1)) if m else None


def _tennis_games(snap, f):
    """{hs, as} games won by each player (home/away of our snapshot) in a completed tennis match, from the final's set
    line score (a tiebreak set reads 7-6); None for a retirement, walkover, suspension or a line score that can't be read"""
    if not f or f.get("status") != "Final" or not f.get("home") or not f.get("away"):
        return None
    try:
        hl = [float(x) for x in f["home"].get("line") or []]
        al = [float(x) for x in f["away"].get("line") or []]
    except (TypeError, ValueError):
        return None
    if len(hl) < 2 or len(hl) != len(al):
        return None
    swap = T._norm(f["home"].get("name") or "") != T._norm(snap["home"]) and T._norm(f["away"].get("name") or "") == T._norm(snap["home"])
    h, a = sum(hl), sum(al)
    return {"hs": a if swap else h, "as": h if swap else a, "players": {}}


def _period_score(snap, per, f):
    """{hs, as} for one period of a final game (home/away of our snapshot) from the final's line score, or None when
    it can't be read. Second halves and 4th quarters that went to overtime are left to Vince (books differ on whether
    overtime counts), as are periods a sport doesn't have."""
    if not f or not f.get("home") or not f.get("away"):
        return None
    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None         # an unplayed bottom 9th ("X")
    hl = [num(x) for x in f["home"].get("line") or []]
    al = [num(x) for x in f["away"].get("line") or []]
    sport, p = snap.get("sport") or "", per[1]
    reg = 2 if sport.startswith("soccer") or sport == "basketball_ncaab" else 3 if sport.startswith("icehockey") \
        else 9 if sport.startswith("baseball") else 4 if sport.startswith(("basketball", "americanfootball")) else None
    if not reg or len(hl) != len(al) or len(hl) < reg:
        return None             # no full regulation line score yet
    ot = len(hl) > reg
    if p.startswith("1st_"):
        idx = list(range(int(p[4]))) if reg == 9 else None
    elif reg == 4:
        idx = {"q1": [0], "q2": [1], "q3": [2], "q4": [3], "h1": [0, 1], "h2": [2, 3]}.get(p)
        if p in ("q4", "h2") and ot:
            idx = None
    elif reg == 2:
        idx = {"h1": [0], "h2": [1]}.get(p)
        if p == "h2" and ot:
            idx = None
    elif reg == 3:              # hockey periods (the site logs a first period as _q1)
        idx = {"p1": [0], "p2": [1], "p3": [2], "q1": [0]}.get(p)
    else:
        idx = None
    if not idx or any(hl[i] is None or al[i] is None for i in idx):
        return None
    swap = T._norm(f["home"].get("name") or "") != T._norm(snap["home"]) and T._norm(f["away"].get("name") or "") == T._norm(snap["home"])
    h, a = sum(hl[i] for i in idx), sum(al[i] for i in idx)
    return {"hs": a if swap else h, "as": h if swap else a, "players": {}}


def grade_bet(data_dir, legs, dec, fin_by_gid):
    """parlays: every leg must be final; a pushed or void leg drops out"""
    out, closes = [], []
    for leg in legs:
        g = grade_leg(data_dir, leg, fin_by_gid)
        if g is None:
            return None
        out.append(g)
    live = [g for g in out if g["y"] is not None]
    if not live:
        result = "push"
    elif any(g["y"] == 0 for g in live):
        result = "lost"
    else:
        result = "won"
    cl = [g["close"] for g in live]
    clv = None
    if live and all(c is not None for c in cl) and dec:
        pc = 1.0
        for c in cl:
            pc *= c
        clv = round(pc * dec - 1, 4)          # expected return per $1 at the price taken, valued at the closing fair
    # CLV is measured against the market consensus at the close (closemkt, Nov 10): the books quoting each leg's exact
    # number in the last pull before the start, de-vigged, with nothing of ours layered on. Our own fair at the close
    # (self-corrected, model-repriced where thin) stays beside it as clvOurs; no consensus at a number -> no CLV.
    ages = [g.get("age") for g in live]
    age = max(ages) if live and all(a is not None for a in ages) else None
    m = market_close(data_dir, legs, dec, [g["y"] for g in out])
    return {"result": result, "clv": m.get("clv"), "closeFair": m.get("closeFair"), "legs": out, "closeAge": m.get("closeAge"),
            "est": any(g.get("est") for g in live), "clvCal": None, "closeSrc": "market", "clvMktV": CMK.VERSION,
            "clvOurs": clv, "closeOurs": round(pc, 4) if clv is not None else None, "closeAgeOurs": age,
            "clvImp": m.get("clvImp"), "closeImp": m.get("closeImp"), "clvWhy": m.get("why")}


def market_close(data_dir, legs, dec, ys=None):
    """closemkt.bet_close with each game's sport and start from its pre-game snapshot, else from the raw pulls"""
    cache = {}

    def meta(gid):
        if gid not in cache:
            sn = _snap(data_dir, gid)
            cache[gid] = (sn["sport"], sn["commence"]) if sn else CMK.meta(data_dir, gid)
        return cache[gid]
    def from_snapshot(leg):
        """the snapshot's raw (unshifted) fair for this exact number when it was a real market consensus: a main game line,
        or a prop / alt row quoted by 2+ books that wasn't repriced from the main line (a repriced row carries the main
        row's weight while not being the main number)"""
        sn = _snap(data_dir, leg.get("gameId"))
        if not sn:
            return None
        row, same = _canon(sn, leg)
        if row is None or row.get("f") is None:
            return None
        peers = [r for r in sn.get("rows", []) if r["mk"] == row["mk"] and r.get("player") == row.get("player") and r.get("team") == row.get("team")]
        main = max(peers, key=lambda r: ((r.get("w") or 0), (r.get("x") or {}).get("nb") or 0)) if peers else row
        nb = (row.get("x") or {}).get("nb") or 0
        main_line = row["mk"] in ("h2h", "spreads", "totals") and not row.get("player")
        if not main_line and (nb < CMK.MIN_BOOKS or (row is not main and (row.get("w") or 0) >= (main.get("w") or 0))):
            return None
        p = row["f"] if same else 1 - row["f"]
        return {"p": round(p, 4), "age": T.close_age(sn, bool(row.get("player")) or not main_line), "src": "snapshot"}
    try:
        return CMK.bet_close(data_dir, legs, dec, lambda g: meta(g)[0], lambda g: meta(g)[1], results=ys, fallback=from_snapshot)
    except Exception as e:
        return {"clv": None, "why": f"error {type(e).__name__}"}


def leg_results(legs, out):
    """compact per-leg results kept on the bet (analysis of which legs hit): market, side, point, player, y, close, stat"""
    res = []
    for leg, g in zip(legs, out):
        res.append({k: v for k, v in {"market": leg.get("market"), "side": leg.get("side"), "point": leg.get("point"),
                                      "player": leg.get("player"), "gameId": leg.get("gameId"), "y": g.get("y"),
                                      "close": g.get("close"), "stat": g.get("stat"), "est": g.get("est") or None,
                                      "void": g.get("void") or None}.items() if v is not None})
    return res


# ---------------------------------------------------------------------------
# picks without structured fields: find the bet in the snapshot from the selection text

def parse_pick(p, snap):
    if p.get("market") and p.get("side"):
        return {k: p.get(k) for k in ("gameId", "market", "side", "point", "player", "team")}
    sel = p.get("selection") or ""
    s = sel.lower()
    nums = [float(x) for x in re.findall(r"[-+]?\d+(?:\.\d+)?", sel)]
    leg = {"gameId": p.get("gameId")}
    if p.get("player"):
        mk = next((m for w, m in PROP_WORDS if w in s), None)
        side = "Under" if "under" in s else "Over"
        if not mk or not nums:
            return None
        return dict(leg, market=mk, side=side, point=nums[-1], player=p["player"])
    if re.search(r"\bover\b|\bunder\b", s) and nums:
        return dict(leg, market="totals", side="Over" if "over" in s else "Under", point=next(n for n in nums if n > 0))
    def named(team):     # full name, nickname ("Sharks") or place ("Western Kentucky")
        w = team.split()
        return any(len(T._norm(x)) > 3 and T._norm(x) in T._norm(sel) for x in (team, w[-1], " ".join(w[:-1])))
    team = snap["home"] if named(snap["home"]) else snap["away"] if named(snap["away"]) else None
    if not team:
        return None
    if re.search(r"\bml\b|moneyline|to win", s) or not nums:
        return dict(leg, market="h2h", side=team)
    return dict(leg, market="spreads", side=team, point=nums[0])


def _pick_dec(p):
    """the price the pick was posted at: the best in-range play-to price, else good-at"""
    prices = [v.get("price") for v in p.get("playTo") or [] if v.get("inRange") and isinstance(v.get("price"), (int, float))]
    am = max(prices, key=lambda a: (a / 100 + 1) if a > 0 else (100 / -a + 1)) if prices else p.get("goodAt")
    if am is None:
        return None, None
    return am, (1 + am / 100) if am > 0 else (1 + 100 / -am)


_MKT = ("clv", "closeFair", "closeAge", "clvCal", "closeSrc", "clvMktV", "clvOurs", "closeOurs", "clvImp", "closeImp", "clvWhy")


def grade_picks(data_dir, fin_by_gid, days=60, log=print):
    n = 0
    for fn in sorted(glob.glob(os.path.join(data_dir, "picks", "*.json")))[-days:]:
        try:
            picks = json.load(open(fn))
        except Exception:
            continue
        changed = False
        for p in picks:
            # every tier grades, "likely" too (0 units: no profit, but its result, flat return and CLV are the record)
            # graded before calibrated CLV, or under an older calibration (closecal.VERSION)
            # graded before CLV moved to the closing market consensus (closemkt.VERSION): re-measure, keep the result
            refill = p.get("status") in ("won", "lost", "push") and p.get("clvMktV") != CMK.VERSION
            if p.get("status") not in (None, "open", "pending") and not refill:
                continue
            snap = _snap(data_dir, p.get("gameId"))
            if not snap:
                continue
            leg = parse_pick(p, snap)
            if not leg:
                continue
            # the price: the best real price first seen at a play-to venue (picklive), else the first raw pull after
            # posting (snapshots), else good-at; never the prices the pick run typed into playTo
            import pickprices as PP
            fp = {k: v for k, v in PP.first_prices(data_dir, p["id"]).items() if k in {x.get("venue") for x in p.get("playTo") or []}}
            src, gv = None, None
            if fp:
                gv = max(fp, key=lambda k: fp[k]["dec"])
                dec, src = fp[gv]["dec"], "first seen"
            else:
                px, _at = PP.posted_prices(data_dir, p)
                px = {k: v for k, v in (px or {}).items() if k in {x.get("venue") for x in p.get("playTo") or []}}
                if px:
                    gv = max(px, key=px.get)
                    dec, src = px[gv], "posted"
            if src:
                am = round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))
            else:
                am, dec = _pick_dec(p)
                src = "goodAt"
            g = grade_bet(data_dir, [leg], dec, fin_by_gid)
            if not g:
                continue
            if refill:          # keep the posted grade; re-measure CLV against the closing market consensus
                if "clvPrev" not in p:
                    p["clvPrev"] = p.get("clv")
                p.update({k: g.get(k) for k in _MKT})
                if p.get("flat") is None:
                    p["flat"] = round(dec - 1, 3) if p["status"] == "won" else -1.0 if p["status"] == "lost" else 0.0
                changed = True
                continue
            u = p.get("units") or 0
            # a player who didn't play (dnp, or scratched and missing from the box): the pick is void, as the books settle it
            p["status"] = "void" if g["legs"] and all(x.get("void") for x in g["legs"]) else g["result"]
            p["profit"] = round(u * (dec - 1), 3) if g["result"] == "won" else -u if g["result"] == "lost" else 0
            # flat 1-unit return at the graded price: compares tiers and sports on one scale whatever the stake
            p["flat"] = round(dec - 1, 3) if g["result"] == "won" else -1.0 if g["result"] == "lost" else 0.0
            p["gradedAt"], p["gradeSrc"], p["gradeVenue"] = am, src, gv
            p.update({k: g.get(k) for k in _MKT})
            if g["legs"] and g["legs"][0].get("stat") is not None:
                p["finalStat"] = g["legs"][0]["stat"]
            changed = True
            n += 1
        if changed:
            json.dump(picks, open(fn, "w"), indent=1, ensure_ascii=False)
    return n


def _find_pick(data_dir, pid):
    fn = os.path.join(data_dir, "picks", f"{pid[:10]}.json")
    try:
        return next((p for p in json.load(open(fn)) if p.get("id") == pid), None)
    except Exception:
        return None


def is_live(b, data_dir, legs=None):
    """a live bet: flagged live and placed after its first game started. A bet flagged live but logged before every one of
    its games began was placed pre-game (the flag was left on), so its yardstick is the pre-game close."""
    if not b.get("live"):
        return False
    try:
        made = datetime.fromisoformat(str(b.get("createdAt")).replace("Z", "+00:00"))
    except Exception:
        return True
    starts = []
    for x in legs or b.get("legs") or [b]:
        c = x.get("commence") or (_snap(data_dir, x.get("gameId")) or {}).get("commence")
        if not c:
            return True
        starts.append(datetime.fromisoformat(c.replace("Z", "+00:00")))
    return not starts or made >= min(starts) - timedelta(minutes=2)


def grade_bets(db, data_dir, fin_by_gid, log=print):
    """pending bets on Firestore: write `auto` (the site settles them with the promo math)"""
    if not db:
        return 0
    n = 0
    for snap in db.collection("bets").where("status", "==", "pending").stream():
        b = snap.to_dict() or {}
        if (b.get("auto") or {}).get("result") or b.get("manual"):
            continue        # manual: logged by hand (non-sports promos, markets we don't price); Vince settles those
        legs = [x for x in (b.get("legs") or []) if x.get("gameId")]
        legs = [LB.resolve(x, data_dir) for x in legs]
        if any(x is None for x in legs):            # a leg we can't identify: leave the bet for Vince to settle
            continue
        if not legs and b.get("gameId") and b.get("market") and b.get("side"):
            legs = [{k: b.get(k) for k in ("gameId", "market", "side", "point", "player", "team")}]
        if not legs and b.get("gameId") and not b.get("pickId"):     # logged before bets carried the fields: read the label
            leg = LB.resolve({"gameId": b["gameId"], "label": b.get("label")}, data_dir)
            legs = [leg] if leg else []
        if not legs and b.get("pickId"):          # tailed one of our picks: grade it the way the pick is graded
            pk = _find_pick(data_dir, b["pickId"])
            snap_ = _snap(data_dir, (pk or {}).get("gameId")) if pk else None
            leg = parse_pick(pk, snap_) if snap_ else None
            legs = [leg] if leg else []
        if not legs:
            continue
        try:
            g = grade_bet(data_dir, legs, b.get("dec"), fin_by_gid)
        except Exception as e:
            log(f"ledger: bet skipped ({type(e).__name__})")
            continue
        if not g:
            # a parlay across games or days: each leg's result goes on the bet as soon as its game is final, so the
            # site shows yesterday's legs as won or lost while the rest are still to play
            if len(b.get("legs") or []) > 1:
                try:
                    done = _legs_done(data_dir, b, fin_by_gid)
                except Exception:
                    done = {}
                if done and done != (b.get("legsDone") or {}):
                    snap.reference.update({"legsDone": done})
            continue
        if is_live(b, data_dir, legs):          # a live bet's yardstick is the live fair minutes later, not the pre-game close
            lc = b.get("liveCheck") or {}
            g["clv"], g["closeFair"] = lc.get("clv"), lc.get("fair")
            g["clvCal"], g["closeAge"] = None, None     # closecal is fit on pre-game closes: it says nothing about a live fair
        up = {"auto": {"result": g["result"], "clv": g["clv"], "closeFair": g["closeFair"], "closeAge": g.get("closeAge"), "clvCal": g.get("clvCal"),
                       "legs": leg_results(legs, g["legs"]), "closeEst": g.get("est") or None,
                       "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}}
        if not is_live(b, data_dir, legs):        # measured against the closing market consensus: no re-measure later
            up.update({"closeSrc": "market", "clvMktV": CMK.VERSION, "clvImp": g.get("clvImp"), "closeImp": g.get("closeImp"),
                       "clvOurs": g.get("clvOurs"), "clvWhy": g.get("clvWhy")})
        lg = last_game(data_dir, legs)
        if lg and not b.get("lastGame"):
            up["lastGame"] = lg
        snap.reference.update(up)
        n += 1
    return n


def last_game(data_dir, legs):
    """the start of the bet's last game (ISO): the day a bet belongs to, however late it was graded"""
    cs = []
    for x in legs or []:
        c = x.get("commence")
        if not c:
            sn = _snap(data_dir, x.get("gameId"))
            c = (sn or {}).get("commence")
        if c:
            cs.append(c)
    return max(cs) if cs else None


def _legs_done(data_dir, b, fin_by_gid):
    """{leg index: {y, stat, void}} for the legs of an open bet whose games are final (index into b["legs"])"""
    out = {}
    for i, x in enumerate(b.get("legs") or []):
        if not x.get("gameId"):
            continue
        r = LB.resolve(x, data_dir)
        if not r:
            continue
        g = grade_leg(data_dir, r, fin_by_gid)
        if g is None:
            continue
        out[str(i)] = {"y": g["y"], "stat": g.get("stat"), "void": bool(g.get("void"))}
    return out


def _legs_of(b, data_dir):
    """the bet's legs resolved to (gameId, market, side, point, player, team), or None if one can't be identified"""
    legs = [x for x in (b.get("legs") or []) if x.get("gameId")]
    if legs:
        legs = [LB.resolve(x, data_dir) for x in legs]
        return None if any(x is None for x in legs) else legs
    if b.get("gameId") and b.get("market") and b.get("side"):
        return [{k: b.get(k) for k in ("gameId", "market", "side", "point", "player", "team")}]
    if b.get("gameId") and not b.get("pickId"):
        leg = LB.resolve({"gameId": b["gameId"], "label": b.get("label")}, data_dir)
        return [leg] if leg else None
    if b.get("pickId"):
        pk = _find_pick(data_dir, b["pickId"])
        snap_ = _snap(data_dir, (pk or {}).get("gameId")) if pk else None
        leg = parse_pick(pk, snap_) if snap_ else None
        return [leg] if leg else None
    return None


def live_fair_at_log(b, live, g):
    """the estimated fair for a bet logged without one: its own close. A live bet's close is the live fair minutes after
    the bet (liveCheck), never the pre-game close (a Kings ML taken down 2 goals is not the 52% it closed at pre-game)."""
    if live:
        return (b.get("liveCheck") or {}).get("fair")
    return (g or {}).get("closeFair")


def backfill_settled(db, data_dir, fin_by_gid, now, log=print):
    """settled bets that never got the engine's numbers (settled by hand before grading, or graded before these fields
    existed): closing line value, each leg's result and close, the legs' structured fields and start times, and an
    estimated fair when none was logged (the close, marked fairAtLogSrc "close"). Never changes a result or a profit.
    Runs hourly (latest/backfill_state.json); our pick files get their structured fields filled the same way."""
    st_fn = os.path.join(data_dir, "latest", "backfill_state.json")
    try:
        last = json.load(open(st_fn)).get("at")
    except Exception:
        last = None
    if last and now - datetime.fromisoformat(last) < timedelta(hours=1):
        return 0
    n, mism = 0, []
    for snap in db.collection("bets").where("status", "in", ["won", "lost", "push"]).stream():
        b = snap.to_dict() or {}
        legs = _legs_of(b, data_dir)
        if not legs:
            continue
        up = {}
        # structured legs with start times (a leg that was only a label gets its market, side, number and player)
        if b.get("legs"):
            new, changed = [], False
            for x, r in zip([y for y in b["legs"] if y.get("gameId")], legs):
                y = dict(x)
                for k in ("market", "side", "point", "player", "team"):
                    if y.get(k) is None and r.get(k) is not None:
                        y[k], changed = r[k], True
                if not y.get("commence"):
                    sn = _snap(data_dir, y["gameId"])
                    if sn:
                        y["commence"], changed = sn["commence"], True
                new.append(y)
            if changed and len(new) == len(b["legs"]):
                up["legs"] = new
        elif not b.get("market") and legs[0].get("market"):
            up.update({k: legs[0].get(k) for k in ("market", "side", "point", "player", "team") if legs[0].get(k) is not None})
        # clvCal / closeAge arrived later: settled pre-game bets get them once (live bets use the live fair instead)
        live = is_live(b, data_dir, legs)
        # CLV against the closing market consensus (closemkt): every settled pre-game bet once per closemkt.VERSION; the
        # number it had before is kept as clvPrev. Needs only the raw pulls, so bets past the snapshot window get it too.
        if not live and b.get("clvMktV") != CMK.VERSION:
            ys = [r.get("y") for r in (b.get("legResults") or [])] or None
            m = market_close(data_dir, legs, b.get("dec"), ys if ys and len(ys) == len(legs) else None)
            up.update({"clv": m.get("clv"), "closeFair": m.get("closeFair"), "closeAge": m.get("closeAge"), "clvCal": None,
                       "closeSrc": "market", "clvMktV": CMK.VERSION, "clvImp": m.get("clvImp"), "closeImp": m.get("closeImp"),
                       "clvWhy": m.get("why")})
            if "clvPrev" not in b:
                up["clvPrev"] = b.get("clv")
            if b.get("auto"):
                up.update({"auto.clv": m.get("clv"), "auto.closeFair": m.get("closeFair"), "auto.closeAge": m.get("closeAge"), "auto.clvCal": None})
        need_cal = False
        need = (b.get("clv") is None and live) or not b.get("legResults") or b.get("fairAtLog") is None
        if need:
            try:
                g = grade_bet(data_dir, legs, b.get("dec"), fin_by_gid)
            except Exception:
                g = None
            if g:
                if g["result"] != b.get("status"):
                    mism.append({"id": snap.id, "status": b.get("status"), "engine": g["result"]})
                if b.get("clv") is None and "clv" not in up:
                    if live:
                        lc = b.get("liveCheck") or {}
                        if lc.get("clv") is not None:
                            up.update({"clv": lc["clv"], "closeFair": lc.get("fair")})
                    elif g["clv"] is not None:
                        up.update({k: g.get(k) for k in _MKT})
                if not b.get("legResults"):
                    up["legResults"] = leg_results(legs, g["legs"])
                fl = live_fair_at_log(b, live, g)
                if b.get("fairAtLog") is None and fl is not None:
                    up.update({"fairAtLog": fl, "fairAtLogSrc": "close"})
        # a live bet whose estimated fair was filled from the pre-game close (before live bets were told apart): that
        # number says nothing about a price taken in play, so it becomes the live fair minutes later, or nothing
        if live and b.get("fairAtLogSrc") == "close" and "fairAtLog" not in up:
            fl = (b.get("liveCheck") or {}).get("fair")
            if fl != b.get("fairAtLog"):
                up.update({"fairAtLog": fl, "fairAtLogSrc": "close" if fl is not None else None})
        if not b.get("lastGame"):          # the day the bet belongs to: its last game, not the day it was graded
            lg = last_game(data_dir, up.get("legs") or legs)
            if lg:
                up["lastGame"] = lg
        if up:
            up["backfilledAt"] = now.isoformat().replace("+00:00", "Z")
            snap.reference.update(up)
            n += 1
    # our picks: structured fields for picks posted before they carried them
    np_ = 0
    for fn in sorted(glob.glob(os.path.join(data_dir, "picks", "*.json"))):
        try:
            picks = json.load(open(fn))
        except Exception:
            continue
        ch = False
        for p in picks:
            if p.get("tier") == "likely" or (p.get("market") and p.get("side")):
                continue
            sn = _snap(data_dir, p.get("gameId"))
            leg = parse_pick(p, sn) if sn else None
            if leg:
                for k in ("market", "side", "point", "player", "team"):
                    if p.get(k) is None and leg.get(k) is not None:
                        p[k] = leg[k]
                ch, np_ = True, np_ + 1
        if ch:
            json.dump(picks, open(fn, "w"), indent=1, ensure_ascii=False)
    json.dump({"at": now.isoformat(), "bets": n, "picks": np_, "mismatch": mism}, open(st_fn, "w"), indent=1)
    if n or np_:
        log(f"ledger: backfilled {n} settled bets and {np_} picks")
    return n


def debug_bets(db, data_dir, fin_by_gid):
    """why each pending bet is or isn't graded yet (written to the private data repo for checking)"""
    out = []
    for snap in db.collection("bets").where("status", "==", "pending").stream():
        b = snap.to_dict() or {}
        row = {"id": snap.id, "label": b.get("label"), "created": b.get("createdAt"), "auto": b.get("auto"), "venue": b.get("venue"),
               "group": (b.get("group") or {}).get("kind"), "promoId": b.get("promoId"), "legs": []}
        for leg in (b.get("legs") or [b]):
            r = LB.resolve(dict(leg, gameId=leg.get("gameId") or b.get("gameId")), data_dir) if (leg.get("gameId") or b.get("gameId")) else None
            info = {"label": leg.get("label"), "gameId": leg.get("gameId") or b.get("gameId"), "resolved": bool(r)}
            if r:
                info.update({k: r.get(k) for k in ("market", "side", "point", "player", "team")})
                s = _snap(data_dir, r["gameId"])
                info["snap"] = bool(s)
                info["final"] = r["gameId"] in fin_by_gid
                if s:
                    try:
                        g = grade_leg(data_dir, r, fin_by_gid)
                        info["grade"] = g
                    except Exception as e:
                        info["grade"] = f"error {type(e).__name__}: {str(e)[:80]}"
            row["legs"].append(info)
        out.append(row)
    # and the last three days' settled bets, to see how each was settled
    cut = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    for snap in db.collection("bets").where("createdAt", ">=", cut).stream():
        b = snap.to_dict() or {}
        if b.get("status") == "pending":
            continue
        out.append({"id": snap.id, "label": b.get("label"), "status": b.get("status"), "profit": b.get("profit"), "auto": b.get("auto"),
                    "venue": b.get("venue"), "group": (b.get("group") or {}).get("kind"),
                    "autoSettled": b.get("autoSettled"), "am": b.get("am"), "stake": b.get("stake"), "promoId": b.get("promoId"),
                    "boostIn": b.get("boostIn"), "live": b.get("live"), "settledAt": b.get("settledAt")})
    json.dump(out, open(os.path.join(data_dir, "latest", "ledger_debug.json"), "w"), indent=1, default=str)
    return len(out)


# ---------------------------------------------------------------------------
# sports on bets logged before the site recorded them (Bet Lab parlays, promo entries, typed-in bets): each leg's
# league from its game's pre-game snapshot or the finals, else from a player or team name in the label. Written once
# per bet as leagues [..] + leaguesAt, at most hourly (it reads every bet).

def apply_inbox(db, data_dir, log=print):
    """New ledger entries Vince asked Claude to log (a bet he placed, a withdrawal), kept in the private data repo
    fixes/new.json: [{op: "promo" | "bet" | "wallet", id, doc, match?, markUsed?}]. Each runs once (latest/inbox_state.json)
    and never overwrites a doc that already exists. A promo with `match` {venue, kinds, boost, minLegs} reuses an unused
    promo Vince already saved that fits instead of creating a duplicate; a bet's promoId "@<inbox id>" resolves to it, and
    markUsed marks that promo used by the bet, as the site's log sheet does. Logging records bets; it never places them."""
    fn, st_fn = os.path.join(data_dir, "fixes", "new.json"), os.path.join(data_dir, "latest", "inbox_state.json")
    try:
        items = json.load(open(fn))
    except Exception:
        return 0
    try:
        st = json.load(open(st_fn))
    except Exception:
        st = {}
    done, alias = set(st.get("done") or []), dict(st.get("alias") or {})
    col = {"promo": "promos", "bet": "bets", "wallet": "wallet"}
    n = 0
    for it in items:
        op, iid = it.get("op"), it.get("id")
        if op not in col or not iid or iid in done:
            continue
        doc = dict(it.get("doc") or {})
        try:
            if op == "promo" and it.get("match"):
                m = it["match"]
                for snap in db.collection("promos").where("venue", "==", m.get("venue")).stream():
                    q = snap.to_dict() or {}
                    if q.get("used") or q.get("kind") not in (m.get("kinds") or [q.get("kind")]):
                        continue
                    if m.get("boost") is not None and abs((q.get("boost") or 0) - m["boost"]) > 1e-9:
                        continue
                    if m.get("minLegs") and (q.get("minLegs") or 1) > m["minLegs"]:
                        continue
                    alias[iid] = snap.id
                    break
                if iid in alias:
                    done.add(iid)
                    log(f"inbox: matched an existing promo")
                    continue
            if op == "bet" and str(doc.get("promoId") or "").startswith("@"):
                doc["promoId"] = alias.get(doc["promoId"][1:], doc["promoId"][1:])
            doc_id = doc.get("id") or iid
            doc["id"] = doc_id
            ref = db.document(f"{col[op]}/{doc_id}")
            if ref.get().exists:
                done.add(iid)
                continue
            ref.set(doc)
            if op == "promo":
                alias[iid] = doc_id
            if op == "bet" and it.get("markUsed") and doc.get("promoId"):
                pref = db.document(f"promos/{doc['promoId']}")
                pd = pref.get().to_dict() or {}
                if pd and not pd.get("used"):
                    pref.update({"used": True, "usedAt": doc.get("createdAt"), "usedBy": doc_id})
            done.add(iid)
            n += 1
        except Exception as e:
            log(f"inbox: {op} failed ({type(e).__name__})")
    os.makedirs(os.path.dirname(st_fn), exist_ok=True)
    json.dump({"done": sorted(done), "alias": alias}, open(st_fn, "w"), indent=1)
    if n:
        log(f"inbox: {n} new ledger entries")
    return n


def apply_fixes(db, data_dir, log=print):
    """corrections Vince asked for on bets he logged (the wrong side of a leg, say), kept in the private data repo
    fixes/bets.json: [{id, leg (the leg's label as logged), side, label (the leg's corrected label)}]. Each runs once,
    and only on a bet that is still pending (latest/fixes_state.json). Changes only the named leg and the bet's label."""
    fn, st_fn = os.path.join(data_dir, "fixes", "bets.json"), os.path.join(data_dir, "latest", "fixes_state.json")
    try:
        fixes = json.load(open(fn))
    except Exception:
        return 0
    try:
        done = set(json.load(open(st_fn)))
    except Exception:
        done = set()
    n = 0
    for f in fixes:
        if f.get("add"):        # a leg left out when the bet was logged: {id, add: {label, gameId, market, side, point, player, ...}}
            key = f"{f['id']}|add|{f['add'].get('label')}"
            if key in done:
                continue
            ref = db.document(f"bets/{f['id']}")
            b = ref.get().to_dict() or {}
            done.add(key)
            if b.get("status") != "pending":
                continue
            legs = b.get("legs") or [{k: b.get(k) for k in ("label", "gameId", "market", "side", "point", "player", "team", "league",
                                                            "matchup", "commence") if b.get(k) is not None}]
            if len(legs) == 1 and legs[0].get("label") == b.get("label") and f.get("firstLabel"):
                legs[0]["label"] = f["firstLabel"]          # the single bet's label named both legs
            if any(x.get("label") == f["add"].get("label") for x in legs):
                continue
            ref.update({"legs": legs + [f["add"]], "fairAtLog": None})
            n += 1
            continue
        key = f"{f['id']}|{f['leg']}|{f['side']}"
        if key in done:
            continue
        ref = db.document(f"bets/{f['id']}")
        b = ref.get().to_dict() or {}
        if b.get("status") != "pending":
            done.add(key)
            continue
        legs = b.get("legs") or []
        hit = [x for x in legs if x.get("label") == f["leg"]]
        if not hit and b.get("label") == f["leg"] and not legs:      # a single bet: the bet itself is the leg
            ref.update({"side": f["side"], "label": f["label"]})
        elif hit:
            for x in hit:
                x["side"], x["label"] = f["side"], f["label"]
            ref.update({"legs": legs, "label": (b.get("label") or "").replace(f["leg"], f["label"])})
        else:
            log("ledger: a bet fix found no matching leg")
            done.add(key)
            continue
        done.add(key)
        n += 1
    json.dump(sorted(done), open(st_fn, "w"))
    if n:
        log(f"ledger: {n} bet fixes applied")
    return n


def backfill_leagues(db, data_dir, now, log=print):
    import config as C
    import refs as R
    st_fn = os.path.join(data_dir, "latest", "leagues_state.json")
    try:
        last = json.load(open(st_fn)).get("at")
    except Exception:
        last = None
    if last and now - datetime.fromisoformat(last) < timedelta(hours=1):
        return 0
    by_gid = {}
    for fn in sorted(glob.glob(os.path.join(data_dir, "finals", "20*.json")))[-21:]:
        try:
            for d in json.load(open(fn)).values():
                if d.get("gameId") and d.get("league"):
                    by_gid[d["gameId"]] = d["league"]
        except Exception:
            pass
    names = []          # (normalized name, league): players with two or more words, and full team names
    for fn in glob.glob(os.path.join(data_dir, "refs", "*.json")):
        sport = os.path.basename(fn)[:-5]
        if sport not in C.SPORTS:
            continue
        try:
            rf = json.load(open(fn))
        except Exception:
            continue
        lg = C.label(sport)
        names += [(k, lg) for k in (rf.get("teams") or {}) if len(k) >= 6]
        names += [(k, lg) for k in (rf.get("players") or {}) if len(k) >= 8]
    names.sort(key=lambda x: -len(x[0]))

    def league_of(leg):
        gid = leg.get("gameId")
        if gid:
            if gid in by_gid:
                return by_gid[gid]
            try:
                return C.label(json.load(open(os.path.join(data_dir, "track", "open", f"{gid}.json")))["sport"])
            except Exception:
                pass
        lab = R.norm(" ".join(str(leg.get(k) or "") for k in ("player", "label", "team", "side")))
        if lab:
            hits = {lg for k, lg in names if k in lab}
            if len(hits) == 1:
                return hits.pop()
        return None

    n = 0
    for snap in db.collection("bets").stream():
        b = snap.to_dict() or {}
        if b.get("leaguesAt"):
            continue
        legs = b.get("legs") or [b]
        found = sorted({x for x in (league_of(dict(l, gameId=l.get("gameId") or (b.get("gameId") if len(legs) == 1 else None))) for l in legs) if x})
        if not found and b.get("league"):
            found = [b["league"]]
        db.document(f"bets/{snap.id}").update({"leagues": found or b.get("leagues") or [], "leaguesAt": now.isoformat()})
        n += 1
    json.dump({"at": now.isoformat()}, open(st_fn, "w"))
    if n:
        log(f"ledger: sports filled in on {n} bets")
    return n
