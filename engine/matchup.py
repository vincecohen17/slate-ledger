"""Matchup layer: runs the league models for every game on the board and
  * writes a matchup doc per game (team comparison, injuries, players, key factors, model lines),
  * stamps our model probability onto every game line and player prop (line["model"]),
  * lets the edge list say whether sports logic agrees with the price.
"""
import math
import re

import refs as R
import stats as ST
import build as B
import model_nba
import model_nhl
import model_nfl
import model_cfb
import model_extra as MX
import model_mlb as MLBM
import context as CX

ALIAS = {"UTAH": "UTA", "GSW": "GS", "NOP": "NO", "SAS": "SA", "NYK": "NY", "WAS": "WSH", "UTH": "UTA"}
SEASON = {"nba": ("2026-27", "2025-26"), "wnba": ("2026", "2025"), "nhl": ("2026-27", "2025-26"), "nfl": ("2026", "2025"), "cfb": ("2026", "2025"),
          "mls": ("2026", "2025"), **{lg: ("2026-27", "2025-26") for lg in ST.SOCCER if lg != "mls"}}
SOCCER = {sport: lg for lg, (_, sport) in ST.SOCCER.items()}


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
    ctx = CX.load(data_dir, today)
    build_all.weather = CX.nfl_weather(data_dir, game_docs) if any(g["sport"] == "americanfootball_nfl" for g in game_docs) else {}
    for lg, sport in (("nba", "basketball_nba"), ("wnba", "basketball_wnba"), ("nhl", "icehockey_nhl"), ("nfl", "americanfootball_nfl"), ("cfb", "americanfootball_ncaaf")):
        if not any(g["sport"] == sport for g in game_docs):
            continue
        try:
            cur, pri = SEASON[lg]
            inj[lg] = CX.apply_injuries((ST.load_json(data_dir, f"stats/{lg}/injuries.json") or {}).get("teams", {}), ctx, lg)
            cg, pg = ST.load_games(lg, cur, data_dir), ST.load_games(lg, pri, data_dir)
            if lg in ("nba", "wnba"):
                models[sport] = (model_nba.WNBA if lg == "wnba" else model_nba.NBA)(cg, pg, inj[lg], today)
                models[sport].min_override = {CX.norm(e["player"]): e["value"] for e in ctx
                                              if e["kind"] == "minutes" and (e.get("league") or "").lower() == lg and e.get("player")}
            elif lg == "nhl":
                models[sport] = model_nhl.NHL(data_dir, cg, pg, inj[lg])
                models[sport].goalie_override = {e.get("team"): e["player"] for e in ctx if e["kind"] == "goalie" and e.get("player")}
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
            models["baseball_mlb"] = MLBM.MLB(data_dir)
            models["baseball_mlb"].sp_override = {CX.norm(e.get("team")): e["player"] for e in ctx if e["kind"] == "starter" and e.get("player")}
            inj["mlb"] = (ST.load_json(data_dir, "stats/mlb/injuries.json") or {}).get("teams", {})
        except Exception as e:
            print(f"model mlb failed: {type(e).__name__}: {str(e)[:120]}")
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
            fn = {"basketball_nba": _nba, "basketball_wnba": _nba, "icehockey_nhl": _nhl, "americanfootball_nfl": _nfl, "americanfootball_ncaaf": _cfb,
                  "baseball_mlb": _mlb}.get(g["sport"]) or (_soccer if g["sport"] in SOCCER else _tennis if g["sport"].startswith("tennis_") else None)
            if not fn:
                continue
            doc = fn(g, m, props_by_game.get(g["id"], []), inj, refs)
            if doc:
                _context_notes(doc, g, ctx)
                out[g["id"]] = doc
        except Exception as e:
            print(f"matchup {g['league']} failed: {type(e).__name__}: {str(e)[:120]}")
    shifts = calibrate(prop_docs)
    for gid, doc in out.items():
        doc["propEdges"] = _disagreements(props_by_game.get(gid, []))
        doc["calibration"] = shifts
    build_all.shifts = shifts
    return out



# ---------------------------------------------------------------------------
# Game-line probabilities for every market shape (main, alternates, team totals, quarters/halves/periods)

PERIOD_FRAC = {"q1": 0.25, "q2": 0.25, "q3": 0.25, "q4": 0.25, "h1": 0.5, "h2": 0.5, "p1": 1 / 3, "p2": 1 / 3, "p3": 1 / 3}


def split_market(mk):
    """'alternate_spreads_h1' -> ('spreads', 'h1'); 'team_totals' -> ('team_totals', None)"""
    m = mk[10:] if mk.startswith("alternate_") else mk
    for p in PERIOD_FRAC:
        if m.endswith("_" + p):
            return m[:-len(p) - 1], p
    return m, None


def _team_side(g, l):
    t = R.norm(l.get("team") or "")
    return "home" if t == R.norm(g["home"]) else "away" if t == R.norm(g["away"]) else None


def normal_probs(g, margin, total, sd_m, sd_t):
    """Score-difference / total model (basketball, football): normal margin and total, periods scale with time."""
    def probs(l):
        base, per = split_market(l["market"])
        f = PERIOD_FRAC.get(per, 1.0) if per else 1.0
        if per and per.startswith("p"):
            return None
        mg, tt, sm, st = margin * f, total * f, sd_m * math.sqrt(f), sd_t * math.sqrt(f)
        pt = l.get("point")
        if base == "h2h":
            ph = 1 - _phi(-mg / sm)
            return {g["home"]: ph, g["away"]: 1 - ph}
        if base == "h2h_3_way":
            tie = _phi((0.5 - mg) / sm) - _phi((-0.5 - mg) / sm)
            ph = 1 - _phi((0.5 - mg) / sm)
            out = {g["home"]: ph, g["away"]: max(0.0, 1 - ph - tie)}
            for o in l.get("outcomes", []):
                out.setdefault(o, tie)
            return out
        if base == "spreads" and pt is not None:
            ph = 1 - _phi((-pt - mg) / sm)
            return {g["home"]: ph, g["away"]: 1 - ph}
        if base == "totals" and pt is not None:
            po = 1 - _phi((pt - tt) / st)
            return {"Over": po, "Under": 1 - po}
        if base == "team_totals" and pt is not None:
            side = _team_side(g, l)
            if side:
                mean = (tt + mg) / 2 if side == "home" else (tt - mg) / 2
                po = 1 - _phi((pt - mean) / (math.sqrt(st ** 2 + sm ** 2) / 2))
                return {"Over": po, "Under": 1 - po}
    return probs


def _pois(mu, n=14):
    p, out = math.exp(-mu), []
    for k in range(n):
        out.append(p)
        p *= mu / (k + 1)
    return out


def hockey_probs(g, m, pj):
    """NHL: Poisson goals per team (regulation), OT/shootout 52% home; periods are a third of the game each."""
    ph_full, pa_full = m._dist
    mu_h = sum(i * p for i, p in enumerate(ph_full))
    mu_a = sum(i * p for i, p in enumerate(pa_full))

    def grid(per):
        if per:
            return _pois(mu_h / 3), _pois(mu_a / 3)
        return ph_full, pa_full

    def probs(l):
        base, per = split_market(l["market"])
        if per and not per.startswith("p"):
            return None
        ph, pa = grid(per)
        N = range(min(len(ph), len(pa)))
        P = lambda cond: sum(ph[i] * pa[j] for i in N for j in N if cond(i, j))
        tie = P(lambda i, j: i == j)
        pt = l.get("point")
        if base == "h2h":
            if per:     # period moneyline: a tie refunds
                w = P(lambda i, j: i > j)
                ph_ = w / max(1e-9, 1 - tie)
            else:
                ph_ = pj["pHomeWin"]
            return {g["home"]: ph_, g["away"]: 1 - ph_}
        if base == "h2h_3_way":
            w = P(lambda i, j: i > j)
            out = {g["home"]: w, g["away"]: 1 - w - tie}
            for o in l.get("outcomes", []):
                out.setdefault(o, tie)
            return out
        if base == "spreads" and pt is not None:
            if per:
                c = P(lambda i, j: i - j + pt > 0)
                push = P(lambda i, j: i - j + pt == 0)
                ph_ = c / max(1e-9, 1 - push)
            else:
                ph_ = m.p_home_cover(pt)
            return {g["home"]: ph_, g["away"]: 1 - ph_}
        if base == "totals" and pt is not None:
            extra = 0 if per else 1          # a full-game tie adds the OT / shootout goal
            o = P(lambda i, j: i + j + (extra if i == j else 0) > pt)
            push = P(lambda i, j: i + j + (extra if i == j else 0) == pt)
            po = o / max(1e-9, 1 - push)
            return {"Over": po, "Under": 1 - po}
        if base == "team_totals" and pt is not None:
            side = _team_side(g, l)
            if not side:
                return None
            share = 0.52 if side == "home" else 0.48
            me, op = (ph, pa) if side == "home" else (pa, ph)
            o = sum(me[i] * op[j] * ((1 if i > pt else 0) if (i != j or per) else ((1 if i + 1 > pt else 0) * share + (1 if i > pt else 0) * (1 - share)))
                    for i in N for j in N)
            push = sum(me[i] for i in N if i == pt) if per else 0
            po = o / max(1e-9, 1 - push)
            return {"Over": po, "Under": 1 - po}
        if base == "btts":
            y = (1 - ph[0]) * (1 - pa[0])
            return {"Yes": y, "No": 1 - y}
    return probs

def _context_notes(doc, g, ctx):
    """verified news as key factors, and a short context list the edge cards carry"""
    ha, aa = (g.get("homeRef") or {}).get("abbr"), (g.get("awayRef") or {}).get("abbr")
    notes = CX.notes(ctx, doc["league"], [ha, aa], [g["home"], g["away"]])
    for n in notes:
        side = "home" if CX.norm(n["team"]) in (CX.norm(ha), CX.norm(g["home"])) else "away"
        doc["factors"].insert(0, {"side": side, "tone": n["tone"], "text": n["text"]})
    auto = [f["text"] for f in doc["factors"] if any(k in f["text"] for k in ("back-to-back", "Weather", "without", "out:", "rested", "likely in goal", "Wind"))]
    doc["context"] = [n["text"] for n in notes] + auto[:4]


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


YES_POISSON = {"player_goal_scorer_anytime", "player_anytime_td"}      # P(at least one) from a Poisson mean


def _stamp_props(pdocs, sides, markets, sport, goalie_fn=None):
    """markets: {market: [stat keys summed]} or {market: {stat key: weight}} (quarter shares, fantasy scoring)."""
    allp = sides["home"]["players"] + sides["away"]["players"]
    for d in pdocs:
        spec = markets.get(d["market"])
        if not spec:
            continue
        wts = spec if isinstance(spec, dict) else {k: 1.0 for k in spec}
        keys = list(wts)
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
                if all(k in l10 for k in keys) and all(w == 1.0 for w in wts.values()):
                    r["last10"] = [round(sum(v), 1) for v in zip(*[l10[k] for k in keys])]
                if p.get("out"):
                    continue
                mean = sum(p["proj"].get(k, 0) * w for k, w in wts.items())
            if r.get("point") is None:
                if d["market"] in YES_POISSON:
                    py = 1 - math.exp(-max(mean, 0))
                    r["model"] = {"mean": round(mean, 2), "Yes": round(py, 4), "No": round(1 - py, 4)}
                continue
            po = B.p_over(d["market"], max(mean, 0.01), r["point"], sport)
            r["model"] = {"mean": round(mean, 2), "raw": round(mean, 2), "Over": round(po, 4), "Under": round(1 - po, 4)}


def _stamp_field(pdocs, market, shares, p_any=1.0):
    """first-scorer style markets: P(player) = his share of the expected scoring x P(anyone scores)"""
    tot = sum(v for v in shares.values() if v > 0)
    if not tot:
        return
    for d in pdocs:
        if d["market"] != market:
            continue
        for r in d["rows"]:
            hit = next((v for n, v in shares.items() if R.norm(n) == R.norm(r["player"])), None)
            if hit is None:
                continue
            py = p_any * hit / tot
            r["model"] = {"mean": round(hit, 3), "Yes": round(py, 4), "No": round(1 - py, 4)}


def _nb_ge(mean, k, t):
    return B._nb_cdf_tail(max(mean, 0.01), k, t)


def _nba_specials(pdocs, pj):
    """double-double / triple-double (independent counts, NBA dispersion) and first basket (scoring share)"""
    disp = B.DISPERSION["basketball_nba"]
    allp = pj["sides"]["home"]["players"] + pj["sides"]["away"]["players"]
    for d in pdocs:
        if d["market"] not in ("player_double_double", "player_triple_double"):
            continue
        need = 2 if d["market"] == "player_double_double" else 3
        for r in d["rows"]:
            p = _match_player(r["player"], allp)
            if not p or p.get("out"):
                continue
            ps = [_nb_ge(p["proj"].get(k, 0), disp.get(mk, 1.3), 10) for k, mk in
                  (("pts", "player_points"), ("reb", "player_rebounds"), ("ast", "player_assists"), ("blk", "player_blocks"), ("stl", "player_steals"))]
            dist = [1.0]
            for q in ps:
                dist = [a * (1 - q) + (dist[i - 1] * q if i else 0) for i, a in enumerate(dist + [0.0])]
            py = sum(dist[need:])
            r["model"] = {"mean": round(py, 3), "Yes": round(py, 4), "No": round(1 - py, 4)}
    starters = {p["name"]: p["proj"].get("pts", 0) * (1.3 if p.get("starter") else 0.6) for p in allp if not p.get("out")}
    _stamp_field(pdocs, "player_first_basket", starters, 1.0)
    for side in ("home", "away"):       # first basket for his own team: share within the team
        team = {p["name"]: p["proj"].get("pts", 0) * (1.3 if p.get("starter") else 0.6) for p in pj["sides"][side]["players"] if not p.get("out")}
        _stamp_field(pdocs, "player_first_team_basket", team, 1.0)


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
    pH = 1 - _phi(-pj["margin"] / m.SD_MARGIN)
    lg = "wnba" if g["sport"] == "basketball_wnba" else "nba"

    lines = _stamp_lines(g, normal_probs(g, pj["margin"], pj["total"], m.SD_MARGIN, m.SD_TOTAL))
    _stamp_props(pdocs, pj["sides"], model_nba.MARKETS, g["sport"])
    _nba_specials(pdocs, pj)
    th, ta = m.teams[h], m.teams[a]

    def team(t, side, name):
        return {"abbr": t["abbr"], "record": f"{t['w']}-{t['l']}", "games": t["games"], "continuity": t.get("continuity"),
                "ortg": _fmt(t["ortg"]), "drtg": _fmt(t["drtg"]), "net": _fmt(t["net"]), "pace": _fmt(t["pace"]),
                "rank": t.get("rank", {}), "last10": t["last10"], "rest": pj["rest"][side],
                "injuries": _injuries(inj.get(lg), t["abbr"], name), "freedMin": pj["sides"][side]["freedMin"],
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
        nt = len(m.teams)
        q = max(2, round(nt * 0.27))
        if th["rank"].get("pace", 99) <= q and ta["rank"].get("pace", 99) <= q:
            factors.append({"side": None, "tone": "info", "text": f"Two top-{q} pace teams: more possessions, totals and counting props up"})
        if th["rank"].get("pace", 0) > nt - q and ta["rank"].get("pace", 0) > nt - q:
            factors.append({"side": None, "tone": "info", "text": f"Two bottom-{q} pace teams: fewer possessions"})
    sp = lines.get("spreads")
    if sp and sp.get("point") is not None:
        mkt_line = sp["point"]
        factors.append({"side": None, "tone": "info", "text": f"Model line {g['homeRef']['abbr'] if g.get('homeRef') else 'home'} {-pj['margin']:+.1f}, "
                        f"market {mkt_line:+g}; model total {pj['total']:.1f}"})
    keys = ["pts", "reb", "ast", "fg3"]
    return {"id": g["id"], "league": lg.upper(), "nTeams": len(m.teams), "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"margin": pj["margin"], "total": pj["total"], "pHome": round(pH, 4), "pace": pj["pace"], "lines": lines},
            "teams": {"home": team(th, "home", g["home"]), "away": team(ta, "away", g["away"])},
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": keys,
            "statLabels": [model_nba.LABEL[k] for k in keys],
            "players": {"home": _player_cards(pj["sides"]["home"]["players"], refs, keys),
                        "away": _player_cards(pj["sides"]["away"]["players"], refs, keys)},
            "lineups": {s: lineup_nba(pj["sides"][s]["players"], refs) for s in ("home", "away")}}


# ---------------------------------------------------------------------------
# NHL

def _goalie_games(pj, m):
    """add expected goals against and GSAx to each recent start (MoneyPuck game by game)"""
    gg = getattr(m, "_gg", None)
    if gg is None:
        gg = m._gg = {R.norm(k): v for k, v in (ST.load_json(m.data_dir, f"stats/nhl/moneypuck/goalie_games_{m.cur_year}.json") or {}).items()}
    from datetime import date as _d, timedelta as _td

    def find(name, day):
        rows = gg.get(R.norm(name or "")) or []
        for r in rows:
            try:
                if abs((_d.fromisoformat(r["date"]) - _d.fromisoformat(day)).days) <= 1:
                    return r
            except Exception:
                pass
        return None
    for side in ("home", "away"):
        gl = pj["sides"][side]["goalie"]
        for st_ in gl.get("starts") or []:
            r = find(st_["name"], st_["date"])
            if r:
                st_["xga"], st_["gsax"] = r["xga"], r["gsax"]
        rows = gg.get(R.norm(gl.get("name") or "")) or []
        if rows:
            last = rows[-10:]
            gl["last10"] = dict(gl.get("last10") or {}, gsax=[r["gsax"] for r in last], xga=[r["xga"] for r in last],
                                sv=[r["sv"] for r in last], sa=[r["sa"] for r in last], ga=[r["ga"] for r in last],
                                date=[r["date"] for r in last], opp=[r["opp"] for r in last])


def _nhl(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a, g["commence"])
    if not pj:
        return None

    lines = _stamp_lines(g, hockey_probs(g, m, pj))

    def goalie_mean(name):
        for side in ("home", "away"):
            gl = pj["sides"][side]["goalie"]
            if gl.get("name") and R.norm(gl["name"]) == R.norm(name):
                return gl
        return None
    _goalie_games(pj, m)
    for side in ("home", "away"):
        gl = pj["sides"][side]["goalie"]
        gl["headshot"] = _head(refs, gl.get("name") or "")
    _stamp_props(pdocs, pj["sides"], model_nhl.MARKETS, g["sport"], goalie_mean)
    g_share = {p["name"]: p["proj"].get("g", 0) for s_ in ("home", "away") for p in pj["sides"][s_]["players"] if not p.get("out")}
    p_goal = 1 - math.exp(-(pj["home"]["goals"] + pj["away"]["goals"]))
    _stamp_field(pdocs, "player_goal_scorer_first", g_share, p_goal)
    _stamp_field(pdocs, "player_goal_scorer_last", g_share, p_goal)
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
        {"k": "xGoals for /60 (all)", "h": th.get("xgf60"), "a": ta.get("xgf60"), "hr": th["rank"].get("xgf60"), "ar": ta["rank"].get("xgf60"), "hi": True, "d": 2},
        {"k": "xGoals against /60 (all)", "h": th.get("xga60"), "a": ta.get("xga60"), "hr": th["rank"].get("xga60"), "ar": ta["rank"].get("xga60"), "hi": False, "d": 2},
        {"k": "Even-strength xGF /60", "h": _fmt(th.get("evXgf60"), 2), "a": _fmt(ta.get("evXgf60"), 2), "hr": th["rank"].get("evXgf60"), "ar": ta["rank"].get("evXgf60"), "hi": True, "d": 2},
        {"k": "Even-strength xGA /60", "h": _fmt(th.get("evXga60"), 2), "a": _fmt(ta.get("evXga60"), 2), "hr": th["rank"].get("evXga60"), "ar": ta["rank"].get("evXga60"), "hi": False, "d": 2},
        {"k": "Even-strength xG share", "h": _fmt(th.get("evXgfPct"), 3), "a": _fmt(ta.get("evXgfPct"), 3), "hr": th["rank"].get("evXgfPct"), "ar": ta["rank"].get("evXgfPct"), "hi": True, "pct": True},
        {"k": "Power play xG per 2 min", "h": per2(th.get("ppXgf60")), "a": per2(ta.get("ppXgf60")), "hr": th["rank"].get("ppXgf60"), "ar": ta["rank"].get("ppXgf60"), "hi": True, "d": 3},
        {"k": "Penalty kill xGA per 2 min", "h": per2(th.get("pkXga60")), "a": per2(ta.get("pkXga60")), "hr": th["rank"].get("pkXga60"), "ar": ta["rank"].get("pkXga60"), "hi": False, "d": 3},
        {"k": "Goalie EV GSAx /60", "h": gh.get("ev60"), "a": ga.get("ev60"), "hr": gh.get("rank", {}).get("ev60"), "ar": ga.get("rank", {}).get("ev60"), "hi": True, "d": 2, "n": gh.get("pool")},
        {"k": "Goalie PK GSAx per 2 min", "h": gh.get("pk2"), "a": ga.get("pk2"), "hr": gh.get("rank", {}).get("pk2"), "ar": ga.get("rank", {}).get("pk2"), "hi": True, "d": 3, "n": gh.get("pool")},
        {"k": "Goalie GSAx /60 (all)", "h": gh.get("gsax60"), "a": ga.get("gsax60"), "hr": gh.get("rank", {}).get("gsax60"), "ar": ga.get("rank", {}).get("gsax60"), "hi": True, "d": 2, "n": gh.get("pool")},
    ]
    factors = []
    for side, t in (("home", th), ("away", ta)):
        if (pj.get("rest") or {}).get(side) == 1:
            factors.append({"side": side, "tone": "neg", "text": f"{t['abbr']} on the second night of a back-to-back (model: about 4% fewer goals for, 3% more against)"})
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
    return pH, normal_probs(g, pj["margin"], pj["total"], m.SD_MARGIN, m.SD_TOTAL)


def _nfl(g, m, pdocs, inj, refs):
    h, a = _abbr(g.get("homeRef"), m.teams), _abbr(g.get("awayRef"), m.teams)
    pj = m.project_game(h, a)
    if not pj:
        return None
    wtot, pf, rf, ruf, wnote = CX.nfl_weather_effect((getattr(build_all, "weather", {}) or {}).get(g["id"]))
    if wnote and (wtot or pf < 1):
        for side in ("home", "away"):
            pj[side]["pts"] = round(pj[side]["pts"] + wtot / 2, 1)
            for p in pj["sides"][side]["players"]:
                pr = p["proj"]
                for k, f in (("pass_yds", pf), ("rec_yds", rf), ("rush_yds", ruf), ("ptd", pf), ("cmp", (1 + pf) / 2)):
                    if k in pr:
                        pr[k] = round(pr[k] * f, 2)
        pj["total"] = round(pj["home"]["pts"] + pj["away"]["pts"], 1)
    pH, probs = _line_probs(g, pj, m)
    lines = _stamp_lines(g, probs)
    _stamp_props(pdocs, pj["sides"], model_nfl.MARKETS, g["sport"])
    nfl_all = pj["sides"]["home"]["players"] + pj["sides"]["away"]["players"]
    td_share = {p["name"]: p["proj"].get("td", 0) for p in nfl_all if not p.get("out")}
    p_any_td = 1 - math.exp(-max(0.1, (pj["home"]["pts"] + pj["away"]["pts"]) / 7.0 * 0.92))
    _stamp_field(pdocs, "player_1st_td", td_share, p_any_td * 0.93)       # ~7% of first TDs go to defense/special teams
    _stamp_field(pdocs, "player_last_td", td_share, p_any_td * 0.93)
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
    if wnote:
        factors.append({"side": None, "tone": "neg" if wtot <= -1 else "info",
                        "text": f"Weather at kickoff: {wnote}" + (f"; model total {wtot:+.1f}, passing x{pf:.2f}" if wtot or pf < 1 else "")})
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


MLB_PROPS = {"batter_hits": "h", "batter_total_bases": "tb", "batter_home_runs": "hr", "batter_singles": "s1", "batter_doubles": "s2",
             "batter_triples": "s3", "batter_walks": "bb", "batter_strikeouts": "k"}
MLB_MEAN_PROPS = {"batter_rbis": "rbi", "batter_runs_scored": "r", "batter_hits_runs_rbis": "hrr", "batter_stolen_bases": "sb"}


def _mlb(g, m, pdocs, inj, refs):
    pj = m.project_game(g["home"], g["away"], g["commence"])
    if not pj:
        return None
    H, A = pj["sides"]["home"], pj["sides"]["away"]

    both_sp = all(pj["sides"][s].get("sp") for s in ("home", "away"))

    def probs(l):
        mk = l["market"]
        if not both_sp:
            return None       # a starter is TBA: no model opinion on game lines until both are posted
        mi = re.search(r"_1st_(\d)_innings$", mk)
        period = f"f{mi.group(1)}" if mi else "full"
        base = re.sub(r"_1st_\d_innings$", "", mk).replace("alternate_", "")
        if base == "h2h":
            ph = m.prob(pj, "win", period)
            return {g["home"]: ph, g["away"]: 1 - ph}
        if base == "h2h_3_way":
            w, t, a = m.prob(pj, "win3", period)
            out = {g["home"]: w, g["away"]: a}
            for o in l["outcomes"]:
                if o not in out:
                    out[o] = t
            return out
        if base == "spreads" and l.get("point") is not None:
            ph = m.prob(pj, "cover", period, l["point"])
            return {g["home"]: ph, g["away"]: 1 - ph}
        if base == "totals" and l.get("point") is not None:
            po = m.prob(pj, "over", period, l["point"])
            return {"Over": po, "Under": 1 - po}
        if base == "team_totals" and period == "full" and l.get("point") is not None:
            side = "home" if R.norm(l.get("team") or "") == R.norm(g["home"]) else "away" if R.norm(l.get("team") or "") == R.norm(g["away"]) else None
            if side:
                po = m.prob(pj, "team_over", period, (side, l["point"]))
                return {"Over": po, "Under": 1 - po}
    lines = _stamp_lines(g, probs)
    hitters = H["lineup"] + A["lineup"]
    sps = {s: pj["sides"][s].get("sp") for s in ("home", "away")}
    for d in pdocs:
        mk = d["market"].replace("_alternate", "")
        for r in d["rows"]:
            if mk.startswith("pitcher_"):
                sp = next((x for x in sps.values() if x and R.norm(x.get("name") or "") == R.norm(r["player"])), None)
                if not sp or not sp.get("proj"):
                    continue
                logs = (m.people.get(str(sp["id"])) or {}).get("pit") or []
                key = {"pitcher_strikeouts": "k", "pitcher_outs": "outs", "pitcher_hits_allowed": "h", "pitcher_walks": "bb",
                       "pitcher_earned_runs": "er"}.get(mk)
                if key:
                    r["last10"] = [x.get(key) for x in logs if x.get("gs")][-10:]
                if mk == "pitcher_record_a_win":
                    side = "home" if sp is sps["home"] else "away"
                    pw = m.prob(pj, "win", "full")
                    pw = pw if side == "home" else 1 - pw
                    py = pw * _phi((sp["proj"]["outs"] - 14.5) / 3.4) * 0.88
                    r["model"] = {"mean": round(py, 3), "Yes": round(py, 4), "No": round(1 - py, 4)}
                    continue
                if not key or r.get("point") is None:
                    continue
                mean = sp["proj"][key]
                if mk == "pitcher_strikeouts":
                    po = MLBM.p_over_dist(sp["kDist"], r["point"])
                elif mk == "pitcher_outs":
                    po = 1 - _phi((r["point"] - mean) / 3.4)
                else:
                    po = B.p_over(mk, max(mean, 0.01), r["point"], "baseball_mlb")
                r["model"] = {"mean": mean, "raw": mean, "Over": round(po, 4), "Under": round(1 - po, 4)}
                continue
            b = _match_player(r["player"], hitters)
            if not b or not b.get("proj"):
                continue
            logs = (m.people.get(str(b["id"])) or {}).get("hit") or []
            stat = MLB_PROPS.get(mk) or MLB_MEAN_PROPS.get(mk)
            lk = {"s2": "d2", "s3": "d3"}.get(stat, stat)
            if stat:
                r["last10"] = [x.get(lk) for x in logs][-10:]
            if mk == "batter_home_runs" and r.get("point") is None or mk == "batter_first_home_run":
                if mk == "batter_home_runs":
                    py = 1 - b["dist"]["hr"][0]
                    r["model"] = {"mean": b["proj"]["hr"], "Yes": round(py, 4), "No": round(1 - py, 4)}
                continue
            if r.get("point") is None or not stat:
                continue
            mean = b["proj"][stat]
            po = MLBM.p_over_dist(b["dist"][stat], r["point"]) if stat in b["dist"] else B.p_over(mk, max(mean, 0.01), r["point"], "baseball_mlb")
            r["model"] = {"mean": round(mean, 2), "raw": round(mean, 2), "Over": round(po, 4), "Under": round(1 - po, 4)}

    hr_share = {b["name"]: b["proj"]["hr"] for b in hitters if b.get("proj")}
    p_any_hr = 1 - math.exp(-sum(hr_share.values()))
    _stamp_field(pdocs, "batter_first_home_run", hr_share, p_any_hr)

    def sp_card(side):
        sp = sps.get(side)
        if not sp:
            return None
        pid = str(sp["id"])
        sv = m.pit[m.year].get(pid) or m.pit[m.year - 1].get(pid) or {}
        logs = [x for x in ((m.people.get(pid) or {}).get("pit") or []) if x.get("gs")]
        rk = m.pit_rank.get(pid, {})
        return {"id": sp["id"], "name": sp["name"], "hand": sp.get("hand"), "headshot": MLBM.HEADSHOT.format(sp["id"]),
                "xera": sv.get("xera"), "era": sv.get("p_era"), "xwoba": sv.get("xwoba"), "k": sv.get("k_percent"), "bb": sv.get("bb_percent"),
                "whiff": sv.get("whiff_percent"), "barrel": sv.get("barrel_batted_rate"), "hardHit": sv.get("hard_hit_percent"),
                "gb": sv.get("groundballs_percent"), "velo": sv.get("ff_avg_speed"), "ip": sv.get("p_formatted_ip"), "rank": rk, "pool": m.pit_pool,
                "arsenal": (m.arsenal.get(pid) or [])[:6], "proj": sp.get("proj"),
                "starts": [{"date": x.get("date"), "opp": x.get("opp"), "ip": x.get("ip"), "k": x.get("k"), "er": x.get("er"), "h": x.get("h"),
                            "bb": x.get("bb"), "pitches": x.get("pitches")} for x in logs[-6:]][::-1],
                "last10": {"k": [x.get("k") for x in logs][-10:], "outs": [x.get("outs") for x in logs][-10:]}}

    def lineup_doc(side):
        S = pj["sides"][side]
        opp_sp = sps["away" if side == "home" else "home"] or {}
        order = []
        for i, b in enumerate(S["lineup"]):
            sv = m.bat[m.year].get(str(b["id"])) or {}
            logs = (m.people.get(str(b["id"])) or {}).get("hit") or []
            order.append({"slot": i + 1, "id": b["id"], "name": b["name"], "pos": b.get("pos"), "bats": b.get("bats"),
                          "headshot": MLBM.HEADSHOT.format(b["id"]), "xwoba": sv.get("xwoba"), "barrel": sv.get("barrel_batted_rate"),
                          "plat": b.get("platX"), "proj": b.get("proj"), "pHr": round(1 - b["dist"]["hr"][0], 3) if b.get("dist") else None,
                          "pHit": round(1 - b["dist"]["h"][0], 3) if b.get("dist") else None,
                          "last10": {"tb": [x.get("tb") for x in logs][-10:], "h": [x.get("h") for x in logs][-10:]}})
        return {"kind": "mlb", "confirmed": S["confirmed"], "order": order, "vsHand": opp_sp.get("hand"),
                "how": "posted lineup" if S["confirmed"] else "projected from the team's last posted lineup; confirm before first pitch"}

    def lineup_xwoba(side):
        rows = pj["sides"][side]["lineup"]
        v = [(m.bat[m.year].get(str(b["id"])) or {}).get("xwoba") for b in rows]
        v = [x * (b.get("platX") or 1) for x, b in zip(v, rows) if x]
        return round(sum(v) / len(v), 3) if v else None

    def lineup_k(side):
        rows = pj["sides"][side]["lineup"]
        v = [b["vsSp"]["k"] for b in rows if b.get("vsSp")]
        return round(sum(v) / len(v), 3) if v else None
    sh, sa = sp_card("home"), sp_card("away")
    f = lambda c, k: (c or {}).get(k)
    pc = lambda v: None if v is None else round(v / 100, 3)
    rk = lambda c, k: (c or {}).get("rank", {}).get(k)
    pool = m.pit_pool
    compare = [
        {"k": "Starter xERA", "h": f(sh, "xera"), "a": f(sa, "xera"), "hr": rk(sh, "xera"), "ar": rk(sa, "xera"), "hi": False, "d": 2, "n": pool},
        {"k": "Starter xwOBA allowed", "h": f(sh, "xwoba"), "a": f(sa, "xwoba"), "hr": rk(sh, "xwoba"), "ar": rk(sa, "xwoba"), "hi": False, "d": 3, "n": pool},
        {"k": "Starter strikeout rate", "h": pc(f(sh, "k")), "a": pc(f(sa, "k")), "hr": rk(sh, "k_percent"), "ar": rk(sa, "k_percent"), "hi": True, "pct": True, "n": pool},
        {"k": "Starter walk rate", "h": pc(f(sh, "bb")), "a": pc(f(sa, "bb")), "hr": rk(sh, "bb_percent"), "ar": rk(sa, "bb_percent"), "hi": False, "pct": True, "n": pool},
        {"k": "Starter whiff rate", "h": pc(f(sh, "whiff")), "a": pc(f(sa, "whiff")), "hr": rk(sh, "whiff_percent"), "ar": rk(sa, "whiff_percent"), "hi": True, "pct": True, "n": pool},
        {"k": "Starter barrel rate allowed", "h": pc(f(sh, "barrel")), "a": pc(f(sa, "barrel")), "hr": rk(sh, "barrel_batted_rate"), "ar": rk(sa, "barrel_batted_rate"), "hi": False, "pct": True, "n": pool},
        {"k": "Lineup xwOBA (vs this hand)", "h": lineup_xwoba("home"), "a": lineup_xwoba("away"), "hi": True, "d": 3},
        {"k": "Lineup K rate vs this starter", "h": lineup_k("home"), "a": lineup_k("away"), "hi": False, "pct": True},
        {"k": "Bullpen / staff ERA", "h": (m.team_pit.get(H["team"]["name"]) or {}).get("era"),
         "a": (m.team_pit.get(A["team"]["name"]) or {}).get("era"), "hi": False, "d": 2},
    ]
    factors = []
    for side, c in (("away", sa), ("home", sh)):
        if c and c.get("proj"):
            ars = ", ".join(f"{a['name']} {a['usage']:.0f}%" for a in c["arsenal"][:3] if a.get("usage"))
            factors.append({"side": side, "tone": "info", "text": f"{c['name']} ({c.get('hand') or '?'}HP): xERA {c['xera'] or '–'}, projected {c['proj']['k']} K over "
                            f"{c['proj']['outs'] / 3:.1f} innings" + (f"; mix {ars}" if ars else "")})
        elif not c:
            factors.append({"side": side, "tone": "neg", "text": "No probable starter posted yet: game-line model held back until both starters are known (props still use a league-average arm)"})
    pk = pj.get("park") or {}
    if pk:
        factors.append({"side": None, "tone": "pos" if (pk.get("runs") or 100) >= 104 else "neg" if (pk.get("runs") or 100) <= 96 else "info",
                        "text": f"{pj['venue']}: park runs index {pk.get('runs') or '–'}, home runs {pk.get('hr') or '–'} (100 = average, Statcast 3-year)"})
    if pj.get("wxNote"):
        factors.append({"side": None, "tone": "info", "text": f"Weather: {pj['wxNote']}; home-run factor x{pj['wx']:.2f}"})
    for side in ("away", "home"):
        if not pj["sides"][side]["confirmed"]:
            factors.append({"side": side, "tone": "info", "text": "Lineup not posted yet: projected from the last posted lineup"})
    factors.append({"side": None, "tone": "info", "text": f"Model runs {A['runs']:.2f}-{H['runs']:.2f} (first five {A['runsF5']:.2f}-{H['runsF5']:.2f}), "
                    f"home win {pj['pHome']:.0%}"})

    def batters(side):
        out = []
        for b in pj["sides"][side]["lineup"]:
            logs = (m.people.get(str(b["id"])) or {}).get("hit") or []
            out.append({"name": b["name"], "pos": b.get("pos"), "status": None, "out": False, "min": b["proj"]["pa"] if b.get("proj") else None,
                        "headshot": MLBM.HEADSHOT.format(b["id"]), "proj": b.get("proj") or {},
                        "last10": {k: [x.get(k) for x in logs][-10:] for k in ("pa", "h", "tb", "hr", "r", "rbi", "bb", "k", "sb")}})
        return out
    rec = lambda side: "{wins}-{losses}".format(**pj["sides"][side]["record"]) if pj["sides"][side].get("record") else None
    return {"id": g["id"], "league": "MLB", "home": g["home"], "away": g["away"], "commence": g["commence"],
            "model": {"pHome": round(pj["pHome"], 4), "total": pj["total"], "margin": pj["margin"], "lines": lines},
            "teams": {"home": {"abbr": (g.get("homeRef") or {}).get("abbr"), "record": rec("home"), "runs": round(H["runs"], 2),
                               "injuries": _injuries(inj.get("mlb"), (g.get("homeRef") or {}).get("abbr"), g["home"])},
                      "away": {"abbr": (g.get("awayRef") or {}).get("abbr"), "record": rec("away"), "runs": round(A["runs"], 2),
                               "injuries": _injuries(inj.get("mlb"), (g.get("awayRef") or {}).get("abbr"), g["away"])}},
            "starters": {"home": sh, "away": sa}, "park": pk, "weather": pj.get("weather"), "series": pj.get("series"),
            "compare": compare, "factors": factors, "propEdges": [], "statKeys": ["h", "tb", "hr", "k"], "statLabels": ["H", "TB", "HR", "K"],
            "players": {"home": batters("home"), "away": batters("away")},
            "lineups": {"home": lineup_doc("home"), "away": lineup_doc("away")}}
