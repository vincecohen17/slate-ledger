"""Walk-forward backtests: replay our models on past games using only what was known the morning of each game.

For every game in history/ (closing sharp fairs from The Odds API):
  * the model is rebuilt from box scores (and MoneyPuck / nflverse) dated before that day; availability comes from who
    actually played (known at the close), NHL starting goalies likewise;
  * our probability for each closing line and main prop is compared with the closing fair and the result;
  * rows go to track/graded/bt_<sport>.jsonl, which the tracker fits (calibration a, b and model trust w per market),
    and calib/backtest.json summarizes how the model did against the close.
"""
import gzip
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timedelta

import history as H
import matchup as MU
import model_cfb
import model_nba
import model_nfl
import model_nhl
import refs as R
import stats as ST
import tracker as T

SEASONS = {   # sport: [(season, prior season, league folder, model year)]
    "basketball_nba": [("2025-26", "2024-25", "nba", None), ("2026-27", "2025-26", "nba", None)],
    "icehockey_nhl": [("2025-26", "2024-25", "nhl", 2025), ("2026-27", "2025-26", "nhl", 2026)],
    "americanfootball_nfl": [("2025", "2024", "nfl", 2025), ("2026", "2025", "nfl", 2026)],
    "americanfootball_ncaaf": [("2025", "2024", "cfb", None), ("2026", "2025", "cfb", None)],
}


def _match(espn_day_games, hg):
    """ESPN game for a history game (team names)"""
    nh, na = R.norm(hg["home"]), R.norm(hg["away"])
    for g in espn_day_games:
        eh, ea = R.norm(g["home"].get("name") or g["home"]["abbr"]), R.norm(g["away"].get("name") or g["away"]["abbr"])
        if (eh == nh and ea == na) or (eh[-6:] == nh[-6:] and ea[-6:] == na[-6:]):
            return g
    return None


def _line(l, hg):
    outs = list(l["fair"].keys())
    return {"market": l["m"], "point": l.get("pt"), "team": l.get("team"), "outcomes": outs, "fair": l["fair"]}


def _side(line, home):
    outs = line["outcomes"]
    if "Over" in outs:
        return "Over"
    if "Yes" in outs:
        return "Yes"
    return home if home in outs and len(outs) == 2 else None


def _grade_line(l, hs, as_, home, away):
    base = l["market"].replace("alternate_", "")
    pt = l.get("point")
    if base == "h2h":
        return None if hs == as_ else float(hs > as_)
    if base == "spreads":
        d = hs + pt - as_
        return None if d == 0 else float(d > 0)
    if base == "totals":
        return None if hs + as_ == pt else float(hs + as_ > pt)
    if base == "team_totals":
        sc = hs if R.norm(l.get("team")) == R.norm(home) else as_ if R.norm(l.get("team")) == R.norm(away) else None
        return None if sc is None or sc == pt else float(sc > pt)
    return None


def run_sport(data_dir, sport):
    rows = []
    for season, prior, lg, year in SEASONS[sport]:
        hist = H.load(data_dir, sport, season)
        if not hist:
            continue
        cur_all = sorted(ST.load_games(lg, season, data_dir), key=lambda g: g["date"])
        pri = ST.load_games(lg, prior, data_dir)
        by_day = defaultdict(list)
        for g in cur_all:
            d = (datetime.fromisoformat(g["date"].replace("Z", "+00:00")) - timedelta(hours=7)).strftime("%Y-%m-%d")
            by_day[d].append(g)
        for day in sorted(hist):
            cut = f"{day}T15:00Z"
            cg = [g for g in cur_all if g["date"] < cut]
            if sport.startswith("americanfootball_ncaaf") and len(cg) < 30:
                continue
            espn_today = by_day.get(day, []) + by_day.get((datetime.fromisoformat(day) + timedelta(days=1)).strftime("%Y-%m-%d"), [])
            try:
                model = _build(sport, data_dir, lg, cg, pri, year, day)
            except Exception as e:
                print(f"backtest {sport} {day} model failed: {type(e).__name__}: {str(e)[:100]}")
                continue
            for hg in hist[day]:
                eg = _match(espn_today, hg)
                if not eg or eg["home"].get("score") is None:
                    continue
                try:
                    rows += _game_rows(sport, model, hg, eg, day, data_dir, lg)
                except Exception as e:
                    print(f"backtest {sport} {day} game failed: {type(e).__name__}: {str(e)[:100]}")
    ST.MP_ASOF = None
    return rows


def _build(sport, data_dir, lg, cg, pri, year, day):
    if sport == "basketball_nba":
        return model_nba.NBA(cg, pri, {}, day)
    if sport == "icehockey_nhl":
        ST.MP_ASOF = day
        m = model_nhl.NHL(data_dir, cg, pri, {}, cur_year=year)
        return m
    if sport == "americanfootball_nfl":
        return ("nfl", year, cg)          # built per game (needs the game's week)
    if sport == "americanfootball_ncaaf":
        return model_cfb.CFB(cg, pri)


def _nfl_week(data_dir, year, home_abbr, away_abbr):
    tw = ST.load_json(data_dir, f"stats/nfl/nflverse/team_week_{year}.json") or []
    for r in tw:
        if ST.NFL2ESPN.get(r["team"], r["team"]) == home_abbr and ST.NFL2ESPN.get(r["opp"], r["opp"]) == away_abbr:
            return int(r["week"])
    return None


def _game_rows(sport, model, hg, eg, day, data_dir, lg):
    home, away = hg["home"], hg["away"]
    ha, aa = eg["home"]["abbr"], eg["away"]["abbr"]
    hs, as_ = eg["home"]["score"], eg["away"]["score"]
    box = {R.norm(p["name"]): p for p in eg.get("players", [])}
    played = {R.norm(p["name"]) for p in eg.get("players", []) if not p.get("dnp") and (p.get("minutes") or p.get("timeOnIce") or len(p) > 6)}
    g = {"home": home, "away": away, "sport": sport, "commence": hg["commence"]}
    sides, probs_fn, prop_markets, goalie_fn = None, None, {}, None
    if sport == "basketball_nba":
        m = model
        # availability: players on either roster who did not play this game are out
        outs = {}
        for abbr in (MU._abbr({"abbr": ha}, m.teams), MU._abbr({"abbr": aa}, m.teams)):
            lst = []
            for pid, logs in m.logs.items():
                if logs and logs[-1]["team"] == abbr and R.norm(logs[-1]["name"]) not in played:
                    lst.append({"id": pid, "name": logs[-1]["name"], "status": "Out"})
            outs[abbr] = {"abbr": abbr, "list": lst}
        m.inj = outs
        pj = m.project_game(MU._abbr({"abbr": ha}, m.teams), MU._abbr({"abbr": aa}, m.teams), hg["commence"])
        if not pj:
            return []
        probs_fn = MU.normal_probs(g, pj["margin"], pj["total"], m.SD_MARGIN, m.SD_TOTAL)
        sides, prop_markets = pj["sides"], model_nba.MARKETS
    elif sport == "icehockey_nhl":
        m = model
        starters = {}
        for p in eg.get("players", []):
            if p.get("goalie") and (p.get("timeOnIce") or 0) >= 30:
                starters.setdefault(p["team"], p["name"])
        m.goalie_override = starters
        pj = m.project_game(MU._abbr({"abbr": ha}, m.teams), MU._abbr({"abbr": aa}, m.teams), hg["commence"])
        if not pj:
            return []
        probs_fn = MU.hockey_probs(g, m, pj)
        sides, prop_markets = pj["sides"], model_nhl.MARKETS

        def goalie_fn(name):
            for s_ in ("home", "away"):
                gl = pj["sides"][s_]["goalie"]
                if gl.get("name") and R.norm(gl["name"]) == R.norm(name):
                    return gl
            return None
    elif sport == "americanfootball_nfl":
        _, year, cg = model
        wk = _nfl_week(data_dir, year, ha, aa)
        if not wk:
            return []
        m = model_nfl.NFL(data_dir, year, cg, {}, asof_week=wk)
        played_nfl = {(ST.NFL2ESPN.get(r["team"], r["team"]), r.get("player_display_name")) for r in m.week_rows}
        m.inj = {t: {"abbr": t, "list": []} for t in (ha, aa)}
        for pid, rows in m.plog.items():
            if rows and rows[-1]["team"] in (ha, aa) and (rows[-1]["team"], rows[-1].get("player_display_name")) not in played_nfl:
                m.inj[rows[-1]["team"]]["list"].append({"name": m._player_rates(rows)["name"] if m._player_rates(rows) else "", "status": "Out"})
        pj = m.project_game(MU._abbr({"abbr": ha}, m.teams), MU._abbr({"abbr": aa}, m.teams))
        if not pj:
            return []
        probs_fn = MU.normal_probs(g, pj["margin"], pj["total"], m.SD_MARGIN, m.SD_TOTAL)
        sides, prop_markets = pj["sides"], model_nfl.MARKETS
    elif sport == "americanfootball_ncaaf":
        m = model
        pj = m.project_game(MU._abbr({"abbr": ha}, m.teams), MU._abbr({"abbr": aa}, m.teams))
        if not pj:
            return []
        probs_fn = MU.normal_probs(g, pj["margin"], pj["total"], m.SD_MARGIN, m.SD_TOTAL)
    out = []
    for l0 in hg["lines"]:
        l = _line(l0, hg)
        side = _side(l, home)
        if not side or (l["market"].replace("alternate_", "") not in ("h2h", "spreads", "totals", "team_totals")):
            continue
        p = (probs_fn(l) or {}).get(side) if probs_fn else None
        y = _grade_line(l, hs, as_, home, away)
        if p is None or y is None or side not in l["fair"]:
            continue
        out.append({"d": day, "g": T.group(sport, "line", l["market"]), "p": round(p, 4), "f": l["fair"][side], "y": y, "src": "bt",
                    "main": bool(l0.get("main"))})
    if sides and prop_markets:
        docs = defaultdict(list)
        for pr in hg["props"]:
            docs[pr["m"]].append({"player": pr["player"], "point": pr.get("pt"), "fair": pr["fair"], "main": pr.get("main")})
        pdocs = [{"market": mk, "rows": rows} for mk, rows in docs.items()]
        MU._stamp_props(pdocs, sides, prop_markets, sport, goalie_fn)
        for d in pdocs:
            for r in d["rows"]:
                mdl = r.get("model") or {}
                side = "Over" if "Over" in mdl else "Yes" if "Yes" in mdl else None
                if not side or side not in r["fair"]:
                    continue
                p_ = box.get(R.norm(r["player"]))
                if not p_ or p_.get("dnp"):
                    continue
                v = T.stat(sport, d["market"], p_)
                if v is None:
                    continue
                if side == "Yes":
                    y = float(v >= 1)
                elif r.get("point") is None or v == r["point"]:
                    continue
                else:
                    y = float(v > r["point"])
                out.append({"d": day, "g": T.group(sport, "prop", d["market"]), "p": mdl[side], "f": r["fair"][side], "y": y, "src": "bt",
                            "main": bool(r.get("main")), "mu": mdl.get("mean"), "v": v, "pt": r.get("point")})
    return out


def summarize(rows):
    """per market: log loss of model / market, and how bets would have done where the model disagreed with the close"""
    by = defaultdict(list)
    for r in rows:
        by[r["g"]].append(r)
    ll = lambda p, y: -(y * math.log(max(1e-6, p)) + (1 - y) * math.log(max(1e-6, 1 - p)))
    out = []
    for gk, rs in by.items():
        n = len(rs)
        mm = sum(ll(r["p"], r["y"]) for r in rs) / n
        mk = sum(ll(r["f"], r["y"]) for r in rs) / n
        # "model edge" bets at the closing fair: take the side the model likes by 4+ points
        bets = []
        for r in rs:
            if r["p"] - r["f"] >= 0.04:
                bets.append((r["f"], r["y"]))
            elif r["f"] - r["p"] >= 0.04:
                bets.append((1 - r["f"], 1 - r["y"]))
        won = sum(y for _, y in bets)
        exp = sum(f for f, _ in bets)
        roi = sum((y / f - 1) for f, y in bets) / len(bets) if bets else None
        out.append({"group": gk, "n": n, "llModel": round(mm, 4), "llMarket": round(mk, 4), "bets": len(bets),
                    "won": won, "expected": round(exp, 1), "roiAtClose": None if roi is None else round(roi, 4)})
    out.sort(key=lambda r: -r["n"])
    return out


def main(data_dir):
    os.makedirs(os.path.join(data_dir, "track", "graded"), exist_ok=True)
    allrows = []
    for sport in SEASONS:
        try:
            rows = run_sport(data_dir, sport)
        except Exception as e:
            print(f"backtest {sport} failed: {type(e).__name__}: {str(e)[:120]}")
            continue
        old = os.path.join(data_dir, "track", "graded", f"bt_{sport}.jsonl")
        if os.path.exists(old):
            os.remove(old)
        with gzip.open(old + ".gz", "wt") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        allrows += [dict(r, sport=sport) for r in rows]
        print(f"backtest {sport}: {len(rows)} graded rows")
    summary = summarize(allrows)
    json.dump({"at": datetime.utcnow().isoformat() + "Z", "rows": len(allrows), "markets": summary},
              open(os.path.join(data_dir, "calib", "backtest.json") if os.path.isdir(os.path.join(data_dir, "calib"))
                   else os.path.join(data_dir, "track", "backtest.json"), "w"), indent=0)
    params, card = T.fit(data_dir)
    print(f"backtest: {len(allrows)} rows, {len(params)} calibrated groups")
    return summary
