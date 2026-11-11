"""CLV against the market consensus at the close (Nov 10): the only close the ledger uses for pre-game bets and picks.

A leg's close is rebuilt from the raw odds pulls we save (snapshots/<UTC day>/<HHMMSS>_<sport>_lines.json.gz for the main
lines, ..._event_<gameId>.json.gz for alternates, team totals, periods and player props): the last pull before the start
that carries the leg's market, the books quoting that exact number (both sides), de-vigged and weighted exactly as
build._consensus does (sharp-weighted, every book equally when no sharp book quotes it). Nothing of ours is layered on:
no self-correcting shift (fairadj), no repricing from a main line or a stat model, no one-sided estimate, no calibration.
A number fewer than MIN_BOOKS books quote at the close (a pick'em app's own line, a thin alt) has no market consensus,
so the leg, and a parlay holding it, gets no CLV. The close's age (minutes from that pull to the start) is kept too:
CLV counts only fresh closes (<= 30 min) on the site, as before.

closes are cached per leg in latest/closemkt_cache.json, since the raw files never change."""
import glob
import gzip
import json
import os
from datetime import datetime, timezone

import build as B
import config as C

VERSION = 4                    # 4: a stale raw close (> FRESH_MIN) gives way to a fresher snapshot consensus
FRESH_MIN = 30
MIN_BOOKS = 2
MAX_AGE_MIN = 72 * 60          # look back up to 3 days; the close's age decides whether CLV counts (fresh = 30 min)


def _t(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _norm(s):
    return " ".join(str(s or "").lower().replace(".", "").replace("'", "").split())


def _pulls(data_dir, sport, gid, commence, kind):
    """raw pull files for this game, newest first, taken before the start: [(time, path)]"""
    start = _t(commence)
    out = []
    for day in {datetime.fromtimestamp(start.timestamp() - k * 86400, timezone.utc).strftime("%Y-%m-%d") for k in range(4)}:
        pat = f"*_{sport}_lines.json.gz" if kind == "lines" else f"*_{sport}_event_{gid}.json.gz"
        for fn in glob.glob(os.path.join(data_dir, "snapshots", day, pat)):
            hms = os.path.basename(fn)[:6]
            try:
                t = datetime.strptime(day + hms, "%Y-%m-%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if t <= start and (start - t).total_seconds() <= MAX_AGE_MIN * 60:
                out.append((t, fn))
    return sorted(out, reverse=True)


_LOADED = {}


def _load(fn):
    if fn not in _LOADED:
        try:
            with gzip.open(fn, "rt") as f:
                d = json.load(f)
        except Exception:
            d = None
        if isinstance(d, dict) and "data" in d and not d.get("bookmakers"):
            d = d["data"]
        _LOADED[fn] = d
        if len(_LOADED) > 400:
            _LOADED.pop(next(iter(_LOADED)))
    return _LOADED[fn]


def _event(payload, gid):
    if isinstance(payload, list):
        return next((e for e in payload if e.get("id") == gid), None)
    if isinstance(payload, dict) and payload.get("id") == gid:
        return payload
    return None


def _consensus_of(line, side):
    """the side's raw consensus chance on a built line, or None when it isn't a real two-book market consensus"""
    if not line or not line.get("fair") or line.get("modelFair") or line.get("oneSided"):
        return None
    if line.get("fairSource") not in ("sharp", "all books"):
        return None
    full = [q for q in line.get("quotes") or [] if all(o in (q.get("prices") or {}) for o in line.get("outcomes") or [])]
    if len(full) < MIN_BOOKS:
        return None
    f = line.get("fairRaw") or line["fair"]
    key = next((o for o in f if _norm(o) == _norm(side)), None)
    return f.get(key) if key else None


def _line_close(ev, leg, label):
    """the leg's consensus on a game line from one event payload"""
    mk, side, pt, team = leg.get("market") or "", leg.get("side"), leg.get("point"), leg.get("team")
    base = mk.replace("alternate_", "")
    g = B.build_game(ev, label)          # a shifted fair keeps the market's own in fairRaw, which is what's read
    g.setdefault("lines", [])
    if ev.get("bookmakers") and any(m.get("key") not in ("h2h", "spreads", "totals") for b in ev["bookmakers"] for m in b.get("markets") or []):
        try:
            B.add_extra_lines(ev, g)
        except Exception:
            pass
    home = ev["home_team"]
    for l in g["lines"]:
        lb = (l.get("market") or "").replace("alternate_", "")
        if lb != base:
            continue
        if "spreads" in base:
            if pt is None or l.get("point") is None:
                continue
            hp = pt if _norm(side) == _norm(home) else -pt
            if abs(l["point"] - hp) > 1e-6:
                continue
        elif "totals" in base:
            if pt is None or l.get("point") is None or abs(l["point"] - pt) > 1e-6:
                continue
            if "team_totals" in base and team and _norm(l.get("team")) != _norm(team) and not _norm(team) in _norm(l.get("team")):
                continue
        c = _consensus_of(l, side)
        if c is not None:
            return c
    return None


def _prop_close(ev, leg, label):
    mk, side, pt, player = leg.get("market") or "", leg.get("side"), leg.get("point"), leg.get("player")
    try:
        docs = B.build_props(ev, label)
    except Exception:
        return None
    base = mk.replace("_alternate", "")
    for doc in (docs.values() if isinstance(docs, dict) else docs):
        if (doc.get("market") or "").replace("_alternate", "") != base:
            continue
        for r in doc.get("rows") or []:
            if _norm(r.get("player")) != _norm(player):
                continue
            if pt is not None and r.get("point") is not None and abs(r["point"] - pt) > 1e-6:
                continue
            c = _consensus_of(r, side)
            if c is not None:
                return c
    return None


def _prop_implied(ev, leg, label, sport):
    """a prop at a number no book quotes (a pick'em app's own line): the market's consensus at the close on the player's
    most balanced two-book row, translated to this number with build's count shape (mean_from_line / p_over). Market-
    derived but not a market price at this number, so it's kept apart from the headline CLV."""
    mk, side, pt, player = leg.get("market") or "", leg.get("side"), leg.get("point"), leg.get("player")
    if side not in ("Over", "Under") or pt is None:
        return None
    try:
        docs = B.build_props(ev, label)
    except Exception:
        return None
    base = mk.replace("_alternate", "")
    best = None
    for doc in (docs.values() if isinstance(docs, dict) else docs):
        if (doc.get("market") or "").replace("_alternate", "") != base:
            continue
        for r in doc.get("rows") or []:
            if _norm(r.get("player")) != _norm(player) or r.get("point") is None:
                continue
            fo = _consensus_of(r, "Over")
            if fo is not None and (best is None or abs(fo - 0.5) < abs(best[1] - 0.5)):
                best = (r["point"], fo)
    if not best:
        return None
    try:
        mean = B.mean_from_line(base, best[0], best[1], sport)
        po = B.p_over(base, mean, pt, sport)
    except Exception:
        return None
    if po is None:
        return None
    return po if side == "Over" else 1 - po


def leg_close(data_dir, leg, sport, commence):
    """{p, age, src} for one leg (a dict named like the ledger: gameId, market, side, point, player, team), or a reason"""
    gid = leg.get("gameId")
    if not gid or not leg.get("market") or not leg.get("side"):
        return {"p": None, "why": "unnamed"}
    label = C.label(sport)
    is_prop = bool(leg.get("player"))
    main = (leg["market"] in ("h2h", "spreads", "totals")) and not is_prop
    kinds = ["lines", "event"] if main else ["event"]
    start = _t(commence)
    for kind in kinds:
        for t, fn in _pulls(data_dir, sport, gid, commence, kind)[:6]:
            ev = _event(_load(fn), gid)
            if not ev or not ev.get("bookmakers"):
                continue
            has = any(m.get("key", "").replace("alternate_", "").replace("_alternate", "") ==
                      leg["market"].replace("alternate_", "").replace("_alternate", "")
                      for b in ev["bookmakers"] for m in b.get("markets") or [])
            if not has:
                continue
            p = _prop_close(ev, leg, label) if is_prop else _line_close(ev, leg, label)
            age = round((start - t).total_seconds() / 60, 1)
            src = os.path.basename(fn)[:6] + ("L" if kind == "lines" else "E")
            if p is not None:
                return {"p": round(p, 4), "age": age, "src": src}
            # the market was there but not this number at two books: no consensus at this number
            imp = _prop_implied(ev, leg, label, sport) if is_prop else None
            return {"p": None, "age": age, "why": "no consensus at this number", **({"imp": round(imp, 4), "src": src} if imp is not None else {})}
    return {"p": None, "why": "no pull with this market before the start"}


def meta(data_dir, gid):
    """(sport, commence) for a game whose pre-game snapshot is gone: from the raw pulls' file names and payload"""
    for fn in sorted(glob.glob(os.path.join(data_dir, "snapshots", "*", f"*_event_{gid}.json.gz")), reverse=True)[:1]:
        name = os.path.basename(fn)
        sport = name[7:name.index("_event_")]
        ev = _event(_load(fn), gid)
        if ev and ev.get("commence_time"):
            return sport, ev["commence_time"]
    return None, None


def _cache(data_dir):
    fn = os.path.join(data_dir, "latest", "closemkt_cache.json")
    try:
        return fn, json.load(open(fn))
    except Exception:
        return fn, {}


def bet_close(data_dir, legs, dec, sport_of, commence_of, results=None, fallback=None):
    """CLV of a (parlay) bet against the closing market consensus: product of its live legs' consensus closes x dec - 1.
    legs: the bet's legs; results: per-leg y (None = push/void, dropped). Returns {clv, closeFair, closeAge, legs: [...]}"""
    fn, cache = _cache(data_dir)
    out, pc, ages, missing = [], 1.0, [], None
    pi, imp_ok, n_imp = 1.0, True, 0
    for i, leg in enumerate(legs):
        if results is not None and i < len(results) and results[i] is None:
            out.append({"skip": True})
            continue
        gid = leg.get("gameId")
        sport, com = sport_of(gid), commence_of(gid)
        if not sport or not com:
            missing = missing or "game not found"
            out.append({"p": None, "why": "game not found"})
            continue
        key = f"{VERSION}|{gid}|{leg.get('market')}|{leg.get('side')}|{leg.get('point')}|{leg.get('player') or ''}|{leg.get('team') or ''}"
        c = cache.get(key)
        if c is None:
            c = leg_close(data_dir, leg, sport, com)
            # no saved raw pull carried this market: the pre-game snapshot's raw consensus row, when it is one
            # or the last raw pull carrying it is stale (a hot game's close went unsaved before Nov 11): the
            # snapshot's consensus when it is fresher
            stale = (c.get("age") or 0) > FRESH_MIN
            if fallback and ((c.get("p") is None and c.get("why", "").startswith("no pull")) or stale):
                fb = fallback(leg)
                if fb and (not stale or (fb.get("p") is not None and fb.get("age") is not None and fb["age"] < c["age"])):
                    c = fb
            cache[key] = c
        out.append(c)
        if c.get("p") is None:
            missing = missing or c.get("why")
            if c.get("imp") is not None:
                pi *= c["imp"]
                n_imp += 1
                ages.append(c.get("age"))
            else:
                imp_ok = False
        else:
            pc *= c["p"]
            pi *= c["p"]
            ages.append(c.get("age"))
    try:
        json.dump(cache, open(fn, "w"), separators=(",", ":"))
    except Exception:
        pass
    live = [c for c in out if not c.get("skip")]
    age = max(a for a in ages if a is not None) if any(a is not None for a in ages) else None
    imp = {"clvImp": round(pi * dec - 1, 4), "closeImp": round(pi, 4), "nImp": n_imp} if live and dec and imp_ok and n_imp else {}
    if not live or missing or not dec:
        return {"clv": None, "closeFair": None, "closeAge": age if imp else None, "why": missing or ("all legs void" if not live else "no price"), "legs": out, **imp}
    return {"clv": round(pc * dec - 1, 4), "closeFair": round(pc, 4), "closeAge": age, "legs": out}
