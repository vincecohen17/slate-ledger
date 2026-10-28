"""Live numbers for Vince's open bets, every live run: each prop leg's current stat from the ESPN box score (free, every
sport including MLB), the projected finish (current + pre-game mean x time left) and the chance it ends over/under its
line. Written into the game's live doc as betLegs {"<betId>#<leg>": {...}} so the Ledger never depends on whether the
game's prop prices were pulled this minute.

Bets logged before legs carried market/side/point (or from a label only) are matched to the game's prop rows by label.
"""
import json
import os
import re

import build as B
import live as LV
import refs as R
import tracker as T

_props_idx = {}


def _index(data_dir):
    """(gameId, market, player) -> pre-game mean, from latest/props.json"""
    fn = os.path.join(data_dir, "latest", "props.json")
    key = (fn, os.path.getmtime(fn) if os.path.exists(fn) else 0)
    if _props_idx.get("key") == key:
        return _props_idx["idx"], _props_idx["by_game"]
    idx, by_game = {}, {}
    try:
        docs = json.load(open(fn))
    except Exception:
        docs = []
    for d in docs:
        by_game.setdefault(d["id"], []).append(d)
        for r in d.get("rows", []):
            k = (d["id"], d["market"], R.norm(r["player"]))
            mean = r.get("mean")
            if mean is None and r.get("fair") and "Over" in r["fair"] and r.get("point") is not None and r.get("isMain"):
                try:
                    mean = B.mean_from_line(d["market"], r["point"], r["fair"]["Over"], d.get("sport", ""))
                except Exception:
                    mean = None
            if mean is not None and k not in idx:
                idx[k] = mean
    _props_idx.update(key=key, idx=idx, by_game=by_game)
    return idx, by_game


LABEL = re.compile(r"^(?P<player>.+?) (?P<side>Over|Under|More|Less) (?P<pt>\d+(?:\.\d+)?) (?P<stat>[a-z0-9 ]+?)$", re.I)
_teams_c = {}


def _teams(data_dir, gid):
    """(home, away) of a game, from the board or the tracker snapshot"""
    if gid in _teams_c:
        return _teams_c[gid]
    out = None
    for fn in (os.path.join(data_dir, "track", "open", f"{gid}.json"), os.path.join(data_dir, "track", "closed", f"{gid}.json")):
        try:
            d = json.load(open(fn))
            out = (d["home"], d["away"])
            break
        except Exception:
            pass
    if not out:
        try:
            for g in json.load(open(os.path.join(data_dir, "latest", "board.json"))).get("games", []):
                if g["id"] == gid:
                    out = (g["home"], g["away"])
                    break
        except Exception:
            pass
    _teams_c[gid] = out
    return out


def _line(leg, text, data_dir):
    """game-line labels the Bet Lab writes: "Detroit Pistons ML", "Detroit Pistons -4.5", "Over 224.5", "Detroit Pistons Over 112.5" """
    tm = _teams(data_dir, leg.get("gameId"))
    if not tm:
        return None
    team = lambda x: next((t for t in tm if R.norm(t) == R.norm(x)), None)
    m = re.match(r"^(Over|Under) (\d+(?:\.\d+)?)$", text)
    if m:
        return dict(leg, market="totals", side=m[1], point=float(m[2]))
    m = re.match(r"^(.+) ML$", text)
    if m and team(m[1]):
        return dict(leg, market="h2h", side=team(m[1]), point=None)
    m = re.match(r"^(.+) ([+-]\d+(?:\.\d+)?)$", text)
    if m and team(m[1]):
        return dict(leg, market="spreads", side=team(m[1]), point=float(m[2]))
    m = re.match(r"^(.+) (Over|Under) (\d+(?:\.\d+)?)$", text)
    if m and team(m[1]):
        return dict(leg, market="team_totals", side=m[2], point=float(m[3]), team=team(m[1]))
    return None


def resolve(leg, data_dir):
    """market / side / point / player for a leg, from its fields or its label (None when it can't be told)"""
    if leg.get("market") and leg.get("side"):
        return leg
    text = re.sub(r"\s*\([^)]*\)\s*$", "", (leg.get("label") or "").strip())     # drop "(Pick6)", "(promo line)"
    ln = _line(leg, text, data_dir)
    if ln:
        return ln
    m = LABEL.match(text)
    if not m:
        return None
    player, pt = m["player"].strip(), float(m["pt"])
    side = {"more": "Over", "less": "Under"}.get(m["side"].lower(), m["side"].title())
    words = m["stat"].lower().strip()
    _, by_game = _index(data_dir)
    best = None
    for d in by_game.get(leg.get("gameId"), []):
        short = re.sub(r"^(player|batter|pitcher)_", "", d["market"]).replace("_", " ")
        if short != words:
            continue
        if any(R.norm(r["player"]) == R.norm(player) for r in d.get("rows", [])):
            best = d["market"]
            break
    if not best:          # no prop row today: the market from the words
        cand = {"walks": "batter_walks", "hits runs rbis": "batter_hits_runs_rbis", "earned runs": "pitcher_earned_runs",
                "total saves": "player_total_saves", "hits": "batter_hits", "strikeouts": "pitcher_strikeouts", "total bases": "batter_total_bases",
                "rbis": "batter_rbis", "runs scored": "batter_runs_scored", "home runs": "batter_home_runs", "outs": "pitcher_outs",
                "hits allowed": "pitcher_hits_allowed"}
        best = cand.get(words) or (f"player_{words.replace(' ', '_')}" if words else None)
    if not best:
        return None
    return dict(leg, market=best, side=side, point=pt, player=player)


def _mlb(market, p):
    v = lambda *ks: next((float(p[k][0] if isinstance(p[k], list) else p[k]) for k in ks if p.get(k) is not None), None)
    bat = lambda k: v(f"batting.{k}")
    pit = lambda k: v(f"pitching.{k}")
    ip = v("pitching.fullInnings.partInnings")
    outs = None if ip is None else int(ip) * 3 + round((ip - int(ip)) * 10)
    s = lambda *xs: None if any(x is None for x in xs) else sum(xs)
    return {"batter_hits": bat("hits"), "batter_runs_scored": bat("runs"), "batter_rbis": v("batting.RBIs", "batting.RBI", "batting.rbis"),
            "batter_hits_runs_rbis": s(bat("hits"), bat("runs"), v("batting.RBIs", "batting.RBI", "batting.rbis")),
            "batter_walks": bat("walks"), "batter_strikeouts": bat("strikeouts"), "batter_home_runs": bat("homeRuns"),
            "pitcher_strikeouts": pit("strikeouts"), "pitcher_walks": pit("walks"), "pitcher_earned_runs": pit("earnedRuns"),
            "pitcher_hits_allowed": pit("hits"), "pitcher_outs": outs}.get(market)


def stat(sport, market, p):
    if sport == "baseball_mlb":
        return _mlb(market, p)
    return T.stat(sport, market, p)


def left_frac(sport, st):
    if sport == "baseball_mlb":
        inn, half, outs = st.get("period") or 1, (st.get("half") or "top"), st.get("outs") or 0
        det = str(st.get("detail") or "").lower()
        if det.startswith(("mid", "end")):           # between halves: the half just finished
            half, outs = ("top" if det.startswith("mid") else "bottom"), 3
        done = 2 * (inn - 1) + (1 if half == "bottom" else 0) + min(3, outs) / 3
        return max(0.0, 1 - done / 18)
    if sport in LV.GAME:
        return LV.time_left(sport, st)[0]
    return 0.5


def compute(data_dir, sport, gid, st, legs, log=print):
    """legs: {key: leg} for this game -> {key: {cur, proj, p, market, player, point, side}}"""
    if not st or not st.get("espnId"):
        return {}
    try:
        bx, _ = LV.box(sport, st["espnId"])
    except Exception:
        bx = {}
    if not bx:
        return {}
    idx, _ = _index(data_dir)
    left = left_frac(sport, st)
    out = {}
    for key, leg0 in legs.items():
        leg = resolve(leg0, data_dir)
        if not leg or not leg.get("player"):
            continue
        p = bx.get(R.norm(leg["player"]))
        cur = stat(sport, leg["market"], p) if p else 0.0
        if cur is None:
            continue
        mean = idx.get((gid, leg["market"], R.norm(leg["player"])))
        rem = (mean or 0) * left
        pt = leg.get("point")
        po = None
        if pt is not None:
            if cur > pt:
                po = 1.0
            elif mean is not None:
                po = B.p_over(leg["market"], max(rem, 0.01), pt - cur, sport) if rem > 0.01 else 0.0
        p_side = None if po is None else (po if leg["side"] in ("Over", "Yes") else 1 - po)
        out[key] = {"cur": cur, "proj": round(cur + rem, 1) if mean is not None else None, "p": None if p_side is None else round(p_side, 4),
                    "market": leg["market"], "player": leg["player"], "point": pt, "side": leg["side"], "left": round(left, 3),
                    "dnp": bool(p and p.get("dnp"))}
    return out
