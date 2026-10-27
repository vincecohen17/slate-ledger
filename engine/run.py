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
from api import OddsAPI, BudgetExceeded  # noqa: E402

NOW = datetime.now(timezone.utc)
def _mode():
    m = os.environ.get("MODE", "").strip()
    if m:
        return m
    try:  # push-triggered runs read the mode from the first word of engine/run-request
        w = open(os.path.join(os.path.dirname(__file__), "run-request")).read().split()
        if w and w[0] in ("mock", "live", "auto", "refresh", "rebuild", "probe", "stats", "history", "backtest"):
            return w[0]
    except OSError:
        pass
    return "auto"       # unknown words never fall into the expensive mock pull


MODE = _mode()
DATA_DIR = os.environ.get("DATA_DIR", "data")


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


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
    elif stop is None:
        # everything fetched: replay the models over it
        import backtest
        backtest.main(DATA_DIR)


def presence_doc():
    db = firestore_db()
    try:
        return (db.document("presence/owner").get().to_dict() or {}) if db else {}
    except Exception:
        return {}


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


def live():
    """In-play: live prices for every game in progress, our live projection, live edges; live props for the game
    being watched. Writes Firestore live/{gameId} and board/live. Runs only while the site is open."""
    import live as LV
    import refs
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        raise RuntimeError("ODDS_API_KEY secret is missing or empty")
    pres = presence_doc()
    at = pres.get("at")
    if not at or (NOW - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() > C.ACTIVE_MINUTES * 60:
        log("site closed: no live pull")
        return
    fn = os.path.join(DATA_DIR, "latest", "live_state.json")
    try:
        ls = json.load(open(fn))
    except Exception:
        ls = {}
    today = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    if ls.get("day") != today:
        ls = {"day": today, "used": 0, "props": {}}
    room = C.LIVE_DAY_CAP - ls.get("used", 0)
    if room <= 0:
        log("live credit cap reached for today")
        return
    games, _ = load_cache(keep_hours=6)
    started = [g for g in games if NOW - timedelta(hours=5) <= datetime.fromisoformat(g["commence"].replace("Z", "+00:00")) <= NOW]
    sports = sorted({g["sport"] for g in started})
    states = LV.scoreboards(sports)
    try:
        mups = json.load(open(os.path.join(DATA_DIR, "latest", "matchups.json")))
    except Exception:
        mups = {}
    api = OddsAPI(key, max_credits=min(2000, room))
    docs, edges_all = [], []
    for s in sports:
        try:
            data = api.odds(s, C.GAME_MARKETS, None, bookmakers=C.EVENT_BOOKS)
        except Exception as e:
            log(f"live odds failed for {C.label(s)}: {type(e).__name__}")
            continue
        Rf = refs.load(s, DATA_DIR)
        for ev in data:
            if datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")) > NOW or not ev.get("bookmakers"):
                continue
            g = build.build_game(ev, C.label(s))
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
            props = []
            want = pres.get("game") == g["id"]
            if want and st and s in ("basketball_nba", "basketball_wnba", "icehockey_nhl", "americanfootball_nfl") \
                    and _age_min(ls["props"].get(g["id"])) >= C.LIVE_PROP_EVERY_MIN:
                try:
                    pm = [m for m in C.core_markets(s) if build.is_player_market(m)]
                    ed, _ = api.event_odds_all(s, g["id"], pm, C.EVENT_BOOKS)
                    props = list(build.build_props(ed, C.label(s)).values()) if ed else []
                    for p in props:
                        refs.decorate_props(p, Rf)
                    bx, _wp = LV.box(s, st["espnId"])
                    LV.live_props(s, props, bx, mu, pj or {})
                    ls["props"][g["id"]] = iso(NOW)
                except Exception as e:
                    log(f"live props failed: {type(e).__name__}")
            E = build.edges([g], props, min_edge=0.015)
            for e in E:
                e["live"] = True
            edges_all += E
            docs.append({"id": g["id"], "league": g["league"], "sport": s, "home": g["home"], "away": g["away"], "commence": g["commence"],
                         "homeRef": g.get("homeRef"), "awayRef": g.get("awayRef"), "state": st,
                         "model": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in (pj or {}).items()},
                         "lines": g["lines"], "props": props, "edges": E[:40], "updated": iso(NOW)})
    ls["used"] = ls.get("used", 0) + api.used_run
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(ls, open(fn, "w"))
    # small summary in the private data repo (what the Live screen shows), for checking without the site
    json.dump([{"id": d["id"], "league": d["league"], "game": f"{d['away']} @ {d['home']}", "state": d["state"], "model": d["model"],
                "edges": len(d["edges"]), "props": len(d["props"])} for d in docs],
              open(os.path.join(DATA_DIR, "latest", "live.json"), "w"), indent=0)
    json.dump(sorted("|".join(k) for k in states), open(os.path.join(DATA_DIR, "latest", "live_keys.json"), "w"), indent=0)
    db = firestore_db()
    if db:
        batch, size = db.batch(), 0
        for d in docs:
            sdoc = _payload(d)
            if size + len(sdoc) > 6_000_000:
                batch.commit()
                batch, size = db.batch(), 0
            batch.set(db.document(f"live/{d['id']}"), {"payload": sdoc, "league": d["league"], "updated": d["updated"]})
            size += len(sdoc)
        summary = [{"id": d["id"], "league": d["league"], "home": d["home"], "away": d["away"], "homeRef": d["homeRef"], "awayRef": d["awayRef"],
                    "state": d["state"], "pHome": (d["model"] or {}).get("pHome"),
                    "mkt": next(((l.get("fair") or {}).get(d["home"]) for l in d["lines"] if l["market"] == "h2h"), None),
                    "edges": len(d["edges"])} for d in docs]
        edges_all.sort(key=lambda e: -e["score"])
        batch.set(db.document("board/live"), {"payload": json.dumps({"games": summary, "edges": edges_all[:150], "updated": iso(NOW),
                                                                      "creditsToday": ls["used"]}, separators=(",", ":"))})
        keep = {d["id"] for d in docs}
        for snap in db.collection("live").stream():
            if snap.id not in keep:
                batch.delete(snap.reference)
        batch.commit()
    log(f"live: {len(docs)} games, {len(edges_all)} edges, credits {api.used_run} (today {ls['used']})")


def presence():
    """(active, force_at): the site writes presence/owner {at, force?} while it is open."""
    db = firestore_db()
    if not db:
        return False, None
    try:
        d = db.document("presence/owner").get()
        x = d.to_dict() or {}
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
        evs = api.events(s, commenceTimeFrom=iso(NOW - timedelta(minutes=5)), commenceTimeTo=horizon)
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
    if MODE == "stats":
        return run_stats(backfill="backfill" in open(os.path.join(os.path.dirname(__file__), "run-request")).read())
    if MODE == "rebuild":
        return rebuild()
    if MODE == "live":
        return live()
    if MODE == "history":
        return run_history()
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
        evs = api.events(s, commenceTimeFrom=iso(NOW - timedelta(hours=3)), commenceTimeTo=to)
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
    try:
        n_graded = tracker.grade(DATA_DIR, NOW)
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
    picks = load_picks()
    previews = load_previews()
    write_data_repo(game_docs, prop_docs, edge_list, status)
    with open(os.path.join(DATA_DIR, "latest", "matchups.json"), "w") as f:
        json.dump(mups, f, separators=(",", ":"))
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
        n = write_firestore(game_docs, prop_docs, edge_list, status, rep, picks, mups, previews)
        log(f"firestore: {n} docs written")
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


def write_firestore(game_docs, prop_docs, edge_list, status, rep=None, picks=(), mups=None, previews=()):
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

    for g in game_docs:
        put(f"games/{g['id']}", {"sport": g["sport"], "league": g["league"], "commence": g["commence"],
                                 "home": g["home"], "away": g["away"]}, g)
    for p in prop_docs:
        put(f"props/{p['id']}__{p['market']}", {"gameId": p["id"], "market": p["market"],
                                                "league": p["league"], "commence": p["commence"]}, p)
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
    writes.append(("board/status", status))

    # Spark plan: 20k writes a day. Past 14k, prop docs only for games starting within 2 hours; past 19k, none.
    day = (NOW - timedelta(hours=7)).strftime("%Y-%m-%d")
    used = old.get("_writes", {}) if isinstance(old.get("_writes"), dict) else {}
    used_today = used.get("n", 0) if used.get("day") == day else 0
    soon = iso(NOW + timedelta(hours=2))
    kept = []
    for path, data in writes:
        if path.startswith("props/") and (used_today + len(kept) > 19000 or (used_today + len(kept) > 14000 and data.get("commence", "") > soon)):
            new.pop(path, None)         # not written: leave the hash unset so it is written later
            continue
        kept.append((path, data))
    if len(kept) < len(writes):
        log(f"firestore write budget: deferred {len(writes) - len(kept)} prop docs")
    writes = kept

    # batches stay well under Firestore's request size limit
    batch, n, size = db.batch(), 0, 0
    for path, data in writes:
        sz = len(data.get("payload", "")) + 500 if isinstance(data, dict) else 500
        if n and (size + sz > 6_000_000 or n % 400 == 0):
            batch.commit()
            batch, size = db.batch(), 0
        batch.set(db.document(path), data)
        n += 1
        size += sz
    # remove docs for games that ended more than 6 hours ago
    gone_before = iso(NOW - timedelta(hours=6))
    for col in ("games", "props", "matchups"):
        for snap in db.collection(col).where("commence", "<", gone_before).stream():
            batch.delete(snap.reference)
            new.pop(f"{col}/{snap.id}", None)
            n += 1
    batch.commit()
    new["_writes"] = {"day": day, "n": used_today + n}
    with open(hash_file, "w") as f:
        json.dump(new, f)
    return n


def _record_failure(e):
    """write what went wrong to the private data repo (key redacted) so it can be read without the Actions log"""
    key = os.environ.get("ODDS_API_KEY") or "\x00"
    msg = f"{type(e).__name__}: {e}"
    detail = getattr(e, "detail", "") or ""
    info = {"at": iso(NOW), "mode": MODE, "error": msg.replace(key, "***")[:400], "detail": str(detail).replace(key, "***")[:400],
            "keyLength": len(os.environ.get("ODDS_API_KEY") or ""), "keyHasSpace": any(c.isspace() for c in (os.environ.get("ODDS_API_KEY") or ""))}
    try:
        os.makedirs(os.path.join(DATA_DIR, "latest"), exist_ok=True)
        json.dump(info, open(os.path.join(DATA_DIR, "latest", "last_error.json"), "w"), indent=1)
    except Exception:
        pass
    log(f"run failed: {type(e).__name__}")


if __name__ == "__main__":
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
