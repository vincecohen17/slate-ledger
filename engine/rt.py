"""Realtime Database: the board the engine publishes (pre/), in-play data (live/) and the site's presence heartbeat.
Free on the Spark plan with no daily write count (Firestore allows 20k writes a day). Only pieces that changed are
written, the site's client downloads only those, and big pieces are deflated: that keeps the free 10 GB a month of
downloads comfortable. Values are strings (JSON, or "z:" + base64 deflated JSON), so names never become database keys.

  live/board                 games in play + live edges (deflated)
  live/g/<gameId>/m          the small part, raw JSON: teams, state, model, betLegs, updated
  live/g/<gameId>/e, /l      live edges, live lines (deflated)
  live/g/<gameId>/p/<market> a live prop market (deflated)
  presence                   {at, view, game, force} written by the site
"""
import hashlib
import json
import os

DEFAULT_URL = "https://slate-ledger-default-rtdb.firebaseio.com"


def url(data_dir):
    if os.environ.get("RTDB_URL"):
        return os.environ["RTDB_URL"]
    try:
        u = json.load(open(os.path.join(data_dir, "latest", "rtdb.json"))).get("url")
        if u:
            return u
    except Exception:
        pass
    return DEFAULT_URL


def ref(path, data_dir):
    from firebase_admin import db
    return db.reference(path, url=url(data_dir))


def z(obj):
    """JSON, deflated and base64'd, marked "z:" (6-7x smaller: the site inflates it with the browser's DecompressionStream)"""
    import base64
    import zlib
    return "z:" + base64.b64encode(zlib.compress(json.dumps(obj, separators=(",", ":")).encode(), 9)).decode()


def _clean(upd):
    """a removal under a path this same update rewrites would clash in one update; the rewrite already replaces it"""
    written = {p for p, v in upd.items() if v is not None}
    def covered(p):
        parts = p.split("/")
        return any("/".join(parts[:i]) in written for i in range(1, len(parts)))
    return {p: v for p, v in upd.items() if v is not None or not covered(p)}


def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _key(*parts):
    return _h("|".join("" if p is None else str(p) for p in parts))[:12]


def pieces(d):
    """{relative path: string} for one live game doc: small state raw (changes every minute), the rest deflated"""
    gid = d["id"]
    out = {f"g/{gid}/m": json.dumps({k: v for k, v in d.items() if k not in ("lines", "props", "edges")}, separators=(",", ":")),
           f"g/{gid}/e": z(d.get("edges") or []), f"g/{gid}/l": z(d.get("lines") or [])}
    for p in d.get("props") or []:
        out[f"g/{gid}/p/{p.get('market')}"] = z(p)
    return out


def publish(docs, board, state, data_dir, log=print, partial=False):
    """write the changed pieces under live/; state keeps the hashes between runs (live_state.json). Returns bytes sent.
    partial: the watcher's free pass: only each game's small m piece (score, clock, bet legs); nothing else is written
    or removed, and the other pieces' hashes are kept for the next full pass."""
    old = state.get("rt") or {}
    now = {}
    for d in docs:
        now.update({p: v for p, v in pieces(d).items() if not partial or p.endswith("/m")})
    if partial:
        upd = {p: v for p, v in now.items() if old.get(p) != _h(v)}
        if upd:
            ref("live", data_dir).update(upd)
        state["rt"] = {**old, **{p: _h(v) for p, v in now.items()}}
        return sum(len(v) for v in upd.values()), len(upd)
    now["board"] = z(board)
    upd, sent = {}, 0
    newh = {}
    for path, s in now.items():
        h = _h(s if path != "board" else json.dumps({k: v for k, v in board.items() if k != "updated"}, separators=(",", ":")))
        newh[path] = h
        if old.get(path) != h or (path == "board" and _stale(state.get("rtBoardAt"))):
            upd[path] = s
            sent += len(s)
    # pieces that went away: a finished game, a line no longer quoted
    live_g = {p.split("/")[1] for p in now if p.startswith("g/")}
    for path in old:
        if path in now:
            continue
        g = path.split("/")[1] if path.startswith("g/") else None
        if g and g not in live_g:
            upd[f"g/{g}"] = None
        elif g:
            upd[path] = None
    upd = _clean(upd)
    if upd:
        ref("live", data_dir).update(upd)
        if "board" in upd:
            from datetime import datetime, timezone
            state["rtBoardAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    state["rt"] = newh
    return sent, len(upd)


def _stale(at, minutes=10):
    from datetime import datetime, timezone
    if not at:
        return True
    return (datetime.now(timezone.utc) - datetime.fromisoformat(at.replace("Z", "+00:00"))).total_seconds() > minutes * 60


def presence(data_dir):
    try:
        return ref("presence", data_dir).get() or {}
    except Exception:
        return {}


def ensure_rules(data_dir, repo_root):
    """put the owner-only rules on the database (the same file the deploy uses), so creating the database in the
    console is the only manual step. The owner's email comes from the private data repo and is never logged."""
    import google.auth.transport.requests
    from google.oauth2 import service_account
    email = json.load(open(os.path.join(data_dir, "config", "access.json")))["email"]
    rules = open(os.path.join(repo_root, "database.rules.template.json")).read().replace("__ALLOWED_EMAIL__", email)
    cred = service_account.Credentials.from_service_account_info(json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"]),
        scopes=["https://www.googleapis.com/auth/firebase.database", "https://www.googleapis.com/auth/userinfo.email"])
    fn = os.path.join(data_dir, "latest", "rt_rules.json")
    h = _h(rules)
    try:
        if json.load(open(fn)).get("h") == h:
            return 200
    except Exception:
        pass
    s = google.auth.transport.requests.AuthorizedSession(cred)
    r = s.put(f"{url(data_dir)}/.settings/rules.json", data=rules.encode())
    if r.status_code == 200:
        json.dump({"h": h}, open(fn, "w"))
    return r.status_code


# ---------------------------------------------------------------------------
# pre-game board under pre/ (Firestore keeps bets, promos, picks, previews and private settings):
#   status (JSON), edges, reports (deflated, "z:" + base64)
#   g/<gameId>             a game with its main lines (each line keeps its position in "_i")
#   ga/<gameId>            its alternate lines (loaded only where needed: a game page, the Lab's switch)
#   p/<gameId>/<market>    a prop market with its main rows;  pa/<gameId>/<market>  its alternate rows
#   mu/<gameId>            matchup;  fin/board, fin/d/<key>  finished games
# The download allowance (10 GB a month) is why pieces are deflated and alternates split off: opening the board
# downloads about 1.5 MB.

def _alt_lines(lines):
    """positions of alternate lines: every alternate_* market, and other points of a market that has a main line.
    Markets with no main line (soccer extras, period lines some books only quote one way) stay with the main board."""
    base = lambda l: (l.get("market") or "").replace("alternate_", "")
    has_main = {(base(l), l.get("team")) for l in lines if l.get("isMain")}
    return {l["_i"] for l in lines if not l.get("isMain") and ((l.get("market") or "").startswith("alternate_") or (base(l), l.get("team")) in has_main)}


def pre_pieces(game_docs, prop_docs, edges, status, mups=None, rep=None, fin=None):
    """bundles, each deflated: a game with its main lines, its alternate lines, a prop market with its main rows, its
    alternate rows, a matchup, a finished game. Sizes for a full board: ~1 MB main, ~1.4 MB alternates, ~0.9 MB main props."""
    out = {"status": json.dumps(status, separators=(",", ":")), "edges": z({"items": edges[:400]})}
    for g in game_docs:
        gid = g["id"]
        lines = [dict(l, _i=i) for i, l in enumerate(g.get("lines") or [])]
        alt_ids = _alt_lines(lines)
        out[f"g/{gid}"] = z(dict({k: v for k, v in g.items() if k != "lines"}, lines=[l for l in lines if l["_i"] not in alt_ids]))
        alt = [l for l in lines if l["_i"] in alt_ids]
        if alt:
            out[f"ga/{gid}"] = z(alt)
    for p in prop_docs:
        rows = [dict(r, _i=i) for i, r in enumerate(p.get("rows") or [])]
        has_main = {r.get("player") for r in rows if r.get("isMain")}
        alt_ids = {r["_i"] for r in rows if not r.get("isMain") and r.get("player") in has_main}
        out[f"p/{p['id']}/{p['market']}"] = z(dict({k: v for k, v in p.items() if k != "rows"}, rows=[r for r in rows if r["_i"] not in alt_ids]))
        alt = [r for r in rows if r["_i"] in alt_ids]
        if alt:
            out[f"pa/{p['id']}/{p['market']}"] = z(alt)
    if mups is not None:
        for gid, mu in mups.items():
            out[f"mu/{gid}"] = z(mu)
    if rep:
        out["reports"] = z(rep)
    if fin is not None:
        import finals
        out["fin/board"] = z({"games": finals.summary(fin)})
        for k, d in fin.items():
            out[f"fin/d/{k}"] = z(d)
    return out


def publish_pre(game_docs, prop_docs, edges, status, data_dir, log=print, mups=None, rep=None, fin=None, opps=None, pickslive=None):
    """write the changed pieces of the pre-game board; hashes in latest/rt_pre.json. Returns (KB sent, pieces)."""
    fn = os.path.join(data_dir, "latest", "rt_pre.json")
    try:
        old = json.load(open(fn))
    except Exception:
        old = {}
    now = pre_pieces(game_docs, prop_docs, edges, status, mups, rep, fin)
    if opps is not None:
        now["opps"] = z({"items": opps})
    if pickslive is not None:
        now["pickslive"] = z(pickslive)
    newh = {p: _h(s) for p, s in now.items()}
    # parts this run didn't build (no matchups, reports or finals this time) stay as they are
    for p, h in old.items():
        if p not in newh and ((p == "opps" and opps is None) or (p == "pickslive" and pickslive is None) or (p.startswith("mu/") and mups is None) or (p == "reports" and not rep) or (p.startswith("fin/") and fin is None)):
            newh[p] = h
    upd = {p: now[p] for p, h in newh.items() if p in now and old.get(p) != h}
    for p in old:
        if p not in newh:
            upd[p] = None
    upd = _clean(upd)
    chunk, size, sent = {}, 0, 0
    r = ref("pre", data_dir)
    for p, v in upd.items():
        chunk[p] = v
        size += len(v or "") + 60
        if size > 4_000_000:
            r.update(chunk)
            sent += size
            chunk, size = {}, 0
    if chunk:
        r.update(chunk)
        sent += size
    json.dump(newh, open(fn, "w"), separators=(",", ":"))
    return sent // 1024, len(upd)
