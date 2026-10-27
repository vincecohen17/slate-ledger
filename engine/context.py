"""Context the box-score models cannot see on their own, applied before 'logic agrees' is decided.

1. Verified news, written by Claude's pick runs to the data repo as context/<YYYY-MM-DD>.json (a list):
     {"league": "NBA", "team": "BOS", "player": "Jayson Tatum", "kind": "status", "value": "Out",
      "source": "NBA injury report", "at": "2026-10-27 10:40 PT"}
   kinds:
     status   value Out | Doubtful | Questionable | Probable | Active   (NBA, WNBA, NFL, NHL skaters)
     minutes  value = expected minutes (a restriction or a bigger role)  (NBA, WNBA)
     goalie   player = confirmed starting goalie                         (NHL)
     starter  player = confirmed starting pitcher                        (MLB; team = full name or abbreviation)
     note     free text shown on the matchup page, no model change
   'team' is the ESPN abbreviation the site shows (BOS, NYR, KC...). Later entries win.
2. Weather for outdoor NFL games (Open-Meteo forecast at kickoff, cached 3 hours): wind, cold and rain
   lower totals and passing; MLB weather comes from MLB's own feed inside the MLB model.
"""
import json
import os
import time
import urllib.request
from datetime import date, datetime, timedelta

# NFL home stadiums: (lat, lon, roof) - roof: open | dome (retractable roofs count as dome; they close in bad weather)
NFL_STADIUMS = {
    "ARI": (33.5276, -112.2626, "dome"), "ATL": (33.7554, -84.4008, "dome"), "BAL": (39.2780, -76.6227, "open"),
    "BUF": (42.7738, -78.7870, "open"), "CAR": (35.2258, -80.8528, "open"), "CHI": (41.8623, -87.6167, "open"),
    "CIN": (39.0955, -84.5161, "open"), "CLE": (41.5061, -81.6995, "open"), "DAL": (32.7473, -97.0945, "dome"),
    "DEN": (39.7439, -105.0201, "open"), "DET": (42.3400, -83.0456, "dome"), "GB": (44.5013, -88.0622, "open"),
    "HOU": (29.6847, -95.4107, "dome"), "IND": (39.7601, -86.1639, "dome"), "JAX": (30.3239, -81.6373, "open"),
    "KC": (39.0489, -94.4839, "open"), "LV": (36.0909, -115.1833, "dome"), "LAC": (33.9535, -118.3392, "dome"),
    "LAR": (33.9535, -118.3392, "dome"), "MIA": (25.9580, -80.2389, "open"), "MIN": (44.9737, -93.2581, "dome"),
    "NE": (42.0909, -71.2643, "open"), "NO": (29.9511, -90.0812, "dome"), "NYG": (40.8135, -74.0745, "open"),
    "NYJ": (40.8135, -74.0745, "open"), "PHI": (39.9008, -75.1675, "open"), "PIT": (40.4468, -80.0158, "open"),
    "SF": (37.4030, -121.9700, "open"), "SEA": (47.5952, -122.3316, "open"), "TB": (27.9759, -82.5033, "open"),
    "TEN": (36.1665, -86.7713, "open"), "WSH": (38.9076, -76.8645, "open"),
}


def load(data_dir, today):
    """verified news entries for today (PT date string)"""
    try:
        out = json.load(open(os.path.join(data_dir, "context", f"{today}.json")))
        return [e for e in out if isinstance(e, dict) and e.get("kind")]
    except Exception:
        return []


def norm(s):
    import refs as R
    return R.norm(s or "")


def for_team(entries, league, abbrs, names=()):
    keys = {norm(a) for a in abbrs if a} | {norm(n) for n in names if n}
    return [e for e in entries if (e.get("league") or "").upper() == league.upper() and norm(e.get("team")) in keys]


def apply_injuries(inj_league, entries, league):
    """merge verified statuses into an ESPN-style injury dict {team name: {abbr, list:[{id, name, status, note}]}}"""
    for e in entries:
        if (e.get("league") or "").upper() != league.upper() or e.get("kind") != "status" or not e.get("player"):
            continue
        abbr = e.get("team")
        team = next((t for t in inj_league.values() if norm(t.get("abbr")) == norm(abbr)), None)
        if team is None:
            team = inj_league.setdefault(f"context:{abbr}", {"abbr": abbr, "list": []})
        status = str(e.get("value") or "").title()
        note = f"Verified: {status} ({e.get('source') or 'news'}, {e.get('at') or 'today'})"
        hit = next((x for x in team["list"] if norm(x.get("name")) == norm(e["player"])), None)
        if hit:
            hit.update(status=status, note=note, verified=True)
        else:
            team["list"].append({"id": f"ctx:{norm(e['player'])}", "name": e["player"], "status": status, "note": note, "verified": True})
    return inj_league


def notes(entries, league, abbrs, names=()):
    """matchup-page factors for the verified entries of one game"""
    out = []
    for e in for_team(entries, league, abbrs, names):
        who = e.get("player") or e.get("team")
        val = e.get("value")
        text = {"status": f"{who}: {val}", "minutes": f"{who}: about {val} minutes", "goalie": f"{who} confirmed in goal",
                "starter": f"{who} confirmed starting"}.get(e["kind"], f"{who}: {val}" if val else str(who))
        tone = "neg" if str(val).lower() in ("out", "doubtful") else "info"
        out.append({"team": e.get("team"), "tone": tone, "text": f"Verified: {text} ({e.get('source') or 'news'}, {e.get('at') or 'today'})"})
    return out


# ---------------------------------------------------------------------------
# NFL weather

def nfl_weather(data_dir, game_docs):
    """{game id: {temp, wind, gust, pop, precip, roof}} for NFL games in the next 8 days"""
    fn = os.path.join(data_dir, "stats", "weather", "nfl.json")
    try:
        cache = json.load(open(fn))
    except Exception:
        cache = {}
    now = time.time()
    out = {}
    for g in game_docs:
        if g.get("sport") != "americanfootball_nfl":
            continue
        home = (g.get("homeRef") or {}).get("abbr")
        st = NFL_STADIUMS.get(home)
        if not st:
            continue
        if st[2] == "dome":
            out[g["id"]] = {"roof": "dome"}
            continue
        c = cache.get(g["id"])
        if c and now - c.get("_at", 0) < 3 * 3600:
            out[g["id"]] = c
            continue
        try:
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={st[0]}&longitude={st[1]}&hourly=temperature_2m,wind_speed_10m,"
                   f"wind_gusts_10m,precipitation_probability,precipitation&temperature_unit=fahrenheit&wind_speed_unit=mph"
                   f"&timezone=UTC&forecast_days=8")
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "slate-ledger"}), timeout=20) as r:
                h = json.loads(r.read())["hourly"]
            t0 = datetime.fromisoformat(g["commence"].replace("Z", "+00:00")).strftime("%Y-%m-%dT%H:00")
            if t0 not in h["time"]:
                continue
            i = h["time"].index(t0)
            # average kickoff through the third quarter
            sl = slice(i, min(i + 3, len(h["time"])))
            avg = lambda k: sum(h[k][sl]) / len(h[k][sl]) if h[k][sl] and None not in h[k][sl] else None
            c = {"roof": "open", "temp": avg("temperature_2m"), "wind": avg("wind_speed_10m"), "gust": avg("wind_gusts_10m"),
                 "pop": avg("precipitation_probability"), "precip": avg("precipitation"), "_at": now}
            cache[g["id"]] = c
            out[g["id"]] = c
        except Exception:
            continue
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(cache, open(fn, "w"), separators=(",", ":"))
    return out


def nfl_weather_effect(w):
    """(total points change, passing yards factor, receiving yards factor, rushing yards factor, note)"""
    if not w or w.get("roof") != "open" or w.get("wind") is None:
        return 0.0, 1.0, 1.0, 1.0, None
    wind, temp = w["wind"], w.get("temp")
    over = max(0.0, wind - 10)
    total = -min(5.0, 0.25 * over)
    pf, rf, ruf = 1 - min(0.25, 0.012 * over), 1 - min(0.2, 0.009 * over), 1 + min(0.06, 0.004 * over)
    wet = (w.get("pop") or 0) >= 60 and (w.get("precip") or 0) >= 1.0
    if wet:
        total -= 1.0; pf *= 0.96; rf *= 0.97
    if temp is not None and temp < 20:
        total -= 1.0; pf *= 0.98
    note = f"{temp:.0f}°F, wind {wind:.0f} mph" + (f" (gusts {w['gust']:.0f})" if w.get("gust") else "") + (", rain likely" if wet else "")
    return total, pf, rf, ruf, note
