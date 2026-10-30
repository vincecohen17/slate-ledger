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
        if w and w[0] in ("mock", "live", "watch", "auto", "refresh", "rebuild", "probe", "stats", "history", "backtest"):
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
    import history
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
    if _age_min(ls.get("betsAt")) >= 5 or "betLegs" not in ls or (pres.get("betsAt") or "") > (ls.get("betsAt") or ""):
        try:
            _db = firestore_db()
            if _db:
                found, live_open = {}, []
                for snap in _db.collection("bets").where("status", "==", "pending").stream():
                    b = snap.to_dict() or {}
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
        except Exception as e:
            log(f"open bets read failed: {type(e).__name__}")
    bet_legs = {g: dict(v) for g, v in (ls.get("betLegs") or {}).items()}
    prop_games = set(sorted(bet_legs)[:4])          # live prop prices cost credits: at most 4 games besides the watched one
    # our open prop picks get the same live and final stat (read from the picks files here: no database reads)
    try:
        for pk in load_picks(3):
            if pk.get("status") in ("void", "won", "lost", "push") or pk.get("tier") == "likely" or not pk.get("gameId") or not pk.get("player"):
                continue
            r = LB.resolve({k: pk.get(k) for k in ("gameId", "market", "side", "point", "player", "team")} | {"label": pk.get("selection")}, DATA_DIR)
            if r and r.get("player"):
                bet_legs.setdefault(pk["gameId"], {})[f"{pk['id']}#0"] = {k: r.get(k) for k in ("gameId", "market", "side", "point", "player", "label")} \
                    | {k: pk.get(k) for k in ("matchup", "league", "commence")}
    except Exception as e:
        log(f"open picks read failed: {type(e).__name__}")
    bet_games = set(bet_legs)
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
            gl = [dict(x, lines=[]) for x in started if x["sport"] == s and x["id"] in bet_legs]
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
            want = pres.get("game") == g["id"] or g["id"] in prop_games
            if want and st and s in ("basketball_nba", "basketball_wnba", "icehockey_nhl", "americanfootball_nfl"):
                # prop prices refresh every few minutes; the player stats (ESPN box score, free) refresh every run,
                # so between price pulls the last lines are kept and re-projected instead of disappearing
                pdir = os.path.join(DATA_DIR, "latest", "live_props")
                pfn = os.path.join(pdir, f"{g['id']}.json")
                try:
                    if full and _age_min(ls["props"].get(g["id"])) >= C.LIVE_PROP_EVERY_MIN:
                        pm = [m for m in C.core_markets(s) if build.is_player_market(m)]
                        ed, _ = api.event_odds_all(s, g["id"], pm, C.EVENT_BOOKS)
                        props = list(build.build_props(ed, C.label(s)).values()) if ed else []
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
    missing = [gid for gid in bet_games if gid not in have]
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
            info = next((v for v in bet_legs[gid].values() if v.get("matchup") and " @ " in v["matchup"] and lg2s.get(v.get("league"))), None)
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
                bl = LB.compute(DATA_DIR, s, g["id"], st, bet_legs[g["id"]], log)
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
            sent, nw = rt.publish(docs, {"games": summary, "edges": edges_all[:150], "updated": iso(NOW), "creditsToday": ls["used"]}, ls, DATA_DIR)
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
        log(f"live: {len(docs)} games, {len(edges_all)} edges, credits {api.used_run} (today {ls['used']})")
    # still worth watching: an open leg on a game in play, or one starting within 15 minutes
    soon = any(NOW - timedelta(minutes=20) <= datetime.fromisoformat(v["commence"].replace("Z", "+00:00")) <= NOW + timedelta(minutes=15)
               for legs in bet_legs.values() for v in legs.values() if v.get("commence"))
    return {"need": soon or any(d.get("betLegs") and not d.get("final") for d in docs)}


def watch():
    """The watcher: one long job (Actions minutes are free on this public repo) that keeps Vince's open bets current
    every minute until they're done, app open or not. While the site is on a live screen (presence.live in the last 3
    minutes) each pass is a full live pull (prices, edges; the same credits the site's own live runs used); otherwise
    it's free (ESPN only). Ends when nothing is left to watch, or after 50 minutes (the auto run starts the next one)."""
    import time
    global NOW
    t0 = time.time()
    passes = {True: 0, False: 0}
    while True:
        NOW = datetime.now(timezone.utc)
        t1 = time.time()
        pres = presence_doc()
        site_live = bool(pres.get("live")) and _age_min(pres.get("at")) <= 3
        try:
            r = live(watch=True, full=site_live, pres=pres) or {}
        except Exception as e:
            log(f"watch: pass failed ({type(e).__name__}: {str(e)[:120]})")
            r = {"need": True}
        passes[site_live] += 1
        if not (r.get("need") or site_live):
            log("watch: nothing left in play")
            break
        if time.time() - t0 > 50 * 60:
            log("watch: 50 minutes up; the next auto run picks it up")
            break
        time.sleep(max(5, 60 - (time.time() - t1)))
    log(f"watch: {passes[True]} full passes, {passes[False]} free passes in {round((time.time() - t0) / 60)} min")


def _watch_wanted():
    """auto runs: should a watcher be running? Open bets (or picks) on a game in play or starting within 15 minutes.
    Reads the pending bets once (a handful of Firestore reads) and asks GitHub whether a watcher is already going."""
    db = firestore_db()
    if not db or not os.environ.get("GITHUB_TOKEN"):
        return False
    lo, hi = iso(NOW - timedelta(hours=5)), iso(NOW + timedelta(minutes=15))
    want = False
    try:
        for snap in db.collection("bets").where("status", "==", "pending").stream():
            b = snap.to_dict() or {}
            if any(lo <= (x.get("commence") or b.get("commence") or "") <= hi for x in (b.get("legs") or [b])):
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
    if not want:
        return False
    import urllib.request
    h = {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"}
    base = "https://api.github.com/repos/vincecohen17/slate-ledger/actions/workflows/pull.yml"
    try:
        for stt in ("in_progress", "queued"):
            runs = json.load(urllib.request.urlopen(urllib.request.Request(f"{base}/runs?status={stt}&per_page=20", headers=h), timeout=20))
            if any((r.get("display_title") or "") == "watch" for r in runs.get("workflow_runs") or []):
                return False
        urllib.request.urlopen(urllib.request.Request(f"{base}/dispatches", data=json.dumps({"ref": "main", "inputs": {"mode": "watch"}}).encode(),
                                                      method="POST", headers=h), timeout=20)
        log("watch: started the watcher for open bets in play")
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
        if not (close or ((active or force) and age > lim)):
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
                if core or ext:
                    queue.append((hrs, s, ev, ext, close))
        queue.sort(key=lambda x: x[0])
        n_events = 0
        for _, s, ev, ext, close in queue:
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
            if close or ev["id"] not in st.setdefault("savedEv", {}):
                save_raw(s, f"event_{ev['id']}", data)     # history: first full view and the close, not every refresh
                st["savedEv"][ev["id"]] = iso(NOW)
            st["events"][ev["id"]] = iso(NOW)
            if ext:
                st["eventsX"][ev["id"]] = iso(NOW)
            n_events += 1
        pulled.append(f"{n_events} events")
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
        log(f"active={active} nothing stale")
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
    if MODE == "probe":
        import probe  # noqa: F401  (runs on import)
        return
    if MODE == "depthprobe":
        import depthprobe  # noqa: F401  (runs on import: where order-book depth can come from)
        return
    if MODE == "stats":
        return run_stats(backfill="backfill" in open(os.path.join(os.path.dirname(__file__), "run-request")).read())
    if MODE == "rebuild":
        return rebuild()
    if MODE == "live":
        return live()
    if MODE == "watch":
        return watch()
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
        import history
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
    fin = {}
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
            n_p = ledger.grade_picks(DATA_DIR, fin_by, log=log)
            n_b = ledger.grade_bets(firestore_db() if os.environ.get("FIREBASE_SERVICE_ACCOUNT") else None, DATA_DIR, fin_by, log=log)
            if firestore_db():
                ledger.debug_bets(firestore_db(), DATA_DIR, fin_by)
                ledger.backfill_leagues(firestore_db(), DATA_DIR, NOW, log=log)
            if n_p or n_b:
                log(f"ledger: {n_p} picks and {n_b} bets graded")
        except Exception as e:
            log(f"ledger grading failed: {type(e).__name__}: {str(e)[:100]}")
        params, card = tracker.fit(DATA_DIR)
    except Exception as e:
        log(f"tracker failed: {type(e).__name__}: {str(e)[:100]}")
        n_graded, params, card = 0, tracker.load_params(DATA_DIR), []
    mups = matchup.build_all(game_docs, prop_docs, DATA_DIR, today)
    n_cal = tracker.apply(params, game_docs, prop_docs)
    edge_list = build.edges(game_docs, prop_docs)
    try:
        tracker.snapshot(DATA_DIR, game_docs, prop_docs, NOW)
    except Exception as e:
        log(f"snapshot failed: {type(e).__name__}")
    # the edges' report card: log new edges, refresh their fairs to the close, grade finished ones, re-rank by history
    import edgecard
    ecard = None
    try:
        n_moved = edgecard.opens(DATA_DIR, game_docs, prop_docs, NOW)
        n_new = edgecard.observe(DATA_DIR, game_docs, prop_docs, edge_list, NOW)
        n_eg = edgecard.grade(DATA_DIR, {d["gameId"]: d for d in (fin or {}).values() if d.get("gameId")}, NOW)
        ecard = edgecard.summarize(DATA_DIR, now=NOW)
        edgecard.rerank(edge_list, ecard)
        edgecard.annotate_moves(edge_list, game_docs, prop_docs)
        status["moved"] = n_moved
        log(f"edge card: {n_new} new edges logged, {n_eg} graded, {ecard.get('n', 0)} in the card; {n_moved} lines moved since first seen")
    except Exception as e:
        log(f"edge card failed: {type(e).__name__}: {str(e)[:120]}")
    opp_list = []
    try:            # arbs and middles across Vince's venues, then phone alerts for the strong new ones
        import opps as OP
        import alerts
        opp_list = OP.find(game_docs, prop_docs, iso(NOW), pulled=load_state())
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
        if ecard:
            rep["edgecard"] = ecard
    picks = load_picks()
    previews = load_previews()
    try:            # where each open pick can be played right now (prices drift, pick'em lines get pulled)
        import picklive
        pl = picklive.check(picks, game_docs, prop_docs, NOW)
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
            kb, npcs = rt.publish_pre(game_docs, prop_docs, edge_list, status, DATA_DIR, log, mups=mups, rep=rep, fin=fin, opps=opp_list, pickslive=pl)
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
