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
PATHS = {"nba": "basketball/nba", "nhl": "hockey/nhl", "nfl": "football/nfl"}
SEASONS = {   # (label, scan from, scan to or None = through yesterday PT)
    "nba": [("2025-26", "2025-10-15", "2026-04-20"), ("2026-27", "2026-10-01", None)],
    "nhl": [("2025-26", "2025-10-01", "2026-04-20"), ("2026-27", "2026-10-01", None)],
    "nfl": [("2025", "2025-09-01", "2026-01-10"), ("2026", "2026-09-01", None)],
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
                    name = k if lg in ("nba", "nhl") else f"{gname}.{k}"
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
    ot = (event.get("status", {}).get("period") or 0) > {"nba": 4, "nhl": 3, "nfl": 4}[lg]
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
    sb = _get(f"{ESPN}/{PATHS[lg]}/scoreboard?dates={d.replace('-', '')}&limit=100")
    events = [e for e in sb.get("events", []) if (e.get("season") or {}).get("type") in (2, 3)]
    games, final = [], True
    for e in events:
        st = e.get("status", {}).get("type", {})
        if not st.get("completed"):
            final = False
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
    return out


def read_moneypuck(data_dir, kind, y, situation="all"):
    fn = os.path.join(data_dir, "stats", "nhl", "moneypuck", f"{kind}_{y}.csv")
    if not os.path.exists(fn):
        return []
    rows = list(csv.DictReader(io.StringIO(open(fn).read())))
    return [r for r in rows if r.get("situation") == situation]
