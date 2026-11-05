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


# The Odds API and ESPN name teams differently ("Los Angeles Clippers" / "LA Clippers", "Kansas St" / "Kansas State",
# "Slavia Praha" / "Slavia Prague"). An unmatched team had no abbreviation on its snapshot, so its finals never got our
# game id and its bets, picks, edges and model rows never graded (Nov 5: 1 in 5 college basketball teams, the Clippers).
_WORD = {"st": "state", "univ": "university", "u": "university", "csu": "cal state", "la": "los angeles", "chi": "chicago",
         "md": "maryland", "liu": "long island university", "gw": "george washington", "umass": "massachusetts",
         "app": "appalachian", "praha": "prague", "koln": "cologne", "hamburger": "hamburg", "olympiakos": "olympiacos",
         "omonoia": "omonia", "miss": "mississippi", "saint": "state", "mt": "mount", "n": "northern", "and": "", "&": "", "the": ""}
_CLUB = {"fc", "cf", "sc", "ac", "afc", "bc", "jk", "tc", "pfc", "rc", "fk", "sk", "cd", "sv", "fsv", "club", "de", "real",
         "1", "04", "05", "07", "calcio"}


_NAME = {"losangelesfc": "LAFC", "newyorkredbulls": "Red Bull New York", "sportinglisbon": "Sporting CP"}
_LETTER = str.maketrans({"ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "ß": "ss", "æ": "ae"})


def _toks(name):
    name = _NAME.get(norm(name), name)
    s = unicodedata.normalize("NFKD", (name or "").translate(_LETTER)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"['\u2019.]", "", s)
    out = []
    for w in re.split(r"[^a-z0-9&]+", s):
        w = _WORD.get(w, w)
        out += [x for x in w.split() if x and x not in _CLUB]
    return out


_idx = {}


def find(teams, name):
    """ESPN's team record for a sportsbook team name: exact, then canonical words (St = State, LA = Los Angeles, club
    tokens dropped), then a unique word-subset match on full names; None when unsure"""
    t = teams.get(norm(name))
    if t or not name:
        return t
    held = _idx.get(id(teams))
    cache = held[1] if held and held[0] is teams else None
    if cache is None:
        canon, full = {}, []
        for rec in {id(v): v for v in teams.values()}.values():
            keys = {rec.get("name"), rec.get("short")}
            for k in keys:
                if k:
                    canon.setdefault(" ".join(_toks(k)), []).append(rec)
            if rec.get("name"):
                full.append((set(_toks(rec["name"])), rec))
        cache = {"canon": canon, "full": full}
        _idx[id(teams)] = (teams, cache)
    tk = _toks(name)
    hit = cache["canon"].get(" ".join(tk)) or []
    if len({r["id"] for r in hit}) == 1:
        return hit[0]
    ts = set(tk)
    if not ts:
        return None
    cands = {r["id"]: r for fs, r in cache["full"] if fs and (fs <= ts or ts <= fs) and max(len(w) for w in fs & ts or {""}) >= 4}
    return next(iter(cands.values())) if len(cands) == 1 else None


_abbr_cache = {}


def abbr_of(sport, name, data_dir):
    """ESPN abbreviation for a team name from the cached refs file (no network)"""
    if sport not in _abbr_cache:
        try:
            _abbr_cache[sport] = json.load(open(os.path.join(data_dir, "refs", f"{sport}.json")))["teams"]
        except Exception:
            _abbr_cache[sport] = {}
    t = find(_abbr_cache[sport], name)
    return t.get("abbr") if t else None


def decorate_game(g, refs):
    g["leagueLogo"] = league_logo(g["sport"])
    for side in ("home", "away"):
        t = find(refs["teams"], g[side])
        if t:
            g[side + "Ref"] = {k: t[k] for k in ("abbr", "short", "color", "alt", "logo")}


def decorate_props(doc, refs):
    for r in doc["rows"]:
        p = refs["players"].get(norm(r["player"]))
        if p:
            r["headshot"], r["team"], r["pos"] = p.get("headshot"), p.get("team"), p.get("pos")
