"""Fetch small samples from each stats source into the data repo (probe/), so parsers can be
written against real payloads. Logs print status codes and sizes only."""
import gzip
import json
import os
import urllib.request

DATA_DIR = os.environ.get("DATA_DIR", "data")
OUT = os.path.join(DATA_DIR, "probe")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (slate-ledger research)", "Accept": "*/*"}
E = "https://site.api.espn.com/apis/site/v2/sports"


def get(url, head=False, limit=None):
    req = urllib.request.Request(url, headers=UA, method="HEAD" if head else "GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        if head:
            return r.status, r.headers.get("content-length"), None
        body = r.read(limit) if limit else r.read()
        return r.status, len(body), body


def save(name, url, head=False, limit=None):
    try:
        st, size, body = get(url, head, limit)
        if body is not None:
            with gzip.open(os.path.join(OUT, name + ".gz"), "wb") as f:
                f.write(body)
        print(f"{name}: {st} {size}")
        return body
    except Exception as e:
        print(f"{name}: FAIL {type(e).__name__} {str(e)[:80]}")


def first_event(body):
    try:
        return json.loads(body)["events"][0]["id"]
    except Exception:
        return None


for lg, path, date in [("nba", "basketball/nba", "20261026"), ("nhl", "hockey/nhl", "20261026"),
                       ("nfl", "football/nfl", "20261026"), ("ncaaf", "football/college-football", "20261024"),
                       ("mlb", "baseball/mlb", "20261026")]:
    sb = save(f"espn_{lg}_scoreboard", f"{E}/{path}/scoreboard?dates={date}")
    ev = first_event(sb) if sb else None
    if ev:
        save(f"espn_{lg}_summary", f"{E}/{path}/summary?event={ev}")
    save(f"espn_{lg}_injuries", f"{E}/{path}/injuries")
save("espn_nba_teams_stats", f"{E}/basketball/nba/teams/1/statistics")
save("espn_nba_gamelog", "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/3112335/gamelog")
save("espn_nba_schedule_team", f"{E}/basketball/nba/teams/2/schedule")
save("espn_nba_scoreboard_lastseason", f"{E}/basketball/nba/scoreboard?dates=20260301")

save("nhl_score", "https://api-web.nhle.com/v1/score/2026-10-26")
save("nhl_club_stats", "https://api-web.nhle.com/v1/club-stats/BOS/now")
save("nhl_schedule", "https://api-web.nhle.com/v1/schedule/2026-10-27")
try:
    sc = json.loads(gzip.open(os.path.join(OUT, "nhl_score.gz")).read())
    gid = sc["games"][0]["id"]
    save("nhl_boxscore", f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore")
except Exception as e:
    print("nhl_boxscore: skip", type(e).__name__)
save("moneypuck_teams_2026", "https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/teams.csv")
save("moneypuck_skaters_2026", "https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/skaters.csv", limit=400000)
save("moneypuck_goalies_2026", "https://moneypuck.com/moneypuck/playerData/seasonSummary/2026/regular/goalies.csv")

base = "https://github.com/nflverse/nflverse-data/releases/download"
for name, asset in [("nflverse_pbp_2026", "pbp/play_by_play_2026.csv.gz"),
                    ("nflverse_player_week_2026", "stats_player/stats_player_week_2026.csv"),
                    ("nflverse_player_stats_2026", "player_stats/player_stats_2026.csv"),
                    ("nflverse_team_week_2026", "stats_team/stats_team_week_2026.csv"),
                    ("nflverse_snaps_2026", "snap_counts/snap_counts_2026.csv")]:
    save(name, f"{base}/{asset}", head=True)
save("nflverse_player_week_2026_sample", f"{base}/stats_player/stats_player_week_2026.csv", limit=300000)

save("nba_cdn_scoreboard", "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json")
save("nba_stats_api", "https://stats.nba.com/stats/leaguedashteamstats?Season=2026-27&SeasonType=Regular%20Season&MeasureType=Advanced&PerMode=PerGame")
save("mlb_statsapi", "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-10-27")
save("openmeteo", "https://api.open-meteo.com/v1/forecast?latitude=40.81&longitude=-74.07&hourly=temperature_2m,wind_speed_10m,precipitation_probability&forecast_days=2")
