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
SV = "https://baseballsavant.mlb.com"
MLB = "https://statsapi.mlb.com/api/v1"


def get(url, head=False, limit=None):
    req = urllib.request.Request(url, headers=UA, method="HEAD" if head else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
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


# --- Odds API sports list (free endpoint, no credits)
key = os.environ.get("ODDS_API_KEY")
if key:
    try:
        st, size, body = get(f"https://api.the-odds-api.com/v4/sports?all=true&apiKey={key}")
        keys = [{k: s.get(k) for k in ("key", "group", "title", "active", "has_outrights")} for s in json.loads(body)]
        json.dump(keys, open(os.path.join(OUT, "oddsapi_sports.json"), "w"), indent=0)
        print(f"oddsapi_sports: {st} {len(keys)}")
    except Exception as e:
        print(f"oddsapi_sports: FAIL {type(e).__name__}")

# --- WNBA (ESPN)
for d in ("20250715", "20260715", "20260601"):
    save(f"espn_wnba_scoreboard_{d}", f"{E}/basketball/wnba/scoreboard?dates={d}")
try:
    ev = json.loads(gzip.open(os.path.join(OUT, "espn_wnba_scoreboard_20250715.gz")).read())["events"][0]["id"]
    save("espn_wnba_summary", f"{E}/basketball/wnba/summary?event={ev}")
except Exception as e:
    print("espn_wnba_summary: skip", type(e).__name__)
save("espn_wnba_injuries", f"{E}/basketball/wnba/injuries")
save("espn_wnba_teams", f"{E}/basketball/wnba/teams")
save("espn_wnba_roster", f"{E}/basketball/wnba/teams/5/roster")

# --- Baseball Savant / Statcast
bat_sel = ("pa,ab,hit,single,double,triple,home_run,strikeout,walk,k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
           "woba,xwoba,xba,xslg,xobp,xiso,wobacon,xwobacon,exit_velocity_avg,launch_angle_avg,sweet_spot_percent,barrel_batted_rate,"
           "hard_hit_percent,avg_best_speed,avg_hyper_speed,whiff_percent,swing_percent,oz_swing_percent,z_swing_percent,"
           "oz_contact_percent,iz_contact_percent,pull_percent,groundballs_percent,flyballs_percent,linedrives_percent,sprint_speed,r_total_stolen_base,r_total_caught_stealing")
pit_sel = ("p_game,p_formatted_ip,pa,strikeout,walk,home_run,k_percent,bb_percent,woba,xwoba,xba,xslg,xera,p_era,exit_velocity_avg,"
           "launch_angle_avg,barrel_batted_rate,hard_hit_percent,whiff_percent,oz_swing_percent,groundballs_percent,flyballs_percent,"
           "n_ff_formatted,ff_avg_speed,ff_avg_spin,n_sl_formatted,n_ch_formatted,n_cu_formatted,n_si_formatted,n_fc_formatted,n_fs_formatted,n_st_formatted,p_starting_p,p_relief")
for t, sel in (("batter", bat_sel), ("pitcher", pit_sel)):
    save(f"savant_custom_{t}_2026", f"{SV}/leaderboard/custom?year=2026&type={t}&filter=&min=1&selections={sel}&chart=false&csv=true")
    save(f"savant_expected_{t}_2026", f"{SV}/leaderboard/expected_statistics?type={t}&year=2026&position=&team=&filterType=bip&min=1&csv=true")
save("savant_arsenal_2026", f"{SV}/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year=2026&team=&min=1&csv=true")
save("savant_arsenal_batter_2026", f"{SV}/leaderboard/pitch-arsenal-stats?type=batter&pitchType=&year=2026&team=&min=1&csv=true")
save("savant_battracking_2026", f"{SV}/leaderboard/bat-tracking?attackZone=&batSide=&contactType=&count=&dateStart=2026-03-01&dateEnd=2026-11-30&gameType=&isHardHit=&minSwings=1&minGroupSwings=1&pitchHand=&pitchType=&seasonStart=&seasonEnd=&team=&type=batter&csv=true")
save("savant_sprint_2026", f"{SV}/leaderboard/sprint_speed?min_season=2026&max_season=2026&position=&team=&min=1&csv=true")
save("savant_parkfactors", f"{SV}/leaderboard/statcast-park-factors?type=year&year=2026&batSide=&stat=index_wOBA&condition=All&rolling=3&parks=mlb", limit=600000)
# grouped statcast search: one row per batter vs left-handed pitchers (platoon splits in one request)
q = ("all=true&hfGT=R%7CF%7CD%7CL%7CW%7C&hfSea=2026%7C&player_type=batter&pitcher_throws=L&group_by=name&min_pitches=0&min_results=0"
     "&min_pas=1&sort_col=pitches&player_event_sort=api_p_release_speed&sort_order=desc&chk_stats_pa=on&chk_stats_abs=on&chk_stats_hits=on"
     "&chk_stats_k_percent=on&chk_stats_bb_percent=on&chk_stats_xwoba=on&chk_stats_woba=on&chk_stats_xslg=on&chk_stats_barrels_per_pa_percent=on&chk_stats_whiff_percent=on")
save("savant_search_grouped_vsL", f"{SV}/statcast_search/csv?{q}", limit=2000000)
# pitch-level search for one day (game logs, splits)
save("savant_search_day", f"{SV}/statcast_search/csv?all=true&hfGT=R%7CF%7CD%7CL%7CW%7C&hfSea=2026%7C&player_type=pitcher&game_date_gt=2026-09-20&game_date_lt=2026-09-20&type=details", limit=3000000)

# --- MLB StatsAPI: lineups, game logs, splits, weather
save("mlb_schedule_hydrated", f"{MLB}/schedule?sportId=1&startDate=2026-09-20&endDate=2026-09-20&hydrate=probablePitcher,lineups,weather,venue,team")
save("mlb_schedule_today", f"{MLB}/schedule?sportId=1&startDate=2026-10-24&endDate=2026-10-31&hydrate=probablePitcher,lineups,weather,venue,team")
try:
    sc = json.loads(gzip.open(os.path.join(OUT, "mlb_schedule_hydrated.gz")).read())
    gpk = sc["dates"][0]["games"][0]["gamePk"]
    save("mlb_boxscore", f"{MLB}/game/{gpk}/boxscore")
except Exception as e:
    print("mlb_boxscore: skip", type(e).__name__)
save("mlb_gamelog_hitter", f"{MLB}/people/592450/stats?stats=gameLog&group=hitting&season=2026")
save("mlb_gamelog_pitcher", f"{MLB}/people/669373/stats?stats=gameLog&group=pitching&season=2026")
save("mlb_splits_hitter", f"{MLB}/people/592450/stats?stats=statSplits&group=hitting&season=2026&sitCodes=vl,vr")
save("mlb_team_roster", f"{MLB}/teams/147/roster?rosterType=active&hydrate=person(stats(type=season,group=hitting,season=2026))", limit=800000)
save("mlb_venues", f"{MLB}/venues?sportId=1&hydrate=location,fieldInfo")

# --- book logo sources (only availability; the site loads them in the browser)
save("logo_google_dk", "https://www.google.com/s2/favicons?domain=draftkings.com&sz=128")
save("logo_ddg_dk", "https://icons.duckduckgo.com/ip3/draftkings.com.ico")
