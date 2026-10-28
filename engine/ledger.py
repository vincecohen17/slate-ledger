"""Grades Vince's logged bets and Claude's picks after the games: the result (from the same box scores the tracker uses,
or the ESPN final for game lines), and closing line value against the sharp no-vig fair at the close (the last pre-game
snapshot, track/open or track/closed).

Bets (Firestore bets/{id}, written by the site) get an `auto` field {result, clv, closeFair, at, legs}; the site applies it
(the promo math lives there). Picks (data repo picks/<date>.json) get status won/lost/push, profit in units and clv.

A bet or pick names its bet with gameId + market + side (+ point, player, team). Older picks only have the selection text;
those are matched to the snapshot's rows by player, number and words.
"""
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

import livebets as LB
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
    close = None
    if row is not None:
        close = row["f"] if same else 1 - row["f"]
    if is_prop:
        p = res["players"].get(T._norm(leg["player"]))
        if not p:
            return None
        if p.get("dnp"):
            return {"y": None, "close": close, "void": True}
        v = LB.stat(snap["sport"], mk, p) if res.get("espn") else T.stat(snap["sport"], mk, p)
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
        return {"y": y, "close": close, "stat": v}
    base = mk.replace("alternate_", "")
    pt = leg.get("point")
    canon_side = snap["home"] if base in ("h2h", "spreads") else "Over"
    row_pt = pt
    if base == "spreads" and pt is not None and T._norm(side or "") != T._norm(snap["home"]):
        row_pt = -pt
    y = T._grade_line(snap, {"mk": mk, "pt": row_pt, "team": leg.get("team")}, res)
    if y is None and base not in ("h2h", "spreads", "totals", "team_totals"):
        return None
    if y is not None:
        mine = (T._norm(side or "") == T._norm(canon_side)) if base in ("h2h", "spreads") else side == "Over"
        y = y if mine else 1 - y
    return {"y": y, "close": close, "score": [res["hs"], res["as"]]}


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
    return {"result": result, "clv": clv, "closeFair": round(pc, 4) if clv is not None else None, "legs": out}


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


def grade_picks(data_dir, fin_by_gid, days=10, log=print):
    n = 0
    for fn in sorted(glob.glob(os.path.join(data_dir, "picks", "*.json")))[-days:]:
        try:
            picks = json.load(open(fn))
        except Exception:
            continue
        changed = False
        for p in picks:
            if p.get("status") not in (None, "open", "pending") or p.get("tier") == "likely":
                continue
            snap = _snap(data_dir, p.get("gameId"))
            if not snap:
                continue
            leg = parse_pick(p, snap)
            if not leg:
                continue
            am, dec = _pick_dec(p)
            g = grade_bet(data_dir, [leg], dec, fin_by_gid)
            if not g:
                continue
            u = p.get("units") or 0
            p["status"] = g["result"]
            p["profit"] = round(u * (dec - 1), 3) if g["result"] == "won" else -u if g["result"] == "lost" else 0
            p["gradedAt"] = am
            p["clv"] = g["clv"]
            p["closeFair"] = g["closeFair"]
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


def grade_bets(db, data_dir, fin_by_gid, log=print):
    """pending bets on Firestore: write `auto` (the site settles them with the promo math)"""
    if not db:
        return 0
    n = 0
    for snap in db.collection("bets").where("status", "==", "pending").stream():
        b = snap.to_dict() or {}
        if (b.get("auto") or {}).get("result"):
            continue
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
            continue
        if b.get("live"):          # a live bet's yardstick is the live fair minutes later, not the pre-game close
            lc = b.get("liveCheck") or {}
            g["clv"], g["closeFair"] = lc.get("clv"), lc.get("fair")
        snap.reference.update({"auto": {"result": g["result"], "clv": g["clv"], "closeFair": g["closeFair"],
                                        "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}})
        n += 1
    return n
