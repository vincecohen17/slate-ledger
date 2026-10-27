"""Historical closing odds for backtests (The Odds API historical endpoints; 10 credits per market returned).

For each league and season in PLAN, day by day:
  1. the historical events list for that day (which games, when they start),
  2. one snapshot per game 10 minutes before start (the close) with the game lines and the main player props,
     from up to 10 books (one credit unit),
  3. de-vigged like the live board (sharp-weighted consensus) and saved compactly:
     history/<sport>/<season>/<YYYY-MM-DD>.json.gz  [{id, commence, home, away, lines:[...], props:[...]}]
Resumable: history/state.json remembers finished days and credits spent. A run stops before the job time limit
and asks GitHub to start the next run until every day is done or the history budget is used.
"""
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta, timezone

import build as B
import config as C

BOOKS = ["pinnacle", "betonlineag", "lowvig", "draftkings", "fanduel", "betmgm", "williamhill_us", "bovada", "betrivers", "espnbet"]
GAME = ["h2h", "spreads", "totals"]
MARKETS = {
    "basketball_nba": GAME + ["player_points", "player_rebounds", "player_assists", "player_threes", "player_points_rebounds_assists"],
    "icehockey_nhl": GAME + ["player_shots_on_goal", "player_points", "player_goal_scorer_anytime", "player_total_saves", "player_assists"],
    "americanfootball_nfl": GAME + ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions", "player_anytime_td",
                                    "player_pass_tds", "player_rush_attempts", "player_pass_completions"],
    "americanfootball_ncaaf": GAME,
    "baseball_mlb": GAME,
}
# (sport, season label used by the stats files, first day, last day or None = yesterday)
PLAN = [
    ("basketball_nba", "2026-27", "2026-10-20", None),
    ("icehockey_nhl", "2026-27", "2026-10-06", None),
    ("americanfootball_nfl", "2026", "2026-09-09", None),
    ("basketball_nba", "2025-26", "2025-10-21", "2026-04-12"),
    ("icehockey_nhl", "2025-26", "2025-10-07", "2026-04-16"),
    ("americanfootball_nfl", "2025", "2025-09-04", "2026-01-04"),
    ("americanfootball_ncaaf", "2025", "2025-08-23", "2025-12-13"),
    ("americanfootball_ncaaf", "2026", "2026-08-29", None),
    ("baseball_mlb", "2026", "2026-03-26", "2026-09-27"),
]
BUDGET = 400_000
TIME_LIMIT_S = 38 * 60


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def compact(ev, label):
    """closing fairs only: lines (market, point, team, fair) and props (market, player, point, fair, books)"""
    g = B.build_game(ev, label)
    B.add_extra_lines(ev, g)
    lines = [{"m": l["market"], "pt": l.get("point"), "team": l.get("team"), "fair": l["fair"], "w": l.get("fairWeight"), "main": l.get("isMain")}
             for l in g["lines"] if l.get("fair")]
    props = []
    for d in B.build_props(ev, label).values():
        for r in d["rows"]:
            if r.get("fair"):
                props.append({"m": d["market"], "player": r["player"], "pt": r.get("point"), "fair": r["fair"], "w": r.get("fairWeight"),
                              "main": r.get("isMain"), "mean": r.get("mean")})
    return {"id": ev["id"], "commence": ev["commence_time"], "home": ev["home_team"], "away": ev["away_team"], "lines": lines, "props": props}


def run(api, data_dir, started=None):
    started = started or time.time()
    sp = os.path.join(data_dir, "history", "state.json")
    try:
        st = json.load(open(sp))
    except Exception:
        st = {"done": {}, "spent": 0, "games": 0}
    yesterday = (datetime.now(timezone.utc) - timedelta(hours=7)).date() - timedelta(days=1)
    stop = None
    for sport, season, first, last in PLAN:
        key = f"{sport}|{season}"
        done = set(st["done"].get(key, []))
        d0 = date.fromisoformat(first)
        d1 = date.fromisoformat(last) if last else yesterday
        day = d0
        while day <= d1:
            ds = day.isoformat()
            day += timedelta(days=1)
            if ds in done:
                continue
            if time.time() - started > TIME_LIMIT_S:
                stop = "time"
                break
            if st["spent"] >= BUDGET:
                stop = "budget"
                break
            t0 = datetime.fromisoformat(ds + "T15:00:00+00:00")       # 8 AM PT: that day's games are listed
            try:
                evs = api.historical_events(sport, _iso(t0), commenceTimeFrom=_iso(t0), commenceTimeTo=_iso(t0 + timedelta(days=1)))
            except Exception as e:
                st.setdefault("errors", []).append(f"{key} {ds} events {type(e).__name__}")
                continue
            out = []
            before = api.used_run
            for ev in (evs or {}).get("data", []):
                close = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) - timedelta(minutes=10)
                try:
                    res = api.historical_event_odds(sport, ev["id"], _iso(close), MARKETS[sport], BOOKS)
                except Exception as e:
                    st.setdefault("errors", []).append(f"{key} {ds} {type(e).__name__}")
                    continue
                data = (res or {}).get("data")
                if data and data.get("bookmakers"):
                    try:
                        out.append(compact(data, C.label(sport)))
                    except Exception as e:
                        st.setdefault("errors", []).append(f"{key} {ds} build {type(e).__name__}")
            st["spent"] += api.used_run - before
            fn = os.path.join(data_dir, "history", sport, season, f"{ds}.json.gz")
            os.makedirs(os.path.dirname(fn), exist_ok=True)
            with gzip.open(fn, "wt") as f:
                json.dump(out, f, separators=(",", ":"))
            st["games"] += len(out)
            done.add(ds)
            st["done"][key] = sorted(done)
            st["errors"] = st.get("errors", [])[-50:]
            json.dump(st, open(sp, "w"))
        if stop:
            break
    st["complete"] = stop is None
    st["at"] = _iso(datetime.now(timezone.utc))
    json.dump(st, open(sp, "w"))
    return st, stop


def load(data_dir, sport, season):
    """{date: [games]} of closing fairs for a season"""
    import glob
    out = {}
    for fn in sorted(glob.glob(os.path.join(data_dir, "history", sport, season, "*.json.gz"))):
        try:
            out[os.path.basename(fn)[:10]] = json.load(gzip.open(fn, "rt"))
        except Exception:
            pass
    return out
