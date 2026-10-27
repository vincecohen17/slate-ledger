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
import model_nfl
import model_cfb
import model_extra as MX

ALIAS = {"UTAH": "UTA", "GSW": "GS", "NOP": "NO", "SAS": "SA", "NYK": "NY", "WAS": "WSH", "UTH": "UTA"}
SEASON = {"nba": ("2026-27", "2025-26"), "nhl": ("2026-27", "2025-26"), "nfl": ("2026", "2025"), "cfb": ("2026", "2025"),
          "epl": ("2026-27", "2025-26"), "ucl": ("2026-27", "2025-26"), "mls": ("2026", "2025")}
SOCCER = {"soccer_epl": "epl", "soccer_uefa_champs_league": "ucl", "soccer_usa_mls": "mls"}


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
    for lg, sport in (("nba", "basketball_nba"), ("nhl", "icehockey_nhl"), ("nfl", "americanfootball_nfl"), ("cfb", "americanfootball_ncaaf")):
        if not any(g["sport"] == sport for g in game_docs):
            continue
        try:
            cur, pri = SEASON[lg]
            inj[lg] = (ST.load_json(data_dir, f"stats/{lg}/injuries.json") or {}).get("teams", {})
            cg, pg = ST.load_games(lg, cur, data_dir), ST.load_games(lg, pri, data_dir)
            if lg == "nba":
                models[sport] = model_nba.NBA(cg, pg, inj[lg], today)
            elif lg == "nhl":
                models[sport] = model_nhl.NHL(data_dir, cg, pg, inj[lg])
            elif lg == "nfl":
                models[sport] = model_nfl.NFL(data_dir, int(cur), cg, inj[lg])
            else:
                models[sport] = model_cfb.CFB(cg, pg)
        except Exception as e:  # a model failure never blocks the board
            print(f"model {lg} failed: {type(e).__name__}: {str(e)[:120]}")
    for sport, lg in SOCCER.items():
        if any(g["sport"] == sport for g in game_docs):
            try:
                cur, pri = SEASON[lg]
                models[sport] = MX.Soccer(ST.load_games(lg, cur, data_dir), ST.load_games(lg, pri, data_dir))
            except Exception as e:
                print(f"model {lg} failed: {type(e).__name__}")
    if any(g["sport"] == "baseball_mlb" for g in game_docs):
        try:
            models["baseball_mlb"] = MX.MLB(data_dir)
        except Exception as e:
            print(f"model mlb failed: {type(e).__name__}")
    if any(g["sport"].startswith("tennis_") for g in game_docs):
        try:
            tm = MX.Tennis(data_dir)
            for g in game_docs:
                if g["sport"].startswith("tennis_"):
                    models[g["sport"]] = tm
        except Exception as e:
            print(f"model tennis failed: {type(e).__name__}")
    props_by_game = {}
    for p in prop_docs:
        props_by_game.setdefault(p["id"], []).append(p)
    for g in game_docs:
        m = models.get(g["sport"])
        if not m:
            continue
        try:
            refs = R.load(g["sport"], data_dir)
            fn = {"basketball_nba": _nba, "icehockey_nhl": _nhl, "americanfootball_nfl": _nfl, "americanfootball_ncaaf": _cfb,
                  "baseball_mlb": _mlb}.get(g["sport"]) or (_soccer if g["sport"] in SOCCER else _tennis if g["sport"].startswith("tennis_") else None)
            if not fn:
                continue
            doc = fn(g, m, props_by_game.get(g["id"], []), inj, refs)
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
                gl = goalie_fn(r["player"])
                if gl is None:
                    continue
                mean = gl["proj"]["saves"]
                r["last10"] = gl.get("last10", {}).get("sv")
            else:
                p = _match_player(r["player"], allp)
                if not p:
                    continue
                l10 = p.get("last10") or {}
                if all(k in l10 for k in keys):
                    r["last10"] = [round(sum(v), 1) for v in zip(*[l10[k] for k in keys])]
                if p.get("out"):
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


def _head(refs, name, fallback=None):
    return (refs.get("players", {}).get(R.norm(name)) or {}).get("headshot") or fallback


def lineup_nba(players, refs):
    """Projected starting five: last game's starters minus anyone out, filled by projected minutes."""
    live = [p for p in players if not p.get("out")]
    starters = [p for p in live if p.get("starter")][:5]
    for p in sorted(live, key=lambda p: -p.get("projMin", 0)):
        if len(starters) >= 5:
            break
        if p not in starters:
            starters.append(p)
    grp = lambda p: {"G": 0, "F": 1, "C": 2}.get(model_nba._pg(p.get("pos")), 1)
    starters.sort(key=grp)
    bench = [p for p in sorted(live, key=lambda p: -p.get("projMin", 0)) if p not in starters][:5]
    card = lambda p: {"name": p["name"], "pos": p.get("pos"), "min": p.get("projMin"), "status": p.get("status"),
                      "headshot": _head(refs, p["name"]), "changed": not p.get("starter")}
    return {"kind": "nba", "starters": [card(p) for p in starters], "bench": [card(p) for p in bench],
            "out": [p["name"] for p in players if p.get("out")][:6], "how": "last game's starters, injuries swapped for the next man up by projected minutes"}


def lineup_nhl(players, goalie, refs):
    live = [p for p in players if not p.get("out") and p.get("projToi")]
    fwd = sorted([p for p in live if (p.get("pos") or "") != "D"], key=lambda p: -p["projToi"])[:12]
    dmen = sorted([p for p in live if (p.get("pos") or "") == "D"], key=lambda p: -p["projToi"])[:6]
    card = lambda p: {"name": p["name"], "pos": p.get("pos"), "toi": p.get("projToi"), "headshot": _head(refs, p["name"])}
    return {"kind": "nhl", "lines": [[card(p) for p in fwd[i:i + 3]] for i in range(0, len(fwd), 3)],
            "pairs": [[card(p) for p in dmen[i:i + 2]] for i in range(0, len(dmen), 2)],
            "goalie": {"name": goalie.get("name"), "headshot": _head(refs, goalie.get("name") or "")},
            "out": [p["name"] for p in players if p.get("out")][:6], "how": "forward lines and defense pairs grouped by projected ice time; confirm with morning skate"}


def lineup_nfl(players, refs):
    live = [p for p in players if not p.get("out")]
    qb = [p for p in live if p["proj"].get("att")][:1]
    rb = sorted([p for p in live if (p.get("pos") or "") in ("RB", "FB")], key=lambda p: -p["proj"].get("car", 0))[:2]
    wr = sorted([p for p in live if p.get("pos") == "WR"], key=lambda p: -p["proj"].get("targets", 0))[:3]
    te = sorted([p for p in live if p.get("pos") == "TE"], key=lambda p: -p["proj"].get("targets", 0))[:1]
    card = lambda p, k: {"name": p["name"], "pos": p.get("pos"), "vol": round(p["proj"].get(k, 0), 1), "volKey": k,
                         "headshot": _head(refs, p["name"], p.get("headshotNfl"))}
    return {"kind": "nfl", "qb": [card(p, "att") for p in qb], "rb": [card(p, "car") for p in rb], "wr": [card(p, "targets") for p in wr],
            "te": [card(p, "targets") for p in te], "out": [p["name"] for p in players if p.get("out")][:6],
            "how": "skill positions by projected touches (attempts, carries, targets)"}


def _player_cards(players, refs, keys, n=9, minutes_key="projMin"):
    out = []
    for p in players[:n + 4]:
        if len(out) >= n:
            break
        if not p.get(minutes_key) and not p.get("out"):
            continue
        ref = refs.get("players", {}).get(R.norm(p["name"])) or {}
        out.append({"name": p["name"], "pos": p.get("pos"), "status": p.get("status"), "note": p.get("note"),
                    "out": p.get("out"), "min": p.get(minutes_key), "headshot": ref.get("headshot") or p.get("headshotNfl"),
                    "proj": dict(p["proj"]), "last10": p.get("last10")})
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
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys)},
            "lineups": {s: lineup_nba(pj["sides"][s]["players"], refs) for s in ("home", "away")}}


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
                return gl
        return None
    _stamp_props(pdocs, pj["sides"], model_nhl.MARKETS, g["sport"], goalie_mean)
    th, ta = m.teams[h], m.teams[a]
    gh, ga = pj["sides"]["home"]["goalie"], pj["sides"]["away"]["goalie"]

    def per2(v):
        return None if v is None else round(v * 2 / 60, 3)

    def team(t, side, name):
        gl = pj["sides"][side]["goalie"]
        return {"abbr": t["abbr"], "gp": t["gp"], "xgfPct": _fmt(t.get("xgfPct"), 3), "xgf60": _fmt(t.get("xgf60"), 2),
                "xga60": _fmt(t.get("xga60"), 2), "gf60": _fmt(t.get("gf60"), 2), "ga60": _fmt(t.get("ga60"), 2),
                "pp": _fmt(t.get("ppXgf60"), 2), "pk": _fmt(t.get("pkXga60"), 2), "rank": t.get("rank", {}),
                "goalie": gl, "goals": pj[side]["goals"], "injuries": _injuries(inj.get("nhl"), t["abbr"], name)}
    compare = [
        {"k": "xGoals share", "h": th.get("xgfPct"), "a": ta.get("xgfPct"), "hr": th["rank"].get("xgfPct"), "ar": ta["rank"].get("xgfPct"), "hi": True, "pct": True},
        {"k": "xGoals for /60", "h": th.get("xgf60"), "a": ta.get("xgf60"), "hr": th["rank"].get("xgf60"), "ar": ta["rank"].get("xgf60"), "hi": True, "d": 2},
        {"k": "xGoals against /60", "h": th.get("xga60"), "a": ta.get("xga60"), "hr": th["rank"].get("xga60"), "ar": ta["rank"].get("xga60"), "hi": False, "d": 2},
        {"k": "Power play xG per 2 min", "h": per2(th.get("ppXgf60")), "a": per2(ta.get("ppXgf60")), "hr": th["rank"].get("ppXgf60"), "ar": ta["rank"].get("ppXgf60"), "hi": True, "d": 3},
        {"k": "Penalty kill xGA per 2 min", "h": per2(th.get("pkXga60")), "a": per2(ta.get("pkXga60")), "hr": th["rank"].get("pkXga60"), "ar": ta["rank"].get("pkXga60"), "hi": False, "d": 3},
        {"k": "Goalie EV GSAx /60", "h": gh.get("ev60"), "a": ga.get("ev60"), "hr": gh.get("rank", {}).get("ev60"), "ar": ga.get("rank", {}).get("ev60"), "hi": True, "d": 2, "n": gh.get("pool")},
        {"k": "Goalie PK GSAx per 2 min", "h": gh.get("pk2"), "a": ga.get("pk2"), "hr": gh.get("rank", {}).get("pk2"), "ar": ga.get("rank", {}).get("pk2"), "hi": True, "d": 3, "n": gh.get("pool")},
        {"k": "Goalie GSAx /60 (all)", "h": gh.get("gsax60"), "a": ga.get("gsax60"), "hr": gh.get("rank", {}).get("gsax60"), "ar": ga.get("rank", {}).get("gsax60"), "hi": True, "d": 2, "n": gh.get("pool")},
    ]
    factors = []
    for side, t in (("home", th), ("away", ta)):
        gl = pj["sides"][side]["goalie"]
        if gl.get("name"):
            tone = "pos" if gl["gsax60"] > 0.1 else "neg" if gl["gsax60"] < -0.1 else "info"
            factors.append({"side": side, "tone": tone, "text": f"{t['abbr']} likely in goal: {gl['name']} ({gl['gsax60']:+.2f} goals saved above expected per 60; "
                            f"{len([s for s in gl['starts'] if s['name'] == gl['name']])} of last {len(gl['starts'])} starts) — confirm"})
    for side, t, o in (("home", th, ta), ("away", ta, th)):
        pr, kr = t["rank"].get("ppXgf60"), o["rank"].get("pkXga60")
        if pr and kr and pr <= 8 and kr >= 25:
            factors.append({"side": side, "tone": "pos", "text": f"{t['abbr']} power play (rank {pr}) vs {o['abbr']} penalty kill (rank {kr})"})
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
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys, 9, "projToi")},
            "lineups": {s: lineup_nhl(pj["sides"][s]["players"], pj["sides"][s]["goalie"], refs) for s in ("home", "away")}}


# ---------------------------------------------------------------------------
# NFL

def _line_probs(g, pj, m):
    pH = 1 - _phi(-pj["margin"] / m.SD_MARGIN)

    def probs(l):
        if l["market"] == "h2h":
            return {g["home"]: pH, g["away"]: 1 - pH}
        if l["market"] in ("spreads", "alternate_spreads"):
            ph = m.p_cover(pj["margin"], l["point"])
            return {g["home"]: ph, g["away"]: 1 - ph}
        if l["market"] in ("totals", "alternate_totals"):
            po = m.p_over(pj["total"], l["point"])
            return {"Over": po, "Under": 1 - po}
    return pH, probs


def _nfl(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a)
    if not pj:
        return None
    pH, probs = _line_probs(g, pj, m)
    lines = _stamp_lines(g, probs)
    _stamp_props(pdocs, pj["sides"], model_nfl.MARKETS, g["sport"])
    for d in pdocs:     # anytime TD: Poisson on projected touchdowns
        if d["market"] != "player_anytime_td":
            continue
        for r in d["rows"]:
            p = _match_player(r["player"], pj["sides"]["home"]["players"] + pj["sides"]["away"]["players"])
            if p and not p.get("out"):
                py = 1 - math.exp(-p["proj"].get("td", 0))
                r["model"] = {"mean": round(p["proj"].get("td", 0), 2), "Yes": round(py, 4), "No": round(1 - py, 4)}
    th, ta = m.teams[h], m.teams[a]
    pct = lambda v: None if v is None else round(v, 3)

    def team(t, side, name):
        return {"abbr": t["abbr"], "record": None, "gp": t["g"], "qb": t.get("qb"), "pts": pj[side]["pts"], "rank": t.get("rank", {}),
                "injuries": _injuries(inj.get("nfl"), t["abbr"], name), "att": pj["sides"][side]["att"], "car": pj["sides"][side]["car"]}
    compare = [
        {"k": "Offense EPA/play", "h": th.get("offRaw"), "a": ta.get("offRaw"), "hr": th["rank"].get("offRaw"), "ar": ta["rank"].get("offRaw"), "hi": True, "d": 3},
        {"k": "Defense EPA/play allowed", "h": th.get("defRaw"), "a": ta.get("defRaw"), "hr": th["rank"].get("defRaw"), "ar": ta["rank"].get("defRaw"), "hi": False, "d": 3},
        {"k": "Success rate", "h": pct(th.get("succ")), "a": pct(ta.get("succ")), "hr": th["rank"].get("succ"), "ar": ta["rank"].get("succ"), "hi": True, "pct": True},
        {"k": "Success rate allowed", "h": pct(th.get("dsucc")), "a": pct(ta.get("dsucc")), "hr": th["rank"].get("dsucc"), "ar": ta["rank"].get("dsucc"), "hi": False, "pct": True},
        {"k": "Explosive play rate", "h": pct(th.get("expl")), "a": pct(ta.get("expl")), "hr": th["rank"].get("expl"), "ar": ta["rank"].get("expl"), "hi": True, "pct": True},
        {"k": "Early-down EPA/play", "h": th.get("edEpa"), "a": ta.get("edEpa"), "hr": th["rank"].get("edEpa"), "ar": ta["rank"].get("edEpa"), "hi": True, "d": 3},
        {"k": "Pass rate over expected", "h": pct(th.get("proe")), "a": pct(ta.get("proe")), "hr": th["rank"].get("proe"), "ar": ta["rank"].get("proe"), "hi": None, "pct": True},
        {"k": "Plays per game", "h": th.get("plays"), "a": ta.get("plays"), "hr": th["rank"].get("plays"), "ar": ta["rank"].get("plays"), "hi": None, "d": 1},
    ]
    factors = []
    for side, t in (("home", th), ("away", ta)):
        if t.get("qbChanged"):
            factors.append({"side": side, "tone": "info", "text": f"{t['abbr']} has a different starting QB ({t['qb']}) than last season; last season counts less"})
        outs = [p for p in pj["sides"][side]["players"] if p["out"] and (p["proj"] or True)]
        named = [p["name"] for p in outs][:4]
        if named:
            factors.append({"side": side, "tone": "neg", "text": f"{t['abbr']} out: {', '.join(named)} (targets and carries redistributed)"})
    factors.append({"side": None, "tone": "info", "text": f"Model: {g['homeRef']['abbr'] if g.get('homeRef') else 'home'} {-pj['margin']:+.1f}, total {pj['total']:.1f} "
                    f"({pj['away']['pts']}-{pj['home']['pts']})"})
    keys = ["pass_yds", "rush_yds", "rec", "rec_yds"]
    return {"id": g["id"], "league": "NFL", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"margin": pj["margin"], "total": pj["total"], "pHome": round(pH, 4), "lines": lines},
            "teams": {"home": team(th, "home", g["home"]), "away": team(ta, "away", g["away"])},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": keys, "statLabels": [model_nfl.LABEL[k] for k in keys],
            "players": {"home": _player_cards(pj["sides"]["home"]["players"], refs, keys, 10, "projMin"),
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys, 10, "projMin")},
            "lineups": {s: lineup_nfl(pj["sides"][s]["players"], refs) for s in ("home", "away")}}


# ---------------------------------------------------------------------------
# College football (team level)

def _cfb(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a)
    if not pj:
        return None
    pH, probs = _line_probs(g, pj, m)
    lines = _stamp_lines(g, probs)
    th, ta = m.teams[h], m.teams[a]
    compare = [
        {"k": "Net rating (pts vs average)", "h": th["net"], "a": ta["net"], "hr": th["rank"]["net"], "ar": ta["rank"]["net"], "hi": True, "d": 1, "n": m.n},
        {"k": "Offense (pts vs average)", "h": th["off"], "a": ta["off"], "hr": th["rank"]["off"], "ar": ta["rank"]["off"], "hi": True, "d": 1, "n": m.n},
        {"k": "Defense (pts saved vs average)", "h": th["def"], "a": ta["def"], "hr": th["rank"]["def"], "ar": ta["rank"]["def"], "hi": True, "d": 1, "n": m.n},
        {"k": "Yards per play (adj)", "h": th["ypp"], "a": ta["ypp"], "hr": th["rank"]["ypp"], "ar": ta["rank"]["ypp"], "hi": True, "d": 2, "n": m.n},
        {"k": "Yards per play allowed (adj)", "h": th["dypp"], "a": ta["dypp"], "hr": th["rank"]["dypp"], "ar": ta["rank"]["dypp"], "hi": False, "d": 2, "n": m.n},
    ]
    factors = [{"side": None, "tone": "info", "text": f"Opponent-adjusted model: {g['homeRef']['abbr'] if g.get('homeRef') else 'home'} {-pj['margin']:+.1f}, "
                f"total {pj['total']:.1f}; home edge worth {m.h:.1f} points this season"}]
    return {"id": g["id"], "league": "NCAAF", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"margin": pj["margin"], "total": pj["total"], "pHome": round(pH, 4), "lines": lines},
            "teams": {"home": {"abbr": th["abbr"], "record": th["record"], "pts": pj["home"]["pts"], "injuries": _injuries(inj.get("cfb"), th["abbr"], g["home"])},
                      "away": {"abbr": ta["abbr"], "record": ta["record"], "pts": pj["away"]["pts"], "injuries": _injuries(inj.get("cfb"), ta["abbr"], g["away"])}},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": [], "statLabels": [],
            "players": {"home": [], "away": []}}


# ---------------------------------------------------------------------------
# Soccer, tennis, MLB (first versions)

def _soccer(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a)
    if not pj:
        return None

    def probs(l):
        if l["market"] == "h2h" and "Draw" in l["outcomes"]:
            return {g["home"]: pj["pHome"], g["away"]: pj["pAway"], "Draw": pj["pDraw"]}
        if l["market"] in ("totals", "alternate_totals"):
            po = m.p_over(l["point"])
            return {"Over": po, "Under": 1 - po}
        if l["market"] in ("spreads", "alternate_spreads"):
            ph = m.p_home_cover(l["point"])
            return {g["home"]: ph, g["away"]: 1 - ph}
        if l["market"] == "btts":
            return {"Yes": pj["btts"], "No": 1 - pj["btts"]}
    lines = _stamp_lines(g, probs)
    th, ta = m.teams[h], m.teams[a]
    compare = [{"k": "Attack (goals vs average)", "h": th["att"], "a": ta["att"], "hr": th["rank"]["att"], "ar": ta["rank"]["att"], "hi": True, "d": 2, "n": m.n},
               {"k": "Defense (goals saved vs average)", "h": th["def"], "a": ta["def"], "hr": th["rank"]["def"], "ar": ta["rank"]["def"], "hi": True, "d": 2, "n": m.n}]
    factors = [{"side": None, "tone": "info", "text": f"Model: {pj['home']['goals']:.2f}-{pj['away']['goals']:.2f} expected goals · home {pj['pHome']:.0%}, draw {pj['pDraw']:.0%}, "
                f"away {pj['pAway']:.0%} · both score {pj['btts']:.0%}. First version: goals-based ratings; xG and lineups come next."}]
    return {"id": g["id"], "league": g["league"], "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"pHome": round(pj["pHome"], 4), "total": pj["total"], "lines": lines},
            "teams": {"home": {"abbr": th["abbr"], "record": th["record"], "goals": pj["home"]["goals"], "injuries": []},
                      "away": {"abbr": ta["abbr"], "record": ta["record"], "goals": pj["away"]["goals"], "injuries": []}},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": [], "statLabels": [], "players": {"home": [], "away": []}}


def _tennis(g, m, pdocs, inj, refs):
    p = m.p_win(g["home"], g["away"])
    if p is None:
        return None
    lines = _stamp_lines(g, lambda l: {g["home"]: p, g["away"]: 1 - p} if l["market"] == "h2h" else None)
    ka, kb = m.find(g["home"]), m.find(g["away"])
    form = lambda k: m.form.get(k, [])[-10:]
    compare = [{"k": "Elo (overall + surface)", "h": m.rating(ka), "a": m.rating(kb), "hi": True, "d": 0},
               {"k": "Tour-level matches", "h": m.n.get(ka), "a": m.n.get(kb), "hi": None, "d": 0},
               {"k": "Wins in last 10", "h": sum(form(ka)), "a": sum(form(kb)), "hi": True, "d": 0}]
    return {"id": g["id"], "league": g["league"], "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"pHome": round(p, 4), "lines": lines},
            "teams": {"home": {"abbr": g["home"].split()[-1][:3].upper(), "record": None, "injuries": []},
                      "away": {"abbr": g["away"].split()[-1][:3].upper(), "record": None, "injuries": []}},
            "compare": compare, "factors": [{"side": None, "tone": "info", "text": f"Elo model: {g['home']} {p:.0%}. First version: surface Elo; serve/return and fatigue come next."}],
            "propEdges": [], "statKeys": [], "statLabels": [], "players": {"home": [], "away": []}}


def _mlb(g, m, pdocs, inj, refs):
    pj = m.project_game(g["home"], g["away"])
    if not pj:
        return None
    sd = 4.3

    def probs(l):
        if l["market"] == "h2h":
            return {g["home"]: pj["pHome"], g["away"]: 1 - pj["pHome"]}
        if l["market"] in ("totals", "alternate_totals"):
            po = 1 - _phi((l["point"] - pj["total"]) / 3.3)
            return {"Over": po, "Under": 1 - po}
        if l["market"] in ("spreads", "alternate_spreads"):
            ph = 1 - _phi((-l["point"] - pj["margin"]) / sd)
            return {g["home"]: ph, g["away"]: 1 - ph}
    lines = _stamp_lines(g, probs)
    for d in pdocs:
        if d["market"] != "pitcher_strikeouts":
            continue
        for r in d["rows"]:
            for side in ("home", "away"):
                sp = pj["sp"].get(side)
                if sp and R.norm(sp["name"] or "") == R.norm(r["player"]) and r.get("point") is not None:
                    po = B.p_over("pitcher_strikeouts", sp["kProj"], r["point"], "baseball_mlb")
                    r["model"] = {"mean": sp["kProj"], "raw": sp["kProj"], "Over": round(po, 4), "Under": round(1 - po, 4)}
    sp = pj["sp"]
    fmt = lambda x, k, d=2: None if not x or x.get(k) is None else round(x[k], d)
    compare = [{"k": "Starter FIP", "h": fmt(sp.get("home"), "fip"), "a": fmt(sp.get("away"), "fip"), "hi": False, "d": 2},
               {"k": "Starter K rate", "h": fmt(sp.get("home"), "kRate", 3), "a": fmt(sp.get("away"), "kRate", 3), "hi": True, "pct": True},
               {"k": "Starter innings per start", "h": fmt(sp.get("home"), "ipPerStart", 1), "a": fmt(sp.get("away"), "ipPerStart", 1), "hi": True, "d": 1},
               {"k": "Offense runs per game", "h": round(m.off[g["home"]]["rpg"], 2), "a": round(m.off[g["away"]]["rpg"], 2), "hi": True, "d": 2},
               {"k": "Offense OPS", "h": m.off[g["home"]]["ops"], "a": m.off[g["away"]]["ops"], "hi": True, "d": 3}]
    factors = [{"side": s, "tone": "info", "text": f"Probable starter: {sp[s]['name']} (FIP {sp[s]['fip']:.2f}, projected {sp[s]['kProj']} strikeouts)"}
               for s in ("away", "home") if sp.get(s)]
    factors.append({"side": None, "tone": "info", "text": f"Model runs {pj['away']['runs']}-{pj['home']['runs']}, total {pj['total']}. First version: starters, bullpen ERA and team offense; Statcast and lineups come next."})
    return {"id": g["id"], "league": "MLB", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"pHome": round(pj["pHome"], 4), "total": pj["total"], "margin": pj["margin"], "lines": lines},
            "teams": {"home": {"abbr": (g.get("homeRef") or {}).get("abbr"), "record": None, "runs": pj["home"]["runs"], "injuries": []},
                      "away": {"abbr": (g.get("awayRef") or {}).get("abbr"), "record": None, "runs": pj["away"]["runs"], "injuries": []}},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": [], "statLabels": [], "players": {"home": [], "away": []}}
