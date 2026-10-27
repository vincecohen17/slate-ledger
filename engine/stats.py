"""Stats collection: season box scores (ESPN), injuries (ESPN), hockey xG (MoneyPuck).

Box scores are stored one file per date in the private data repo:
  stats/<league>/<season>/<YYYY-MM-DD>.json   {"date", "final": bool, "games": [...]}
A date is fetched once it is final, so a daily run only touches yesterday and today.
"""
import csv
import io
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
UA = {"User-Agent": "Mozilla/5.0 (slate-ledger)"}
# soccer leagues: our label -> (ESPN path, Odds API sport key)
SOCCER = {"epl": ("soccer/eng.1", "soccer_epl"), "ucl": ("soccer/uefa.champions", "soccer_uefa_champs_league"),
          "uel": ("soccer/uefa.europa", "soccer_uefa_europa_league"), "mls": ("soccer/usa.1", "soccer_usa_mls"),
          "laliga": ("soccer/esp.1", "soccer_spain_la_liga"), "bund": ("soccer/ger.1", "soccer_germany_bundesliga"),
          "seriea": ("soccer/ita.1", "soccer_italy_serie_a"), "ligue1": ("soccer/fra.1", "soccer_france_ligue_one"),
          "ligamx": ("soccer/mex.1", "soccer_mexico_ligamx")}
PATHS = {"nba": "basketball/nba", "wnba": "basketball/wnba", "nhl": "hockey/nhl", "nfl": "football/nfl",
         "cfb": "football/college-football", "mlb": "baseball/mlb", **{lg: v[0] for lg, v in SOCCER.items()}}
LEAGUES = ["nba", "wnba", "nhl", "nfl", "cfb", *SOCCER]          # box scores / scores collected every stats run
PLAYER_LEAGUES = ["nba", "wnba", "nhl", "nfl", "cfb"]           # leagues with injury reports we use
EXTRA = {"cfb": "&groups=80"}       # FBS only
SEASONS = {   # (label, scan from, scan to or None = through yesterday PT)
    "nba": [("2025-26", "2025-10-15", "2026-04-20"), ("2026-27", "2026-10-01", None)],
    "nhl": [("2025-26", "2025-10-01", "2026-04-20"), ("2026-27", "2026-10-01", None)],
    "nfl": [("2025", "2025-09-01", "2026-01-10"), ("2026", "2026-09-01", None)],
    "cfb": [("2025", "2025-08-20", "2026-01-20"), ("2026", "2026-08-20", None)],
    "wnba": [("2025", "2025-05-14", "2025-10-20"), ("2026", "2026-05-01", None)],
    "epl": [("2025-26", "2025-08-14", "2026-05-25"), ("2026-27", "2026-08-14", None)],
    "ucl": [("2025-26", "2025-09-15", "2026-06-01"), ("2026-27", "2026-09-10", None)],
    "uel": [("2025-26", "2025-09-20", "2026-05-25"), ("2026-27", "2026-09-20", None)],
    "mls": [("2025", "2025-02-20", "2025-12-10"), ("2026", "2026-02-20", None)],
    "ligamx": [("2025-26", "2025-07-08", "2026-05-31"), ("2026-27", "2026-07-01", None)],
    **{lg: [("2025-26", "2025-08-14", "2026-05-25"), ("2026-27", "2026-08-14", None)] for lg in ("laliga", "bund", "seriea", "ligue1")},
}
MONEYPUCK = "https://moneypuck.com/moneypuck/playerData/seasonSummary/{y}/regular/{kind}.csv"


def _get(url, raw=False):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        b = r.read()
    return b if raw else json.loads(b.decode())


def today_pt():
    return (datetime.now(timezone.utc) - timedelta(hours=7)).date()


def _num(v):
    try:
        if isinstance(v, str) and ":" in v:          # mm:ss -> minutes
            m, s = v.split(":")
            return round(int(m) + int(s) / 60, 2)
        return float(str(v).replace("+", ""))
    except (TypeError, ValueError):
        return None


def _split(v):
    """'11-22' or '15/27' -> (11, 22)"""
    for sep in ("-", "/"):
        if isinstance(v, str) and sep in v.strip("-"):
            a, b = v.split(sep)[:2]
            return _num(a), _num(b)
    return None, None


# ---------------------------------------------------------------------------
# box score parsing (compact, league-specific)

def _team_stats(lg, t):
    out = {}
    for s in t.get("statistics", []):
        k, v = s.get("name"), s.get("displayValue")
        if k is None:
            continue
        if any(x in k for x in ("-", "/")) or k in ("completionAttempts", "thirdDownEff", "fourthDownEff"):
            a, b = _split(v)
            out[k] = [a, b]
        else:
            out[k] = _num(v)
    return out


def _players(lg, bx):
    rows = {}
    for side in bx.get("players", []):
        abbr = side["team"]["abbreviation"]
        for grp in side.get("statistics", []):
            keys = grp.get("keys") or []
            gname = grp.get("name") or ("batting" if "atBats" in keys else "pitching" if "earnedRuns" in keys else "stats")
            for a in grp.get("athletes", []):
                ath = a["athlete"]
                pid = ath.get("id")
                r = rows.setdefault(pid, {"id": pid, "name": ath.get("displayName"), "team": abbr,
                                          "pos": (ath.get("position") or {}).get("abbreviation"),
                                          "starter": bool(a.get("starter")), "dnp": bool(a.get("didNotPlay"))})
                for k, v in zip(keys, a.get("stats", [])):
                    name = k if lg in ("nba", "wnba", "nhl") else f"{gname}.{k}"
                    if any(x in k for x in ("-", "/")):
                        r[name] = list(_split(v))
                    else:
                        r[name] = _num(v)
                if lg == "nhl" and gname == "goalies":
                    r["goalie"] = True
    return list(rows.values())


def parse_summary(lg, s, event):
    comp = s["header"]["competitions"][0]
    teams = {}
    for c in comp["competitors"]:
        teams[c["homeAway"]] = {"abbr": c["team"]["abbreviation"], "name": c["team"]["displayName"],
                                "id": c["team"]["id"], "score": _num(c.get("score"))}
    for t in s["boxscore"].get("teams", []):
        side = "home" if t["team"]["abbreviation"] == teams["home"]["abbr"] else "away"
        teams[side]["stats"] = _team_stats(lg, t)
    ot = (event.get("status", {}).get("period") or 0) > {"nba": 4, "wnba": 4, "nhl": 3, "nfl": 4, "cfb": 4}.get(lg, 99)
    return {"id": event["id"], "date": comp.get("date"), "home": teams["home"], "away": teams["away"],
            "ot": ot, "players": _players(lg, s["boxscore"])}


def fetch_day(lg, season, d, data_dir, force=False):
    fn = os.path.join(data_dir, "stats", lg, season, f"{d}.json")
    if not force and os.path.exists(fn):
        try:
            if json.load(open(fn)).get("final"):
                return 0
        except Exception:
            pass
    sb = _get(f"{ESPN}/{PATHS[lg]}/scoreboard?dates={d.replace('-', '')}&limit=300{EXTRA.get(lg, '')}")
    soccer = PATHS[lg].startswith("soccer")
    events = [e for e in sb.get("events", []) if soccer or (e.get("season") or {}).get("type") in (2, 3)]
    games, final = [], True
    for e in events:
        st = e.get("status", {}).get("type", {})
        if not st.get("completed"):
            final = False
            continue
        if soccer:   # scores are enough for the soccer ratings; skip the heavy summaries
            c = e["competitions"][0]["competitors"]
            side = {x["homeAway"]: x for x in c}
            games.append({"id": e["id"], "date": e["date"], "players": [],
                          **{k: {"abbr": side[k]["team"]["abbreviation"], "name": side[k]["team"]["displayName"],
                                 "score": _num(side[k].get("score"))} for k in ("home", "away")}})
            continue
        try:
            games.append(parse_summary(lg, _get(f"{ESPN}/{PATHS[lg]}/summary?event={e['id']}"), e))
        except Exception:
            final = False
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    past = date.fromisoformat(d) < today_pt()
    json.dump({"date": d, "final": final and past, "games": games}, open(fn, "w"), separators=(",", ":"))
    return len(games)


def collect(lg, data_dir, backfill=False, workers=8):
    """Fetch every needed date. Daily runs only touch dates not yet final."""
    jobs = []
    for season, start, end in SEASONS[lg]:
        d0 = date.fromisoformat(start)
        d1 = date.fromisoformat(end) if end else today_pt() - timedelta(days=1)
        if not backfill and end:
            # finished seasons are only collected on a backfill
            if os.path.isdir(os.path.join(data_dir, "stats", lg, season)):
                continue
        d = d0
        while d <= d1:
            jobs.append((season, d.isoformat()))
            d += timedelta(days=1)
    n = 0
    with ThreadPoolExecutor(workers) as ex:
        for k in ex.map(lambda j: _safe(fetch_day, lg, j[0], j[1], data_dir), jobs):
            n += k or 0
    return n


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return 0


def load_games(lg, season, data_dir):
    d = os.path.join(data_dir, "stats", lg, season)
    out = []
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            try:
                out += json.load(open(os.path.join(d, f)))["games"]
            except Exception:
                pass
    return out


# ---------------------------------------------------------------------------
# injuries

def injuries(lg, data_dir):
    try:
        data = _get(f"{ESPN}/{PATHS[lg]}/injuries")
    except Exception:
        return None
    out = {}
    for t in data.get("injuries", []):
        lst = []
        for x in t.get("injuries", []):
            a = x.get("athlete", {})
            lst.append({"id": str(a.get("id") or (a.get("links") or [{}])[0].get("href", "").split("/id/")[-1].split("/")[0]),
                        "name": a.get("displayName"), "pos": (a.get("position") or {}).get("abbreviation"),
                        "status": (x.get("status") or "").title(), "detail": (x.get("details") or {}).get("type"),
                        "ret": (x.get("details") or {}).get("returnDate"), "note": x.get("shortComment"), "date": x.get("date")})
        abbr = None
        for a in [x.get("athlete", {}) for x in t.get("injuries", [])]:
            abbr = (a.get("team") or {}).get("abbreviation") or abbr
        out[t.get("displayName")] = {"abbr": abbr, "list": lst}
    fn = os.path.join(data_dir, "stats", lg, "injuries.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump({"at": datetime.now(timezone.utc).isoformat(), "teams": out}, open(fn, "w"), separators=(",", ":"))
    return out


# ---------------------------------------------------------------------------
# MoneyPuck (hockey xG)

def moneypuck(data_dir, years=(2025, 2026), refresh_current=True):
    out = {}
    for y in years:
        for kind in ("teams", "skaters", "goalies"):
            fn = os.path.join(data_dir, "stats", "nhl", "moneypuck", f"{kind}_{y}.csv")
            if os.path.exists(fn) and (y != max(years) or not refresh_current):
                continue
            try:
                body = _get(MONEYPUCK.format(y=y, kind=kind), raw=True)
                os.makedirs(os.path.dirname(fn), exist_ok=True)
                open(fn, "wb").write(body)
                out[f"{kind}_{y}"] = len(body)
            except Exception:
                out[f"{kind}_{y}"] = 0
    try:
        out["goalieGames"] = moneypuck_goalie_games(data_dir, max(years))
    except Exception as e:
        out["goalieGames"] = type(e).__name__
    return out


MP_GOALIE_GAMES = "https://moneypuck.com/moneypuck/playerData/careers/gameByGame/{kind}/goalies/{pid}.csv"


def moneypuck_goalie_games(data_dir, y=2026, min_gp=2, max_age_h=8):
    """Per-game goalie lines (shots, goals, expected goals -> GSAx) for every goalie with real work this season.
    stats/nhl/moneypuck/goalie_games_<y>.json  {name: [{date, opp, home, sa, ga, sv, xga, gsax, toi}]}"""
    fn = os.path.join(data_dir, "stats", "nhl", "moneypuck", f"goalie_games_{y}.json")
    if os.path.exists(fn) and (datetime.now().timestamp() - os.path.getmtime(fn)) < max_age_h * 3600:
        return {"cached": True}
    goalies = [r for r in read_moneypuck(data_dir, "goalies", y) if float(r.get("games_played") or 0) >= min_gp]
    out, fails = {}, 0

    def one(r):
        rows = []
        for kind in ("regular", "playoffs"):
            try:
                body = _get(MP_GOALIE_GAMES.format(kind=kind, pid=r["playerId"]), raw=True).decode("utf-8", "ignore")
            except Exception:
                continue
            for g in csv.DictReader(io.StringIO(body)):
                if g.get("situation") != "all" or str(g.get("season")) != str(y):
                    continue
                f = lambda k: float(g.get(k) or 0)
                d = str(g.get("gameDate") or "")
                rows.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else d, "opp": g.get("opposingTeam"), "home": g.get("home_or_away") == "HOME",
                             "sa": int(f("ongoal")), "ga": int(f("goals")), "sv": int(f("ongoal") - f("goals")), "xga": round(f("xGoals"), 2),
                             "gsax": round(f("xGoals") - f("goals"), 2), "toi": round(f("icetime") / 60, 1)})
        rows.sort(key=lambda x: x["date"])
        return r["name"], rows
    with ThreadPoolExecutor(6) as ex:
        for name, rows in ex.map(one, goalies):
            if rows:
                out[name] = rows[-20:]
            else:
                fails += 1
    if out:
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        json.dump(out, open(fn, "w"), separators=(",", ":"))
    return {"goalies": len(out), "missing": fails}


def read_moneypuck(data_dir, kind, y, situation="all"):
    fn = os.path.join(data_dir, "stats", "nhl", "moneypuck", f"{kind}_{y}.csv")
    if not os.path.exists(fn):
        return []
    rows = list(csv.DictReader(io.StringIO(open(fn).read())))
    return [r for r in rows if r.get("situation") == situation]


def load_json(data_dir, rel):
    try:
        return json.load(open(os.path.join(data_dir, rel)))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# nflverse (NFL): weekly player stats + team efficiency from play-by-play

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
NFL2ESPN = {"LA": "LAR", "WAS": "WSH"}


def nflverse(data_dir, seasons=(2025, 2026)):
    out = {}
    d = os.path.join(data_dir, "stats", "nfl", "nflverse")
    os.makedirs(d, exist_ok=True)
    for y in seasons:
        cur = y == max(seasons)
        fn = os.path.join(d, f"player_week_{y}.csv")
        if cur or not os.path.exists(fn):
            try:
                open(fn, "wb").write(_get(f"{NFLVERSE}/stats_player/stats_player_week_{y}.csv", raw=True))
                out[f"players_{y}"] = "ok"
            except Exception as e:
                out[f"players_{y}"] = type(e).__name__
        tfn = os.path.join(d, f"team_week_{y}.json")
        if cur or not os.path.exists(tfn):
            try:
                import gzip as _gz
                raw = _gz.decompress(_get(f"{NFLVERSE}/pbp/play_by_play_{y}.csv.gz", raw=True)).decode()
                json.dump(_nfl_team_weeks(raw), open(tfn, "w"), separators=(",", ":"))
                out[f"pbp_{y}"] = "ok"
            except Exception as e:
                out[f"pbp_{y}"] = type(e).__name__
    return out


def _nfl_team_weeks(csv_text):
    """Per team per game: plays, EPA, success, pass rate vs expected, explosives, early-down EPA, red-zone TDs."""
    agg = {}
    for r in csv.DictReader(io.StringIO(csv_text)):
        if r.get("season_type") != "REG" and r.get("season_type") != "POST":
            continue
        if r.get("play_type") not in ("pass", "run") or r.get("qb_kneel") == "1" or r.get("qb_spike") == "1":
            continue
        try:
            epa = float(r["epa"]) if r.get("epa") not in ("", "NA", None) else None
        except ValueError:
            epa = None
        if epa is None:
            continue
        gid, off, de = r["game_id"], NFL2ESPN.get(r["posteam"], r["posteam"]), NFL2ESPN.get(r["defteam"], r["defteam"])
        is_pass = r.get("pass") == "1"
        yds = float(r.get("yards_gained") or 0)
        xp = r.get("xpass")
        down = r.get("down")
        for team, side in ((off, "o"), (de, "d")):
            a = agg.setdefault((gid, team), {"game": gid, "week": int(r.get("week") or 0), "team": team,
                                             "opp": de if side == "o" else off})
            k = lambda n: f"{side}_{n}"
            a[k("plays")] = a.get(k("plays"), 0) + 1
            a[k("epa")] = a.get(k("epa"), 0.0) + epa
            a[k("succ")] = a.get(k("succ"), 0) + (1 if r.get("success") == "1" else 0)
            a[k("pass")] = a.get(k("pass"), 0) + (1 if is_pass else 0)
            if xp not in ("", "NA", None):
                a[k("xpass")] = a.get(k("xpass"), 0.0) + float(xp)
                a[k("xn")] = a.get(k("xn"), 0) + 1
            a[k("expl")] = a.get(k("expl"), 0) + (1 if (is_pass and yds >= 20) or (not is_pass and yds >= 12) else 0)
            if down in ("1", "2"):
                a[k("ed_epa")] = a.get(k("ed_epa"), 0.0) + epa
                a[k("ed_n")] = a.get(k("ed_n"), 0) + 1
            if is_pass:
                a[k("pass_epa")] = a.get(k("pass_epa"), 0.0) + epa
            else:
                a[k("rush_epa")] = a.get(k("rush_epa"), 0.0) + epa
    return list(agg.values())


def read_nfl_players(data_dir, y):
    fn = os.path.join(data_dir, "stats", "nfl", "nflverse", f"player_week_{y}.csv")
    if not os.path.exists(fn):
        return []
    rows = list(csv.DictReader(io.StringIO(open(fn).read())))
    for r in rows:
        r["team"] = NFL2ESPN.get(r.get("team"), r.get("team"))
        r["opponent_team"] = NFL2ESPN.get(r.get("opponent_team"), r.get("opponent_team"))
    return rows


# ---------------------------------------------------------------------------
# Tennis: ESPN tour scoreboards (every completed singles match, one file per tour per month)

def _tennis_day(tour, d):
    sb = _get(f"{ESPN}/tennis/{tour}/scoreboard?dates={d.replace('-', '')}")
    out = []
    for ev in sb.get("events", []):
        groups = ev.get("groupings") or [{"competitions": ev.get("competitions", [])}]
        for gr in groups:
            if "doubles" in str((gr.get("grouping") or {}).get("slug", "")).lower():
                continue
            for c in gr.get("competitions", []):
                if not (c.get("status", {}).get("type", {}) or {}).get("completed"):
                    continue
                comp = c.get("competitors", [])
                if len(comp) != 2:
                    continue
                names = [(x.get("athlete") or {}).get("displayName") for x in comp]
                if not all(names):
                    continue
                w = [x for x in comp if x.get("winner")]
                if len(w) != 1:
                    continue
                win = (w[0].get("athlete") or {}).get("displayName")
                lose = names[1] if names[0] == win else names[0]
                out.append({"date": d, "tour": tour, "event": ev.get("name"), "winner_name": win, "loser_name": lose,
                            "surface": (c.get("venue") or {}).get("surface") or ""})
    return out


def tennis(data_dir, days_back=540, workers=8):
    d = os.path.join(data_dir, "stats", "tennis")
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):              # clear the empty files left by the old source
        if f.endswith(".csv") and os.path.getsize(os.path.join(d, f)) == 0:
            os.remove(os.path.join(d, f))
    end = today_pt() - timedelta(days=1)
    jobs = []
    for tour in ("atp", "wta"):
        for i in range(days_back):
            day = (end - timedelta(days=i)).isoformat()
            fn = os.path.join(d, tour, f"{day}.json")
            if os.path.exists(fn) and i > 3:
                continue
            jobs.append((tour, day, fn))
    n = 0

    def run(j):
        tour, day, fn = j
        try:
            rows = _tennis_day(tour, day)
        except Exception:
            return 0
        os.makedirs(os.path.dirname(fn), exist_ok=True)
        json.dump(rows, open(fn, "w"), separators=(",", ":"))
        return len(rows)
    with ThreadPoolExecutor(workers) as ex:
        for k in ex.map(run, jobs):
            n += k
    return {"days": len(jobs), "matches": n}


def load_tennis(data_dir):
    d = os.path.join(data_dir, "stats", "tennis")
    rows = []
    for tour in ("atp", "wta"):
        p = os.path.join(d, tour)
        if os.path.isdir(p):
            for f in sorted(os.listdir(p)):
                try:
                    rows += json.load(open(os.path.join(p, f)))
                except Exception:
                    pass
    # a tournament scoreboard repeats finished matches on later days: keep each match once, at its first date
    rows.sort(key=lambda r: r["date"])
    seen, out = set(), []
    for r in rows:
        k = (r.get("event"), r["winner_name"], r["loser_name"])
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# MLB: StatsAPI probables, team hitting and pitcher season lines

MLBAPI = "https://statsapi.mlb.com/api/v1"


def mlb(data_dir, season=None):
    """MLB: Statcast (Baseball Savant) + StatsAPI (schedule, lineups, rosters, game logs) + ESPN injuries."""
    import stats_mlb
    out = stats_mlb.collect(data_dir)
    try:
        inj = injuries("mlb", data_dir)
        out["injuries"] = len(inj or {})
    except Exception as e:
        out["injuries"] = type(e).__name__
    return out
