"""Matchup layer: runs the league models for every game on the board and
  * writes a matchup doc per game (team comparison, injuries, players, key factors, model lines),
  * stamps our model probability onto every game line and player prop (line["model"]),
  * lets the edge list say whether sports logic agrees with the price.
"""
import math

import refs as R
import stats as ST
import build as B
import model_nba
import model_nhl

ALIAS = {"UTAH": "UTA", "GSW": "GS", "NOP": "NO", "SAS": "SA", "NYK": "NY", "WAS": "WSH", "UTH": "UTA"}
SEASON = {"nba": ("2026-27", "2025-26"), "nhl": ("2026-27", "2025-26")}


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _abbr(ref, teams):
    a = (ref or {}).get("abbr")
    if a in teams:
        return a
    return ALIAS.get(a, a)


def _injuries(inj, abbr, name):
    for tname, t in (inj or {}).items():
        if t.get("abbr") == abbr or R.norm(tname) == R.norm(name):
            return [x for x in t["list"] if (x.get("status") or "").lower() not in ("active", "")]
    return []


def _fmt(v, d=1):
    return None if v is None else round(v, d)


# ---------------------------------------------------------------------------

def build_all(game_docs, prop_docs, data_dir, today):
    out = {}
    models = {}
    inj = {}
    for lg, sport in (("nba", "basketball_nba"), ("nhl", "icehockey_nhl")):
        if not any(g["sport"] == sport for g in game_docs):
            continue
        try:
            cur, pri = SEASON[lg]
            inj[lg] = (ST.load_json(data_dir, f"stats/{lg}/injuries.json") or {}).get("teams", {})
            cg, pg = ST.load_games(lg, cur, data_dir), ST.load_games(lg, pri, data_dir)
            models[sport] = model_nba.NBA(cg, pg, inj[lg], today) if lg == "nba" else model_nhl.NHL(data_dir, cg, pg, inj[lg])
        except Exception as e:  # a model failure never blocks the board
            print(f"model {lg} failed: {type(e).__name__}: {str(e)[:120]}")
    props_by_game = {}
    for p in prop_docs:
        props_by_game.setdefault(p["id"], []).append(p)
    for g in game_docs:
        m = models.get(g["sport"])
        if not m:
            continue
        try:
            refs = R.load(g["sport"], data_dir)
            doc = (_nba if g["sport"] == "basketball_nba" else _nhl)(g, m, props_by_game.get(g["id"], []), inj, refs)
            if doc:
                out[g["id"]] = doc
        except Exception as e:
            print(f"matchup {g['league']} failed: {type(e).__name__}: {str(e)[:120]}")
    shifts = calibrate(prop_docs)
    for gid, doc in out.items():
        doc["propEdges"] = _disagreements(props_by_game.get(gid, []))
        doc["calibration"] = shifts
    build_all.shifts = shifts
    return out


def _stamp_lines(g, prob_fn):
    """prob_fn(line) -> {outcome: model prob}"""
    res = {}
    for l in g["lines"]:
        try:
            p = prob_fn(l)
        except Exception:
            p = None
        if p:
            l["model"] = {o: round(v, 4) for o, v in p.items()}
            if l.get("isMain"):
                res[l["market"]] = {"point": l.get("point"), "model": l["model"], "market": l.get("fair")}
    return res


def _match_player(name, players):
    n = R.norm(name)
    for p in players:
        if R.norm(p["name"]) == n:
            return p
    last = n[-6:]
    cands = [p for p in players if R.norm(p["name"]).endswith(last)]
    return cands[0] if len(cands) == 1 else None


def _stamp_props(pdocs, sides, markets, sport, goalie_fn=None):
    allp = sides["home"]["players"] + sides["away"]["players"]
    for d in pdocs:
        keys = markets.get(d["market"])
        if not keys:
            continue
        for r in d["rows"]:
            if d["market"] == "player_total_saves" and goalie_fn:
                mean = goalie_fn(r["player"])
                if mean is None:
                    continue
            else:
                p = _match_player(r["player"], allp)
                if not p or p.get("out"):
                    continue
                mean = sum(p["proj"].get(k, 0) for k in keys)
            if r.get("point") is None:
                if d["market"] == "player_goal_scorer_anytime":
                    py = 1 - math.exp(-mean)
                    r["model"] = {"mean": round(mean, 2), "Yes": round(py, 4)}
                continue
            po = B.p_over(d["market"], max(mean, 0.01), r["point"], sport)
            r["model"] = {"mean": round(mean, 2), "raw": round(mean, 2), "Over": round(po, 4), "Under": round(1 - po, 4)}


def _disagreements(pdocs):
    out = []
    for d in pdocs:
        for r in d["rows"]:
            f, m = r.get("fair") or {}, r.get("model") or {}
            if r.get("isMain") and "Over" in f and "Over" in m:
                out.append({"player": r["player"], "market": d["market"], "point": r["point"], "mean": m["mean"],
                            "model": m["Over"], "fair": f["Over"], "diff": round(m["Over"] - f["Over"], 3), "headshot": r.get("headshot")})
    out.sort(key=lambda x: -abs(x["diff"]))
    return out[:12]


def calibrate(prop_docs, min_n=12, strength=0.8):
    """Line up each (sport, market) model level with the market across the slate.
    shift = median(model mean - market-implied mean) on main lines; the model keeps its
    player-by-player opinions, only the overall level moves. Returns the shifts applied."""
    import statistics
    gaps = {}
    for d in prop_docs:
        for r in d["rows"]:
            if r.get("isMain") and r.get("mean") is not None and (r.get("model") or {}).get("raw") is not None:
                gaps.setdefault((d["sport"], d["market"]), []).append(r["model"]["raw"] - r["mean"])
    shifts = {k: statistics.median(v) * strength for k, v in gaps.items() if len(v) >= min_n}
    for d in prop_docs:
        sh = shifts.get((d["sport"], d["market"]))
        if not sh:
            continue
        for r in d["rows"]:
            m = r.get("model")
            if not m or m.get("raw") is None or r.get("point") is None:
                continue
            mean = max(0.01, m["raw"] - sh)
            po = B.p_over(d["market"], mean, r["point"], d["sport"])
            m.update(mean=round(mean, 2), Over=round(po, 4), Under=round(1 - po, 4), shift=round(sh, 2))
    return {f"{k[0].split('_')[-1].upper()} {k[1]}": round(v, 2) for k, v in shifts.items()}


def _player_cards(players, refs, keys, n=9, minutes_key="projMin"):
    out = []
    for p in players[:n + 4]:
        if len(out) >= n:
            break
        if not p.get(minutes_key) and not p.get("out"):
            continue
        ref = refs.get("players", {}).get(R.norm(p["name"])) or {}
        out.append({"name": p["name"], "pos": p.get("pos"), "status": p.get("status"), "note": p.get("note"),
                    "out": p.get("out"), "min": p.get(minutes_key), "headshot": ref.get("headshot"),
                    "proj": {k: p["proj"].get(k) for k in keys}, "last10": p.get("last10")})
    return out


# ---------------------------------------------------------------------------
# NBA

def _nba(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a, g["commence"])
    if not pj:
        return None
    pH = 1 - _phi(-pj["margin"] / model_nba.SD_MARGIN)

    def probs(l):
        if l["market"] == "h2h":
            return {g["home"]: pH, g["away"]: 1 - pH}
        if l["market"] == "spreads":
            ph = m.p_cover(pj["margin"], l["point"])
            return {g["home"]: ph, g["away"]: 1 - ph}
        if l["market"] == "totals":
            po = m.p_over(pj["total"], l["point"])
            return {"Over": po, "Under": 1 - po}
    lines = _stamp_lines(g, probs)
    _stamp_props(pdocs, pj["sides"], model_nba.MARKETS, g["sport"])
    th, ta = m.teams[h], m.teams[a]

    def team(t, side, name):
        return {"abbr": t["abbr"], "record": f"{t['w']}-{t['l']}", "games": t["games"], "continuity": t.get("continuity"),
                "ortg": _fmt(t["ortg"]), "drtg": _fmt(t["drtg"]), "net": _fmt(t["net"]), "pace": _fmt(t["pace"]),
                "rank": t.get("rank", {}), "last10": t["last10"], "rest": pj["rest"][side],
                "injuries": _injuries(inj.get("nba"), t["abbr"], name), "freedMin": pj["sides"][side]["freedMin"],
                "injuryPts": pj["sides"][side].get("injuryPts"), "pts": pj[side]["pts"]}
    compare = [
        {"k": "Net rating", "h": th["net"], "a": ta["net"], "hr": th["rank"].get("net"), "ar": ta["rank"].get("net"), "hi": True, "d": 1},
        {"k": "Offense (pts/100)", "h": th["ortg"], "a": ta["ortg"], "hr": th["rank"].get("ortg"), "ar": ta["rank"].get("ortg"), "hi": True, "d": 1},
        {"k": "Defense (pts/100 allowed)", "h": th["drtg"], "a": ta["drtg"], "hr": th["rank"].get("drtg"), "ar": ta["rank"].get("drtg"), "hi": False, "d": 1},
        {"k": "Pace", "h": th["pace"], "a": ta["pace"], "hr": th["rank"].get("pace"), "ar": ta["rank"].get("pace"), "hi": None, "d": 1},
        {"k": "Effective FG%", "h": th["efg"], "a": ta["efg"], "hr": th["rank"].get("efg"), "ar": ta["rank"].get("efg"), "hi": True, "pct": True},
        {"k": "Opp effective FG%", "h": th["o_efg"], "a": ta["o_efg"], "hr": th["rank"].get("o_efg"), "ar": ta["rank"].get("o_efg"), "hi": False, "pct": True},
        {"k": "Turnover rate", "h": th["tovp"], "a": ta["tovp"], "hr": th["rank"].get("tovp"), "ar": ta["rank"].get("tovp"), "hi": False, "pct": True},
    ]
    factors = []
    for side, t, nm in (("home", th, g["home"]), ("away", ta, g["away"])):
        r = pj["rest"][side]
        if r == 1:
            factors.append({"side": side, "tone": "neg", "text": f"{t['abbr']} on the second night of a back-to-back"})
        elif r and r >= 3:
            factors.append({"side": side, "tone": "pos", "text": f"{t['abbr']} rested ({r} days off)"})
        outs = [p for p in pj["sides"][side]["players"] if p["out"] and p.get("baseMin", 0) >= 15]
        if outs:
            factors.append({"side": side, "tone": "neg", "text": f"{t['abbr']} without {', '.join(p['name'] for p in outs[:3])} "
                            f"({pj['sides'][side]['freedMin']:.0f} min to redistribute)"})
        if (t.get("continuity") or 1) < 0.6:
            factors.append({"side": side, "tone": "info", "text": f"{t['abbr']} returned {t['continuity']:.0%} of last season's minutes; this season's games carry more weight"})
    if th["pace"] and ta["pace"]:
        if th["rank"].get("pace", 99) <= 8 and ta["rank"].get("pace", 99) <= 8:
            factors.append({"side": None, "tone": "info", "text": "Two top-8 pace teams: more possessions, totals and counting props up"})
        if th["rank"].get("pace", 0) >= 23 and ta["rank"].get("pace", 0) >= 23:
            factors.append({"side": None, "tone": "info", "text": "Two bottom-8 pace teams: fewer possessions"})
    sp = lines.get("spreads")
    if sp and sp.get("point") is not None:
        mkt_line = sp["point"]
        factors.append({"side": None, "tone": "info", "text": f"Model line {g['homeRef']['abbr'] if g.get('homeRef') else 'home'} {-pj['margin']:+.1f}, "
                        f"market {mkt_line:+g}; model total {pj['total']:.1f}"})
    keys = ["pts", "reb", "ast", "fg3"]
    return {"id": g["id"], "league": "NBA", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"margin": pj["margin"], "total": pj["total"], "pHome": round(pH, 4), "pace": pj["pace"], "lines": lines},
            "teams": {"home": team(th, "home", g["home"]), "away": team(ta, "away", g["away"])},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": keys,
            "statLabels": [model_nba.LABEL[k] for k in keys],
            "players": {"home": _player_cards(pj["sides"]["home"]["players"], refs, keys),
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys)}}


# ---------------------------------------------------------------------------
# NHL

def _nhl(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a)
    if not pj:
        return None

    def probs(l):
        if l["market"] == "h2h":
            return {g["home"]: pj["pHomeWin"], g["away"]: 1 - pj["pHomeWin"]}
        if l["market"] == "spreads":
            ph = m.p_home_cover(l["point"])
            return {g["home"]: ph, g["away"]: 1 - ph}
        if l["market"] == "totals":
            po = m.p_over(l["point"])
            return {"Over": po, "Under": 1 - po}
    lines = _stamp_lines(g, probs)

    def goalie_mean(name):
        for side in ("home", "away"):
            gl = pj["sides"][side]["goalie"]
            if gl.get("name") and R.norm(gl["name"]) == R.norm(name):
                return gl["proj"]["saves"]
        return None
    _stamp_props(pdocs, pj["sides"], model_nhl.MARKETS, g["sport"], goalie_mean)
    th, ta = m.teams[h], m.teams[a]

    def team(t, side, name):
        gl = pj["sides"][side]["goalie"]
        return {"abbr": t["abbr"], "gp": t["gp"], "xgfPct": _fmt(t.get("xgfPct"), 3), "xgf60": _fmt(t.get("xgf60"), 2),
                "xga60": _fmt(t.get("xga60"), 2), "gf60": _fmt(t.get("gf60"), 2), "ga60": _fmt(t.get("ga60"), 2),
                "pp": _fmt(t.get("ppXgf60"), 2), "pk": _fmt(t.get("pkXga60"), 2), "rank": t.get("rank", {}),
                "goalie": gl, "goals": pj[side]["goals"], "injuries": _injuries(inj.get("nhl"), t["abbr"], name)}
    compare = [
        {"k": "xGoals share", "h": th.get("xgfPct"), "a": ta.get("xgfPct"), "hr": th["rank"].get("xgfPct"), "ar": ta["rank"].get("xgfPct"), "hi": True, "pct": True},
        {"k": "xGoals for /60", "h": th.get("xgf60"), "a": ta.get("xgf60"), "hr": th["rank"].get("off"), "ar": ta["rank"].get("off"), "hi": True, "d": 2},
        {"k": "xGoals against /60", "h": th.get("xga60"), "a": ta.get("xga60"), "hr": th["rank"].get("def"), "ar": ta["rank"].get("def"), "hi": False, "d": 2},
        {"k": "Power play xG /60", "h": th.get("ppXgf60"), "a": ta.get("ppXgf60"), "hr": th["rank"].get("ppXgf60"), "ar": ta["rank"].get("ppXgf60"), "hi": True, "d": 2},
        {"k": "Penalty kill xGA /60", "h": th.get("pkXga60"), "a": ta.get("pkXga60"), "hr": th["rank"].get("pkXga60"), "ar": ta["rank"].get("pkXga60"), "hi": False, "d": 2},
        {"k": "Goalie GSAx /60", "h": pj["sides"]["home"]["goalie"]["gsax60"], "a": pj["sides"]["away"]["goalie"]["gsax60"], "hi": True, "d": 2},
    ]
    factors = []
    for side, t in (("home", th), ("away", ta)):
        gl = pj["sides"][side]["goalie"]
        if gl.get("name"):
            tone = "pos" if gl["gsax60"] > 0.1 else "neg" if gl["gsax60"] < -0.1 else "info"
            factors.append({"side": side, "tone": tone, "text": f"{t['abbr']} likely in goal: {gl['name']} ({gl['gsax60']:+.2f} goals saved above expected per 60; "
                            f"{len([s for s in gl['starts'] if s == gl['name']])} of last {len(gl['starts'])} starts) — confirm"})
    for side, t, o in (("home", th, ta), ("away", ta, th)):
        pr, kr = t["rank"].get("ppXgf60"), o["rank"].get("pkXga60")
        if pr and kr and pr <= 8 and kr >= 25:
            factors.append({"side": side, "tone": "pos", "text": f"{t['abbr']} power play ({pr}th) vs {o['abbr']} penalty kill ({kr}th)"})
    tl = lines.get("totals")
    factors.append({"side": None, "tone": "info", "text": f"Model: {g['homeRef']['abbr'] if g.get('homeRef') else 'home'} win {pj['pHomeWin']:.0%}, "
                    f"expected goals {pj['home']['goals']:.2f}-{pj['away']['goals']:.2f}"
                    + (f", P(over {tl['point']}) {tl['model'].get('Over', 0):.0%}" if tl else "")})
    keys = ["sog", "pts", "g"]
    return {"id": g["id"], "league": "NHL", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"pHome": round(pj["pHomeWin"], 4), "total": pj["total"], "margin": pj["margin"], "lines": lines},
            "teams": {"home": team(th, "home", g["home"]), "away": team(ta, "away", g["away"])},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": keys,
            "statLabels": [model_nhl.LABEL[k] for k in keys],
            "players": {"home": _player_cards(pj["sides"]["home"]["players"], refs, keys, 9, "projToi"),
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys, 9, "projToi")}}
