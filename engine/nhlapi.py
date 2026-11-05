"""NHL's own public API (api-web.nhle.com, no key) for what ESPN's box score lacks: power-play points.

A power-play point is a goal or an assist on a goal scored while the scoring team has more skaters on the ice because
of a penalty. The play-by-play gives every goal's situationCode (four digits: away goalie in net 1/0, away skaters,
home skaters, home goalie in net 1/0), its scorer and up to two assisters. An extra attacker for a pulled goalie is not
a power play, so a side whose goalie is out counts one skater fewer. Shootout goals don't count.
"""
import json
import urllib.request
from datetime import datetime, timedelta

import refs as R

BASE = "https://api-web.nhle.com/v1"
# ESPN abbreviation -> NHL abbreviation where they differ
ALIAS = {"TB": "TBL", "NJ": "NJD", "SJ": "SJS", "LA": "LAK", "WAS": "WSH", "MON": "MTL", "CLB": "CBJ", "NAS": "NSH", "UTAH": "UTA"}
_score_cache, _pbp_cache = {}, {}


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "slate-ledger"}), timeout=20) as r:
        return json.loads(r.read())


def _ab(a):
    a = (a or "").upper()
    return ALIAS.get(a, a)


def game_id(start_iso, home_abbr, away_abbr):
    """the NHL game id for a game starting at start_iso (UTC) between these teams (ESPN or NHL abbreviations)"""
    try:
        t = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    want = {_ab(home_abbr), _ab(away_abbr)}
    for d in sorted({(t - timedelta(hours=h)).strftime("%Y-%m-%d") for h in (0, 5, 8)}):     # UTC, Eastern and Pacific dates
        if d not in _score_cache:
            try:
                _score_cache[d] = _get(f"{BASE}/score/{d}").get("games") or []
            except Exception:
                _score_cache[d] = []
        for g in _score_cache[d]:
            if {_ab((g.get("homeTeam") or {}).get("abbrev")), _ab((g.get("awayTeam") or {}).get("abbrev"))} == want:
                return g.get("id")
    return None


def _skaters(code, side):
    """effective skaters for 'away'/'home' from a situationCode (a pulled goalie's extra attacker doesn't count)"""
    ag, as_, hs, hg = (int(c) for c in code)
    return (as_ - (1 - ag)) if side == "away" else (hs - (1 - hg))


def pp_points(nhl_id, fresh=False):
    """{normalized player name: power-play points} for the game, plus '_n' = power-play goals found; None if unavailable"""
    if not nhl_id:
        return None
    if fresh or nhl_id not in _pbp_cache:
        try:
            _pbp_cache[nhl_id] = _get(f"{BASE}/gamecenter/{nhl_id}/play-by-play")
        except Exception:
            return None
    pbp = _pbp_cache[nhl_id]
    names = {}
    for p in pbp.get("rosterSpots") or []:
        nm = f"{(p.get('firstName') or {}).get('default', '')} {(p.get('lastName') or {}).get('default', '')}".strip()
        names[p.get("playerId")] = nm
    home_id = (pbp.get("homeTeam") or {}).get("id")
    out, n = {}, 0
    for pl in pbp.get("plays") or []:
        if pl.get("typeDescKey") != "goal":
            continue
        if ((pl.get("periodDescriptor") or {}).get("periodType") or "") == "SO":
            continue
        code, det = str(pl.get("situationCode") or ""), pl.get("details") or {}
        if len(code) != 4 or not code.isdigit():
            continue
        side = "home" if det.get("eventOwnerTeamId") == home_id else "away"
        other = "away" if side == "home" else "home"
        if _skaters(code, side) <= _skaters(code, other):
            continue
        n += 1
        for k in ("scoringPlayerId", "assist1PlayerId", "assist2PlayerId"):
            pid = det.get(k)
            if pid and names.get(pid):
                key = R.norm(names[pid])
                out[key] = out.get(key, 0) + 1
    out["_n"] = n
    return out


def pp_from_espn_summary(s):
    """power-play points for the game behind an ESPN summary (its header gives the start and both teams)"""
    try:
        comp = s["header"]["competitions"][0]
        side = {c["homeAway"]: c["team"]["abbreviation"] for c in comp["competitors"]}
        gid = game_id(comp.get("date"), side["home"], side["away"])
    except Exception:
        return None
    return pp_points(gid, fresh=True)
