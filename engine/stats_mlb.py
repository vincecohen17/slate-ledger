"""MLB data: Baseball Savant (Statcast) leaderboards and platoon splits, MLB StatsAPI schedule,
lineups, rosters, handedness, game logs and weather, Statcast park factors.

Everything lands in the private data repo under stats/mlb/:
  schedule.json             StatsAPI schedule (5 days back for projected lineups, 2 ahead) with lineups, probables, weather
  teams.json                team hitting/pitching season totals (StatsAPI)
  people.json               {id: {name, bats, throws, pos, team, logs:[last 15 games]}} for everyone on an upcoming roster
  savant/<year>/batters.json, pitchers.json   Statcast expected stats, batted ball, plate discipline, bat tracking
  savant/arsenal.json       pitch mix, whiff and run value by pitch (current season)
  savant/splits.json        batters vs LHP/RHP and pitchers vs LHB/RHB (this season + last, Statcast search)
  savant/parks.json         Statcast park factors by venue (3-year rolling)
Savant files refresh every 12 hours, rosters and game logs every 6, the schedule every run.
"""
import csv
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (slate-ledger)", "Accept": "*/*"}
MLBAPI = "https://statsapi.mlb.com/api/v1"
SV = "https://baseballsavant.mlb.com"
GAME_TYPES = "R,F,D,L,W"

BAT_SEL = ("pa,ab,hit,single,double,triple,home_run,strikeout,walk,k_percent,bb_percent,batting_avg,slg_percent,on_base_percent,"
           "woba,xwoba,xba,xslg,xobp,xiso,wobacon,xwobacon,exit_velocity_avg,launch_angle_avg,sweet_spot_percent,barrel_batted_rate,"
           "hard_hit_percent,whiff_percent,swing_percent,oz_swing_percent,z_swing_percent,iz_contact_percent,pull_percent,"
           "groundballs_percent,flyballs_percent,linedrives_percent,sprint_speed,r_total_stolen_base,r_total_caught_stealing")
PIT_SEL = ("p_game,p_formatted_ip,pa,strikeout,walk,home_run,hit,k_percent,bb_percent,woba,xwoba,xba,xslg,xera,p_era,exit_velocity_avg,"
           "launch_angle_avg,barrel_batted_rate,hard_hit_percent,whiff_percent,oz_swing_percent,groundballs_percent,flyballs_percent,"
           "ff_avg_speed,ff_avg_spin,p_starting_p,p_quality_start,p_total_stolen_base")
SPLIT_STATS = ("chk_stats_pa=on&chk_stats_abs=on&chk_stats_hits=on&chk_stats_k_percent=on&chk_stats_bb_percent=on&chk_stats_xwoba=on"
               "&chk_stats_woba=on&chk_stats_xslg=on&chk_stats_barrels_per_pa_percent=on&chk_stats_whiff_percent=on")


def season_for(d):
    """MLB season year for a date (Jan-Feb belong to last season)."""
    return d.year if d.month >= 3 else d.year - 1


def _get(url, raw=False, timeout=60, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
                b = r.read()
            return b if raw else json.loads(b.decode())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 + 3 * i)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _csv(body):
    txt = body.decode("utf-8-sig", "ignore")
    rows = list(csv.DictReader(io.StringIO(txt)))
    out = []
    for r in rows:
        r = {k.strip().strip('"').strip(): v for k, v in r.items() if k}
        out.append(r)
    return out


def _fresh(path, hours):
    return os.path.exists(path) and (time.time() - os.path.getmtime(path)) < hours * 3600


def _dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(obj, open(path, "w"), separators=(",", ":"))


def _name(r):
    n = r.get("last_name, first_name") or r.get("last_name") or r.get("name") or r.get("player_name") or ""
    if "," in n:
        last, first = [x.strip() for x in n.split(",", 1)]
        return f"{first} {last}"
    return n


def _rows_by_id(rows, id_key="player_id"):
    out = {}
    for r in rows:
        pid = r.get(id_key) or r.get("id") or r.get("player_id")
        if not pid:
            continue
        o = {"name": _name(r)}
        for k, v in r.items():
            if k in ("last_name, first_name", "last_name", "first_name", "name", "player_name", id_key, "id", "year"):
                continue
            fv = _f(v)
            o[k] = fv if fv is not None else (v or None)
        out[str(pid)] = o
    return out


# ---------------------------------------------------------------------------
# Savant

def savant(data_dir, year, diag):
    d = os.path.join(data_dir, "stats", "mlb", "savant")
    for y in (year, year - 1):
        fb, fp = os.path.join(d, str(y), "batters.json"), os.path.join(d, str(y), "pitchers.json")
        hours = 12 if y == year else 24 * 30
        if not _fresh(fb, hours):
            try:
                bat = _rows_by_id(_csv(_get(f"{SV}/leaderboard/custom?year={y}&type=batter&filter=&min=1&selections={BAT_SEL}&chart=false&csv=true", raw=True)))
                try:
                    bt = _csv(_get(f"{SV}/leaderboard/bat-tracking?attackZone=&batSide=&contactType=&count=&dateStart={y}-03-01&dateEnd={y}-11-30"
                                   f"&gameType=&isHardHit=&minSwings=1&minGroupSwings=1&pitchHand=&pitchType=&seasonStart=&seasonEnd=&team=&type=batter&csv=true", raw=True))
                    for r in bt:
                        pid = r.get("id")
                        if pid in bat:
                            bat[pid].update(batSpeed=_f(r.get("avg_bat_speed")), blastPerSwing=_f(r.get("blast_per_swing")),
                                            squaredUp=_f(r.get("squared_up_per_swing")), swingLength=_f(r.get("swing_length")))
                except Exception as e:
                    diag[f"battracking_{y}"] = type(e).__name__
                _dump(fb, bat)
                diag[f"batters_{y}"] = len(bat)
            except Exception as e:
                diag[f"batters_{y}"] = f"FAIL {type(e).__name__}"
        if not _fresh(fp, hours):
            try:
                pit = _rows_by_id(_csv(_get(f"{SV}/leaderboard/custom?year={y}&type=pitcher&filter=&min=1&selections={PIT_SEL}&chart=false&csv=true", raw=True)))
                _dump(fp, pit)
                diag[f"pitchers_{y}"] = len(pit)
            except Exception as e:
                diag[f"pitchers_{y}"] = f"FAIL {type(e).__name__}"
    fa = os.path.join(d, "arsenal.json")
    if not _fresh(fa, 12):
        try:
            ars = {}
            for r in _csv(_get(f"{SV}/leaderboard/pitch-arsenal-stats?type=pitcher&pitchType=&year={year}&team=&min=1&csv=true", raw=True)):
                ars.setdefault(r["player_id"], []).append({
                    "type": r.get("pitch_type"), "name": r.get("pitch_name"), "usage": _f(r.get("pitch_usage")), "pitches": _f(r.get("pitches")),
                    "rv100": _f(r.get("run_value_per_100")), "whiff": _f(r.get("whiff_percent")), "k": _f(r.get("k_percent")),
                    "xwoba": _f(r.get("est_woba")), "hardHit": _f(r.get("hard_hit_percent")), "team": r.get("team_name_alt")})
            for v in ars.values():
                v.sort(key=lambda p: -(p["usage"] or 0))
            _dump(fa, ars)
            diag["arsenal"] = len(ars)
        except Exception as e:
            diag["arsenal"] = f"FAIL {type(e).__name__}"
    fs = os.path.join(d, "splits.json")
    if not _fresh(fs, 12):
        try:
            seas = urllib.parse.quote(f"{year}|{year - 1}|")
            out = {}
            for ptype, hand_key, hands in (("batter", "pitcher_throws", ("L", "R")), ("pitcher", "batter_stands", ("L", "R"))):
                for h in hands:
                    url = (f"{SV}/statcast_search/csv?all=true&hfGT=R%7C&hfSea={seas}&player_type={ptype}&{hand_key}={h}&group_by=name"
                           f"&min_pitches=0&min_results=0&min_pas=1&sort_col=pitches&sort_order=desc&{SPLIT_STATS}")
                    for r in _csv(_get(url, raw=True, timeout=120)):
                        pid = r.get("player_id")
                        if not pid:
                            continue
                        o = out.setdefault(ptype, {}).setdefault(pid, {})
                        o["v" + h] = {k: _f(r.get(k)) for k in ("pa", "abs", "hits", "singles", "doubles", "triples", "hrs", "so", "bb",
                                                                "xwoba", "woba", "xslg", "k_percent", "bb_percent", "barrels_per_pa_percent")}
            _dump(fs, out)
            diag["splits"] = {k: len(v) for k, v in out.items()}
        except Exception as e:
            diag["splits"] = f"FAIL {type(e).__name__}"
    fpk = os.path.join(d, "parks.json")
    if not _fresh(fpk, 24 * 7):
        try:
            html = _get(f"{SV}/leaderboard/statcast-park-factors?type=year&year={year}&batSide=&stat=index_wOBA&condition=All&rolling=3&parks=mlb",
                        raw=True).decode("utf-8", "ignore")
            m = re.search(r"var data = (\[.*?\]);", html, re.S)
            parks = {}
            for r in json.loads(m.group(1)) if m else []:
                parks[str(r.get("venue_id"))] = {"name": r.get("venue_name"), "team": r.get("name_display_club"), "teamId": r.get("main_team_id"),
                                                 **{k[6:]: _f(v) for k, v in r.items() if k.startswith("index_")}}
            if parks:
                _dump(fpk, parks)
            diag["parks"] = len(parks)
        except Exception as e:
            diag["parks"] = f"FAIL {type(e).__name__}"


# ---------------------------------------------------------------------------
# StatsAPI

def _log_row(sp, group):
    s = sp.get("stat", {})
    row = {"date": sp.get("date"), "opp": (sp.get("opponent") or {}).get("name"), "home": sp.get("isHome")}
    if group == "hitting":
        row.update(pa=s.get("plateAppearances"), ab=s.get("atBats"), h=s.get("hits"), tb=s.get("totalBases"), hr=s.get("homeRuns"),
                   r=s.get("runs"), rbi=s.get("rbi"), bb=s.get("baseOnBalls"), k=s.get("strikeOuts"), sb=s.get("stolenBases"),
                   d2=s.get("doubles"), d3=s.get("triples"))
        row["s1"] = (row["h"] or 0) - (row["d2"] or 0) - (row["d3"] or 0) - (row["hr"] or 0)
        row["hrr"] = (row["h"] or 0) + (row["r"] or 0) + (row["rbi"] or 0)
    else:
        ip = _f(s.get("inningsPitched")) or 0
        outs = int(ip) * 3 + round((ip - int(ip)) * 10)
        row.update(ip=s.get("inningsPitched"), outs=outs, k=s.get("strikeOuts"), bb=s.get("baseOnBalls"), h=s.get("hits"),
                   er=s.get("earnedRuns"), bf=s.get("battersFaced"), pitches=s.get("numberOfPitches"), gs=s.get("gamesStarted"),
                   w=s.get("wins"))
    return row


def statsapi(data_dir, year, diag):
    d = os.path.join(data_dir, "stats", "mlb")
    os.makedirs(d, exist_ok=True)
    today = (datetime.now(timezone.utc) - timedelta(hours=7)).date()
    sched = _get(f"{MLBAPI}/schedule?sportId=1&startDate={today - timedelta(days=5)}&endDate={today + timedelta(days=2)}"
                 f"&gameType={GAME_TYPES}&hydrate=probablePitcher,lineups,weather,venue,team")
    _dump(os.path.join(d, "schedule.json"), sched)
    upcoming = [g for x in sched.get("dates", []) for g in x.get("games", []) if x["date"] >= str(today)]
    diag["schedule"] = len(upcoming)
    if not _fresh(os.path.join(d, "teams.json"), 6):
        th = _get(f"{MLBAPI}/teams/stats?season={year}&group=hitting&stats=season&sportId=1&gameType=R")
        tp = _get(f"{MLBAPI}/teams/stats?season={year}&group=pitching&stats=season&sportId=1&gameType=R")
        _dump(os.path.join(d, "teams.json"), {"hitting": th, "pitching": tp})
    # everyone who could play in an upcoming game: active rosters + posted lineups + probables
    pp = os.path.join(d, "people.json")
    people = {}
    try:
        people = json.load(open(pp))
    except Exception:
        pass
    teams = {g["teams"][s]["team"]["id"] for g in upcoming for s in ("home", "away")}
    ids = set()
    rp = os.path.join(d, "rosters.json")
    rosters = {}
    try:
        rosters = json.load(open(rp)) if _fresh(rp, 6) else {}
    except Exception:
        pass
    for t in teams:
        if str(t) not in rosters:
            try:
                ro = _get(f"{MLBAPI}/teams/{t}/roster?rosterType=active")
                rosters[str(t)] = [{"id": r["person"]["id"], "pos": (r.get("position") or {}).get("abbreviation")} for r in ro.get("roster", [])]
            except Exception:
                rosters[str(t)] = []
    _dump(rp, rosters)
    for t in teams:
        ids |= {r["id"] for r in rosters.get(str(t), [])}
    for g in upcoming:
        for s in ("home", "away"):
            if g["teams"][s].get("probablePitcher"):
                ids.add(g["teams"][s]["probablePitcher"]["id"])
        for key in ("homePlayers", "awayPlayers"):
            ids |= {p["id"] for p in (g.get("lineups") or {}).get(key, [])}
    stale = [i for i in ids if str(i) not in people or (time.time() - people[str(i)].get("at", 0)) > 6 * 3600]

    def batch(chunk):
        q = ",".join(str(i) for i in chunk)
        res = _get(f"{MLBAPI}/people?personIds={q}&hydrate=currentTeam,stats(group=[hitting,pitching],type=[gameLog],season={year},gameType=[{GAME_TYPES}])")
        out = {}
        for p in res.get("people", []):
            o = {"name": p.get("fullName"), "bats": (p.get("batSide") or {}).get("code"), "throws": (p.get("pitchHand") or {}).get("code"),
                 "pos": (p.get("primaryPosition") or {}).get("abbreviation"), "team": (p.get("currentTeam") or {}).get("id"), "at": time.time()}
            for st in p.get("stats", []):
                grp = (st.get("group") or {}).get("displayName")
                rows = [_log_row(sp, grp) for sp in st.get("splits", [])]
                rows.sort(key=lambda r: r.get("date") or "")
                if rows:
                    o["hit" if grp == "hitting" else "pit"] = rows[-15:]
            out[str(p["id"])] = o
        return out
    chunks = [stale[i:i + 40] for i in range(0, len(stale), 40)]
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda c: _safe(batch, c), chunks):
            people.update(res or {})
    _dump(pp, people)
    diag["people"] = len(people)
    diag["peopleFetched"] = len(stale)


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def collect(data_dir):
    today = (datetime.now(timezone.utc) - timedelta(hours=7)).date()
    year = season_for(today)
    diag = {"season": year}
    try:
        statsapi(data_dir, year, diag)
    except Exception as e:
        diag["statsapi"] = f"FAIL {type(e).__name__}"
    savant(data_dir, year, diag)
    return diag
