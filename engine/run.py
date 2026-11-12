"""One engine run: pull odds, build the board, write Firestore and the data repo.

Env:
  ODDS_API_KEY              The Odds API key (GitHub secret)
  FIREBASE_SERVICE_ACCOUNT  service-account JSON (GitHub secret); omit to skip Firestore
  DATA_DIR                  checkout of the private data repo
  MODE                      auto (app-driven, tiered; the scheduled default) | mock (one capped pull)
                            | rebuild (no credits) | stats | probe
Logs carry counts only: this repo is public, so no prices, picks or keys.
"""
import gzip
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
import config as C  # noqa: E402
import build  # noqa: E402
import refs  # noqa: E402
import reports  # noqa: E402
import matchup  # noqa: E402
from api import OddsAPI, BudgetExceeded, APIError  # noqa: E402

NOW = datetime.now(timezone.utc)
def _mode():
    m = os.environ.get("MODE", "").strip()
    if m:
        return m
    try:  # push-triggered runs read the mode from the first word of engine/run-request
        w = open(os.path.join(os.path.dirname(__file__), "run-request")).read().split()
        if w and w[0] in ("mock", "live", "watch", "audit", "auto", "refresh", "rebuild", "probe", "stats", "history", "backtest"):
            return w[0]
    except OSError:
        pass
    return "auto"       # unknown words never fall into the expensive mock pull


MODE = _mode()
DATA_DIR = os.environ.get("DATA_DIR", "data")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


_LOG = []


def log(msg):
    line = f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}"
    _LOG.append(line)
    print(line, flush=True)


def _save_log():
    """the run's log lines (counts only, never keys or odds) in the private data repo, since Actions logs aren't
    always reachable: latest/log_<mode>.txt"""
    try:
        key = os.environ.get("ODDS_API_KEY") or "\x00"
        text = "\n".join(_LOG[-300:]).replace(key, "***")
        open(os.path.join(DATA_DIR, "latest", f"log_{MODE or 'auto'}.txt"), "w").write(text + "\n")
    except Exception:
        pass


def save_raw(sport, kind, payload):
    day = NOW.strftime("%Y-%m-%d")
    d = os.path.join(DATA_DIR, "snapshots", day)
    os.makedirs(d, exist_ok=True)
    fn = os.path.join(d, f"{NOW.strftime('%H%M%S')}_{sport}_{kind}.json.gz")
    with gzip.open(fn, "wt") as f:
        json.dump(payload, f, separators=(",", ":"))


RAW = lambda *p: os.path.join(DATA_DIR, "latest", "raw", *p)


def cache_put(name, payload):
    os.makedirs(RAW(), exist_ok=True)
    with open(RAW(name), "w") as f:
        json.dump(payload, f, separators=(",", ":"))


def load_cache(keep_hours=6):
    """Current raw state: lines_<sport>.json (sport-level) + event_<id>.json (alternates, periods, props)."""
    import glob
    cutoff = iso(NOW - timedelta(hours=keep_hours))
    games, events = {}, {}
    for fn in glob.glob(RAW("lines_*.json")):
        sport = os.path.basename(fn)[6:-5]
        for ev in json.load(open(fn)):
            if ev.get("bookmakers") and ev["commence_time"] >= cutoff:
                games[ev["id"]] = (sport, ev)
    for fn in glob.glob(RAW("event_*.json")):
        ev = json.load(open(fn))
        if ev.get("commence_time", "") < cutoff:
            os.remove(fn)          # finished games leave the cache (snapshots keep the history)
            continue
        events[ev["id"]] = ev
    game_docs, prop_docs = [], []
    for gid, (sport, ev) in games.items():
        g = build.build_game(ev, C.label(sport))
        if gid in events:
            build.add_extra_lines(events[gid], g)
            prop_docs += list(build.build_props(events[gid], C.label(sport)).values())
        game_docs.append(g)
    return game_docs, prop_docs


def load_state():
    try:
        return json.load(open(os.path.join(DATA_DIR, "latest", "refresh_state.json")))
    except Exception:
        return {"lines": {}, "events": {}, "day": None, "usedToday": 0}


def save_state(st):
    os.makedirs(os.path.join(DATA_DIR, "latest"), exist_ok=True)
    json.dump(st, open(os.path.join(DATA_DIR, "latest", "refresh_state.json"), "w"), separators=(",", ":"))


_DB = None


def firestore_db():
    global _DB
    if _DB is None and os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
        import firebase_admin
        from firebase_admin import credentials, firestore
        if not firebase_admin._apps:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])))
        _DB = firestore.client()
    return _DB


def run_history():
    """historical closing odds, resumable; hands off to a fresh run before the job time limit"""
    import oddshistory as history     # engine/history.py is the player-history estimate (histFair) since Nov 10
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY secret is missing or empty")
    api = OddsAPI(key)
    st, stop = history.run(api, DATA_DIR)
    log(f"history: {st['games']} games so far, {st['spent']} credits, stop={stop}, left {api.remaining}")
    if stop == "time" and os.environ.get("GITHUB_TOKEN"):
        import urllib.request
        req = urllib.request.Request("https://api.github.com/repos/vincecohen17/slate-ledger/actions/workflows/pull.yml/dispatches",
                                     data=json.dumps({"ref": "main", "inputs": {"mode": "history"}}).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"})
        try:
            urllib.request.urlopen(req, timeout=20)
            log("history: next run requested")
        except Exception as e:
            log(f"history: could not request the next run ({type(e).__name__})")
    elif stop in (None, "budget"):
        # everything fetched (or the history budget is used): replay the models over it
        import backtest
        backtest.main(DATA_DIR)


def presence_doc():
    """the site's heartbeat {at, view, game, force}: Realtime Database first (where the site writes it now), else Firestore"""
    db = firestore_db()
    if not db:
        return {}
    import rt
    a = rt.presence(DATA_DIR)
    try:
        b = db.document("presence/owner").get().to_dict() or {}
    except Exception:
        b = {}
    return a if (a.get("at") or "") >= (b.get("at") or "") else b


def _live_pre(s, g, mu, games):
    """pre-game starting point for the live model: our model, else the sharp pre-game market"""
    m = (mu or {}).get("model") or {}
    pg = next((x for x in games if x["id"] == g["id"]), None) or {}
    ml = next((l for l in pg.get("lines", []) if l["market"] == "h2h" and l.get("fair")), None)
    if s.startswith("tennis_"):
        pm = (ml or {}).get("fair", {}).get(g["home"])
        if pm is None and m.get("pHome") is None:
            return None
        return {"pHome": pm if m.get("pHome") is None else (m["pHome"] if pm is None else 0.6 * pm + 0.4 * m["pHome"])}
    if s.startswith("soccer_"):
        if mu and mu.get("teams", {}).get("home", {}).get("goals") is not None:
            return {"home": mu["teams"]["home"]["goals"], "away": mu["teams"]["away"]["goals"]}
        tt = next((l for l in pg.get("lines", []) if l["market"] == "totals" and l.get("fair")), None)
        if not (ml and tt):
            return None
        f = ml["fair"]
        share = (f.get(g["home"], 0.4) + 0.5 * f.get("Draw", 0.25))
        return {"home": tt["point"] * share, "away": tt["point"] * (1 - share)}
    if mu:
        if s == "icehockey_nhl":
            pre = {"home": mu["teams"]["home"].get("goals"), "away": mu["teams"]["away"].get("goals")}
        elif s == "baseball_mlb":
            pre = {"home": mu["teams"]["home"].get("runs"), "away": mu["teams"]["away"].get("runs")}
        else:
            pre = {"margin": m.get("margin"), "total": m.get("total")}
        if all(v is not None for v in pre.values()):
            return pre
    sp = next((l for l in pg.get("lines", []) if l["market"] == "spreads" and l.get("fair")), None)
    tt = next((l for l in pg.get("lines", []) if l["market"] == "totals" and l.get("fair")), None)
    if not (sp and tt):
        return None
    if s == "icehockey_nhl":
        return {"home": tt["point"] / 2 + 0.1, "away": tt["point"] / 2 - 0.1}
    if s == "baseball_mlb":
        return {"home": tt["point"] / 2, "away": tt["point"] / 2}
    return {"margin": -sp["point"], "total": tt["point"]}


def live(watch=False, full=True, pres=None):
    """In-play: live prices for every game in progress, our live projection, live edges; live props for the game
    being watched. Writes the Realtime Database live/. A plain live run goes only while the site is open.
    watch=True: one pass of the watcher loop (watch()). full=False: no Odds API calls at all (free): only the games with
    Vince's open bets or picks get their score, each leg's live stat, projected finish and chance from ESPN, written as
    the games' small live/g/<id>/m pieces (lines, edges and the board are left as the last full pass wrote them).
    Returns {"need": open legs still in play or starting within 15 minutes}."""
    import live as LV
    import refs
    key = os.environ.get("ODDS_API_KEY")
    if full and not key:
        raise RuntimeError("ODDS_API_KEY secret is missing or empty")
    pres = pres if pres is not None else presence_doc()
    at = pres.get("at")
    # the site dispatches live runs itself; if its presence write didn't land (Firebase daily limit), trust the dispatch
    asked = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    if not watch and not asked and (not at or (NOW - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() > C.ACTIVE_MINUTES * 60):
        log("site closed: no live pull")
        return {"need": False}
    fn = os.path.join(DATA_DIR, "latest", "live_state.json")
    try:
        ls = json.load(open(fn))
    except Exception:
        ls = {}
    today = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    if ls.get("day") != today:
        ls = {"day": today, "used": 0, "props": {}, **{k: ls[k] for k in ("betLegs", "betsAt", "liveBets", "rt", "rtBoardAt", "fsLiveCleared", "liveMeta") if k in ls}}
    room = C.LIVE_DAY_CAP - ls.get("used", 0)
    if full and room <= 0:
        if not watch:
            log("live credit cap reached for today")
            return {"need": False}
        full = False            # out of in-play credits: the watcher keeps the bets' stats going for free
    games, _ = load_cache(keep_hours=6)
    started = [g for g in games if NOW - timedelta(hours=5) <= datetime.fromisoformat(g["commence"].replace("Z", "+00:00")) <= NOW]
    sports = sorted({g["sport"] for g in started})
    states = LV.scoreboards(sports)
    try:
        mups = json.load(open(os.path.join(DATA_DIR, "latest", "matchups.json")))
    except Exception:
        mups = {}
    api = OddsAPI(key, max_credits=min(2000, max(room, 0))) if full else None
    docs, edges_all = [], []
    # every leg of Vince's open bets, by game: live stat + chance from the ESPN box score each run (betLegs), and games
    # with an open prop bet also get live prop prices
    import livebets as LB
    bet_legs, bet_games = {}, set()
    # open bets are read every 5 minutes (Firestore reads are on a daily quota), the legs kept in live_state between reads
    # ... and right away when the site says a bet was just saved (presence.betsAt)
    if _age_min(ls.get("betsAt")) >= 5 or "betLegs" not in ls or "betGames" not in ls or (pres.get("betsAt") or "") > (ls.get("betsAt") or ""):
        try:
            _db = firestore_db()
            if _db:
                found, live_open, open_games, ungraded = {}, [], set(), set()
                for snap in _db.collection("bets").where("status", "==", "pending").stream():
                    b = snap.to_dict() or {}
                    open_games |= {x["gameId"] for x in (b.get("legs") or [b]) if x.get("gameId")}
                    done = (b.get("auto") or {}).get("legsDone") or b.get("legsDone") or {}
                    # games with a leg grading hasn't settled yet (a parlay's finished game whose leg is in legsDone is done)
                    ungraded |= {x["gameId"] for i, x in enumerate(b.get("legs") or [b]) if x.get("gameId") and str(i) not in done}
                    if b.get("live") and not b.get("liveCheck") and b.get("dec"):
                        legs = [{k: x.get(k) for k in ("gameId", "market", "side", "point", "player", "team", "label")}
                                for x in (b.get("legs") or [b]) if x.get("gameId")]
                        if legs:
                            live_open.append({"id": snap.id, "legs": legs, "dec": b["dec"], "at": b.get("createdAt")})
                    for i, leg in enumerate(b.get("legs") or [b]):
                        if not leg.get("gameId"):
                            continue
                        r = LB.resolve(leg, DATA_DIR)
                        if r and r.get("player"):
                            found.setdefault(leg["gameId"], {})[f"{snap.id}#{i}"] = {k: r.get(k) for k in ("gameId", "market", "side", "point", "player", "label")} \
                                | {k: leg.get(k) or b.get(k) for k in ("matchup", "league", "commence")}
                ls["betLegs"], ls["betsAt"], ls["liveBets"] = found, iso(NOW), live_open
                ls["betGames"] = sorted(open_games)      # every game with an open leg, team bets included
                ls["betGamesOpen"] = sorted(ungraded)
        except Exception as e:
            log(f"open bets read failed: {type(e).__name__}")
    bet_legs = {g: dict(v) for g, v in (ls.get("betLegs") or {}).items()}
    prop_games = set(sorted(bet_legs)[:4])          # live prop prices cost credits: at most 4 games besides the watched one
    # our open prop picks get the same live and final stat (read from the picks files here: no database reads)
    pick_games = set()
    try:
        for pk in load_picks(3):
            if pk.get("status") in ("void", "won", "lost", "push") or pk.get("tier") == "likely" or not pk.get("gameId") or not pk.get("player"):
                continue
            r = LB.resolve({k: pk.get(k) for k in ("gameId", "market", "side", "point", "player", "team")} | {"label": pk.get("selection")}, DATA_DIR)
            if r and r.get("player"):
                pick_games.add(pk["gameId"])
                bet_legs.setdefault(pk["gameId"], {})[f"{pk['id']}#0"] = {k: r.get(k) for k in ("gameId", "market", "side", "point", "player", "label")} \
                    | {k: pk.get(k) for k in ("matchup", "league", "commence")}
    except Exception as e:
        log(f"open picks read failed: {type(e).__name__}")
    bet_games = set(bet_legs)
    ungraded_ids = set(ls.get("betGamesOpen") if "betGamesOpen" in ls else (ls.get("betGames") or []))
    # the games the watcher keeps current: any open bet leg (moneylines, spreads and totals too) or open prop pick
    watch_ids = bet_games | set(ls.get("betGames") or [])
    for s in sports:
        if full:
            try:
                data = api.odds(s, C.GAME_MARKETS, None, bookmakers=C.EVENT_BOOKS)
            except Exception as e:
                log(f"live odds failed for {C.label(s)}: {type(e).__name__}")
                continue
            gl = [build.build_game(ev, C.label(s)) for ev in data
                  if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) <= NOW and ev.get("bookmakers")]
        else:       # free pass: the games with open legs, from the pre-game cache (no prices)
            gl = [dict(x, lines=[]) for x in started if x["sport"] == s and x["id"] in watch_ids]
        Rf = refs.load(s, DATA_DIR)
        for g in gl:
            refs.decorate_game(g, Rf)
            LV.stale_after(g, 120)
            ha, aa = (g.get("homeRef") or {}).get("abbr"), (g.get("awayRef") or {}).get("abbr")
            st = states.get((s, refs.norm(ha or ""), refs.norm(aa or ""))) or states.get((s, refs.norm(g["home"]), refs.norm(g["away"])))
            started_h = (NOW - datetime.fromisoformat(g["commence"].replace("Z", "+00:00"))).total_seconds() / 3600
            if st is None and started_h > (3.8 if s.startswith(("americanfootball", "baseball")) else 2.4):
                continue        # finished: the scoreboard no longer lists it as in play
            mu = mups.get(g["id"]) or {}
            # the game open on the site (and games with open bets): its alternate lines, team totals and (where tracked)
            # player props in one event pull every LIVE_PROP_EVERY_MIN; the payload is kept between pulls so any live
            # line, alt or not, can be priced and logged
            want = pres.get("game") == g["id"] or g["id"] in prop_games
            props_ok = s in ("basketball_nba", "basketball_wnba", "basketball_ncaab", "icehockey_nhl", "americanfootball_nfl")
            ed, fresh_ev = None, False
            pdir = os.path.join(DATA_DIR, "latest", "live_props")
            efn = os.path.join(pdir, f"{g['id']}.ev.json")
            if want and st:
                try:
                    if full and _age_min(ls["props"].get(g["id"])) >= C.LIVE_PROP_EVERY_MIN:
                        em = [m for m in C.core_markets(s) if props_ok or not build.is_player_market(m)]
                        ed, _ = api.event_odds_all(s, g["id"], em, C.EVENT_BOOKS)
                        ls["props"][g["id"]] = iso(NOW)
                        if ed:
                            fresh_ev = True
                            os.makedirs(pdir, exist_ok=True)
                            json.dump(ed, open(efn, "w"), separators=(",", ":"))
                    if ed is None and full and os.path.exists(efn) and _age_min(datetime.fromtimestamp(os.path.getmtime(efn), timezone.utc).isoformat()) < 20:
                        ed = json.load(open(efn))
                    if ed:
                        build.add_extra_lines(ed, g)
                        LV.stale_after(g, 120)
                except Exception as e:
                    log(f"live alt lines failed: {type(e).__name__}")
            pre = _live_pre(s, g, mu, games)
            if s.startswith("tennis_"):
                pj = LV.project_tennis(st, g["home"], pre["pHome"]) if st and pre else None
            else:
                pj = LV.project(s, st, pre) if st and pre else None
            if pj:
                f = LV.line_probs(s, g, pj)
                for l in g["lines"]:
                    try:
                        p = f(l)
                    except Exception:
                        p = None
                    if p:
                        l["model"] = {o: round(v, 4) for o, v in p.items()}
            props, fresh_props = [], False
            if want and st and props_ok:
                # prop prices refresh every few minutes; the player stats (ESPN box score, free) refresh every run,
                # so between price pulls the last lines are kept and re-projected instead of disappearing
                pdir = os.path.join(DATA_DIR, "latest", "live_props")
                pfn = os.path.join(pdir, f"{g['id']}.json")
                try:
                    if fresh_ev:
                        props = list(build.build_props(ed, C.label(s)).values())
                        for p in props:
                            refs.decorate_props(p, Rf)
                        fresh_props = bool(props)
                        if props:
                            os.makedirs(pdir, exist_ok=True)
                            json.dump(props, open(pfn, "w"), separators=(",", ":"))
                        ls["props"][g["id"]] = iso(NOW)
                    if not props and os.path.exists(pfn):
                        props = json.load(open(pfn))
                    if props:
                        bx, _wp = LV.box(s, st["espnId"])
                        LV.live_props(s, props, bx, mu, pj or {})
                except Exception as e:
                    log(f"live props failed: {type(e).__name__}")
            bl = {}
            if st and bet_legs.get(g["id"]):
                try:
                    bl = LB.compute(DATA_DIR, s, g["id"], st, bet_legs[g["id"]], log)
                except Exception as e:
                    log(f"bet legs failed: {type(e).__name__}")
            E = build.edges([g], props if fresh_props else [], min_edge=0.015) if full else []     # kept lines are for stats, not edges
            for e in E:
                e["live"] = True
            edges_all += E
            docs.append({"id": g["id"], "league": g["league"], "sport": s, "home": g["home"], "away": g["away"], "commence": g["commence"],
                         "homeRef": g.get("homeRef"), "awayRef": g.get("awayRef"), "state": st,
                         "model": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in (pj or {}).items()},
                         "lines": g["lines"], "props": props, "edges": E[:40], "betLegs": bl, "updated": iso(NOW)})
    # games that just ended still have open bets until the ledger grades them: keep them in the live feed with the final
    # score and each leg's final stat, so a finished bet reads won or lost instead of going back to "not started"
    meta = ls.setdefault("liveMeta", {})            # teams and sport of every game seen in play (the odds cache drops a game once it ends)
    for d in docs:
        meta[d["id"]] = {k: d.get(k) for k in ("sport", "league", "home", "away", "commence", "homeRef", "awayRef")} | {"id": d["id"]}
    for k in [k for k, v in meta.items() if datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) < NOW - timedelta(hours=16)]:
        del meta[k]
    have = {d["id"] for d in docs}
    missing = [gid for gid in watch_ids if gid not in have]
    if missing:
        gm = {**meta, **{g["id"]: g for g in games}}
        # a game that ended before live mode saw it in play: rebuild it from the bet itself (matchup, league, start)
        lg2s = {C.label(x): x for x in C.SPORTS}
        for gid in missing:
            if gid in gm:
                continue
            try:                # the pre-game snapshot the tracker keeps for every game: sport, teams, abbreviations, start
                tg = json.load(open(os.path.join(DATA_DIR, "track", "open", f"{gid}.json")))
                gm[gid] = {"id": gid, "sport": tg["sport"], "league": C.label(tg["sport"]), "home": tg["home"], "away": tg["away"],
                           "commence": tg["commence"], "homeRef": {"abbr": tg.get("ha")}, "awayRef": {"abbr": tg.get("aa")}}
                continue
            except Exception:
                pass
            info = next((v for v in bet_legs.get(gid, {}).values() if v.get("matchup") and " @ " in v["matchup"] and lg2s.get(v.get("league"))), None)
            if not info:
                continue
            away, home = info["matchup"].split(" @ ", 1)
            g = {"id": gid, "sport": lg2s[info["league"]], "league": info["league"], "home": home.strip(), "away": away.strip(),
                 "commence": info.get("commence") or iso(NOW - timedelta(hours=3))}
            try:
                refs.decorate_game(g, refs.load(g["sport"], DATA_DIR))
            except Exception:
                pass
            gm[gid] = g
        ms = [gm[x] for x in missing if x in gm and NOW - timedelta(hours=14) <= datetime.fromisoformat(gm[x]["commence"].replace("Z", "+00:00")) <= NOW]
        post = {}
        if ms:
            try:
                LV.scoreboards(sorted({g["sport"] for g in ms}), post=post)
            except Exception as e:
                log(f"live: final scoreboards failed ({type(e).__name__})")
        for g in ms:
            s = g["sport"]
            ha, aa = (g.get("homeRef") or {}).get("abbr"), (g.get("awayRef") or {}).get("abbr")
            st = post.get((s, refs.norm(ha or ""), refs.norm(aa or ""))) or post.get((s, refs.norm(g["home"]), refs.norm(g["away"])))
            if not st:
                continue
            try:
                bl = LB.compute(DATA_DIR, s, g["id"], st, bet_legs[g["id"]], log) if bet_legs.get(g["id"]) else {}
            except Exception as e:
                log(f"bet legs (final) failed: {type(e).__name__}")
                bl = {}
            docs.append({"id": g["id"], "league": g["league"], "sport": s, "home": g["home"], "away": g["away"], "commence": g["commence"],
                         "homeRef": g.get("homeRef"), "awayRef": g.get("awayRef"), "state": st, "model": {}, "final": True,
                         "lines": [], "props": [], "edges": [], "betLegs": bl, "updated": iso(NOW)})
        n_fin = sum(1 for d in docs if d.get("final"))
        if n_fin:
            log(f"live: {n_fin} finished games kept for their open bets")
    ls["used"] = ls.get("used", 0) + (api.used_run if api else 0)
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(ls, open(fn, "w"))
    # small summary in the private data repo (what the Live screen shows), for checking without the site
    json.dump([{"id": d["id"], "league": d["league"], "game": f"{d['away']} @ {d['home']}", "state": d["state"], "model": d["model"],
                "edges": len(d["edges"]), "props": len(d["props"]), "betLegs": len(d["betLegs"])} for d in docs],
              open(os.path.join(DATA_DIR, "latest", "live.json"), "w"), indent=0)
    json.dump(sorted("|".join(k) for k in states), open(os.path.join(DATA_DIR, "latest", "live_keys.json"), "w"), indent=0)
    # live bets: the live fair 5-30 minutes after the bet, as that bet's closing-line check
    if ls.get("liveBets"):
        try:
            import livebets as LB2
            chk = LB2.live_checks(ls["liveBets"], {d["id"]: d for d in docs}, NOW, DATA_DIR)
            if chk:
                _db = firestore_db()
                for bid, c in chk.items():
                    _db.document(f"bets/{bid}").update({"liveCheck": c})
                ls["liveBets"] = [b for b in ls["liveBets"] if b["id"] not in chk]
                log(f"live: price check written for {len(chk)} live bets")
        except Exception as e:
            log(f"live: live bet check failed ({type(e).__name__})")
    # in-play data goes to the Realtime Database (no daily write count); only changed pieces are sent
    summary = [{"id": d["id"], "league": d["league"], "home": d["home"], "away": d["away"], "homeRef": d["homeRef"], "awayRef": d["awayRef"],
                "state": d["state"], "pHome": (d["model"] or {}).get("pHome"),
                "mkt": next(((l.get("fair") or {}).get(d["home"]) for l in d["lines"] if l["market"] == "h2h"), None),
                "edges": len(d["edges"])} for d in docs if not d.get("final")]
    edges_all.sort(key=lambda e: -e["score"])
    # in-play arbs and middles across Vince's venues, from this pass's live prices (gone in minutes: shown, never alerted)
    live_opps = []
    if full:
        try:
            import opps as OP
            gl = [{**d, "lines": d["lines"]} for d in docs if not d.get("final") and d.get("lines")]
            pl = [{**p, "id": d["id"], "league": d["league"], "commence": d["commence"], "home": d["home"], "away": d["away"], "sport": d["sport"]}
                  for d in docs if not d.get("final") for p in (d.get("props") or [])]
            live_opps = OP.find(gl, pl, iso(NOW), live=True)[:40]
            for o in live_opps:
                o["at"] = iso(NOW)
        except Exception as e:
            log(f"live opps failed: {type(e).__name__}: {str(e)[:80]}")
    if firestore_db() and not full:
        import rt
        try:
            sent, nw = rt.publish(docs, None, ls, DATA_DIR, partial=True)
            if nw:
                log(f"watch: {nw} games' bet stats updated ({sent // 1024} KB)")
        except Exception as e:
            log(f"watch: realtime database write failed ({type(e).__name__})")
    elif firestore_db():
        import rt
        try:
            sent, nw = rt.publish(docs, {"games": summary, "edges": edges_all[:150], "opps": live_opps, "updated": iso(NOW), "creditsToday": ls["used"]}, ls, DATA_DIR)
            day_kb = ((ls.get("rtKb") or {}).get("kb", 0) if (ls.get("rtKb") or {}).get("day") == today else 0) + sent / 1024
            ls["rtKb"] = {"day": today, "kb": round(day_kb)}
            log(f"live: {nw} pieces, {sent // 1024} KB to the realtime database (today {round(day_kb)} KB)")
        except Exception as e:
            log(f"live: realtime database write failed ({type(e).__name__}); using Firestore")
            _fs_live(docs, edges_all, ls, today, pres, bet_games)
            ls["rtOk"] = False
        else:
            ls["rtOk"] = True
            if True:              # cheap: only sends when the rules file changed (latest/rt_rules.json)
                try:
                    code = rt.ensure_rules(DATA_DIR, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    ls["rtRules"] = code == 200
                    if code != 200:
                        log(f"live: realtime database rules not set ({code})")
                except Exception as e:
                    log(f"live: realtime database rules not set ({type(e).__name__})")
        if ls.get("rtOk") and not ls.get("fsLiveCleared"):       # the old Firestore copies, once
            try:
                db = firestore_db()
                for snap in db.collection("live").stream():
                    snap.reference.delete()
                db.document("board/live").delete()
                ls["fsLiveCleared"] = True
            except Exception as e:
                log(f"live: old Firestore live docs not cleared yet ({type(e).__name__})")
    fs = ls.get("fs") or {}
    ls["fs"] = fs
    json.dump(ls, open(fn, "w"))
    if full:
        log(f"live: {len(docs)} games, {len(edges_all)} edges, {len(live_opps)} arbs/middles, credits {api.used_run} (today {ls['used']})")
    # still worth watching: an open leg on a game in play, or one starting within 15 minutes
    starts = {g["id"]: g["commence"] for g in games}
    for gid, legs in bet_legs.items():          # legs carry their game's start when the cache has dropped it
        starts.setdefault(gid, next((v.get("commence") for v in legs.values() if v.get("commence")), None))
    soon = any(iso(NOW - timedelta(minutes=20)) <= (starts.get(gid) or "") <= iso(NOW + timedelta(minutes=15)) for gid in watch_ids)
    # a game with an open leg starts later today: the watcher waits for it instead of ending (nothing else would start it
    # in time: GitHub runs the 5-minute cron only every few hours)
    upcoming = any(iso(NOW) < (starts.get(gid) or "") <= iso(NOW + timedelta(hours=WATCH_AHEAD_H)) for gid in watch_ids)
    return {"need": soon or any(d["id"] in watch_ids and not d.get("final") for d in docs), "upcoming": upcoming,
            "inplay": sorted(d["id"] for d in docs if d["id"] in watch_ids and not d.get("final")),
            # finished games still waiting on grading (a parlay's finished game whose leg is already graded isn't: asking
            # for a rebuild every 15 minutes until the parlay's last game ends spent a run for nothing)
            # ... and only until 7 hours after the start: a leg grading can't read won't settle by asking again (the
            # hourly and audit runs still retry it)
            "final": sorted(d["id"] for d in docs if d.get("final") and (d["id"] in ungraded_ids or d["id"] in pick_games)
                            and datetime.fromisoformat(d["commence"].replace("Z", "+00:00")) > NOW - timedelta(hours=7))}


WATCH_AHEAD_H = 6          # the watcher starts this far ahead of the first game with an open leg and waits for it


def _ppprobe():
    """check the power-play point sources on the last few days of finished NHL games (logs counts only): the NHL
    play-by-play's power-play goals must match the NHL box score's per-player powerPlayGoals, and ESPN's fallback
    parse is compared with it"""
    import nhlapi, stats as ST2, live as LV2
    games = []
    for d in range(1, 4):
        day = (NOW - timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            games += [g for g in nhlapi._get(f"{nhlapi.BASE}/score/{day}").get("games") or [] if g.get("gameState") in ("OFF", "FINAL")]
        except Exception as e:
            log(f"ppprobe: score {day} failed ({type(e).__name__})")
    ok = bad = 0
    esp_ok = esp_bad = esp_none = 0
    for g in games[:12]:
        pp = nhlapi.pp_points(g["id"])
        if pp is None:
            log("ppprobe: play-by-play missing")
            continue
        n_pp = pp.pop("_n", 0)
        try:
            bx = nhlapi._get(f"{nhlapi.BASE}/gamecenter/{g['id']}/boxscore")
            ppg = 0
            for side in ("homeTeam", "awayTeam"):
                for grp in ("forwards", "defense"):
                    ppg += sum(int(p.get("powerPlayGoals") or 0) for p in ((bx.get("playerByGameStats") or {}).get(side) or {}).get(grp) or [])
        except Exception as e:
            log(f"ppprobe: boxscore failed ({type(e).__name__})")
            continue
        ok, bad = (ok + 1, bad) if ppg == n_pp else (ok, bad + 1)
        if ppg != n_pp:
            log(f"ppprobe: mismatch: play-by-play {n_pp} PP goals vs box score {ppg}")
        # ESPN's own summary for the same game, parsed without the NHL source
        try:
            sb = LV2._get(f"{LV2.ESPN}/hockey/nhl/scoreboard?dates={g.get('gameDate', '').replace('-', '')}")
            ev = next((e for e in sb.get("events") or [] if {nhlapi._ab(c["team"]["abbreviation"]) for c in e["competitions"][0]["competitors"]}
                       == {nhlapi._ab((g.get("homeTeam") or {}).get("abbrev")), nhlapi._ab((g.get("awayTeam") or {}).get("abbrev"))}), None)
            if ev:
                summ = LV2._get(f"{LV2.ESPN}/hockey/nhl/summary?event={ev['id']}")
                plays = summ.get("plays") or []
                sc = [p for p in plays if p.get("scoringPlay")]
                keys = sorted({k for p in sc[:3] for k in p.keys()})
                st_ex = [str((p.get("strength") or {}).get("text") if isinstance(p.get("strength"), dict) else p.get("strength")) for p in sc[:6]]
                import nhlapi as _n
                saved = _n.pp_from_espn_summary
                _n.pp_from_espn_summary = lambda s_: None          # force the ESPN-only parse
                try:
                    rows = {}
                    n_esp = ST2.add_pp_points(rows, summ)
                finally:
                    _n.pp_from_espn_summary = saved
                if not plays:
                    esp_none += 1
                elif n_esp == n_pp:
                    esp_ok += 1
                else:
                    esp_bad += 1
                    log(f"ppprobe: ESPN parse found {n_esp} PP goals vs NHL {n_pp}; scoring play keys {keys}; strength {st_ex}")
        except Exception as e:
            log(f"ppprobe: ESPN check failed ({type(e).__name__}: {str(e)[:80]})")
    log(f"ppprobe: NHL play-by-play matches box score PP goals in {ok} of {ok + bad} games; ESPN parse agrees in {esp_ok}, "
        f"differs in {esp_bad}, no plays in {esp_none}")


def _promodesk():
    """promo requests to Claude's desk and its answers back (promodesk.py); one Firestore query"""
    try:
        import promodesk
        db = firestore_db()
        if db:
            promodesk.sync(db, DATA_DIR, log=log)
    except Exception as e:
        log(f"promodesk failed: {type(e).__name__}: {str(e)[:80]}")


def watch():
    """The watcher: one long job (Actions minutes are free on this public repo) that keeps Vince's open bets current
    every minute until they're done, app open or not. While the site is on a live screen (presence.live in the last 3
    minutes) each pass is a full live pull (prices, edges; the same credits the site's own live runs used); otherwise
    it's free (ESPN only). Before the first game with an open leg starts (up to WATCH_AHEAD_H hours ahead) it waits,
    checking every 5 minutes (every minute once the site is on a live screen). Ends when nothing is left to watch; after
    50 minutes it starts the next watcher itself, so tracking never depends on the cron or the site being open."""
    import time
    global NOW
    t0 = time.time()
    passes = {True: 0, False: 0}
    idle, r = 0, {}
    asked, seen_live, graded_ask, last_grade = set(), set(), {}, 0.0      # graded_ask: gameId -> when its rebuild was asked
    st0 = load_state()
    last_hot = 0.0
    last_steam, steam_api, steam_cr = 0.0, None, 0
    while True:
        NOW = datetime.now(timezone.utc)
        t1 = time.time()
        # closing prices: a game starting within CLOSE_ASK_MIN gets an auto run (its close capture pulls anything over
        # CLOSE_STALE_MIN old), whether or not the site is open. CLV and the report cards are only as good as the close.
        try:
            board_soon = _closer(asked, st0)
        except Exception as e:
            log(f"watch: close check failed ({type(e).__name__})")
            board_soon = True
        pres = presence_doc()
        site_live = bool(pres.get("live")) and _age_min(pres.get("at")) <= 3
        waiting = r.get("upcoming") and not r.get("need") and not site_live
        if waiting and idle % 5:            # nothing in play yet: a full check every 5 minutes, presence every minute
            idle += 1
        else:
            idle = 1 if waiting else 0
            try:
                r = live(watch=True, full=site_live, pres=pres) or {}
            except Exception as e:
                log(f"watch: pass failed ({type(e).__name__}: {str(e)[:120]})")
                r = {"need": True}
            passes[site_live] += 1
        # settle promptly: a watched game that was in play and is now final (or gone) gets a rebuild run, which collects
        # the final and grades every bet and pick on it, instead of waiting for the next auto run
        now_live = set(r.get("inplay") or [])
        # a final game still in the feed has open bets or picks: its first rebuild came before the final was collectable
        # (ESPN's summary lags the scoreboard), so ask again every 15 minutes until it grades and leaves the feed
        ended = (seen_live - now_live) | {x for x in (r.get("final") or []) if time.time() - graded_ask.get(x, 0) > 15 * 60}
        seen_live |= now_live
        if ended and time.time() - last_grade > 120:
            if _dispatch("rebuild", "grade finals"):
                graded_ask.update({x: time.time() for x in ended})
                seen_live -= ended
                last_grade = time.time()
        # hot games (picks, open bets, arbs, edges): an auto run every HOT_ASK_MIN refreshes whatever of them is past the
        # hot clock (the run decides; one that finds everything fresh spends nothing). Site open or not.
        hot_soon = _hot_soon()
        if hot_soon and time.time() - last_hot > C.HOT_ASK_MIN * 60:
            if _dispatch("auto", f"hot refresh, {hot_soon} games"):
                last_hot = time.time()
        # steam: a sharp move with Vince's venues still at the old price, caught within STEAM_EVERY_MIN
        if time.time() - last_steam >= C.STEAM_EVERY_MIN * 60 - 5:
            last_steam = time.time()
            try:
                steam_cr += _steam_pass(steam_api or None)
            except Exception as e:
                log(f"watch: steam failed ({type(e).__name__}: {str(e)[:80]})")
        if not (r.get("need") or r.get("upcoming") or site_live or board_soon or hot_soon):
            log("watch: nothing left in play and no game starting soon")
            break
        if time.time() - t0 > 50 * 60:
            log("watch: 50 minutes up; starting the next watcher")
            _dispatch_watch(chain=True)
            break
        time.sleep(max(5, 60 - (time.time() - t1)))
    log(f"watch: {passes[True]} full passes, {passes[False]} free passes in {round((time.time() - t0) / 60)} min"
        + (f", steam scans spent {steam_cr} credits" if steam_cr else ""))


_STEAM_API = None


def _steam_pass(_unused=None):
    """one steam scan for sports with a board game starting within STEAM_AHEAD_H: publishes live/steam and alerts"""
    global _STEAM_API
    import steam
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        return 0
    try:
        gs = json.load(open(os.path.join(DATA_DIR, "latest", "board.json")))
        gs = gs["games"] if isinstance(gs, dict) else gs
    except Exception:
        return 0
    lo, hi = iso(NOW), iso(NOW + timedelta(hours=C.STEAM_AHEAD_H))
    sports = sorted({g.get("sport") for g in gs if g.get("sport") and lo < (g.get("commence") or "") <= hi})
    if not sports:
        return 0
    if _STEAM_API is None:
        _STEAM_API = OddsAPI(key, max_credits=C.STEAM_DAY_MAX)
    items, spent = steam.scan(_STEAM_API, DATA_DIR, NOW, sports, log=log)
    try:
        import rt
        rt.ref("live/steam", DATA_DIR).set(rt.z({"at": iso(NOW), "items": items, "windows": steam.windows(DATA_DIR)}))
    except Exception as e:
        log(f"steam: publish failed ({type(e).__name__})")
    if items:
        import alerts
        alerts.steam(DATA_DIR, NOW, items, log=log)
        log(f"steam: {len(items)} venues lagging a sharp move")
    return spent


def _hot_soon(hours=18):
    """hot games (latest/hot.json, written by auto runs) starting within `hours`"""
    try:
        h = json.load(open(os.path.join(DATA_DIR, "latest", "hot.json"))).get("games") or {}
    except Exception:
        return 0
    lo, hi = iso(NOW), iso(NOW + timedelta(hours=hours))
    return sum(1 for g in h.values() if lo < (g.get("commence") or "") <= hi)


def _watch_wanted():
    """auto runs: should a watcher be running? Open bets (or picks) on a game in play or starting within 15 minutes.
    Reads the pending bets once (a handful of Firestore reads) and asks GitHub whether a watcher is already going."""
    db = firestore_db()
    if not db or not os.environ.get("GITHUB_TOKEN"):
        return False
    lo, hi = iso(NOW - timedelta(hours=5)), iso(NOW + timedelta(hours=WATCH_AHEAD_H))
    import glob
    starts = {}             # every cached game's start, for legs saved without one
    for f in glob.glob(RAW("lines_*.json")):
        try:
            starts.update({ev["id"]: ev.get("commence_time") for ev in json.load(open(f))})
        except Exception:
            pass
    want = False
    try:
        for snap in db.collection("bets").where("status", "==", "pending").stream():
            b = snap.to_dict() or {}
            if any(lo <= (x.get("commence") or starts.get(x.get("gameId")) or b.get("commence") or "") <= hi for x in (b.get("legs") or [b])):
                want = True
                break
    except Exception as e:
        log(f"watch: open bets not read ({type(e).__name__})")
    if not want:
        try:
            want = any(pk.get("status") not in ("void", "won", "lost", "push") and pk.get("tier") != "likely" and pk.get("player")
                       and lo <= (pk.get("commence") or "") <= hi for pk in load_picks(2))
        except Exception:
            pass
    if not want:            # hot games (picks, bets, arbs, edges) in the next 18 h keep their prices fresh through the watcher
        want = _hot_soon() > 0
    if not want:            # no open bet soon: still keep the watcher up for close capture when any game starts soon
        hz = iso(NOW + timedelta(hours=WATCH_AHEAD_H))
        want = any(iso(NOW) < (v or "") <= hz for v in starts.values())
    if not want:
        return False
    return _dispatch_watch()


CLOSE_ASK_MIN = 12          # ask for a close capture this long before a start


def _closer(asked, st0):
    """dispatch an auto run when a board game starts within CLOSE_ASK_MIN minutes and its prices are older than the
    close threshold; returns whether any board game starts within WATCH_AHEAD_H hours (the watcher stays up for those)"""
    import glob
    games = []                  # light: ids, sports and starts from the cached line pulls (no prop building every minute)
    for fn in glob.glob(RAW("lines_*.json")):
        try:
            games += [{"id": ev["id"], "sport": os.path.basename(fn)[6:-5], "commence": ev.get("commence_time")} for ev in json.load(open(fn))]
        except Exception:
            pass
    lo, hi = NOW + timedelta(minutes=1), NOW + timedelta(minutes=CLOSE_ASK_MIN)
    due = []
    for g in games:
        try:
            t = datetime.fromisoformat(g["commence"].replace("Z", "+00:00"))
        except Exception:
            continue
        if lo < t <= hi and g["id"] not in asked:
            due.append(g)
    if due:
        stale = [g for g in due if _age_min((st0.get("lines") or {}).get(g["sport"])) > C.CLOSE_STALE_MIN
                 or _age_min((st0.get("events") or {}).get(g["id"])) > C.CLOSE_STALE_MIN]
        if stale and _dispatch("auto", f"close for {len(stale)} games"):
            asked |= {g["id"] for g in due}
        elif not stale:
            asked |= {g["id"] for g in due}
    horizon = iso(NOW + timedelta(hours=WATCH_AHEAD_H))
    return any(iso(NOW) < (g.get("commence") or "") <= horizon for g in games)


def _dispatch(mode, why=""):
    """start an engine run in `mode` unless one is already waiting to start"""
    if not os.environ.get("GITHUB_TOKEN"):
        return False
    import urllib.request
    h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    base = "https://api.github.com/repos/vincecohen17/slate-ledger/actions/workflows/pull.yml"
    try:
        for stt in ("queued", "pending", "waiting"):
            runs = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/runs?status={stt}&per_page=20", headers=h), timeout=20))
            if any((r.get("display_title") or "") == mode for r in runs.get("workflow_runs") or []):
                return True            # one is already on its way
        urllib.request.urlopen(urllib.request.Request(f"{base}/dispatches", data=json.dumps({"ref": "main", "inputs": {"mode": mode}}).encode(),
                                                      method="POST", headers=h), timeout=20)
        log(f"watch: started a {mode} run ({why})")
        return True
    except Exception as e:
        log(f"watch: could not start a {mode} run ({type(e).__name__})")
        return False


def _dispatch_watch(chain=False):
    """start a watcher unless one is going or waiting (chain: a watcher handing over to the next, so its own run doesn't count)"""
    if not os.environ.get("GITHUB_TOKEN"):
        return False
    import urllib.request
    h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    base = "https://api.github.com/repos/vincecohen17/slate-ledger/actions/workflows/pull.yml"
    try:
        for stt in (("queued", "pending", "waiting") if chain else ("in_progress", "queued", "pending", "waiting")):
            runs = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/runs?status={stt}&per_page=20", headers=h), timeout=20))
            if any((r.get("display_title") or "") == "watch" for r in runs.get("workflow_runs") or []):
                return False
        urllib.request.urlopen(urllib.request.Request(f"{base}/dispatches", data=json.dumps({"ref": "main", "inputs": {"mode": "watch"}}).encode(),
                                                      method="POST", headers=h), timeout=20)
        log("watch: handed over to the next watcher" if chain else "watch: started the watcher for open bets")
        return True
    except Exception as e:
        log(f"watch: could not start the watcher ({type(e).__name__})")
        return False


def _fs_live(docs, edges_all, ls, today, pres, bet_games):
    """fallback until the Realtime Database exists: in-play docs to Firestore (quota-aware)"""
    db = firestore_db()
    fs = ls.get("fs") if (ls.get("fs") or {}).get("day") == today else {"day": today, "n": 0}
    quota_wait = (ls.get("quota") or {}).get("day") == today and _age_min((ls.get("quota") or {}).get("at")) < 30
    if db and not quota_wait:
        # Spark plan: 20k writes a day shared with the pre-game runs. A live doc is written only when it changed; near the
        # cap, only the watched game and games with open bets.
        try:
            hz = json.load(open(os.path.join(DATA_DIR, "latest", "hashes.json"))).get("_writes", {})
            auto_today = hz.get("n", 0) if hz.get("day") == today else 0
        except Exception:
            auto_today = 0
        tight = auto_today + fs["n"] > 18500
        hashes, writes = ls.get("hash") or {}, []
        for d in docs:
            if tight and d["id"] not in bet_games and pres.get("game") != d["id"]:
                continue
            sdoc = _payload(d)
            h = hashlib.sha1(_payload({k: v for k, v in d.items() if k != "updated"}).encode()).hexdigest()
            if hashes.get(d["id"]) == h:
                continue
            writes.append((f"live/{d['id']}", {"payload": sdoc, "league": d["league"], "updated": d["updated"]}, d["id"], h))
        summary = [{"id": d["id"], "league": d["league"], "home": d["home"], "away": d["away"], "homeRef": d["homeRef"], "awayRef": d["awayRef"],
                    "state": d["state"], "pHome": (d["model"] or {}).get("pHome"),
                    "mkt": next(((l.get("fair") or {}).get(d["home"]) for l in d["lines"] if l["market"] == "h2h"), None),
                    "edges": len(d["edges"])} for d in docs if not d.get("final")]
        edges_all.sort(key=lambda e: -e["score"])
        hb = hashlib.sha1(json.dumps([summary, edges_all[:150]], separators=(",", ":")).encode()).hexdigest()
        if hashes.get("_board") != hb or _age_min(ls.get("boardAt")) >= 10:       # the "updated" stamp still moves every 10 min
            writes.append(("board/live", {"payload": json.dumps({"games": summary, "edges": edges_all[:150], "updated": iso(NOW),
                                                                  "creditsToday": ls["used"]}, separators=(",", ":"))}, "_board", hb))
        keep = {d["id"] for d in docs}
        gone = [x for x in ls.get("docs", []) if x not in keep]
        try:
            batch, size, n = db.batch(), 0, 0
            for path, data, gid, h in writes:
                if size + len(data["payload"]) > 6_000_000:
                    batch.commit()
                    batch, size = db.batch(), 0
                batch.set(db.document(path), data)
                size += len(data["payload"])
                n += 1
            for gid in gone:
                batch.delete(db.document(f"live/{gid}"))
                n += 1
            batch.commit()
            for path, data, gid, h in writes:
                if gid:
                    hashes[gid] = h
            if any(g == "_board" for _, _, g, _ in writes):
                ls["boardAt"] = iso(NOW)
            ls["hash"] = {k: v for k, v in hashes.items() if k in keep or k == "_board"}
            ls["docs"] = sorted(keep | (set(ls.get("docs", [])) - set(gone)))
            fs["n"] += n
        except Exception as e:
            if not _fs_busy(e):
                raise
            ls["quota"] = {"day": today, "at": iso(NOW)}
            log("live: Firestore daily write quota reached; pausing live writes for 30 minutes")
    elif quota_wait:
        log("live: Firestore quota hit earlier; skipping writes")
    ls["fs"] = fs


def presence():
    """(active, force_at): the site writes presence/owner {at, force?} while it is open."""
    db = firestore_db()
    if not db:
        return False, None
    try:
        x = presence_doc()
    except Exception:
        return False, None
    at = x.get("at")
    active = bool(at) and (NOW - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() < C.ACTIVE_MINUTES * 60
    return active, x.get("force")


def _age_min(ts):
    if not ts:
        return 1e9
    return (NOW - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 60


def _limit(table, hours_to_start):
    for h, m in table:
        if hours_to_start <= h:
            return m
    return None


def _paced_cap(P, st, plan_key):
    """Daily credit allowance: what is left this billing cycle (minus the history reserve) spread over the days left,
    weighted toward the weekend slates. Free plan: its fixed cap."""
    if plan_key == "free" or st.get("remaining") is None:
        return P["day_cap"]
    today = (NOW - timedelta(hours=7)).date()
    reset = today.replace(day=min(C.CYCLE_DAY, 28))
    while reset <= today:
        reset = (reset.replace(day=1) + timedelta(days=32)).replace(day=min(C.CYCLE_DAY, 28))
    days = [(today + timedelta(days=i)).weekday() for i in range((reset - today).days)]
    w = [C.DAY_WEIGHT[d] for d in days] or [1.0]
    spendable = max(0, st["remaining"] + st.get("usedToday", 0) - st.get("historyLeft", C.HISTORY_RESERVE))
    return int(min(P["day_cap"], max(4000, spendable * w[0] / sum(w))))


def _merge_event(old, new):
    """keep markets from the last pull that this pull did not ask for (core vs extended tiers)"""
    if not old:
        return new
    out = dict(new)
    books = {b["key"]: {m["key"]: m for m in b.get("markets", [])} for b in old.get("bookmakers", [])}
    for b in new.get("bookmakers", []):
        books.setdefault(b["key"], {}).update({m["key"]: m for m in b.get("markets", [])})
    titles = {b["key"]: b for b in old.get("bookmakers", []) + new.get("bookmakers", [])}
    out["bookmakers"] = [dict(titles[k], markets=list(ms.values())) for k, ms in books.items()]
    return out


def _hot(st, core_of):
    """Games whose prices we act on: open picks, Vince's pending bets (Firestore, read at most every 15 min), arbs and
    middles, and real edges. Returns {gameId: {"why": [...], "ext": bool}}; ext = something on it sits in the extended
    markets (alternates, periods, specials), which then refresh on the hot clock too. Written to latest/hot.json."""
    import glob
    games = {}
    for fn in glob.glob(RAW("lines_*.json")):
        try:
            for ev in json.load(open(fn)):
                games[ev["id"]] = (os.path.basename(fn)[6:-5], ev.get("commence_time") or "")
        except Exception:
            pass
    now = iso(NOW)
    hot, rank = {}, {}
    def add(gid, why, market=None, r=9.0):
        g = games.get(gid)
        if not g or g[1] <= now:
            return
        h = hot.setdefault(gid, {"why": [], "ext": False, "sport": g[0], "commence": g[1]})
        if why not in h["why"]:
            h["why"].append(why)
        if market and market not in core_of(g[0]) and market not in C.GAME_MARKETS:
            h["ext"] = True
        rank[gid] = min(rank.get(gid, 9.0), r)
    for pk in load_picks(3):
        if pk.get("status") not in ("void", "won", "lost", "push") and pk.get("gameId"):
            add(pk["gameId"], "pick", pk.get("market"), 0)
    bets = st.get("hotBets") or {}
    if _age_min(bets.get("at")) > 15:
        legs = []
        try:
            db = firestore_db()
            if db:
                for snap in db.collection("bets").where("status", "==", "pending").stream():
                    b = snap.to_dict() or {}
                    for x in (b.get("legs") or [b]):
                        if x.get("gameId"):
                            legs.append([x["gameId"], x.get("market")])
                bets = {"at": iso(NOW), "legs": legs}
                st["hotBets"] = bets
        except Exception as e:
            log(f"hot: open bets not read ({type(e).__name__})")
    for gid, mk in bets.get("legs") or []:
        add(gid, "bet", mk, 0)
    try:
        for o in json.load(open(os.path.join(DATA_DIR, "latest", "opps.json"))):
            if not o.get("suspect"):
                for lg in o.get("legs") or []:
                    add(o.get("gameId") or lg.get("gameId"), "arb" if o.get("kind") == "arb" else "middle", lg.get("market") or o.get("market"), 1)
    except Exception:
        pass
    try:
        es = json.load(open(os.path.join(DATA_DIR, "latest", "edges.json")))
        for e in sorted(es, key=lambda e: -(e.get("edge") or 0)):
            if (e.get("edge") or 0) >= C.HOT_EDGE_MIN and not (e.get("muted") or e.get("avoid") or e.get("est") or e.get("model")) and (e.get("edge") or 0) < 0.25:
                add(e.get("gameId"), "edge", e.get("market"), 2 - min(1.0, e.get("edge") or 0))
    except Exception:
        pass
    keep = sorted(hot, key=lambda g: (rank[g], hot[g]["commence"]))[:C.HOT_MAX]
    hot = {g: hot[g] for g in keep}
    fn = os.path.join(DATA_DIR, "latest", "hot.json")
    try:                # rewritten only when the list changes (or hourly), so idle runs don't make data commits
        old = json.load(open(fn)) if os.path.exists(fn) else {}
        if old.get("games") != hot or _age_min(old.get("at")) > 60:
            json.dump({"at": iso(NOW), "games": hot}, open(fn, "w"), separators=(",", ":"))
    except Exception:
        pass
    return hot


def auto():
    """App-driven refresh: spend credits only while the site is open (plus close capture on the paid
    plan), and only on what is stale for how soon it starts."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY secret is missing or empty")
    if MODE == "auto":
        try:                # open bets in play: make sure the free every-minute watcher is going, app open or not
            _watch_wanted()
        except Exception as e:
            log(f"watch: check failed ({type(e).__name__})")
    _promodesk()
    st = load_state()
    try:
        st["historyLeft"] = max(0, C.HISTORY_RESERVE - json.load(open(os.path.join(DATA_DIR, "history", "state.json"))).get("spent", 0))
    except Exception:
        pass
    plan_key = C.PLAN if C.PLAN != "auto" else st.get("plan", "free")
    P = C.PLANS[plan_key]
    today = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    if st.get("day") != today:
        st.update(day=today, usedToday=0)
    active, force_at = presence()
    force = (bool(force_at) and force_at != st.get("lastForce") and _age_min(force_at) < 15) or MODE == "refresh"
    paid = plan_key != "free"
    room = max(0, _paced_cap(P, st, plan_key) - st.get("usedToday", 0))
    if not (active or force or paid) and C.PLAN == "auto" and _age_min(st.get("planCheck")) > 60:
        probe = OddsAPI(key, max_credits=0)          # /sports is free: read the quota to notice an upgrade
        try:
            probe.sports()
            st["planCheck"] = iso(NOW)
            if probe.remaining is not None and probe.used_total is not None and probe.remaining + probe.used_total > 50_000:
                st["plan"] = plan_key = "5m"
                P, paid = C.PLANS["5m"], True
                room = max(0, _paced_cap(P, st, plan_key) - st.get("usedToday", 0))
            save_state(st)
        except Exception:
            pass
    if not (active or force or paid):
        log("site closed: nothing to do")
        return
    if room <= 0:
        log("daily credit ceiling reached")
        return
    api = OddsAPI(key, max_credits=min(P["run_cap"], room))
    act = {s["key"] for s in api.sports() if s.get("active")}
    if C.PLAN == "auto" and api.remaining is not None and api.used_total is not None:
        detected = "5m" if api.remaining + api.used_total > 50_000 else "free"     # free key: 500 a month
        if detected != plan_key:
            log(f"plan detected from quota: {detected}")
            plan_key, P, paid = detected, C.PLANS[detected], detected != "free"
            st["plan"] = detected
    if api.remaining is not None:
        st["remaining"] = api.remaining
        room = max(0, _paced_cap(P, st, plan_key) - st.get("usedToday", 0))
        api.max_credits = min(P["run_cap"], room)
        st["dayCap"] = _paced_cap(P, st, plan_key)
    sports = [s for s in C.SPORTS if s in act] + sorted(s for s in act if s.startswith("tennis_") and C.wanted(s))
    errors, pulled = [], []
    horizon = iso(NOW + timedelta(days=7))
    # hot games (picks, open bets, arbs, edges) keep fresh prices with the site closed too, within the day's budget
    hot = _hot(st, C.core_markets) if paid else {}
    hot_ok = paid and (active or force or st.get("usedToday", 0) < C.HOT_DAY_SHARE * _paced_cap(P, st, plan_key))
    def _hrs(t):
        return (datetime.fromisoformat(t.replace("Z", "+00:00")) - NOW).total_seconds() / 3600
    hot_lines = {}
    for gid, h in hot.items():
        hot_lines[h["sport"]] = min(hot_lines.get(h["sport"], 1e9), _hrs(h["commence"]))
    n_hot = 0
    soon = {}
    for s in sports:
        try:
            evs = api.events(s, commenceTimeFrom=iso(NOW - timedelta(minutes=5)), commenceTimeTo=horizon)
        except (APIError, RuntimeError) as e:          # one sport's hiccup skips that sport, not the whole run
            errors.append(f"{s}: {e}")
            continue
        if evs:
            soon[s] = sorted(evs, key=lambda e: e["commence_time"])
    # game lines: one call per sport
    for s, evs in soon.items():
        hrs = (datetime.fromisoformat(evs[0]["commence_time"].replace("Z", "+00:00")) - NOW).total_seconds() / 3600
        age = _age_min(st["lines"].get(s))
        lim = max(_limit(C.LINES_STALE, hrs) or 240, P["min_lines_age"])
        close = paid and hrs * 60 <= C.CLOSE_WINDOW_MIN and age > C.CLOSE_STALE_MIN
        hot_due = hot_ok and s in hot_lines and age > _limit(C.HOT_STALE, max(0, hot_lines[s]))
        if not (close or hot_due or ((active or force) and age > lim)):
            continue
        try:
            data = api.odds(s, C.GAME_MARKETS, C.GAME_REGIONS, commenceTimeTo=horizon)
        except BudgetExceeded as e:
            errors.append(f"budget stop ({e})")
            break
        except RuntimeError as e:
            errors.append(str(e))
            continue
        cache_put(f"lines_{s}.json", data)
        save_raw(s, "lines", data)
        st["lines"][s] = iso(NOW)
        pulled.append(f"{C.label(s)} lines")
    # event markets (alternates, periods, every prop): paid plan only
    if P["props"]:
        queue = []
        for s, evs in soon.items():
            if not C.event_markets(s):
                continue
            for ev in evs:
                hrs = (datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) - NOW).total_seconds() / 3600
                age = _age_min(st["events"].get(ev["id"]))
                age_x = _age_min(st.setdefault("eventsX", {}).get(ev["id"]))
                lim = _limit(C.PROPS_STALE, hrs)
                close = hrs * 60 <= C.CLOSE_WINDOW_MIN and age > C.CLOSE_STALE_MIN
                core = close or ((active or force) and lim is not None and age > lim)
                # alternates, periods and specials move less: refreshed at a third of the pace
                ext = (active or force) and lim is not None and age_x > lim * C.EXTENDED_SLOWER and not close
                h = hot.get(ev["id"]) if hot_ok else None
                if h:               # a hot game: core markets on the hot clock, extended ones too when something on it lives there
                    hl = _limit(C.HOT_STALE, max(0, hrs))
                    core = core or age > hl
                    ext = (ext or (h["ext"] and age_x > hl * 2)) and not close
                if core or ext:
                    queue.append((hrs, s, ev, ext, close, bool(h)))
        queue.sort(key=lambda x: (not x[5], x[0]))          # hot games first, so a budget stop never starves them
        n_events = 0
        for hrs_ev, s, ev, ext, close, is_hot in queue:
            try:
                bad = st.setdefault("badMarkets", {}).get(s, [])
                mk = C.event_markets(s) if ext else C.core_markets(s)
                data, rejected = api.event_odds_all(s, ev["id"], [m for m in mk if m not in bad], C.EVENT_BOOKS)
                if rejected:
                    st["badMarkets"][s] = sorted(set(bad) | set(rejected))
                if data is None:
                    raise RuntimeError(f"no markets accepted for {C.label(s)}")
            except BudgetExceeded as e:
                errors.append(f"budget stop after {len(pulled)} pulls ({e})")
                break
            except RuntimeError as e:
                errors.append(str(e))
                st["events"][ev["id"]] = iso(NOW)     # do not retry a failing event until it is stale again
                continue
            try:
                prev = json.load(open(RAW(f"event_{ev['id']}.json")))
            except Exception:
                prev = None
            data = _merge_event(prev, data)
            cache_put(f"event_{ev['id']}.json", data)
            # a hot game refreshes every few minutes, so its pulls are never CLOSE_STALE_MIN old and `close` stays false:
            # save any pull inside the close window when the last saved one is that old (else its CLV close is a day old)
            in_win = 0 <= hrs_ev * 60 <= C.CLOSE_WINDOW_MIN
            if close or ev["id"] not in st.setdefault("savedEv", {}) or (
                    in_win and _age_min(st["savedEv"].get(ev["id"])) > C.CLOSE_STALE_MIN):
                save_raw(s, f"event_{ev['id']}", data)     # history: first full view and the close, not every refresh
                st["savedEv"][ev["id"]] = iso(NOW)
            st["events"][ev["id"]] = iso(NOW)
            if ext:
                st["eventsX"][ev["id"]] = iso(NOW)
            n_events += 1
            n_hot += is_hot
        pulled.append(f"{n_events} events" + (f" ({n_hot} hot)" if n_hot else ""))
    st["usedToday"] = st.get("usedToday", 0) + api.used_run
    if api.remaining is not None:
        st["remaining"] = api.remaining
    for k in ("events", "eventsX", "savedEv"):      # forget finished events
        st[k] = {i: t for i, t in st.get(k, {}).items() if _age_min(t) < 36 * 60}
    if force:
        st["lastForce"] = force_at
    stale_stats = _age_min(st.get("stats")) > 20
    if not api.used_run and not stale_stats:
        save_state(st)
        log(f"active={active} nothing stale ({len(hot)} hot games, all fresh)")
        return
    game_docs, prop_docs = load_cache()
    prev = {}
    try:
        prev = json.load(open(os.path.join(DATA_DIR, "latest", "board.json")))["status"]
    except Exception:
        pass
    status = dict(prev, updated=iso(NOW) if api.used_run else prev.get("updated", iso(NOW)), mode="auto", plan=plan_key,
                  creditsRemaining=api.remaining if api.remaining is not None else prev.get("creditsRemaining"),
                  creditsThisRun=api.used_run, creditsToday=st["usedToday"], linesAt=st["lines"], errors=errors,
                  games={lg: sum(1 for g in game_docs if g["league"] == lg) for lg in sorted({g["league"] for g in game_docs})})
    st["stats"] = iso(NOW)
    save_state(st)
    publish(game_docs, prop_docs, status, stamp=NOW.strftime("%Y-%m-%dT%H%M%S"))
    log(f"hot games: {len(hot)} ({sum('pick' in h['why'] for h in hot.values())} picks, {sum('bet' in h['why'] for h in hot.values())} bets, "
        f"{sum(bool({'arb', 'middle'} & set(h['why'])) for h in hot.values())} arbs/middles, {sum('edge' in h['why'] for h in hot.values())} edges)")
    log(f"active={active} force={force} pulled {pulled} credits {api.used_run} (today {st['usedToday']}), left {api.remaining}")


def plan():
    """What to pull this run. Returns (horizon_hours, prop_window_hours, prop_markets, prop_regions, cap)."""
    if MODE == "mock":
        return 36, 30, C.MOCK_PROP_MARKETS, C.MOCK_PROP_REGIONS, C.MOCK_MAX_CREDITS
    return 168, 30, C.PROP_MARKETS, C.PROP_REGIONS, None


def rebuild():
    """Rebuild the board from the raw cache (or the newest snapshots) for zero credits."""
    import glob
    if glob.glob(RAW("lines_*.json")):
        game_docs, prop_docs = load_cache()
        prev = {}
        try:
            prev = json.load(open(os.path.join(DATA_DIR, "latest", "board.json")))["status"]
        except Exception:
            pass
        status = dict(prev, mode="rebuild", rebuiltAt=iso(NOW), errors=[], creditsThisRun=0)
        status.setdefault("updated", iso(NOW))
        return publish(game_docs, prop_docs, status, stamp=f"cache-{prev.get('updated', '')}")
    days = sorted(glob.glob(os.path.join(DATA_DIR, "snapshots", "*")))
    if not days:
        sys.exit("no snapshots to rebuild from")
    files = sorted(glob.glob(os.path.join(days[-1], "*.json.gz")))
    stamp = os.path.basename(files[-1]).split("_")[0]
    files = [f for f in files if os.path.basename(f).startswith(stamp + "_")]
    game_docs, prop_docs = [], []
    for f in files:
        name = os.path.basename(f)[len(stamp) + 1:-8]
        sport = next((s for s in list(C.SPORTS) if name.startswith(s + "_")), None) or (
            "_".join(name.split("_")[:3]) if name.startswith("tennis_") else None)
        if not sport:
            continue
        data = json.load(gzip.open(f))
        if name.endswith("_lines"):
            cache_put(f"lines_{sport}.json", data)
            game_docs += [build.build_game(ev, C.label(sport)) for ev in data if ev.get("bookmakers")]
        else:
            if data.get("id"):
                cache_put(f"event_{data['id']}.json", data)
            prop_docs += list(build.build_props(data, C.label(sport)).values())
    prev = {}
    try:
        prev = json.load(open(os.path.join(DATA_DIR, "latest", "board.json")))["status"]
    except Exception:
        pass
    status = dict(prev, mode="rebuild", rebuiltAt=iso(NOW), errors=[], creditsThisRun=0)
    status.setdefault("updated", iso(NOW))
    publish(game_docs, prop_docs, status, stamp=f"{os.path.basename(days[-1])}T{stamp}")


def main():
    if MODE not in ("live", "watch"):       # the self-correcting fair (fairadj.py): pre-game prices only
        import fairadj
        build.FAIR_SHIFT = fairadj.load(DATA_DIR)
    if MODE == "probe":
        import probe  # noqa: F401  (runs on import)
        return
    if MODE == "depthprobe":
        import depthprobe  # noqa: F401  (runs on import: where order-book depth can come from)
        return
    if MODE == "stats":
        return run_stats(backfill="backfill" in open(os.path.join(os.path.dirname(__file__), "run-request")).read())
    if MODE == "rebuild":
        _promodesk()
        return rebuild()
    if MODE == "live":
        return live()
    if MODE == "watch":
        return watch()
    if MODE == "ppprobe":
        return _ppprobe()
    if MODE == "marketbackfill":
        import market
        return market.backfill(DATA_DIR, NOW, log)
    if MODE == "splitsprobe":
        import market
        return market.probe(DATA_DIR, log)
    if MODE == "leadlag":
        import leadlag
        return leadlag.run(DATA_DIR, log)
    if MODE == "policytest":
        import policytest
        return policytest.run(DATA_DIR, log)
    if MODE == "audit":
        import audit
        return audit.run(firestore_db(), DATA_DIR, log)
    if MODE == "history":
        return run_history()
    if MODE == "rtdb_setup":
        import rtdb_setup
        return rtdb_setup.main(DATA_DIR, log)
    if MODE == "backtest":
        import backtest
        return backtest.main(DATA_DIR)
    if MODE in ("auto", "refresh"):     # refresh = a forced pull (Claude's pick runs ask for one first)
        return auto()
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY secret is missing or empty")
    horizon, prop_win, prop_mk, prop_rg, cap = plan()
    api = OddsAPI(key, max_credits=cap)
    errors = []

    active = {s["key"] for s in api.sports() if s.get("active")}
    sports = [s for s in C.SPORTS if s in active] + sorted(s for s in active if s.startswith("tennis_") and C.wanted(s))
    log(f"mode={MODE} sports active={len(sports)}")

    to = iso(NOW + timedelta(hours=horizon))
    game_docs, prop_docs, raw_by_sport = [], [], {}
    upcoming = {}
    for s in sports:
        try:
            evs = api.events(s, commenceTimeFrom=iso(NOW - timedelta(hours=3)), commenceTimeTo=to)
        except RuntimeError as e:                       # one sport's hiccup skips that sport, not the whole run
            errors.append(f"{s}: {e}")
            continue
        if not evs:
            continue
        upcoming[s] = evs
        try:
            data = api.odds(s, C.GAME_MARKETS, C.GAME_REGIONS, commenceTimeTo=to)
        except BudgetExceeded as e:
            errors.append(f"budget stop before {C.label(s)} lines ({e})")
            break
        except RuntimeError as e:
            errors.append(str(e))
            continue
        save_raw(s, "lines", data)
        cache_put(f"lines_{s}.json", data)
        raw_by_sport[s] = data
        for ev in data:
            if ev.get("bookmakers"):
                game_docs.append(build.build_game(ev, C.label(s)))
        log(f"{C.label(s)}: {len(data)} games, run credits {api.used_run}")

    # props: soonest games first, across sports, until the budget says stop
    cutoff = NOW + timedelta(hours=prop_win)
    queue = []
    for s, evs in upcoming.items():
        if s not in prop_mk:
            continue
        for ev in evs:
            t = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if NOW - timedelta(minutes=10) <= t <= cutoff:
                queue.append((t, s, ev))
    queue.sort(key=lambda x: x[0])
    n_props = 0
    for _, s, ev in queue:
        try:
            data = api.event_odds(s, ev["id"], prop_mk[s], prop_rg)
        except BudgetExceeded as e:
            errors.append(f"budget stop after {n_props} prop games ({e})")
            break
        except RuntimeError as e:
            errors.append(str(e))
            continue
        save_raw(s, f"props_{ev['id']}", data)
        cache_put(f"event_{ev['id']}.json", data)
        docs = build.build_props(data, C.label(s))
        prop_docs.extend(docs.values())
        n_props += 1
    log(f"props: {n_props} games, {len(prop_docs)} market docs, run credits {api.used_run}")

    status = {
        "updated": iso(NOW), "mode": MODE,
        "creditsRemaining": api.remaining, "creditsUsedTotal": api.used_total,
        "creditsThisRun": api.used_run,
        "games": {lg: sum(1 for g in game_docs if g["league"] == lg) for lg in sorted({g["league"] for g in game_docs})},
        "propGames": n_props, "errors": errors,
    }
    publish(game_docs, prop_docs, status, stamp=NOW.strftime("%Y-%m-%dT%H%M%S"))
    log(f"credits left {api.remaining}, errors {len(errors)}")


def run_stats(backfill=False):
    import stats
    diag = {"at": iso(NOW), "nflverse": stats.nflverse(DATA_DIR), "tennis": stats.tennis(DATA_DIR), "mlb": stats.mlb(DATA_DIR)}
    os.makedirs(os.path.join(DATA_DIR, "stats"), exist_ok=True)
    json.dump(diag, open(os.path.join(DATA_DIR, "stats", "last_run.json"), "w"), indent=1)   # private repo: full diagnostics
    log(f"stats sources: {sum(1 for v in diag['tennis'].values() if isinstance(v, int))} tennis files")
    for lg in stats.LEAGUES:
        n = stats.collect(lg, DATA_DIR, backfill=backfill)
        inj = stats.injuries(lg, DATA_DIR) if lg in stats.PLAYER_LEAGUES else None
        log(f"stats {lg}: {n} games fetched, injuries for {len(inj or {})} teams")
    log(f"moneypuck: {stats.moneypuck(DATA_DIR)}")
    try:
        diag["mpGames"] = stats.moneypuck_games(DATA_DIR)
        json.dump(diag, open(os.path.join(DATA_DIR, "stats", "last_run.json"), "w"), indent=1)
    except Exception as e:
        log(f"moneypuck games failed: {type(e).__name__}")


def _backtest_summary():
    """historical backfill progress (history/state.json) and walk-forward results (calib/backtest.json) for the Reports page"""
    out = {}
    try:
        st = json.load(open(os.path.join(DATA_DIR, "history", "state.json")))
        import oddshistory as history
        out["history"] = {"games": st.get("games", 0), "spent": st.get("spent", 0), "budget": history.BUDGET, "complete": st.get("complete"),
                          "at": st.get("at"), "days": {k: len(v) for k, v in st.get("done", {}).items()}}
    except Exception:
        pass
    try:
        bt = json.load(open(os.path.join(DATA_DIR, "calib", "backtest.json")))
        out.update(at=bt.get("at"), rows=bt.get("rows"), markets=bt.get("markets", [])[:60])
    except Exception:
        pass
    return out


def load_picks(days=14):
    """Claude's picks live in the data repo as picks/<YYYY-MM-DD>.json (a list)."""
    import glob
    out = []
    for fn in sorted(glob.glob(os.path.join(DATA_DIR, "picks", "*.json")))[-days:]:
        try:
            for p in json.load(open(fn)):
                if p.get("id"):
                    out.append(p)
        except Exception as e:
            log(f"picks file unreadable: {os.path.basename(fn)} ({type(e).__name__})")
    return out


def load_previews(days=3):
    """Claude's written previews: previews/<YYYY-MM-DD>.json, a list of {gameId, ...}."""
    import glob
    out = []
    for fn in sorted(glob.glob(os.path.join(DATA_DIR, "previews", "*.json")))[-days:]:
        try:
            out += [p for p in json.load(open(fn)) if p.get("gameId")]
        except Exception as e:
            log(f"previews file unreadable: {os.path.basename(fn)} ({type(e).__name__})")
    return out


def publish(game_docs, prop_docs, status, stamp):
    """Decorate, score, report, and write everywhere."""
    by_sport = {}
    for g in game_docs:
        by_sport.setdefault(g["sport"], []).append(g)
    for p in prop_docs:
        by_sport.setdefault(p["sport"], [])
    n_logo = n_head = 0
    for sport in by_sport:
        R = refs.load(sport, DATA_DIR)
        for g in by_sport[sport]:
            refs.decorate_game(g, R)
            n_logo += bool(g.get("homeRef"))
        for p in prop_docs:
            if p["sport"] == sport:
                refs.decorate_props(p, R)
                n_head += sum(1 for r in p["rows"] if r.get("headshot"))
    if MODE != "rebuild" or os.environ.get("REFRESH_STATS"):
        try:
            import stats
            for lg in stats.LEAGUES:
                stats.collect(lg, DATA_DIR)
                if lg in stats.PLAYER_LEAGUES:
                    stats.injuries(lg, DATA_DIR)
            stats.mlb(DATA_DIR)
            stats.moneypuck(DATA_DIR)
            nv = os.path.join(DATA_DIR, "stats", "nfl", "nflverse", "team_week_2026.json")
            if not os.path.exists(nv) or (NOW.timestamp() - os.path.getmtime(nv)) > 12 * 3600:
                stats.nflverse(DATA_DIR)
        except Exception as e:
            log(f"stats refresh failed: {type(e).__name__}")
    today = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    import tracker
    fin, mstats = {}, None
    try:
        n_graded = 0
        try:        # before grading: finals read the pre-game snapshots that grading clears
            import finals
            fin = finals.collect(DATA_DIR, sorted({g["sport"] for g in game_docs}), game_docs, NOW, log=log)
        except Exception as e:
            log(f"finals failed: {type(e).__name__}: {str(e)[:100]}")
            fin = {}
        n_graded = tracker.grade(DATA_DIR, NOW)
        try:        # the ledger: grade Vince's pending bets and our picks (result + closing line value)
            import ledger
            fin_by = {d["gameId"]: d for d in fin.values() if d.get("gameId")}
            if firestore_db():
                ledger.apply_fixes(firestore_db(), DATA_DIR, log=log)
                ledger.apply_inbox(firestore_db(), DATA_DIR, log=log)
            n_p = ledger.grade_picks(DATA_DIR, fin_by, log=log)
            n_b = ledger.grade_bets(firestore_db() if os.environ.get("FIREBASE_SERVICE_ACCOUNT") else None, DATA_DIR, fin_by, log=log)
            if firestore_db():
                ledger.debug_bets(firestore_db(), DATA_DIR, fin_by)
                ledger.backfill_leagues(firestore_db(), DATA_DIR, NOW, log=log)
                ledger.backfill_settled(firestore_db(), DATA_DIR, fin_by, NOW, log=log)
            if n_p or n_b:
                log(f"ledger: {n_p} picks and {n_b} bets graded")
        except Exception as e:
            log(f"ledger grading failed: {type(e).__name__}: {str(e)[:100]}")
        params, card = tracker.fit(DATA_DIR)
        try:            # deep model statistics (Reports): only when the graded rows changed
            import modelstats
            mstats, mnew = modelstats.compute(DATA_DIR, log=log)
            modelstats.oos_weights(DATA_DIR, params, mstats, log=log)
            if mnew or not os.path.exists(os.path.join(DATA_DIR, "calib", "fairadj.json")):
                try:        # refit and re-test the fair correction (takes effect from the next run)
                    import fairadj
                    fairadj.compute(DATA_DIR, log=log)
                except Exception as e:
                    log(f"fairadj failed: {type(e).__name__}: {str(e)[:80]}")
            import policytest
            if mnew or "ai" not in (mstats.get("policy") or {}) or (mstats.get("policy") or {}).get("v") != policytest.VERSION:     # did the edge muting and the gated blend help? (walk-forward, live record)
                pt = policytest.run(DATA_DIR, log=log, ms=mstats)
                mstats["policy"] = {"muting": {k: pt["muting"][k] for k in ("all", "kept", "muted")} | {"days": pt["muting"]["days"]},
                                    "mutingLogic": {k: pt["mutingLogic"][k] for k in ("all", "kept", "muted")},
                                    "mutingSupport": {k: pt["mutingSupport"][k] for k in ("all", "kept", "muted")} | {"days": pt["mutingSupport"]["days"]},
                                    "blending": pt["blending"], "residual": pt.get("residual"), "logic": pt.get("logic"), "ai": pt.get("ai"), "v": policytest.VERSION}
                json.dump(mstats, open(os.path.join(DATA_DIR, "stats", "modelstats.json"), "w"), separators=(",", ":"))
            try:        # every model / logic / pick-rule change with its before-after effect (changes.py), Reports > Models
                import changes as CH
                fn_ch = os.path.join(DATA_DIR, "stats", "changes.json")
                if mnew or not os.path.exists(fn_ch) or _age_min(datetime.fromtimestamp(os.path.getmtime(fn_ch), timezone.utc).isoformat()) > 180:
                    mstats["changes"] = CH.compute(DATA_DIR, log=log)
                else:
                    mstats["changes"] = json.load(open(fn_ch))
                mstats["timeline"] = CH.daily(DATA_DIR)
            except Exception as e:
                log(f"changes failed: {type(e).__name__}: {str(e)[:80]}")
                try:
                    mstats["changes"] = json.load(open(os.path.join(DATA_DIR, "stats", "model_changes.json")))
                except Exception:
                    pass
            try:        # the self-correcting fair: what's live, and the walk-forward test behind it
                fa = json.load(open(os.path.join(DATA_DIR, "calib", "fairadj.json")))
                mstats["fairadj"] = {k: fa.get(k) for k in ("at", "halfLife", "shifts", "eval")}
            except Exception:
                pass
        except Exception as e:
            log(f"modelstats failed: {type(e).__name__}: {str(e)[:100]}")
    except Exception as e:
        log(f"tracker failed: {type(e).__name__}: {str(e)[:100]}")
        n_graded, params, card = 0, tracker.load_params(DATA_DIR), []
    mups = matchup.build_all(game_docs, prop_docs, DATA_DIR, today)
    try:        # props no sportsbook prices (faceoffs won, pick'em-only stats): an estimate from the player's last games
        import history
        history.stamp(DATA_DIR, prop_docs, NOW, log=log)
    except Exception as e:
        log(f"history failed: {type(e).__name__}")
    n_cal = tracker.apply(params, game_docs, prop_docs)
    edge_list = build.edges(game_docs, prop_docs)
    import edgecard
    n_moved = 0
    try:        # opening fairs first: the tracker's snapshot keeps them (residual feature op) and market.explain reads them
        n_moved = edgecard.opens(DATA_DIR, game_docs, prop_docs, NOW)      # (props' move / against reasons)
        edgecard.annotate_moves(edge_list, game_docs, prop_docs)
    except Exception as e:
        log(f"opens failed: {type(e).__name__}")
    try:
        tracker.snapshot(DATA_DIR, game_docs, prop_docs, NOW)
    except Exception as e:
        log(f"snapshot failed: {type(e).__name__}")
    # the edges' report card: log new edges, refresh their fairs to the close, grade finished ones, re-rank by history
    ecard, pbook, mcard, mboard = None, None, None, None
    try:        # market behavior: line movement by book, who moved first, DraftKings splits -> doc["mkt"], e["mkt"]
        import market
        mboard = market.run(DATA_DIR, game_docs, edge_list, NOW, log)
    except Exception as e:
        log(f"market failed: {type(e).__name__}: {str(e)[:100]}")
    try:
        try:            # steam: fair moved toward the edge's side in the last hour (graded by the report card)
            edgecard.steam(DATA_DIR, edge_list, game_docs, prop_docs, NOW)
        except Exception as e:
            log(f"steam failed: {type(e).__name__}")
        n_new = edgecard.observe(DATA_DIR, game_docs, prop_docs, edge_list, NOW)
        n_eg = edgecard.grade(DATA_DIR, {d["gameId"]: d for d in (fin or {}).values() if d.get("gameId")}, NOW)
        try:        # grade the market signals on finished games (how sides that moved / got the money did vs their close)
            import market
            n_mg = market.grade(DATA_DIR, {d["gameId"]: d for d in (fin or {}).values() if d.get("gameId")}, log)
            if n_mg or not os.path.exists(os.path.join(DATA_DIR, "stats", "marketcard.json")):
                mcard = market.card(DATA_DIR, log)
            else:
                mcard = json.load(open(os.path.join(DATA_DIR, "stats", "marketcard.json")))
        except Exception as e:
            log(f"market grading failed: {type(e).__name__}: {str(e)[:100]}")
        try:        # calibrated CLV: refit how much our closes are really worth when new edges graded (or none yet)
            import closecal
            if n_eg or not os.path.exists(os.path.join(DATA_DIR, "calib", "closecal.json")):
                closecal.compute(DATA_DIR, log=log)
        except Exception as e:
            log(f"closecal failed: {type(e).__name__}: {str(e)[:80]}")
        ecard = edgecard.summarize(DATA_DIR, now=NOW)
        try:
            import closecal
            cc = closecal.load(DATA_DIR) or {}
            ecard["closecal"] = {"wf": cc.get("wf"), "params": cc.get("params")}
        except Exception:
            pass
        edgecard.rerank(edge_list, ecard)
        try:        # what the record says to lean on and avoid right now (Edges page, pick runs), stamped on each edge
            import playbook
            pbook = playbook.build(DATA_DIR, ecard, params)
            playbook.tag(edge_list, pbook, NOW)
        except Exception as e:
            log(f"playbook failed: {type(e).__name__}: {str(e)[:80]}")
        status["moved"] = n_moved
        log(f"edge card: {n_new} new edges logged, {n_eg} graded, {ecard.get('n', 0)} in the card; {n_moved} lines moved since first seen")
    except Exception as e:
        log(f"edge card failed: {type(e).__name__}: {str(e)[:120]}")
    opp_list = []
    try:            # arbs and middles across Vince's venues, then phone alerts for the strong new ones
        import opps as OP
        import alerts
        opp_list = OP.find(game_docs, prop_docs, iso(NOW), pulled=load_state())
        try:        # which ones are real: survived a second pull, fresh book prices, no proxy venue, Vince's own checks
            opp_list = OP.annotate(opp_list, DATA_DIR, NOW, db=firestore_db(), log=log)
        except Exception as e:
            log(f"opps annotate failed: {type(e).__name__}: {str(e)[:80]}")
        status["opps"] = len(opp_list)
        n_al = alerts.send(DATA_DIR, NOW, edges=edge_list[:60], opps=opp_list, log=log)
        try:
            if firestore_db() and alerts.share_topic(firestore_db(), DATA_DIR):
                log("alerts: topic shared with the site")
        except Exception as e:
            log(f"alerts: could not share the topic ({type(e).__name__})")
        log(f"opportunities: {sum(o['kind'] == 'arb' for o in opp_list)} arbs, {sum(o['kind'] == 'middle' for o in opp_list)} middles; {n_al} alerts sent")
    except Exception as e:
        log(f"opportunities failed: {type(e).__name__}: {str(e)[:120]}")
    status["tuning"] = {"graded": n_graded, "groups": len(params), "calibrated": n_cal}
    for e in edge_list:     # what the model knew about this game beyond the box scores (verified news, weather, rest)
        c = (mups.get(e["gameId"]) or {}).get("context")
        if c:
            e["ctx"] = c[:3]
    status["edges"] = len(edge_list)
    status["matchups"] = len(mups)
    status["calibration"] = getattr(matchup.build_all, "shifts", {})
    rep = reports.update(game_docs, prop_docs, DATA_DIR, stamp)
    if isinstance(rep, dict):
        rep["models"] = [r for r in card if r.get("llModel") is not None][:80]
        rep["modelsPending"] = [r for r in card if r.get("llModel") is None][:80]
        rep["backtest"] = _backtest_summary()
        if mcard:
            rep["marketcard"] = mcard
        if ecard:
            rep["edgecard"] = ecard
            if pbook:
                rep["playbook"] = pbook
    picks = load_picks()
    previews = load_previews()
    try:            # where each open pick can be played right now (prices drift, pick'em lines get pulled)
        import picklive
        pl = picklive.check(picks, game_docs, prop_docs, NOW)
        try:            # the first real price at each venue, which grading uses (never the price the pick run wrote)
            import pickprices
            pickprices.record_first(DATA_DIR, pl, iso(NOW))
        except Exception as e:
            log(f"pick prices not recorded: {type(e).__name__}")
        json.dump(pl, open(os.path.join(DATA_DIR, "latest", "pickslive.json"), "w"), indent=0)
    except Exception as e:
        pl = None
        log(f"pick check failed: {type(e).__name__}: {str(e)[:100]}")
    write_data_repo(game_docs, prop_docs, edge_list, status)
    with open(os.path.join(DATA_DIR, "latest", "matchups.json"), "w") as f:
        json.dump(mups, f, separators=(",", ":"))
    with open(os.path.join(DATA_DIR, "latest", "opps.json"), "w") as f:
        json.dump(opp_list, f, separators=(",", ":"))
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
        pre_ok = False
        try:        # games, props, edges and status go to the Realtime Database (free, only changed pieces sent)
            import rt
            rc = rt.ensure_rules(DATA_DIR, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if rc != 200:
                raise RuntimeError(f"rules {rc}")
            kb, npcs = rt.publish_pre(game_docs, prop_docs, edge_list, status, DATA_DIR, log, mups=mups, rep=rep, fin=fin, opps=opp_list, pickslive=pl,
                                      mstats=mstats, mkt=mboard)
            pre_ok = True
            log(f"realtime database: {npcs} pieces, {kb} KB")
        except Exception as e:
            log(f"realtime database write failed ({type(e).__name__}); using Firestore for the board")
        try:
            n = write_firestore(game_docs, prop_docs, edge_list, status, rep, picks, mups, previews, fin, pre_ok=pre_ok)
            log(f"firestore: {n} docs written")
        except Exception as e:      # the data repo is already written; the site catches up on the next run
            if not _fs_busy(e):
                raise
            log(f"firestore busy ({type(e).__name__}): skipped this run")
    log(f"published: {len(game_docs)} games ({n_logo} with logos), {len(prop_docs)} prop docs "
        f"({n_head} rows with headshots), {len(edge_list)} edges, {len(mups)} matchups, {len(picks)} picks")


# ---------------------------------------------------------------------------

def _payload(doc):
    s = json.dumps(doc, separators=(",", ":"))
    if len(s) > 950_000:  # Firestore doc limit is 1 MiB: drop soft books from non-main lines
        for l in doc.get("lines", doc.get("rows", [])):
            if not l.get("isMain"):
                l["quotes"] = [q for q in l["quotes"] if q["venue"] or q["kind"] in ("sharp", "exchange")]
        s = json.dumps(doc, separators=(",", ":"))
    return s


def write_data_repo(game_docs, prop_docs, edge_list, status):
    d = os.path.join(DATA_DIR, "latest")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "board.json"), "w") as f:
        json.dump({"status": status, "games": game_docs}, f, separators=(",", ":"))
    with open(os.path.join(d, "props.json"), "w") as f:
        json.dump(prop_docs, f, separators=(",", ":"))
    with open(os.path.join(d, "edges.json"), "w") as f:
        json.dump(edge_list, f, separators=(",", ":"))
    with open(os.path.join(DATA_DIR, "credits.jsonl"), "a") as f:
        f.write(json.dumps({"t": status["updated"], "mode": MODE, "run": status.get("creditsThisRun", 0),
                            "remaining": status.get("creditsRemaining")}) + "\n")


def write_firestore(game_docs, prop_docs, edge_list, status, rep=None, picks=(), mups=None, previews=(), fin=None, pre_ok=False):
    db = firestore_db()

    hash_file = os.path.join(DATA_DIR, "latest", "hashes.json")
    try:
        old = json.load(open(hash_file))
    except Exception:
        old = {}
    new, writes = {}, []

    def put(path, meta, doc):
        s = _payload(doc)
        h = hashlib.sha1(s.encode()).hexdigest()
        new[path] = h
        if old.get(path) != h:
            writes.append((path, dict(meta, payload=s, updated=status["updated"])))

    if pre_ok:          # the board lives in the Realtime Database: Firestore keeps only picks, previews and user data
        game_docs, prop_docs, rep, mups, fin = [], [], None, None, None
    for g in game_docs:
        put(f"games/{g['id']}", {"sport": g["sport"], "league": g["league"], "commence": g["commence"],
                                 "home": g["home"], "away": g["away"]}, g)
    for p in prop_docs:
        put(f"props/{p['id']}__{p['market']}", {"gameId": p["id"], "market": p["market"],
                                                "league": p["league"], "commence": p["commence"]}, p)
    if not pre_ok:
        put("board/edges", {}, {"items": edge_list[:400]})
    if rep:
        put("board/reports", {}, rep)
    for gid, mu in (mups or {}).items():
        put(f"matchups/{gid}", {"league": mu["league"], "commence": mu["commence"]}, mu)
    for pv in previews:
        put(f"previews/{pv['gameId']}", {"date": pv.get("date")}, pv)
    for pk in picks:
        put(f"picks/{pk['id']}", {"date": pk.get("date"), "gameId": pk.get("gameId"),
                                  "status": pk.get("status", "pending")}, pk)
    if fin is not None:
        import finals
        for k, d in fin.items():
            put(f"finals/{k}", {"date": d["date"], "league": d["league"], "gameId": d.get("gameId")}, d)
        put("board/finals", {}, {"games": finals.summary(fin)})
    if not pre_ok:
        writes.append(("board/status", status))

    # Spark plan: 20k writes a day, shared with the live lane (its count is in live_state.json). Prop docs for games more
    # than 3 hours out are rewritten at most every 30 minutes; past 12k writes, prop docs only for games starting within
    # 2 hours; past 16.5k, none.
    day = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    used = old.get("_writes", {}) if isinstance(old.get("_writes"), dict) else {}
    used_today = used.get("n", 0) if used.get("day") == day else 0
    try:
        lsf = json.load(open(os.path.join(DATA_DIR, "latest", "live_state.json")))
        live_today = (lsf.get("fs") or {}).get("n", 0) if (lsf.get("fs") or {}).get("day") == day else 0
    except Exception:
        live_today = 0
    q = old.get("_quota") or {}
    if q.get("day") == day and _age_min(q.get("at")) < 60:
        log("firestore: daily quota hit earlier, skipping writes for now (data repo still updated)")
        return 0
    pt_old = old.get("_pt") or {}
    new["_pt"] = {}
    soon, later = iso(NOW + timedelta(hours=2)), iso(NOW + timedelta(hours=3))
    kept = []
    for path, data in writes:
        if path.startswith("props/"):
            tot = used_today + live_today + len(kept)
            far = data.get("commence", "") > later
            if tot > 16500 or (tot > 12000 and data.get("commence", "") > soon) or (far and path in pt_old and _age_min(pt_old[path]) < 30):
                if path in old:
                    new[path] = old[path]       # not written: keep the old hash so the change is written later
                else:
                    new.pop(path, None)
                if path in pt_old:
                    new["_pt"][path] = pt_old[path]
                continue
            new["_pt"][path] = iso(NOW)
        kept.append((path, data))
    if len(kept) < len(writes):
        log(f"firestore write budget: deferred {len(writes) - len(kept)} prop docs")
    writes = kept

    # batches stay well under Firestore's request size limit; a quota error keeps what was committed and stops cleanly
    batch, n, size, inb, done = db.batch(), 0, 0, [], 0
    try:
        for path, data in writes:
            sz = len(data.get("payload", "")) + 500 if isinstance(data, dict) else 500
            if inb and (size + sz > 6_000_000 or len(inb) >= 400):
                batch.commit()
                done += len(inb)
                batch, size, inb = db.batch(), 0, []
            batch.set(db.document(path), data)
            inb.append(path)
            n += 1
            size += sz
        if pre_ok and not old.get("_fsPreCleared2"):      # the old Firestore copies of the board, once
            for col in ("games", "props", "matchups", "finals"):
                for snap in db.collection(col).stream():
                    batch.delete(snap.reference)
                    inb.append(None)
                    n += 1
                    if len(inb) >= 400:
                        batch.commit()
                        done += len(inb)
                        batch, size, inb = db.batch(), 0, []
            for d in ("board/edges", "board/reports", "board/finals"):
                batch.delete(db.document(d))
                inb.append(None)
            new["_fsPreCleared2"] = True
        elif old.get("_fsPreCleared2"):
            new["_fsPreCleared2"] = True
        # remove docs for games that ended more than 6 hours ago
        gone_before = iso(NOW - timedelta(hours=6))
        for col in ("games", "props", "matchups"):
            for snap in db.collection(col).where("commence", "<", gone_before).stream():
                batch.delete(snap.reference)
                new.pop(f"{col}/{snap.id}", None)
                inb.append(None)
                n += 1
        if fin is not None:     # finals: keep three days
            cut = (NOW - timedelta(hours=7) - timedelta(days=2)).strftime("%Y-%m-%d")
            for snap in db.collection("finals").where("date", "<", cut).stream():
                batch.delete(snap.reference)
                new.pop(f"finals/{snap.id}", None)
                inb.append(None)
                n += 1
        batch.commit()
        done += len(inb)
    except Exception as e:
        if not _fs_busy(e):
            raise
        for path in inb:              # this batch didn't land: write it again next time
            if path:
                if path in old:
                    new[path] = old[path]
                else:
                    new.pop(path, None)
        new["_quota"] = {"day": day, "at": iso(NOW)}
        log(f"firestore: {type(e).__name__} after {done} writes; pausing writes for an hour")
        n = done
    if "_quota" not in new and q.get("day") == day:
        new["_quota"] = q
    new["_writes"] = {"day": day, "n": used_today + n}
    with open(hash_file, "w") as f:
        json.dump(new, f)
    return n


def _fs_busy(e):
    """Firestore saying no for now: daily quota (429), timeouts (504), unavailable (503)"""
    t = f"{type(e).__name__} {e}".lower()
    return any(x in t for x in ("quota", "429", "resourceexhausted", "deadline", "504", "503", "unavailable"))


def _record_failure(e):
    """write what went wrong to the private data repo (key redacted) so it can be read without the Actions log"""
    key = os.environ.get("ODDS_API_KEY") or "\x00"
    msg = f"{type(e).__name__}: {e}"
    detail = getattr(e, "detail", "") or ""
    info = {"at": iso(NOW), "mode": MODE, "error": msg.replace(key, "***")[:400], "detail": str(detail).replace(key, "***")[:400],
            "where": [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in __import__("traceback").extract_tb(e.__traceback__)][-6:],
            "keyLength": len(os.environ.get("ODDS_API_KEY") or ""), "keyHasSpace": any(c.isspace() for c in (os.environ.get("ODDS_API_KEY") or ""))}
    try:
        os.makedirs(os.path.join(DATA_DIR, "latest"), exist_ok=True)
        json.dump(info, open(os.path.join(DATA_DIR, "latest", "last_error.json"), "w"), indent=1)
    except Exception:
        pass
    log(f"run failed: {type(e).__name__}")


if __name__ == "__main__":
    import atexit
    atexit.register(_save_log)
    try:
        main()
        try:
            os.remove(os.path.join(DATA_DIR, "latest", "last_error.json"))
        except OSError:
            pass
    except SystemExit:
        raise
    except Exception as e:
        _record_failure(e)
        sys.exit(1)
