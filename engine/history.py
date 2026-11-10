"""History estimate for props no sportsbook prices (Nov 10): faceoffs won, odd pick'em-only stats.

A prop row with no fair (no two books quote it; only the pick'em apps list it) gets histFair {Over, Under} from the
player's own recent games in our daily box scores (stats/<league>/<season>/<date>.json, read with tracker.stat): the mean
of his last LAST games where the stat was recorded (at least MIN_N), shrunk a little toward his season mean, at the row's
number with build.p_over (his own dispersion when there are enough games). It is NOT a fair: rows keep fair null, so it
never makes an edge, a pick or a CLV. The site's projection uses it for a leg nothing else prices, labelled as an
estimate from his last games."""
import glob
import json
import os
from datetime import timedelta

import build as B
import tracker as T

LEAGUE_DIR = {"icehockey_nhl": "nhl", "basketball_nba": "nba", "basketball_wnba": "wnba", "basketball_ncaab": "ncaab",
              "americanfootball_nfl": "nfl", "americanfootball_ncaaf": "cfb"}
LAST, MIN_N, LOOKBACK_DAYS = 15, 5, 120


def _norm(s):
    return " ".join(str(s or "").lower().replace(".", "").replace("'", "").replace("-", " ").split())


def _logs(data_dir, sport, now):
    """player name -> [(date, box dict)] oldest first, from the last LOOKBACK_DAYS of daily box scores"""
    lg = LEAGUE_DIR.get(sport)
    if not lg:
        return {}
    since = (now - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    out = {}
    for fn in sorted(glob.glob(os.path.join(data_dir, "stats", lg, "*", "*.json"))):
        day = os.path.basename(fn)[:10]
        if day < since:
            continue
        try:
            d = json.load(open(fn))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue            # a league whose daily file isn't a box score (NFL stats come from nflverse in another shape)
        for g in d.get("games") or []:
            if not isinstance(g, dict) or not isinstance(g.get("players"), list):
                continue
            for p in g["players"]:
                if not isinstance(p, dict) or p.get("dnp"):
                    continue
                out.setdefault(_norm(p.get("name")), []).append((day, p))
    return out


def estimate(sport, market, player, point, logs):
    """{p_over, mean, n} from the player's last games, or None"""
    rows = logs.get(_norm(player)) or []
    vals = []
    for _, p in rows:
        try:
            v = T.stat(sport, market, p)
        except Exception:
            v = None
        if v is not None:
            vals.append(float(v))
    if len(vals) < MIN_N:
        return None
    last = vals[-LAST:]
    mean_l, mean_s = sum(last) / len(last), sum(vals) / len(vals)
    w = len(last) / (len(last) + 5)                 # a short sample leans on the season
    mean = w * mean_l + (1 - w) * mean_s
    if mean <= 0:
        return None
    try:
        if len(last) >= 8 and not B._is_normal(market):
            var = sum((v - mean_l) ** 2 for v in last) / (len(last) - 1)
            k = max(1.0, min(3.0, var / mean_l)) if mean_l > 0 else None
            saved = B.DISPERSION.setdefault(sport, {}).get(market)
            B.DISPERSION[sport][market] = k or saved or 1.3
            try:
                po = B.p_over(market, mean, point, sport)
            finally:
                if saved is None:
                    B.DISPERSION[sport].pop(market, None)
                else:
                    B.DISPERSION[sport][market] = saved
        else:
            po = B.p_over(market, mean, point, sport)
    except Exception:
        return None
    if po is None:
        return None
    return {"p": min(0.99, max(0.01, po)), "mean": round(mean, 2), "n": len(last)}


def stamp(data_dir, prop_docs, now, log=print):
    """histFair on every prop row with no fair; returns how many rows got one"""
    cache, n = {}, 0
    for doc in prop_docs:
        sport = doc.get("sport")
        if sport not in LEAGUE_DIR:
            continue
        for r in doc.get("rows") or []:
            if r.get("fair") or r.get("point") is None or not r.get("player"):
                continue
            if sport not in cache:
                cache[sport] = _logs(data_dir, sport, now)
            e = estimate(sport, doc["market"], r["player"], r["point"], cache[sport])
            if e:
                r["histFair"] = {"Over": round(e["p"], 4), "Under": round(1 - e["p"], 4)}
                r["histMean"], r["histN"] = e["mean"], e["n"]
                n += 1
    if n:
        log(f"history: {n} unpriced prop rows estimated from players' last games")
    return n
