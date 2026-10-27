"""One engine run: pull odds, build the board, write Firestore and the data repo.

Env:
  ODDS_API_KEY              The Odds API key (GitHub secret)
  FIREBASE_SERVICE_ACCOUNT  service-account JSON (GitHub secret); omit to skip Firestore
  DATA_DIR                  checkout of the private data repo
  MODE                      mock (one capped pull) | live (tiered schedule)
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
from api import OddsAPI, BudgetExceeded  # noqa: E402

NOW = datetime.now(timezone.utc)
MODE = os.environ.get("MODE", "mock")
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


def plan():
    """What to pull this run. Returns (horizon_hours, prop_window_hours, prop_markets, prop_regions, cap)."""
    if MODE == "mock":
        return 36, 30, C.MOCK_PROP_MARKETS, C.MOCK_PROP_REGIONS, C.MOCK_MAX_CREDITS
    return 168, 30, C.PROP_MARKETS, C.PROP_REGIONS, None


def main():
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("ODDS_API_KEY missing")
    horizon, prop_win, prop_mk, prop_rg, cap = plan()
    api = OddsAPI(key, max_credits=cap)
    errors = []

    active = {s["key"] for s in api.sports() if s.get("active")}
    sports = [s for s in C.SPORTS if s in active]
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
            errors.append(f"budget stop before {C.SPORTS[s]} lines ({e})")
            break
        except RuntimeError as e:
            errors.append(str(e))
            continue
        save_raw(s, "lines", data)
        raw_by_sport[s] = data
        for ev in data:
            if ev.get("bookmakers"):
                game_docs.append(build.build_game(ev, C.SPORTS[s]))
        log(f"{C.SPORTS[s]}: {len(data)} games, run credits {api.used_run}")

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
        docs = build.build_props(data, C.SPORTS[s])
        prop_docs.extend(docs.values())
        n_props += 1
    log(f"props: {n_props} games, {len(prop_docs)} market docs, run credits {api.used_run}")

    edge_list = build.edges(game_docs, prop_docs)
    status = {
        "updated": iso(NOW), "mode": MODE,
        "creditsRemaining": api.remaining, "creditsUsedTotal": api.used_total,
        "creditsThisRun": api.used_run,
        "games": {C.SPORTS[s]: sum(1 for g in game_docs if g["sport"] == s) for s in sports},
        "propGames": n_props, "edges": len(edge_list), "errors": errors,
    }

    write_data_repo(game_docs, prop_docs, edge_list, status)
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT"):
        n = write_firestore(game_docs, prop_docs, edge_list, status)
        log(f"firestore: {n} docs written")
    log(f"done: {len(game_docs)} games, {len(edge_list)} edges, credits left {api.remaining}, errors {len(errors)}")


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
        f.write(json.dumps({"t": status["updated"], "mode": MODE, "run": status["creditsThisRun"],
                            "remaining": status["creditsRemaining"]}) + "\n")


def write_firestore(game_docs, prop_docs, edge_list, status):
    import firebase_admin
    from firebase_admin import credentials, firestore

    cred = credentials.Certificate(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]))
    firebase_admin.initialize_app(cred)
    db = firestore.client()

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
    writes.append(("board/status", status))

    batch, n = db.batch(), 0
    for path, data in writes:
        batch.set(db.document(path), data)
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = db.batch()
    # remove docs for games that ended more than 6 hours ago
    gone_before = iso(NOW - timedelta(hours=6))
    for col in ("games", "props"):
        for snap in db.collection(col).where("commence", "<", gone_before).stream():
            batch.delete(snap.reference)
            new.pop(f"{col}/{snap.id}", None)
            n += 1
    batch.commit()
    with open(hash_file, "w") as f:
        json.dump(new, f)
    return n


if __name__ == "__main__":
    main()
