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
    if st.get("final"):                          # the game is over: every stat is final
        return 0.0
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


# first-period player props (player_pass_yds_q1, player_points_q1, first half): until the period ends the box score's game
# total IS the period total, so the leg tracks live like any prop; at the end of the period the value is locked
# (latest/period_locks.json). A lock read during the break (clock 0:00 / "End of 1st") is exact and grades the leg;
# one read in the last minute of play is close but not exact, so that leg is left for Vince to settle.
_FIRST = {"_q1": 1, "_p1": 1, "_h1": 2, "_1h": 2}


def first_period(market):
    """(full-game market, last period of the window) for a first-period market, else None"""
    for suf, e in _FIRST.items():
        if market.endswith(suf):
            return market[: -len(suf)], e
    return None


def _locks(data_dir):
    try:
        return json.load(open(os.path.join(data_dir, "latest", "period_locks.json")))
    except Exception:
        return {}


def period_lock(data_dir, gid, market, player):
    """the locked first-period value {v, exact} or None"""
    return _locks(data_dir).get(f"{gid}|{market}|{R.norm(player)}")


def _window(sport, st, end):
    """(fraction of the first-period window left, exact break reading, window over)"""
    n, per_min, _ = LV.GAME.get(sport, (4, 12, 5))
    p, clock = st.get("period") or 1, LV._clock_min(st.get("clock"))
    det = str(st.get("detail") or "").lower()
    if st.get("final") or p > end:
        return 0.0, False, True
    at_break = p == end and (clock <= 0.001 or det.startswith(("end", "half")))
    left = ((end - p) * per_min + clock) / (end * per_min)
    return max(0.0, left), at_break, False


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
    left0 = left_frac(sport, st)
    out, locks, lock_changed = {}, None, False
    for key, leg0 in legs.items():
        leg = resolve(leg0, data_dir)
        if not leg or not leg.get("player"):
            continue
        p = R.find_player(bx, leg["player"], leg.get("team"))
        left = left0
        fp = first_period(leg["market"])
        if fp and sport in LV.GAME:
            base, end = fp
            left, at_break, over = _window(sport, st, end)
            if locks is None:
                locks = _locks(data_dir)
            lk = f"{gid}|{leg['market']}|{R.norm(leg['player'])}"
            if over:                                   # the window is done: its locked value, if one was read
                if lk not in locks:
                    continue
                cur = locks[lk]["v"]
            else:
                cur = stat(sport, base, p) if p else 0.0
                if cur is None:
                    continue
                old = locks.get(lk)
                if not (old and old.get("exact")) or at_break:   # keep the latest in-window reading; an exact one stays
                    locks[lk] = {"v": cur, "exact": bool(at_break)}
                    lock_changed = True
        else:
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
    if lock_changed:
        os.makedirs(os.path.join(data_dir, "latest"), exist_ok=True)
        json.dump(locks, open(os.path.join(data_dir, "latest", "period_locks.json"), "w"), separators=(",", ":"))
    return out


# ---------------------------------------------------------------------------
# live bets: how good was the price? A live bet's closing line isn't the pre-game close; the honest yardstick is the
# live fair a few minutes after the bet (the market has caught up with whatever moved it). Written to the bet as
# liveCheck {fair, clv, at, mins}; grading uses it as the bet's CLV.

def _norm(s):
    return R.norm(s or "")


def live_fair(doc, leg):
    """the live no-vig fair of this leg's side in a live game doc, or None"""
    mk, side, pt = (leg.get("market") or ""), leg.get("side"), leg.get("point")
    if leg.get("player"):
        for p in doc.get("props") or []:
            if p.get("market") != mk:
                continue
            for r in p.get("rows") or []:
                if _norm(r.get("player")) != _norm(leg["player"]) or (pt is not None and r.get("point") is not None and abs(r["point"] - pt) > 1e-6):
                    continue
                f = r.get("fair") or {}
                k = next((x for x in f if _norm(x) == _norm(side)), None)
                if k and f[k] is not None:
                    return f[k]
        return None
    base = mk.replace("alternate_", "")
    for l in doc.get("lines") or []:
        if (l.get("market") or "").replace("alternate_", "") != base or not l.get("fair"):
            continue
        if leg.get("team") and l.get("team") and _norm(l["team"]) != _norm(leg["team"]):
            continue
        lp = l.get("point")
        if base.startswith("spreads"):
            want = pt if _norm(side) == _norm(doc.get("home")) else (-pt if pt is not None else None)
            if want is None or lp is None or abs(lp - want) > 1e-6:
                continue
        elif pt is not None and lp is not None and abs(lp - pt) > 1e-6:
            continue
        k = next((x for x in l["fair"] if _norm(x) == _norm(side)), None)
        if k and l["fair"][k] is not None:
            return l["fair"][k]
    return None


def live_checks(pending, docs_by_gid, now, data_dir, after_min=5, until_min=30):
    """pending: [{id, legs, dec, at}] live bets not yet checked -> {betId: liveCheck} for those 5-30 minutes old whose
    every leg has a live fair right now"""
    from datetime import datetime
    out = {}
    for b in pending:
        try:
            age = (now - datetime.fromisoformat(b["at"].replace("Z", "+00:00"))).total_seconds() / 60
        except Exception:
            continue
        if age < after_min or age > until_min or not b.get("dec"):
            continue
        p = 1.0
        for leg in b["legs"]:
            d = docs_by_gid.get(leg.get("gameId"))
            f = live_fair(d, resolve(leg, data_dir) or leg) if d else None
            if f is None:
                p = None
                break
            p *= f
        if p is not None:
            out[b["id"]] = {"fair": round(p, 4), "clv": round(p * b["dec"] - 1, 4), "mins": round(age, 1),
                            "at": now.isoformat().replace("+00:00", "Z")}
    return out
