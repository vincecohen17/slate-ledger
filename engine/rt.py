"""Realtime Database: in-play data and the site's presence heartbeat. Free on the Spark plan with no daily write count
(Firestore allows 20k writes a day), so live numbers can update every minute. Only the pieces that changed are written,
and the site's client only downloads those pieces, which keeps the 10 GB/month download allowance comfortable.

Layout (every value is a JSON string, so player and team names never have to be database keys):
  live/board            summary of games in play + live edges (what board/live was)
  live/g/<gameId>/m     everything small: teams, state, model, betLegs, updated
  live/g/<gameId>/e     live edges
  live/g/<gameId>/l/<k> one game line each (key from market/point/team), with its position in "_i"
  live/g/<gameId>/p/<k> one prop market each, with "_i"
  presence              {at, view, game, force} written by the site
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


def _h(s):
    return hashlib.sha1(s.encode()).hexdigest()[:16]


def _key(*parts):
    return _h("|".join("" if p is None else str(p) for p in parts))[:12]


def pieces(d):
    """{relative path: JSON string} for one live game doc"""
    gid = d["id"]
    out = {}
    small = {k: v for k, v in d.items() if k not in ("lines", "props", "edges")}
    out[f"g/{gid}/m"] = json.dumps(small, separators=(",", ":"))
    out[f"g/{gid}/e"] = json.dumps(d.get("edges") or [], separators=(",", ":"))
    for i, l in enumerate(d.get("lines") or []):
        out[f"g/{gid}/l/{_key(l.get('market'), l.get('point'), l.get('team'), l.get('period'))}"] = json.dumps(dict(l, _i=i), separators=(",", ":"))
    for i, p in enumerate(d.get("props") or []):
        out[f"g/{gid}/p/{_key(p.get('market'))}"] = json.dumps(dict(p, _i=i), separators=(",", ":"))
    return out


def publish(docs, board, state, data_dir, log=print):
    """write the changed pieces under live/; state keeps the hashes between runs (live_state.json). Returns bytes sent."""
    old = state.get("rt") or {}
    now = {}
    for d in docs:
        now.update(pieces(d))
    now["board"] = json.dumps(board, separators=(",", ":"))
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
# pre-game board: games, props, edges, status under pre/ (Firestore keeps user data, picks, matchups, previews, finals)
#   pre/status, pre/edges          JSON strings
#   pre/g/<gameId>/m, /l/<k>       a game without its lines, then one line each (with "_i")
#   pre/p/<gameId>/<market>/m, /r/<k>   a prop market without its rows, then one row each (with "_i")

def pre_pieces(game_docs, prop_docs, edges, status):
    out = {"status": json.dumps(status, separators=(",", ":")), "edges": json.dumps({"items": edges[:400]}, separators=(",", ":"))}
    for g in game_docs:
        gid = g["id"]
        out[f"g/{gid}/m"] = json.dumps({k: v for k, v in g.items() if k != "lines"}, separators=(",", ":"))
        seen = set()
        for i, l in enumerate(g.get("lines") or []):
            k = _key(l.get("market"), l.get("point"), l.get("team"), l.get("period"))
            while k in seen:
                k = _key(k, i)
            seen.add(k)
            out[f"g/{gid}/l/{k}"] = json.dumps(dict(l, _i=i), separators=(",", ":"))
    for p in prop_docs:
        base = f"p/{p['id']}/{p['market']}"
        out[f"{base}/m"] = json.dumps({k: v for k, v in p.items() if k != "rows"}, separators=(",", ":"))
        seen = set()
        for i, r in enumerate(p.get("rows") or []):
            k = _key(r.get("player"), r.get("point"), r.get("alt"), r.get("side"))
            while k in seen:
                k = _key(k, i)
            seen.add(k)
            out[f"{base}/r/{k}"] = json.dumps(dict(r, _i=i), separators=(",", ":"))
    return out


def publish_pre(game_docs, prop_docs, edges, status, data_dir, log=print):
    """write the changed pieces of the pre-game board; hashes in latest/rt_pre.json. Returns (KB sent, pieces)."""
    fn = os.path.join(data_dir, "latest", "rt_pre.json")
    try:
        old = json.load(open(fn))
    except Exception:
        old = {}
    now = pre_pieces(game_docs, prop_docs, edges, status)
    newh = {p: _h(s) for p, s in now.items()}
    upd = {p: now[p] for p, h in newh.items() if old.get(p) != h}
    for p in old:
        if p not in now:
            upd[p] = None
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
