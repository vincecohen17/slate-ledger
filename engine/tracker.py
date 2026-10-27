"""Model report card and self-tuning.

Every run snapshots our model's probability and the sharp fair for each line and prop of games that have not
started (the last snapshot before first pitch / puck drop / tip is the closing view). After games end, the
snapshots are graded against box scores. Per market (e.g. NBA player points, NHL totals) we then fit:
  * a calibration of the model:  P = sigmoid(a + b * logit(model))   (b < 1 = the model was too confident)
  * the blend weight w of model vs market that predicted best:  sigmoid(w * logit(model_cal) + (1 - w) * logit(fair))
The calibration is applied to every model probability on the board, so 'logic agrees' tightens as results come in,
and the report card on the Reports page shows how the model is doing against the closing market.

Files in the data repo:
  track/open/<gameId>.json      latest pre-game snapshot
  track/graded/<sport>.jsonl    one row per graded line: group, model p, fair p, result
  calib/params.json             fitted a, b, w per group (plus pooled fallbacks)
"""
import glob
import gzip
import json
import math
import os
from datetime import datetime, timedelta, timezone

import stats as ST

MIN_GROUP, MIN_POOL = 150, 400
LG = {"basketball_nba": "nba", "basketball_wnba": "wnba", "icehockey_nhl": "nhl", "americanfootball_nfl": "nfl",
      "americanfootball_ncaaf": "cfb", **{v[1]: k for k, v in ST.SOCCER.items()}}
ALIAS = {"UTAH": "UTA", "GSW": "GS", "NOP": "NO", "SAS": "SA", "NYK": "NY", "WAS": "WSH", "UTH": "UTA"}


def _lg(p):
    return math.log(min(0.999, max(0.001, p)) / (1 - min(0.999, max(0.001, p))))


def _sig(x):
    return 1 / (1 + math.exp(-max(-30, min(30, x))))


def _norm(s):
    import refs as R
    return R.norm(s or "")


def _split(mk):
    import matchup as MU
    base, per = MU.split_market(mk)
    return base + (f"_{per}" if per else "")


def group(sport, kind, market):
    return f"{sport}|{kind}|{_split(market) if kind == 'line' else market.replace('_alternate', '')}"


def _canon_line(g, l):
    """the side we track for a game line, or None (3-way and odd markets are skipped)"""
    base = _split(l["market"]).split("_")[0]
    outs = l.get("outcomes") or []
    if len(outs) != 2:
        return None
    if "Over" in outs:
        return "Over"
    if "Yes" in outs:
        return "Yes"
    if g["home"] in outs and base in ("h2h", "spreads"):
        return g["home"]
    return None


# ---------------------------------------------------------------------------
# snapshot

def snapshot(data_dir, game_docs, prop_docs, now):
    d = os.path.join(data_dir, "track", "open")
    os.makedirs(d, exist_ok=True)
    props = {}
    for p in prop_docs:
        props.setdefault(p["id"], []).append(p)
    n = 0
    for g in game_docs:
        if datetime.fromisoformat(g["commence"].replace("Z", "+00:00")) <= now:
            continue        # started: keep the last pre-game snapshot
        rows = []
        for l in g["lines"]:
            side = _canon_line(g, l)
            m, f = l.get("model") or {}, l.get("fair") or {}
            pm = m.get("_raw", {}).get(side) if isinstance(m.get("_raw"), dict) else m.get(side)
            # main lines are kept even without a model number: the ledger needs every closing fair
            if side and f.get(side) is not None and (pm is not None or l.get("isMain")):
                rows.append({"g": group(g["sport"], "line", l["market"]), "mk": l["market"], "pt": l.get("point"), "team": l.get("team"),
                             "side": side, "p": round(pm, 4) if pm is not None else None, "f": f[side], "w": l.get("fairWeight")})
        for doc in props.get(g["id"], []):
            for r in doc["rows"]:
                m, f = r.get("model") or {}, r.get("fair") or {}
                side = "Over" if "Over" in m else "Yes" if "Yes" in m else None
                if not side and r.get("isMain") and "Over" in f:
                    side = "Over"
                pm = (m.get("_raw") or {}).get(side, m.get(side)) if side else None
                if side and f.get(side) is not None and (pm is not None or r.get("isMain")):
                    rows.append({"g": group(g["sport"], "prop", doc["market"]), "mk": doc["market"], "pt": r.get("point"),
                                 "player": r["player"], "side": side, "p": round(pm, 4) if pm is not None else None, "f": f[side], "w": r.get("fairWeight")})
        if not rows:
            continue
        snap = {"id": g["id"], "sport": g["sport"], "commence": g["commence"], "home": g["home"], "away": g["away"],
                "ha": (g.get("homeRef") or {}).get("abbr"), "aa": (g.get("awayRef") or {}).get("abbr"), "rows": rows}
        fn = os.path.join(d, f"{g['id']}.json")
        s = json.dumps(snap, separators=(",", ":"), sort_keys=True)
        try:
            if open(fn).read() == s:
                continue
        except Exception:
            pass
        open(fn, "w").write(s)
        n += 1
    return n


# ---------------------------------------------------------------------------
# results

def _espn_game(data_dir, snap):
    lg = LG.get(snap["sport"])
    if not lg:
        return None
    t = datetime.fromisoformat(snap["commence"].replace("Z", "+00:00")) - timedelta(hours=7)
    want = {ALIAS.get(snap.get("ha"), snap.get("ha")), ALIAS.get(snap.get("aa"), snap.get("aa"))}
    for dd in (0, -1, 1):
        day = (t + timedelta(days=dd)).strftime("%Y-%m-%d")
        for fn in glob.glob(os.path.join(data_dir, "stats", lg, "*", f"{day}.json")):
            try:
                games = json.load(open(fn))["games"]
            except Exception:
                continue
            for g in games:
                have = {ALIAS.get(g["home"]["abbr"], g["home"]["abbr"]), ALIAS.get(g["away"]["abbr"], g["away"]["abbr"])}
                if have == want and g["home"].get("score") is not None:
                    swap = ALIAS.get(g["home"]["abbr"], g["home"]["abbr"]) != ALIAS.get(snap.get("ha"), snap.get("ha"))
                    hs, as_ = g["home"]["score"], g["away"]["score"]
                    if swap:
                        hs, as_ = as_, hs
                    return {"hs": hs, "as": as_, "players": {_norm(p["name"]): p for p in g.get("players", [])}}
    return None


def _mlb_game(data_dir, snap):
    sched = ST.load_json(data_dir, "stats/mlb/schedule.json") or {}
    people = ST.load_json(data_dir, "stats/mlb/people.json") or {}
    t = datetime.fromisoformat(snap["commence"].replace("Z", "+00:00"))
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            th, ta = g["teams"]["home"], g["teams"]["away"]
            if _norm(th["team"]["name"]) != _norm(snap["home"]) or _norm(ta["team"]["name"]) != _norm(snap["away"]):
                continue
            if abs((datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")) - t).total_seconds()) > 12 * 3600:
                continue
            day = g.get("officialDate")
            players = {}
            for p in people.values():
                for kind in ("hit", "pit"):
                    for r in p.get(kind) or []:
                        if r.get("date") == day:
                            players.setdefault(_norm(p["name"]), {}).update({("p_" if kind == "pit" else "b_") + k: v for k, v in r.items()})
            return {"hs": th.get("score"), "as": ta.get("score"), "players": players}
    return None


def _v(p, k, i=None):
    v = p.get(k)
    if isinstance(v, list):
        v = v[i or 0]
    return float(v) if v is not None else None


def _sum(p, *ks):
    vals = [_v(p, k) for k in ks]
    return None if all(v is None for v in vals) else sum(v or 0 for v in vals)


def stat(sport, market, p):
    m = market.replace("_alternate", "")
    if sport in ("basketball_nba", "basketball_wnba"):
        three = lambda: _v(p, "threePointFieldGoalsMade-threePointFieldGoalsAttempted", 0)
        tens = lambda: sum(1 for k in ("points", "rebounds", "assists", "steals", "blocks") if (_v(p, k) or 0) >= 10)
        return {"player_points": lambda: _v(p, "points"), "player_rebounds": lambda: _v(p, "rebounds"), "player_assists": lambda: _v(p, "assists"),
                "player_threes": three, "player_blocks": lambda: _v(p, "blocks"), "player_steals": lambda: _v(p, "steals"),
                "player_turnovers": lambda: _v(p, "turnovers"), "player_points_rebounds_assists": lambda: _sum(p, "points", "rebounds", "assists"),
                "player_points_rebounds": lambda: _sum(p, "points", "rebounds"), "player_points_assists": lambda: _sum(p, "points", "assists"),
                "player_rebounds_assists": lambda: _sum(p, "rebounds", "assists"), "player_blocks_steals": lambda: _sum(p, "blocks", "steals"),
                "player_double_double": lambda: 1.0 if tens() >= 2 else 0.0, "player_triple_double": lambda: 1.0 if tens() >= 3 else 0.0,
                "player_fantasy_points": lambda: _sum(p, "points") + 1.2 * (_v(p, "rebounds") or 0) + 1.5 * (_v(p, "assists") or 0)
                + 3 * (_v(p, "blocks") or 0) + 3 * (_v(p, "steals") or 0) - (_v(p, "turnovers") or 0)}.get(m, lambda: None)()
    if sport == "icehockey_nhl":
        return {"player_points": lambda: _sum(p, "goals", "assists"), "player_goals": lambda: _v(p, "goals"), "player_assists": lambda: _v(p, "assists"),
                "player_shots_on_goal": lambda: _v(p, "shotsTotal"), "player_blocked_shots": lambda: _v(p, "blockedShots"),
                "player_total_saves": lambda: _v(p, "saves"), "player_goal_scorer_anytime": lambda: _v(p, "goals"),
                "player_faceoffs_won": lambda: _v(p, "faceoffsWon")}.get(m, lambda: None)()
    if sport in ("americanfootball_nfl", "americanfootball_ncaaf"):
        tds = lambda: _sum(p, "rushing.rushingTouchdowns", "receiving.receivingTouchdowns")
        return {"player_pass_yds": lambda: _v(p, "passing.passingYards"), "player_pass_tds": lambda: _v(p, "passing.passingTouchdowns"),
                "player_pass_completions": lambda: _v(p, "passing.completions/passingAttempts", 0),
                "player_pass_attempts": lambda: _v(p, "passing.completions/passingAttempts", 1),
                "player_pass_interceptions": lambda: _v(p, "passing.interceptions"), "player_rush_yds": lambda: _v(p, "rushing.rushingYards"),
                "player_rush_attempts": lambda: _v(p, "rushing.rushingAttempts"), "player_receptions": lambda: _v(p, "receiving.receptions"),
                "player_reception_yds": lambda: _v(p, "receiving.receivingYards"),
                "player_rush_reception_yds": lambda: _sum(p, "rushing.rushingYards", "receiving.receivingYards"),
                "player_pass_rush_yds": lambda: _sum(p, "passing.passingYards", "rushing.rushingYards"),
                "player_pass_rush_reception_yds": lambda: _sum(p, "passing.passingYards", "rushing.rushingYards", "receiving.receivingYards"),
                "player_anytime_td": tds, "player_tds_over": tds, "player_rush_reception_tds": tds,
                "player_rush_tds": lambda: _v(p, "rushing.rushingTouchdowns"), "player_reception_tds": lambda: _v(p, "receiving.receivingTouchdowns"),
                "player_rush_longest": lambda: _v(p, "rushing.longRushing"), "player_reception_longest": lambda: _v(p, "receiving.longReception"),
                "player_sacks": lambda: _v(p, "defensive.sacks"), "player_tackles_assists": lambda: _v(p, "defensive.totalTackles"),
                "player_solo_tackles": lambda: _v(p, "defensive.soloTackles"), "player_kicking_points": lambda: _v(p, "kicking.totalKickingPoints"),
                "player_field_goals": lambda: _v(p, "kicking.fieldGoalsMade/fieldGoalAttempts", 0),
                "player_pats": lambda: _v(p, "kicking.extraPointsMade/extraPointAttempts", 0)}.get(m, lambda: None)()
    if sport == "baseball_mlb":
        if m.startswith("batter_"):
            k = {"hits": "h", "total_bases": "tb", "home_runs": "hr", "rbis": "rbi", "runs_scored": "r", "hits_runs_rbis": "hrr", "singles": "s1",
                 "doubles": "d2", "triples": "d3", "walks": "bb", "strikeouts": "k", "stolen_bases": "sb"}.get(m[7:])
            return _v(p, "b_" + k) if k else None
        k = {"pitcher_strikeouts": "k", "pitcher_outs": "outs", "pitcher_hits_allowed": "h", "pitcher_walks": "bb", "pitcher_earned_runs": "er"}.get(m)
        return _v(p, "p_" + k) if k else None
    return None


def _grade_line(snap, row, res):
    hs, as_ = res["hs"], res["as"]
    base = row["mk"].replace("alternate_", "")
    if "_q" in base or "_h" in base[-3:] or "_p" in base[-3:] or "innings" in base:
        return None          # period results are not stored yet
    pt = row.get("pt")
    if base == "h2h":
        return None if hs == as_ else float(hs > as_)
    if base == "spreads":
        d = hs + pt - as_
        return None if d == 0 else float(d > 0)
    if base == "totals":
        return None if hs + as_ == pt else float(hs + as_ > pt)
    if base == "team_totals":
        sc = hs if _norm(row.get("team")) == _norm(snap["home"]) else as_ if _norm(row.get("team")) == _norm(snap["away"]) else None
        return None if sc is None or sc == pt else float(sc > pt)
    return None


def grade(data_dir, now):
    """grade finished games; returns number of rows graded"""
    n = 0
    for fn in glob.glob(os.path.join(data_dir, "track", "open", "*.json")):
        try:
            snap = json.load(open(fn))
        except Exception:
            os.remove(fn)
            continue
        start = datetime.fromisoformat(snap["commence"].replace("Z", "+00:00"))
        if now - start < timedelta(hours=4):
            continue
        res = _mlb_game(data_dir, snap) if snap["sport"] == "baseball_mlb" else _espn_game(data_dir, snap)
        if not res:
            if now - start > timedelta(days=3):
                os.remove(fn)       # never found a result: drop it
            continue
        out = []
        for r in snap["rows"]:
            if r.get("p") is None:
                continue        # kept only for the ledger's closing line value
            if "player" in r:
                p = res["players"].get(_norm(r["player"]))
                if not p or p.get("dnp"):
                    continue
                v = stat(snap["sport"], r["mk"], p)
                if v is None:
                    continue
                if r["side"] == "Yes":
                    y = float(v >= (1 if r.get("pt") is None else r["pt"] + 0.5))
                elif r.get("pt") is None or v == r["pt"]:
                    continue
                else:
                    y = float(v > r["pt"])
            else:
                y = _grade_line(snap, r, res)
                if y is None:
                    continue
                if r["side"] not in ("Over", "Yes") and r["side"] != snap["home"]:
                    continue
            out.append({"d": snap["commence"][:10], "g": r["g"], "p": r["p"], "f": r["f"], "y": y})
        if out:
            gd = os.path.join(data_dir, "track", "graded")
            os.makedirs(gd, exist_ok=True)
            with open(os.path.join(gd, f"{snap['sport']}.jsonl"), "a") as f:
                for o in out:
                    f.write(json.dumps(o, separators=(",", ":")) + "\n")
            n += len(out)
        # keep the closing snapshot (sharp fair at the close) for the ledger's closing line value, three weeks
        cd = os.path.join(data_dir, "track", "closed")
        os.makedirs(cd, exist_ok=True)
        snap["result"] = {"hs": res["hs"], "as": res["as"]}
        json.dump(snap, open(os.path.join(cd, os.path.basename(fn)), "w"), separators=(",", ":"))
        os.remove(fn)
    cut = (now - timedelta(days=21)).timestamp()
    for fn in glob.glob(os.path.join(data_dir, "track", "closed", "*.json")):
        if os.path.getmtime(fn) < cut:
            os.remove(fn)
    return n


# ---------------------------------------------------------------------------
# fit

def _ll(ps, ys):
    return -sum(y * math.log(max(1e-6, p)) + (1 - y) * math.log(max(1e-6, 1 - p)) for p, y in zip(ps, ys)) / max(1, len(ys))


def _fit_ab(xs, ys, lam=20.0):
    """ridge logistic: y ~ sigmoid(a + b x), shrunk toward a=0, b=1"""
    a, b = 0.0, 1.0
    for _ in range(25):
        ga = gb = haa = hab = hbb = 0.0
        for x, y in zip(xs, ys):
            p = _sig(a + b * x)
            r, w = p - y, p * (1 - p)
            ga += r; gb += r * x
            haa += w; hab += w * x; hbb += w * x * x
        ga += lam * a; gb += lam * (b - 1); haa += lam; hbb += lam
        det = haa * hbb - hab * hab
        if abs(det) < 1e-9:
            break
        da, db = (hbb * ga - hab * gb) / det, (haa * gb - hab * ga) / det
        a, b = a - da, b - db
        if abs(da) + abs(db) < 1e-6:
            break
    return a, max(0.2, min(1.5, b))


def fit(data_dir, force=False):
    """refits only when the graded files changed (the backtest files are large); otherwise returns the saved fit"""
    files = sorted(glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl")) + glob.glob(os.path.join(data_dir, "track", "graded", "*.jsonl.gz")))
    sig = [[os.path.basename(f), os.path.getsize(f)] for f in files]
    sp = os.path.join(data_dir, "calib", "fit_sig.json")
    if not force and ST.load_json(data_dir, "calib/fit_sig.json") == sig:
        params, report = load_params(data_dir), ST.load_json(data_dir, "calib/report.json")
        if params and report is not None:
            return params, report
    params, report = _fit(data_dir, files)
    json.dump(sig, open(sp, "w"))
    return params, report


def _fit(data_dir, files):
    rows = []
    for fn in files:
        sport = os.path.basename(fn).split(".")[0]
        sport = sport[3:] if sport.startswith("bt_") else sport      # backtest rows (gzipped)
        for line in (gzip.open(fn, "rt") if fn.endswith(".gz") else open(fn)):
            try:
                r = json.loads(line)
                r["sport"] = sport
                rows.append(r)
            except Exception:
                pass
    groups = {}
    for r in rows:
        groups.setdefault(r["g"], []).append(r)
        kind = r["g"].split("|")[1]
        groups.setdefault(f"{r['sport']}|{kind}|*", []).append(r)
    params, report = {}, []
    for gk, rs in groups.items():
        pooled = gk.endswith("|*")
        if len(rs) < (MIN_POOL if pooled else MIN_GROUP):
            report.append({"group": gk, "n": len(rs)})
            continue
        xs, ys, fs = [_lg(r["p"]) for r in rs], [r["y"] for r in rs], [r["f"] for r in rs]
        a, b = _fit_ab(xs, ys)
        cal = [_sig(a + b * x) for x in xs]
        best = (1e9, 0.0)
        for i in range(21):
            w = i / 20
            ll = _ll([_sig(w * _lg(c) + (1 - w) * _lg(f)) for c, f in zip(cal, fs)], ys)
            best = min(best, (ll, w))
        params[gk] = {"a": round(a, 4), "b": round(b, 4), "w": best[1], "n": len(rs)}
        report.append({"group": gk, "n": len(rs), "llModel": round(_ll([r["p"] for r in rs], ys), 4), "llCal": round(_ll(cal, ys), 4),
                       "llMarket": round(_ll(fs, ys), 4), "llBlend": round(best[0], 4), "a": round(a, 3), "b": round(b, 3), "w": best[1],
                       "hit": round(sum(ys) / len(ys), 3)})
    os.makedirs(os.path.join(data_dir, "calib"), exist_ok=True)
    json.dump(params, open(os.path.join(data_dir, "calib", "params.json"), "w"), indent=0)
    report.sort(key=lambda r: -r["n"])
    json.dump(report, open(os.path.join(data_dir, "calib", "report.json"), "w"), indent=0)
    return params, report


# ---------------------------------------------------------------------------
# apply to the board

def load_params(data_dir):
    return ST.load_json(data_dir, "calib/params.json") or {}


def _cal(params, gk, p):
    sport, kind = gk.split("|")[:2]
    pr = params.get(gk) or params.get(f"{sport}|{kind}|*")
    if not pr or p is None:
        return p
    return _sig(pr["a"] + pr["b"] * _lg(p))


def apply(params, game_docs, prop_docs):
    """calibrate every two-way model probability in place; raw values stay in model['_raw']"""
    if not params:
        return 0
    n = 0
    for g in game_docs:
        for l in g["lines"]:
            m, side = l.get("model"), _canon_line(g, l)
            if not m or not side or side not in m:
                continue
            other = next((o for o in l["outcomes"] if o != side), None)
            c = _cal(params, group(g["sport"], "line", l["market"]), m[side])
            if c is not None and other:
                m["_raw"] = {side: m[side]}
                m[side], m[other] = round(c, 4), round(1 - c, 4)
                n += 1
    for d in prop_docs:
        for r in d["rows"]:
            m = r.get("model")
            if not m:
                continue
            side, other = ("Over", "Under") if "Over" in m else ("Yes", "No") if "Yes" in m else (None, None)
            if not side:
                continue
            c = _cal(params, group(d["sport"], "prop", d["market"]), m[side])
            if c is not None:
                m["_raw"] = {side: m[side]}
                m[side], m[other] = round(c, 4), round(1 - c, 4)
                n += 1
    return n
