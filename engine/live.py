"""Live betting: in-game state (ESPN), our live projection, live prices and live edges.

Run mode "live" (started by the site every minute while the Live screen or a live game is open):
  * ESPN scoreboards give score, clock, period and situation (down/distance/possession, outs/bases) for games in play;
  * our live model starts from the pre-game projection (latest/matchups.json) and plays out the time that is left:
      basketball / football: normal margin and total, spread shrinking with the square root of time left,
        scoring pace blended between the pre-game rate and what this game has shown; football adds the
        expected points of the drive in progress;
      hockey: Poisson goals for the time left, OT/shootout for ties;
      baseball: run expectancy for the half-inning in progress (outs and runners) plus each team's rate for the
        innings left, negative binomial runs, extra innings for ties;
  * live prices (Odds API in-play markets) are de-vigged the same way as pre-game; quotes older than 2 minutes
    are marked stale, and edges are the venue prices that beat the live fair;
  * live player props for the game being watched: current stat + rate x time left (rate blends the pre-game
    projection with what the player has done so far), priced against the live prop lines.
Firestore: live/{gameId} per game in play, board/live for the list.
"""
import json
import math
import os
import urllib.request
from datetime import datetime, timedelta, timezone

import build as B
import refs as R
import stats as ST

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
PATH = {"basketball_nba": "basketball/nba", "basketball_wnba": "basketball/wnba", "icehockey_nhl": "hockey/nhl",
        "americanfootball_nfl": "football/nfl", "americanfootball_ncaaf": "football/college-football", "baseball_mlb": "baseball/mlb"}
GAME = {"basketball_nba": (4, 12, 5), "basketball_wnba": (4, 10, 5), "americanfootball_nfl": (4, 15, 10),
        "americanfootball_ncaaf": (4, 15, 0), "icehockey_nhl": (3, 20, 5)}           # periods, minutes each, OT minutes
SD = {"basketball_nba": (12.5, 18.5), "basketball_wnba": (11.0, 15.0), "americanfootball_nfl": (13.5, 13.0),
      "americanfootball_ncaaf": (15.5, 15.0)}
RE24 = {0: [0.50, 0.88, 1.12, 1.38, 1.46, 1.73, 1.96, 2.29], 1: [0.27, 0.52, 0.68, 0.94, 0.90, 1.13, 1.38, 1.53],
        2: [0.10, 0.22, 0.32, 0.36, 0.43, 0.48, 0.56, 0.75]}       # runs expected for the rest of the half-inning


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "slate-ledger"}), timeout=20) as r:
        return json.loads(r.read())


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _clock_min(s):
    try:
        if ":" in str(s):
            m, sec = str(s).split(":")
            return int(m) + float(sec) / 60
        return float(s) / 60 if float(s) > 60 else float(s)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# in-game state

def _path(sport):
    if sport in PATH:
        return PATH[sport]
    if sport.startswith("soccer_"):
        return next((v[0] for v in ST.SOCCER.values() if v[1] == sport), None)
    if sport.startswith("tennis_atp"):
        return "tennis/atp"
    if sport.startswith("tennis_wta"):
        return "tennis/wta"
    return None


def scoreboards(sports, post=None):
    """{(sport, homeAbbr, awayAbbr): state} for games in progress (tennis: player names). With a dict as `post`, games
    that have finished are put there too (state final=True), so bets on them read as won or lost until they're graded."""
    out = {}
    done = set()
    for sport in sports:
        path = _path(sport)
        if not path:
            continue
        if path.startswith("tennis/"):
            if path not in done:
                done.add(path)
                out.update({(s_, a, b): v for (a, b), v in _tennis_live(path).items() for s_ in sports if _path(s_) == path})
            continue
        if path.startswith("soccer/"):
            out.update(_soccer_live(sport, path))
            continue
        try:
            sb = _get(f"{ESPN}/{path}/scoreboard" + ("?groups=80" if sport == "americanfootball_ncaaf" else ""))
        except Exception:
            continue
        for e in sb.get("events", []):
            c = e["competitions"][0]
            st = c.get("status") or e.get("status") or {}
            ph = (st.get("type") or {}).get("state")
            if ph != "in" and not (ph == "post" and post is not None):
                continue
            side = {x["homeAway"]: x for x in c["competitors"]}
            sit = c.get("situation") or {}
            state = {"espnId": e["id"], "period": st.get("period"), "clock": st.get("displayClock"), "detail": (st.get("type") or {}).get("shortDetail"),
                     "home": {"abbr": side["home"]["team"]["abbreviation"], "id": side["home"]["team"]["id"], "score": float(side["home"].get("score") or 0)},
                     "away": {"abbr": side["away"]["team"]["abbreviation"], "id": side["away"]["team"]["id"], "score": float(side["away"].get("score") or 0)}}
            if sport.startswith("americanfootball"):
                state.update(down=sit.get("down"), distance=sit.get("distance"), yardLine=sit.get("yardLine"), possession=sit.get("possession"),
                             redZone=sit.get("isRedZone"), downText=sit.get("downDistanceText") or sit.get("shortDownDistanceText"))
            if sport == "baseball_mlb":
                state.update(outs=sit.get("outs"), on1=bool(sit.get("onFirst")), on2=bool(sit.get("onSecond")), on3=bool(sit.get("onThird")),
                             balls=sit.get("balls"), strikes=sit.get("strikes"),
                             half="top" if str(state["detail"] or "").lower().startswith(("top", "mid")) else "bottom")
                if str(state["detail"] or "").lower().startswith(("mid", "end")):
                    state["between"] = True
            if ph == "post":
                state.update(final=True, detail=(st.get("type") or {}).get("shortDetail") or "Final")
                post[(sport, R.norm(state["home"]["abbr"]), R.norm(state["away"]["abbr"]))] = state
                continue
            out[(sport, R.norm(state["home"]["abbr"]), R.norm(state["away"]["abbr"]))] = state
    return out


def _soccer_live(sport, path):
    out = {}
    try:
        sb = _get(f"{ESPN}/{path}/scoreboard")
    except Exception:
        return out
    for e in sb.get("events", []):
        c = e["competitions"][0]
        st = c.get("status") or {}
        if (st.get("type") or {}).get("state") != "in":
            continue
        side = {x["homeAway"]: x for x in c["competitors"]}
        reds = {"home": 0, "away": 0}
        ids = {side["home"]["team"]["id"]: "home", side["away"]["team"]["id"]: "away"}
        for dt in c.get("details") or []:
            if "red" in str((dt.get("type") or {}).get("text", "")).lower():
                reds[ids.get((dt.get("team") or {}).get("id"), "home")] += 1
        clock = str(st.get("displayClock") or "0'")
        try:
            minute = sum(int(x) for x in clock.replace("'", "").split("+") if x.strip().isdigit())
        except Exception:
            minute = 0
        state = {"espnId": e["id"], "period": st.get("period"), "clock": clock, "minute": minute, "detail": (st.get("type") or {}).get("shortDetail") or clock,
                 "reds": reds,
                 "home": {"abbr": side["home"]["team"]["abbreviation"], "id": side["home"]["team"]["id"], "score": float(side["home"].get("score") or 0)},
                 "away": {"abbr": side["away"]["team"]["abbreviation"], "id": side["away"]["team"]["id"], "score": float(side["away"].get("score") or 0)}}
        out[(sport, R.norm(state["home"]["abbr"]), R.norm(state["away"]["abbr"]))] = state
        out[(sport, R.norm(side["home"]["team"].get("displayName")), R.norm(side["away"]["team"].get("displayName")))] = state
    return out


def _tennis_live(path):
    """{(name a, name b): state} for singles matches in play, both name orders"""
    out = {}
    try:
        sb = _get(f"{ESPN}/{path}/scoreboard")
    except Exception:
        return out
    for ev in sb.get("events", []):
        for gr in ev.get("groupings") or [{"competitions": ev.get("competitions", [])}]:
            if "doubles" in str((gr.get("grouping") or {}).get("slug", "")).lower():
                continue
            for c in gr.get("competitions", []):
                stt = c.get("status") or {}
                if (stt.get("type") or {}).get("state") != "in":
                    continue
                comp = c.get("competitors", [])
                if len(comp) != 2:
                    continue
                pl = []
                for x in comp:
                    sets = [int(float(ls.get("value") or 0)) for ls in x.get("linescores") or []]
                    pl.append({"name": (x.get("athlete") or {}).get("displayName"), "sets": sets, "serving": bool(x.get("possession"))})
                if not all(p["name"] for p in pl):
                    continue
                best_of = 5 if any(len(p_["sets"]) >= 4 for p_ in pl) or ("grand slam" in str(ev.get("name", "")).lower() and "atp" in path) else 3
                state = {"espnId": c.get("id"), "detail": (stt.get("type") or {}).get("shortDetail") or "", "players": pl, "bestOf": best_of,
                         "set": stt.get("period") or len(pl[0]["sets"])}
                a, b = R.norm(pl[0]["name"]), R.norm(pl[1]["name"])
                out[(a, b)] = state
                out[(b, a)] = state
    return out


def box(sport, espn_id):
    """live box score players {normalized name: stat row}"""
    lg = {"basketball_nba": "nba", "basketball_wnba": "wnba", "icehockey_nhl": "nhl", "americanfootball_nfl": "nfl",
          "americanfootball_ncaaf": "cfb", "baseball_mlb": "mlb"}[sport]
    try:
        s = _get(f"{ESPN}/{PATH[sport]}/summary?event={espn_id}")
        return {R.norm(p["name"]): p for p in ST._players(lg, s.get("boxscore", {}))}, s.get("winprobability")
    except Exception:
        return {}, None


# ---------------------------------------------------------------------------
# live projection

def time_left(sport, st):
    """fraction of regulation left and elapsed minutes"""
    n, per_min, ot = GAME[sport]
    p, clock = st.get("period") or 1, _clock_min(st.get("clock"))
    total = n * per_min
    if p > n:
        return 0.0, total
    left = (n - p) * per_min + clock
    return max(0.0, left / total), total - left


def project(sport, st, pre):
    """live expected final (margin, total, home, away) and spreads; pre = pre-game {margin, total} (home view)"""
    hs, as_ = st["home"]["score"], st["away"]["score"]
    if sport in SD:
        r, elapsed = time_left(sport, st)
        sdm, sdt = SD[sport]
        n, per_min, _ = GAME[sport]
        pre_rate = pre["total"] / (n * per_min)
        obs_rate = (hs + as_) / elapsed if elapsed > 1 else pre_rate
        w = min(0.5, elapsed / (n * per_min))
        rate = w * obs_rate + (1 - w) * pre_rate
        rem_min = r * n * per_min
        drift = pre["margin"] * r
        if sport.startswith("americanfootball") and st.get("possession") and st.get("yardLine") is not None and r > 0:
            ytg = st["yardLine"]                      # ESPN gives yards from the possessing team's goal line in some feeds
            ytg = 100 - ytg if ytg <= 50 and st.get("possession") == st["home"]["id"] else ytg
            ep = max(0.0, 6.0 - 0.07 * min(99, max(1, ytg)))
            drift += ep if st["possession"] == st["home"]["id"] else -ep
        margin = (hs - as_) + drift
        total = hs + as_ + rate * rem_min
        sm, stt = max(1.0, sdm * math.sqrt(max(r, 0.002))), max(1.0, sdt * math.sqrt(max(r, 0.002)))
        return {"margin": margin, "total": total, "sdMargin": sm, "sdTotal": stt, "left": r,
                "pHome": 1 - _phi(-margin / sm) if r > 0 else float(hs > as_)}
    if sport == "icehockey_nhl":
        r, _ = time_left(sport, st)
        lh, la = pre["home"] * r, pre["away"] * r
        return {"lh": lh, "la": la, "hs": int(hs), "as": int(as_), "left": r}
    if sport == "baseball_mlb":
        inning = st.get("period") or 1
        outs = st.get("outs") or 0
        bases = (1 if st.get("on1") else 0) + (2 if st.get("on2") else 0) + (4 if st.get("on3") else 0)
        idx = {0: 0, 1: 1, 2: 2, 4: 3, 3: 4, 5: 5, 6: 6, 7: 7}[bases]
        rh, ra = pre["home"] / 9, pre["away"] / 9
        k = inning
        top, between = st.get("half") == "top", st.get("between")
        a_left = max(0, 9 - k)                                  # away innings still to bat after this one
        h_left = max(0, (10 - k) if top else (9 - k))           # home still bats the bottom of this inning when it is the top / middle
        cur_a = RE24[min(2, outs)][idx] * (ra / 0.5) if top and not between else 0.0
        cur_h = RE24[min(2, outs)][idx] * (rh / 0.5) if not top and not between else 0.0
        mh, ma = cur_h + rh * h_left, cur_a + ra * a_left
        return {"mh": mh, "ma": ma, "hs": int(hs), "as": int(as_), "left": (h_left + a_left) / 18}
    if sport.startswith("soccer_"):
        minute = st.get("minute") or 0
        r = max(0.0, (93 - minute) / 93) if (st.get("period") or 1) <= 2 else 0.0
        lh, la = pre["home"] * r, pre["away"] * r
        rh, ra = (st.get("reds") or {}).get("home", 0), (st.get("reds") or {}).get("away", 0)
        lh *= 0.7 ** rh * 1.2 ** ra
        la *= 0.7 ** ra * 1.2 ** rh
        return {"lh": lh, "la": la, "hs": int(hs), "as": int(as_), "left": r}
    return None


# ---------------------------------------------------------------------------
# tennis: serve point probabilities -> game, tiebreak, set, match

from functools import lru_cache


def _hold(sv):
    q = 1 - sv
    return sv ** 4 * (1 + 4 * q + 10 * q * q) + 20 * sv ** 3 * q ** 3 * sv * sv / max(1e-9, 1 - 2 * sv * q)


def _tiebreak(sa, sb):
    """P(A wins a tiebreak) when A serves first"""
    @lru_cache(None)
    def f(a, b):
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a == b and a >= 6:
            x, y = sa * (1 - sb), (1 - sa) * sb
            return x / (x + y) if x + y else 0.5
        n = a + b
        a_srv = n == 0 or ((n - 1) // 2) % 2 == 1
        p = sa if a_srv else 1 - sb
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)
    return f(0, 0)


def _set(sa, sb, ga=0, gb=0, a_srv=True):
    ha, hb = _hold(sa), _hold(sb)
    tb_a, tb_b = _tiebreak(sa, sb), 1 - _tiebreak(sb, sa)

    @lru_cache(None)
    def f(a, b, srv):
        if (a >= 6 and a - b >= 2) or a == 7:
            return 1.0
        if (b >= 6 and b - a >= 2) or b == 7:
            return 0.0
        if a == 6 and b == 6:
            return tb_a if srv else tb_b
        p = ha if srv else 1 - hb
        return p * f(a + 1, b, not srv) + (1 - p) * f(a, b + 1, not srv)
    return f(ga, gb, a_srv)


def tennis_match(sa, sb, best_of=3, sets_a=0, sets_b=0, ga=0, gb=0, a_srv=None):
    need = best_of // 2 + 1
    cur = 0.5 * (_set(sa, sb, ga, gb, True) + _set(sa, sb, ga, gb, False)) if a_srv is None else _set(sa, sb, ga, gb, a_srv)
    ps = 0.5 * (_set(sa, sb) + _set(sa, sb, 0, 0, False))

    @lru_cache(None)
    def m(a, b):
        if a >= need:
            return 1.0
        if b >= need:
            return 0.0
        return ps * m(a + 1, b) + (1 - ps) * m(a, b + 1)
    return cur * m(sets_a + 1, sets_b) + (1 - cur) * m(sets_a, sets_b + 1)


def serve_probs(p_match, best_of=3, base=0.64):
    """serve point win chances for A and B that reproduce a pre-match win chance"""
    lo, hi = -0.2, 0.2
    for _ in range(40):
        d = (lo + hi) / 2
        if tennis_match(base + d, base - d, best_of) < p_match:
            lo = d
        else:
            hi = d
    d = (lo + hi) / 2
    return base + d, base - d


def project_tennis(st, home, p_home):
    """live match win chance for `home` from sets and games (points are not in the feed)"""
    pl = st["players"]
    hi = 0 if R.norm(pl[0]["name"]) == R.norm(home) else 1
    A, Bp = pl[hi], pl[1 - hi]
    sa, sb = serve_probs(min(0.97, max(0.03, p_home)), st.get("bestOf", 3))
    n = max(len(A["sets"]), len(Bp["sets"]))
    sets_a = sum(1 for i in range(n - 1) if (A["sets"][i] if i < len(A["sets"]) else 0) > (Bp["sets"][i] if i < len(Bp["sets"]) else 0))
    sets_b = (n - 1) - sets_a if n else 0
    ga = A["sets"][-1] if A["sets"] else 0
    gb = Bp["sets"][-1] if Bp["sets"] else 0
    srv = True if A.get("serving") else False if Bp.get("serving") else None
    p = tennis_match(sa, sb, st.get("bestOf", 3), sets_a, sets_b, min(ga, 7), min(gb, 7), srv)
    return {"pHome": p, "sets": [sets_a, sets_b], "games": [ga, gb], "serve": [round(sa, 3), round(sb, 3)], "left": None}


def _pois(mu, n=25):
    p, out = math.exp(-max(mu, 0)), []
    for k in range(n):
        out.append(p)
        p *= max(mu, 1e-9) / (k + 1)
    return out


def _nb(mu, k=1.8, n=25):
    if mu <= 0:
        return [1.0] + [0.0] * (n - 1)
    r, q = mu / (k - 1), 1 / k
    lp, out = r * math.log(q), []
    for x in range(n):
        out.append(math.exp(lp))
        lp += math.log((x + r) / (x + 1)) + math.log(1 - q)
    return out


def line_probs(sport, g, pj):
    """prob function for live full-game lines (h2h, spreads, totals, team totals)"""
    home, away = g["home"], g["away"]
    if sport in SD:
        def f(l):
            mk = l["market"].replace("alternate_", "")
            pt = l.get("point")
            if mk == "h2h":
                return {home: pj["pHome"], away: 1 - pj["pHome"]}
            if mk == "spreads" and pt is not None:
                ph = 1 - _phi((-pt - pj["margin"]) / pj["sdMargin"])
                return {home: ph, away: 1 - ph}
            if mk == "totals" and pt is not None:
                po = 1 - _phi((pt - pj["total"]) / pj["sdTotal"])
                return {"Over": po, "Under": 1 - po}
            if mk == "team_totals" and pt is not None:
                side = "home" if R.norm(l.get("team")) == R.norm(home) else "away"
                mean = (pj["total"] + pj["margin"]) / 2 if side == "home" else (pj["total"] - pj["margin"]) / 2
                po = 1 - _phi((pt - mean) / (math.hypot(pj["sdTotal"], pj["sdMargin"]) / 2))
                return {"Over": po, "Under": 1 - po}
        return f
    if sport.startswith("tennis_"):
        return lambda l: {home: pj["pHome"], away: 1 - pj["pHome"]} if l["market"] == "h2h" else None
    if sport.startswith("soccer_"):
        ph, pa = _pois(pj["lh"], 15), _pois(pj["la"], 15)
        H, A = pj["hs"], pj["as"]
        grid = [(H + i, A + j, ph[i] * pa[j]) for i in range(15) for j in range(15)]
        hw = sum(p for h, a, p in grid if h > a)
        dr = sum(p for h, a, p in grid if h == a)
        pj["pHome"], pj["pDraw"] = hw, dr

        def ah(pt, handicap=True):
            """asian handicap / total, quarter lines split in two, pushes removed"""
            parts = [pt] if (pt * 4) % 2 == 0 else [pt - 0.25, pt + 0.25]
            vals = []
            for q in parts:
                if handicap:
                    w = sum(p for h, a, p in grid if h + q > a)
                    push = sum(p for h, a, p in grid if h + q == a)
                else:
                    w = sum(p for h, a, p in grid if h + a > q)
                    push = sum(p for h, a, p in grid if h + a == q)
                vals.append(w / max(1e-9, 1 - push))
            return sum(vals) / len(vals)

        def f(l):
            mk = l["market"].replace("alternate_", "")
            pt = l.get("point")
            if mk == "h2h":
                out = {home: hw, away: 1 - hw - dr}
                for o in l.get("outcomes", []):
                    out.setdefault(o, dr)
                return out
            if mk == "spreads" and pt is not None:
                c = ah(pt, True)
                return {home: c, away: 1 - c}
            if mk == "totals" and pt is not None:
                o = ah(pt, False)
                return {"Over": o, "Under": 1 - o}
            if mk == "btts":
                y = sum(p for h, a, p in grid if h > 0 and a > 0)
                return {"Yes": y, "No": 1 - y}
        return f
    if sport in ("icehockey_nhl", "baseball_mlb"):
        if sport == "icehockey_nhl":
            ph, pa = _pois(pj["lh"]), _pois(pj["la"])
            xtra, home_x = 1, 0.52
        else:
            ph, pa = _nb(pj["mh"]), _nb(pj["ma"])
            xtra, home_x = 0, 0.52
        H, A = pj["hs"], pj["as"]
        N = range(len(ph))
        grid = [(H + i, A + j, ph[i] * pa[j]) for i in N for j in N]
        win = sum(p for h, a, p in grid if h > a) + home_x * sum(p for h, a, p in grid if h == a)
        pj["pHome"] = win

        def f(l):
            mk = l["market"].replace("alternate_", "")
            pt = l.get("point")
            if mk == "h2h":
                return {home: win, away: 1 - win}
            if mk == "spreads" and pt is not None:
                c = sum(p for h, a, p in grid if h + pt > a)
                return {home: c, away: 1 - c}
            if mk == "totals" and pt is not None:
                o = sum(p for h, a, p in grid if h + a + (xtra if h == a else 0) > pt)
                return {"Over": o, "Under": 1 - o}
            if mk == "team_totals" and pt is not None:
                side = "home" if R.norm(l.get("team")) == R.norm(home) else "away"
                o = sum(p for h, a, p in grid if (h if side == "home" else a) > pt)
                return {"Over": o, "Under": 1 - o}
        return f
    return lambda l: None


# ---------------------------------------------------------------------------
# live player props (the game being watched)

def live_props(sport, prop_docs, players_box, mu, pj):
    """stamp live projections onto prop rows: current + rate x time left"""
    if not mu:
        return 0
    allp = {R.norm(p["name"]): p for s in ("home", "away") for p in (mu.get("players", {}).get(s) or [])}
    r = pj.get("left", 0)
    n = 0
    stat_of = {"basketball_nba": {"player_points": ["points"], "player_rebounds": ["rebounds"], "player_assists": ["assists"],
                                  "player_threes": ["3pm"], "player_points_rebounds_assists": ["points", "rebounds", "assists"],
                                  "player_points_rebounds": ["points", "rebounds"], "player_points_assists": ["points", "assists"],
                                  "player_rebounds_assists": ["rebounds", "assists"], "player_steals": ["steals"], "player_blocks": ["blocks"]},
               "icehockey_nhl": {"player_shots_on_goal": ["shotsTotal"], "player_points": ["goals", "assists"], "player_goals": ["goals"],
                                 "player_assists": ["assists"], "player_blocked_shots": ["blockedShots"]},
               "americanfootball_nfl": {"player_pass_yds": ["passing.passingYards"], "player_rush_yds": ["rushing.rushingYards"],
                                        "player_reception_yds": ["receiving.receivingYards"], "player_receptions": ["receiving.receptions"],
                                        "player_rush_attempts": ["rushing.rushingAttempts"]}}
    stat_of["basketball_wnba"] = stat_of["basketball_nba"]
    proj_of = {"points": "pts", "rebounds": "reb", "assists": "ast", "3pm": "fg3", "steals": "stl", "blocks": "blk", "shotsTotal": "sog",
               "goals": "g", "assists_nhl": "a", "blockedShots": "blk", "passing.passingYards": "pass_yds", "rushing.rushingYards": "rush_yds",
               "receiving.receivingYards": "rec_yds", "receiving.receptions": "rec", "rushing.rushingAttempts": "car"}
    table = stat_of.get(sport, {})
    for d in prop_docs:
        keys = table.get(d["market"])
        if not keys:
            continue
        for row in d["rows"]:
            b = players_box.get(R.norm(row["player"]))
            pre = allp.get(R.norm(row["player"]))
            if not b or row.get("point") is None:
                continue

            def cur_of(k):
                if k == "3pm":
                    v = b.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                    return (v or [0])[0] or 0
                return b.get(k) or 0
            cur = sum(cur_of(k) for k in keys)
            pre_mean = 0.0
            for k in keys:
                pk = "a" if (k == "assists" and sport == "icehockey_nhl") else proj_of.get(k)
                pre_mean += ((pre or {}).get("proj") or {}).get(pk, 0) or 0
            played = b.get("minutes") or b.get("timeOnIce") or 0
            if sport.startswith("basketball") or sport == "icehockey_nhl":
                pre_min = (pre or {}).get("min") or 0
                rate_pre = pre_mean / pre_min if pre_min else 0
                rate_obs = cur / played if played else rate_pre
                w = played / (played + 20.0)
                rem_min = max(0.0, pre_min - played) if pre_min else 0
                rem_min = min(rem_min, pre_min * max(r, 0.02) * 1.3) if pre_min else 0
                rem = (w * rate_obs + (1 - w) * rate_pre) * rem_min
            else:
                rem = pre_mean * r
            need = row["point"] - cur
            if need < 0:
                po = 1.0
            else:
                po = B.p_over(d["market"], max(rem, 0.01), need, sport)
            row["live"] = {"cur": round(cur, 1), "proj": round(cur + rem, 1), "Over": round(po, 4), "Under": round(1 - po, 4)}
            row["model"] = {"mean": round(cur + rem, 2), "Over": round(po, 4), "Under": round(1 - po, 4)}
            n += 1
    return n


def stale_after(game_doc, seconds=120):
    """live quotes older than `seconds` behind the freshest book are stale"""
    for l in game_doc.get("lines", []):
        ts = [q.get("updated") for q in l.get("quotes", []) if q.get("updated")]
        if not ts:
            continue
        newest = max(datetime.fromisoformat(t) for t in ts)
        for q in l["quotes"]:
            if q.get("updated") and (newest - datetime.fromisoformat(q["updated"])).total_seconds() > seconds:
                q["stale"] = True
