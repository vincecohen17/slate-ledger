"""Team logos/colors and player headshots from ESPN's public site API.

Cached in the private data repo (refs/<league>.json) and refreshed once a day.
Everything here is best effort: if ESPN is unreachable the board still builds,
it just shows team monograms instead of logos.
"""
import json
import os
import re
import time
import unicodedata
import urllib.request

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
# odds sport key -> (espn path, league logo slug)
LEAGUES = {
    "basketball_nba": ("basketball/nba", "nba"),
    "icehockey_nhl": ("hockey/nhl", "nhl"),
    "americanfootball_nfl": ("football/nfl", "nfl"),
    "americanfootball_ncaaf": ("football/college-football", "ncaa"),
    "baseball_mlb": ("baseball/mlb", "mlb"),
    "basketball_ncaab": ("basketball/mens-college-basketball", "ncaa"),
    "soccer_epl": ("soccer/eng.1", "eng.1"),
    "soccer_uefa_champs_league": ("soccer/uefa.champions", "uefa.champions"),
    "soccer_usa_mls": ("soccer/usa.1", "usa.1"),
    "basketball_wnba": ("basketball/wnba", "wnba"),
    "soccer_uefa_europa_league": ("soccer/uefa.europa", "uefa.europa"),
    "soccer_spain_la_liga": ("soccer/esp.1", "esp.1"),
    "soccer_germany_bundesliga": ("soccer/ger.1", "ger.1"),
    "soccer_italy_serie_a": ("soccer/ita.1", "ita.1"),
    "soccer_france_ligue_one": ("soccer/fra.1", "fra.1"),
    "soccer_mexico_ligamx": ("soccer/mex.1", "mex.1"),
}
ROSTER_SPORTS = {"basketball_nba", "basketball_wnba", "icehockey_nhl", "americanfootball_nfl", "baseball_mlb"}
SOCCER_LOGO_IDS = {"eng.1": 23, "uefa.champions": 2, "usa.1": 19, "uefa.europa": 2310, "esp.1": 15, "ger.1": 10, "ita.1": 12,
                   "fra.1": 9, "mex.1": 22}
MAX_AGE = 20 * 3600


def league_logo(sport):
    slug = LEAGUES.get(sport, (None, None))[1]
    if not slug:
        return None
    if "." in slug:
        return f"https://a.espncdn.com/i/leaguelogos/soccer/500/{SOCCER_LOGO_IDS[slug]}.png"
    return f"https://a.espncdn.com/i/teamlogos/leagues/500/{slug}.png"


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "slate-ledger"}), timeout=20) as r:
        return json.loads(r.read().decode())


def _fetch_league(sport):
    path = LEAGUES[sport][0]
    data = _get(f"{ESPN}/{path}/teams?limit=1000")
    teams = {}
    for t in data["sports"][0]["leagues"][0]["teams"]:
        t = t["team"]
        logo = (t.get("logos") or [{}])[0].get("href")
        rec = {"id": t["id"], "abbr": t.get("abbreviation"), "name": t.get("displayName"),
               "short": t.get("shortDisplayName") or t.get("name"),
               "color": "#" + t["color"] if t.get("color") else None,
               "alt": "#" + t["alternateColor"] if t.get("alternateColor") else None, "logo": logo}
        for k in {t.get("displayName"), f"{t.get('location', '')} {t.get('name', '')}".strip()}:
            if k:
                teams[norm(k)] = rec
    players = {}
    if sport in ROSTER_SPORTS:
        seen = set()
        for rec in teams.values():
            if rec["id"] in seen:
                continue
            seen.add(rec["id"])
            try:
                ro = _get(f"{ESPN}/{path}/teams/{rec['id']}/roster")
            except Exception:
                continue
            groups = ro.get("athletes", [])
            items = groups if groups and "items" not in groups[0] else [a for g in groups for a in g.get("items", [])]
            for a in items:
                hs = (a.get("headshot") or {}).get("href")
                players[norm(a.get("fullName") or a.get("displayName"))] = {
                    "id": a.get("id"), "headshot": hs, "team": rec["abbr"],
                    "pos": (a.get("position") or {}).get("abbreviation")}
            time.sleep(0.05)
    return {"fetched": time.time(), "teams": teams, "players": players}


def load(sport, data_dir):
    """Return cached refs for a league, refreshing from ESPN when stale."""
    if sport not in LEAGUES:
        return {"teams": {}, "players": {}}
    d = os.path.join(data_dir, "refs")
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, f"{sport}.json")
    cached = None
    try:
        cached = json.load(open(fn))
    except Exception:
        pass
    if cached and time.time() - cached.get("fetched", 0) < MAX_AGE:
        return cached
    try:
        fresh = _fetch_league(sport)
        if fresh["teams"]:
            json.dump(fresh, open(fn, "w"), separators=(",", ":"))
            return fresh
    except Exception:
        pass
    return cached or {"teams": {}, "players": {}}


def decorate_game(g, refs):
    g["leagueLogo"] = league_logo(g["sport"])
    for side in ("home", "away"):
        t = refs["teams"].get(norm(g[side]))
        if t:
            g[side + "Ref"] = {k: t[k] for k in ("abbr", "short", "color", "alt", "logo")}


def decorate_props(doc, refs):
    for r in doc["rows"]:
        p = refs["players"].get(norm(r["player"]))
        if p:
            r["headshot"], r["team"], r["pos"] = p.get("headshot"), p.get("team"), p.get("pos")
