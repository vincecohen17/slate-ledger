"""The edges' report card: did the prices we flagged actually beat the market, and where?

Every edge the board publishes is logged the first time we see it (venue, price, fair, edge, logic, how long before the
start). Until the game starts, each run refreshes that line's sharp fair and the venue's current price, so the last
values before the start are the close. After the game, each logged edge is graded: CLV at the price we first flagged
(close x decimal - 1), the CLV had you waited for the last price, the best price the venue ever showed, and the result.

That answers, by venue, market, edge size, logic, line type and timing: which flagged edges are real (positive CLV over
hundreds of bets), which venues are slow to move and where, and which edge types are mirages. The summary feeds the
Reports page and nudges each new edge's rank toward categories that have held up (shrunk hard toward zero until there
is enough history).

Also here: opening lines. The first fair we saw for each main line and main prop row, so the board can show how far
the market has moved (the market's own verdict on news and sharp money).

Files (private data repo): track/edgelog/open.json (pre-game and ungraded), track/edgecard/<sport>.jsonl (graded rows),
stats/edgecard.json (summary), track/opens.json (first fairs).
"""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

import refs as R

N = lambda s: R.norm(s or "")


def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _iso(d):
    return d.isoformat().replace("+00:00", "Z")


def _dec(am):
    if am is None:
        return None
    return 1 + am / 100 if am > 0 else 1 + 100 / -am


def _key(gid, market, side, point, player, team):
    return f"{gid}|{market}|{side}|{'' if point is None else point}|{player or ''}|{team or ''}"


def _index(game_docs, prop_docs):
    """(gid, market, side, own point, player, team) -> (line/row, side key) for every line and prop row"""
    idx = {}
    for g in game_docs:
        for l in g.get("lines") or []:
            for o in (l.get("fair") or {}):
                pt = l.get("point")
                if pt is not None and "spreads" in (l.get("market") or "") and o == g.get("away"):
                    pt = -pt
                idx[_key(g["id"], l["market"], o, pt, None, l.get("team"))] = (l, o)
    for p in prop_docs:
        for r in p.get("rows") or []:
            for o in (r.get("fair") or {}):
                idx[_key(p["id"], p["market"], o, r.get("point"), r.get("player"), None)] = (r, o)
    return idx


def _venue_price(line, side, venue):
    for q in line.get("quotes") or []:
        if q.get("book") == venue:
            pe = (q.get("prices") or {}).get(side) or {}
            am = pe.get("allInAm", pe.get("am"))
            if am is not None:
                return am
    for d in line.get("dfs") or []:        # pick'em: the app's break-even stands in for a price
        if d.get("book") == venue and d.get("side") == side and d.get("be"):
            return None
    return None


def _sane(dec, fair):
    """a placeholder quote (+99900) or a price three times what the fair supports is a feed error, not an edge"""
    return dec is not None and 1 < dec <= 50 and (fair is None or dec * fair <= 3)


def _ok(r):
    # a close 20+ points from the fair at the flag is a mismatch (feed error or the other side), not a market move
    return r.get("hrs", -1) >= 0 and _sane(r.get("dec"), r.get("fair")) and (r.get("close") is None or abs(r["close"] - r["fair"]) <= 0.20)


def observe(data_dir, game_docs, prop_docs, edges, now, log=print):
    fn = os.path.join(data_dir, "track", "edgelog", "open.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    try:
        book = json.load(open(fn))
    except Exception:
        book = {}
    sport_of = {g["id"]: g.get("sport") for g in game_docs}
    t = _iso(now)
    new = 0
    for e in edges:
        # in-play prices against a pre-game fair mean nothing: only edges flagged before the start are logged
        if e.get("live") or _dt(e["commence"]) <= now:
            continue
        k = _key(e["gameId"], e["market"], e["side"], e.get("point"), e.get("player"), e.get("team") if not e.get("player") else None)
        ent = book.get(k)
        if ent is None:
            ent = book[k] = {"main": bool(e.get("main", True)), "gid": e["gameId"], "sport": sport_of.get(e["gameId"]), "league": e.get("league"), "commence": e["commence"],
                             "market": e["market"], "side": e["side"], "point": e.get("point"), "player": e.get("player"),
                             "team": e.get("team") if not e.get("player") else None, "label": e.get("label"), "obs": {}}
        dec = (1 / e["be"]) if e.get("pickem") and e.get("be") else (1 / 0.5774 if e.get("pickem") else _dec(e.get("am")))
        if not dec or not _sane(dec, e.get("fair")):
            continue
        v = ent["obs"].get(e["book"])
        if v is None:
            ent["obs"][e["book"]] = {"dec": round(dec, 4), "fair": e["fair"], "edge": e["edge"], "t": t, "logic": e.get("logic"), "mp": e.get("modelP"), "steam": e.get("fairMove"), "fs": e.get("fairShift"),
                                     "model": e.get("model"), "est": e.get("est"), "pickem": bool(e.get("pickem")),
                                     "best": round(dec, 4), "bestT": t, "last": round(dec, 4), "lastT": t}
            new += 1
        else:
            v["last"], v["lastT"] = round(dec, 4), t
            if dec > v["best"]:
                v["best"], v["bestT"] = round(dec, 4), t
    # refresh every logged line's fair and the venues' current prices until the start: the last values are the close
    idx = _index(game_docs, prop_docs)
    for k, ent in book.items():
        if _dt(ent["commence"]) <= now:
            continue
        hit = idx.get(k)
        if not hit:
            continue
        line, o = hit
        f = (line.get("fair") or {}).get(o)
        if f is not None:
            ent["close"], ent["closeT"] = f, t
        ent["main"] = bool(line.get("isMain"))
        for venue, v in ent["obs"].items():
            if v.get("pickem"):
                continue
            am = _venue_price(line, o, venue)
            if am is not None:
                d = _dec(am)
                v["last"], v["lastT"] = round(d, 4), t
                if d > v["best"]:
                    v["best"], v["bestT"] = round(d, 4), t
    json.dump(book, open(fn, "w"), separators=(",", ":"))
    return new


def grade(data_dir, fin_by_gid, now, log=print):
    """grade logged edges whose games are final; rows go to track/edgecard/<sport>.jsonl"""
    import ledger
    fn = os.path.join(data_dir, "track", "edgelog", "open.json")
    try:
        book = json.load(open(fn))
    except Exception:
        return 0
    gfn = os.path.join(data_dir, "track", "edgelog", "graded.json")
    try:
        done = json.load(open(gfn))
    except Exception:
        done = {}
    out, keep, n = {}, {}, 0
    for k, ent in book.items():
        if k in done:           # graded already (it came back onto the board after the start): never twice
            continue
        start = _dt(ent["commence"])
        if now < start + timedelta(hours=2) and ent["gid"] not in fin_by_gid:
            keep[k] = ent
            continue
        leg = {"gameId": ent["gid"], "market": ent["market"], "side": ent["side"], "point": ent["point"],
               "player": ent.get("player"), "team": ent.get("team")}
        try:
            g = ledger.grade_leg(data_dir, leg, fin_by_gid)
        except Exception:
            g = None
        if g is None:
            if now - start < timedelta(days=2):
                keep[k] = ent
            continue
        close = ent.get("close")
        if close is None:
            close = g.get("close")
        y = g.get("y")
        if close is None or g.get("void"):
            continue
        hrs = None
        done[k] = ent["commence"][:10]
        for venue, v in ent["obs"].items():
            hrs = (start - _dt(v["t"])).total_seconds() / 3600
            if hrs < 0 or not _sane(v["dec"], v.get("fair")):
                continue
            kind = ("pick'em" if v.get("pickem") else "prop" if ent.get("player") else "line")
            row = {"d": ent["commence"][:10], "sport": ent.get("sport"), "league": ent.get("league"), "venue": venue,
                   "mk": ent["market"], "kind": kind, "main": ent.get("main", False), "est": bool(v.get("est")), "model": bool(v.get("model")),
                   "logic": v.get("logic"), "mp": v.get("mp"), "steam": v.get("steam"), "edge": v["edge"],
                   "gid": ent["gid"], "side": ent["side"], "pt": ent.get("point"), "player": ent.get("player"), "fs": v.get("fs"), "hrs": round(hrs, 2), "dec": v["dec"], "fair": v["fair"], "close": close,
                   "clv": round(close * v["dec"] - 1, 4), "clvLast": round(close * v["last"] - 1, 4), "clvBest": round(close * v["best"] - 1, 4),
                   "y": y, "pnl": None if y is None else round(y * (v["dec"] - 1) - (1 - y), 4), "ca": g.get("age")}
            if v.get("mp") is not None and v["fair"] - 1 / v["dec"] >= 0.005:     # support ratio at the flag (see build.edges)
                row["sup"] = round((v["mp"] - 1 / v["dec"]) / (v["fair"] - 1 / v["dec"]), 3)
                row["supSrc"] = "flag"
            out.setdefault(ent.get("sport") or "other", []).append(row)
            n += 1
    d = os.path.join(data_dir, "track", "edgecard")
    os.makedirs(d, exist_ok=True)
    for sport, rows in out.items():
        with open(os.path.join(d, f"{sport}.jsonl"), "a") as f:
            for r in rows:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    json.dump(keep, open(fn, "w"), separators=(",", ":"))
    cut = (now - timedelta(days=5)).strftime("%Y-%m-%d")
    json.dump({k: d for k, d in done.items() if d >= cut}, open(gfn, "w"), separators=(",", ":"))
    return n


# ---------------------------------------------------------------------------
# summary

def _bucket_edge(e):
    return "1.5-3%" if e < 0.03 else "3-5%" if e < 0.05 else "5-8%" if e < 0.08 else "8%+"


def _bucket_hrs(h):
    return "under 1h" if h < 1 else "1-3h" if h < 3 else "3-12h" if h < 12 else "12h+"


def _kind(r):
    if r["kind"] == "pick'em":
        return "Pick'em"
    if r["est"]:
        return "One-sided (est. fair)"
    if r["model"]:
        return "Alt line (model fair)"
    base = "Prop" if r["kind"] == "prop" else "Game line"
    return f"{base}, main" if r["main"] else f"{base}, alt"


def _mkname(r):
    m = r["mk"]
    for p in ("player_", "batter_", "pitcher_"):
        if m.startswith(p):
            m = m[len(p):]
    m = m.replace("alternate_", "alt ").replace("_", " ")
    return f"{r['league']} {m}"


def _logic_size(league, logic, edge):
    lg = {"agrees": "agrees", "disagrees": "disagrees"}.get(logic, "other")
    return f"{league} · {lg} · {'5%+' if (edge or 0) >= 0.05 else 'under 5%'}"


def _band(sup):
    return None if sup is None else "below" if sup < 0 else "between" if sup < 1.0 else "near" if sup < 1.5 else "beyond"


def _sup_size(league, sup, edge):
    """league x where the model sits between price and fair x price-edge size: the cell the support override is judged in"""
    return f"{league} · {_band(sup) or 'no model'} · {'5%+' if (edge or 0) >= 0.05 else 'under 5%'}"


SUP_NAME = {"below": "Model under the price", "between": "Model between price and fair", "near": "Model near the fair",
            "beyond": "Model past the fair (1.5x+)", None: "No model"}


DIMS = {
    "venue": lambda r: r["venue"],
    "kind": _kind,
    "market": _mkname,
    "edge": lambda r: _bucket_edge(r["edge"]),
    "logic": lambda r: {"agrees": "Logic agrees", "neutral": "Logic neutral", "disagrees": "Logic disagrees"}.get(r["logic"], "No model"),
    "timing": lambda r: _bucket_hrs(r["hrs"]),
    "league": lambda r: r["league"],
    "venueKind": lambda r: f"{r['venue']} · {_kind(r)}",
    "venueLeague": lambda r: f"{r['venue']} · {r['league']}",
    # league x model logic x price-edge size: where the logic flag carries signal (big edges the model backs vs fights)
    "logicSize": lambda r: _logic_size(r.get("league"), r.get("logic"), r.get("edge")),
    "support": lambda r: SUP_NAME[_band(r.get("sup"))],
    "supportSize": lambda r: f"{SUP_NAME[_band(r.get('sup'))]} · {'5%+' if (r.get('edge') or 0) >= 0.05 else 'under 5%'}",
    "supLeague": lambda r: _sup_size(r.get("league"), r.get("sup"), r.get("edge")),
    "fairShift": lambda r: ("Corrected fair · " + ("Over/Yes" if r.get("side") in ("Over", "Yes") else "Under/No")) if r.get("fs") else "Raw fair",
    "steam": lambda r: "No fair history" if r.get("steam") is None else "Fair moved 2+ pts toward it" if r["steam"] >= 0.02 else "Fair moved away" if r["steam"] <= -0.01 else "Static gap",
}


RECENT_HL = 5.0       # days: the recency weight half-life for "lately" stats (gapR / roiR), which rerank leans on half
_REF = {"d": None}


def _recent(g):
    """recency-weighted results (half-life RECENT_HL days before the newest graded day): gap, ROI, effective n"""
    if not g or not _REF["d"]:
        return {}
    ref = datetime.fromisoformat(_REF["d"])
    w = [0.5 ** (max(0, (ref - datetime.fromisoformat(r["d"])).days) / RECENT_HL) for r in g]
    sw = sum(w)
    if sw <= 0:
        return {}
    return {"gapR": round(sum(wi * (r["y"] - r["close"]) for wi, r in zip(w, g)) / sw, 4),
            "roiR": round(sum(wi * r["pnl"] for wi, r in zip(w, g)) / sw, 4), "nR": round(sw * sw / sum(wi * wi for wi in w), 1)}


CLOSE_OK_MIN = 30          # CLV counts only where the close was pulled within this many minutes of the start


def _ccal(r):
    import closecal
    p = closecal.cal(r["close"], r.get("kind") or "prop", None, closecal.typ(r))
    return p * r["dec"] - 1


def _ckey(r):
    return r.get("gid") or (r.get("d"), r.get("sport"))


def clus(g, base="close"):
    """game-clustered significance. Graded edges are not independent: the same prop at five venues, or a ladder of
    alt lines on one player, all win or lose together, so a z that counts every row as its own coin flip runs 2-8x too
    big. Rows are summed per game (gid; older rows without one per day x sport) and the spread of those sums gives the
    standard error (CR0 with a G/(G-1) small-sample factor). zG: results against `base` (the close, or the fair at the
    flag); roiT: flat ROI over its clustered SE. None below 6 games."""
    g = [r for r in g if r.get("y") is not None and r.get(base) is not None]
    cl = {}
    for r in g:
        c = cl.setdefault(_ckey(r), [0.0, 0.0, 0])
        c[0] += r["y"] - r[base]
        c[1] += r.get("pnl") or 0.0
        c[2] += 1
    G = len(cl)
    if G < 6:
        return {"games": G, "zG": None, "roiT": None}
    n = len(g)
    res = sum(c[0] for c in cl.values())
    roi = sum(c[1] for c in cl.values()) / n
    k = G / (G - 1)
    vr = k * sum(c[0] ** 2 for c in cl.values())
    vp = k * sum((c[1] - roi * c[2]) ** 2 for c in cl.values())
    return {"games": G, "zG": round(res / vr ** 0.5, 2) if vr > 0 else None,
            "roiT": round(roi * n / vp ** 0.5, 2) if vp > 0 else None, "roiSE": round(vp ** 0.5 / n, 4) if vp > 0 else None}


def _agg(rows):
    n = len(rows)
    g = [r for r in rows if r["y"] is not None]
    # CLV on a stale close (prices last pulled hours before the start) is just an earlier price: left out of every CLV
    # number; rows from before close ages were kept (ca missing) count
    fresh = [r for r in rows if not (r.get("ca") is not None and r["ca"] > CLOSE_OK_MIN)]
    cr = fresh or rows          # every close stale: report them rather than nothing (staleClose says so)
    nc = len(cr)
    clv = sum(r["clv"] for r in cr) / nc
    return {**_recent(g), **clus(g), "n": n, "nClv": len(fresh), "staleClose": n - len(fresh),
            "clv": round(clv, 4), "beat": round(sum(r["clv"] > 0 for r in cr) / nc, 3),
            # calibrated CLV: the close corrected to how our closes have actually done (closecal); the honest one
            "clvCal": round(sum(_ccal(r) for r in cr) / nc, 4),
            "clvLast": round(sum(r["clvLast"] for r in cr) / nc, 4), "clvBest": round(sum(r["clvBest"] for r in cr) / nc, 4),
            "edge": round(sum(r["edge"] for r in rows) / n, 4),
            "graded": len(g), "roi": round(sum(r["pnl"] for r in g) / len(g), 4) if g else None,
            "win": round(sum(r["y"] for r in g) / len(g), 3) if g else None,
            "exp": round(sum(r["close"] for r in g) / len(g), 3) if g else None,
            # results against what the fair expected: the honest test (CLV on model-priced lines is measured against our
            # own model, so it can look good while the bets lose)
            "gap": round((sum(r["y"] for r in g) - sum(r["close"] for r in g)) / len(g), 4) if g else None,
            # zRes counts every row as independent: kept only for the muting rule, whose threshold the walk-forward test
            # chose on this scale (policytest.muting). Every displayed confidence uses zG (game-clustered).
            "zRes": round((sum(r["y"] for r in g) - sum(r["close"] for r in g)) / max(1e-9, sum(r["close"] * (1 - r["close"]) for r in g)) ** 0.5, 2) if len(g) > 2 else None,
            # z of the CLV mean: how sure we are it isn't noise
            "z": round(clv / ((sum((r["clv"] - clv) ** 2 for r in cr) / max(1, nc - 1)) ** 0.5 / nc ** 0.5), 2) if nc > 2 and len({r["clv"] for r in cr}) > 1 else None}


def summarize(data_dir, days=120, now=None):
    now = now or datetime.now(timezone.utc)
    cut = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = []
    for fn in glob.glob(os.path.join(data_dir, "track", "edgecard", "*.jsonl")):
        for line in open(fn):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("d", "") >= cut and _ok(r):       # history logged before the fixes: drop in-play and junk-quote rows
                rows.append(r)
    import closecal
    closecal.load(data_dir)
    _REF["d"] = max((r["d"] for r in rows if r.get("y") is not None), default=None)
    out = {"updated": _iso(now), "n": len(rows), "since": min((r["d"] for r in rows), default=None), "recentHL": RECENT_HL}
    if rows:
        out["all"] = _agg(rows)
        for dim, f in DIMS.items():
            by = {}
            for r in rows:
                by.setdefault(f(r), []).append(r)
            out[dim] = sorted(({"k": k, **_agg(v)} for k, v in by.items() if len(v) >= 3), key=lambda x: -x["n"])[:150 if dim in ("supLeague", "logicSize") else 60]
        # the last 30 days by day, for a trend line
        by = {}
        for r in rows:
            by.setdefault(r["d"], []).append(r)
        out["daily"] = [{"d": d, **_agg(v)} for d, v in sorted(by.items())][-45:]
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "stats", "edgecard.json"), "w"), separators=(",", ":"))
    return out


MUTE_N, MUTE_Z = 100, -2.0      # chosen by the walk-forward test (engine/policytest.py)


def rerank(edges, card, prior=150):
    """nudge each edge's rank by how its kind of edge has actually done: wins against what the fair expected (venue x
    kind, else kind), shrunk toward zero (n / (n + prior)). A kind that wins 5 points less than its fair said drops about
    a quarter. Stamps e["track"] for the site."""
    if not card or not card.get("n"):
        return
    vk = {x["k"]: x for x in card.get("venueKind", [])}
    kd = {x["k"]: x for x in card.get("kind", [])}
    ls = {x["k"]: x for x in card.get("logicSize", [])}
    sl = {x["k"]: x for x in card.get("supLeague", [])}
    for e in edges:
        r = {"kind": "pick'em" if e.get("pickem") else "prop" if e.get("player") else "line", "est": bool(e.get("est")),
             "model": bool(e.get("model")), "main": bool(e.get("main", True))}
        k = _kind(r)
        s = vk.get(f"{e['book']} · {k}")
        if not s or (s.get("graded") or 0) < 30:
            s = kd.get(k)
        if not s or (s.get("graded") or 0) < 30 or s.get("gap") is None:
            continue
        w = s["graded"] / (s["graded"] + prior)
        # lean half on how this kind has done lately (recency-weighted, half-life RECENT_HL days) when there's enough of it
        gap = s["gap"]
        if s.get("gapR") is not None and (s.get("nR") or 0) >= 30:
            gap = 0.5 * s["gap"] + 0.5 * s["gapR"]
        adj = max(-0.5, min(0.5, gap * w * 6))
        e["track"] = {"n": s["graded"], "clv": s["clv"], "beat": s["beat"], "cat": s["k"], "win": s["win"], "exp": s["exp"], "gap": s["gap"],
                      "roi": s["roi"], "z": s.get("zG"), "games": s.get("games"), "gapR": s.get("gapR"), "roiR": s.get("roiR"), "nR": s.get("nR")}
        e["score"] = round(e["score"] * (1 + adj), 4)
        # muted: this kind of edge (or this venue's version of it) has lost to its fair by 2+ standard errors over 100+
        # graded edges. It stays visible in a "losing so far" section but out of the main list, alerts and picks; it
        # comes back by itself when its record recovers.
        for c in (kd.get(k), vk.get(f"{e['book']} · {k}")):
            if c and (c.get("graded") or 0) >= MUTE_N and c.get("zRes") is not None and c["zRes"] <= MUTE_Z and (c.get("gap") or 0) < 0:
                e["muted"] = c["k"]
                break
        # the logic override (walk-forward tested, engine/policytest.py): a 5%+ price edge our model backs comes back when
        # that league's record for such edges has held its fair; one the model fights is muted when that record has lost
        # the support override (same idea, sharper: where the model sits between the venue's price and our fair) takes
        # precedence when the edge has a model number; a 5%+ edge whose model sits between price and fair comes back when
        # that league's cell has held its fair, one whose model is under the price or far past the fair is muted when its
        # cell has lost
        c = sl.get(_sup_size(e.get("league"), e.get("support"), e.get("edge"))) if e.get("supBand") else None
        if (e.get("edge") or 0) >= 0.05 and c and (c.get("graded") or 0) >= MUTE_N:
            if e["supBand"] in ("between", "near") and (c.get("gap") or 0) >= 0:
                e.pop("muted", None)
                e["supportBacked"] = True
            elif e["supBand"] in ("below", "beyond") and c.get("zRes") is not None and c["zRes"] <= MUTE_Z:
                e["muted"] = c["k"]
        elif (e.get("edge") or 0) >= 0.05 and e.get("logic") in ("agrees", "disagrees"):
            c = ls.get(_logic_size(e.get("league"), e.get("logic"), e.get("edge")))
            if c and (c.get("graded") or 0) >= MUTE_N:
                if e["logic"] == "agrees" and (c.get("gap") or 0) >= 0:
                    e.pop("muted", None)
                    e["logicBacked"] = True
                elif e["logic"] == "disagrees" and c.get("zRes") is not None and c["zRes"] <= MUTE_Z:
                    e["muted"] = c["k"]
        # a market-priced edge in the last hour before the start has done best of any timing (live record): a mild lift
        hrs = (_dt(e["commence"]) - datetime.now(timezone.utc)).total_seconds() / 3600 if e.get("commence") else None
        if not e.get("model") and not e.get("est") and hrs is not None and 0 <= hrs < 1:
            e["score"] = round(e["score"] * 1.1, 4)
    edges.sort(key=lambda x: (bool(x.get("muted")), -x["score"]))


# ---------------------------------------------------------------------------
# opening lines (first fair we saw)

def opens(data_dir, game_docs, prop_docs, now):
    """stamp open (first seen fair) on main lines and main prop rows; returns how many lines moved 3+ points"""
    fn = os.path.join(data_dir, "track", "opens.json")
    try:
        st = json.load(open(fn))
    except Exception:
        st = {}
    t = _iso(now)
    live = {g["id"] for g in game_docs}
    moved = 0

    def stamp(gid, key, line):
        nonlocal moved
        f = line.get("fair") or {}
        if not f:
            return
        g = st.setdefault(gid, {})
        o = g.get(key)
        if o is None:
            g[key] = {"f": {k: round(v, 4) for k, v in f.items() if v is not None}, "t": t, "pt": line.get("point")}
            return
        line["open"], line["openAt"], line["openPt"] = o["f"], o["t"], o.get("pt")
        if o.get("pt") != line.get("point") or any(abs((f.get(k) or 0) - v) >= 0.03 for k, v in o["f"].items() if f.get(k) is not None):
            moved += 1

    for g in game_docs:
        for l in g.get("lines") or []:
            if l.get("isMain") or l.get("market") == "h2h":
                stamp(g["id"], f"{l['market']}|{l.get('team') or ''}", l)       # by market: a moved line keeps its open
    for p in prop_docs:
        for r in p.get("rows") or []:
            if r.get("isMain"):
                stamp(p["id"], f"{p['market']}|{r.get('player')}", r)
    cut = now - timedelta(days=2)
    st = {gid: v for gid, v in st.items() if gid in live or any(_dt(x["t"]) > cut for x in v.values())}
    json.dump(st, open(fn, "w"), separators=(",", ":"))
    return moved


def annotate_moves(edges, game_docs, prop_docs):
    """each edge gets where its line opened (first fair we saw for that side, and the point then)"""
    idx = _index(game_docs, prop_docs)
    n = 0
    for e in edges:
        hit = idx.get(_key(e["gameId"], e["market"], e["side"], e.get("point"), e.get("player"), e.get("team") if not e.get("player") else None))
        if not hit:
            continue
        line, o = hit
        if line.get("open") and line["open"].get(o) is not None:
            pt = line.get("openPt")
            if pt is not None and "spreads" in (e.get("market") or "") and not e.get("player") and e.get("point") is not None and line.get("point") is not None \
                    and abs(e["point"] + line["point"]) < 1e-6:
                pt = -pt        # away side: its own handicap
            e["open"], e["openPt"], e["openAt"] = line["open"][o], pt, line.get("openAt")
            n += 1
    return n


# ---------------------------------------------------------------------------
# steam: the sharp fair moved toward an edge's side in the last ~75 minutes (the venue hasn't followed, or there'd be no
# edge). A short fair history of every main line and main prop row (track/fairhist.json, 90 minutes) gives the move.
# Edges carry steam (points moved); the edge log keeps it so the report card grades steam vs static gaps by results.
STEAM_MIN, STEAM_PTS = 75, 0.02


def steam(data_dir, edges, game_docs, prop_docs, now):
    fn = os.path.join(data_dir, "track", "fairhist.json")
    try:
        hist = json.load(open(fn))
    except Exception:
        hist = {}
    m = int(now.timestamp() // 60)
    idx = _index(game_docs, prop_docs)
    for k, (line, o) in idx.items():
        if not (line.get("isMain") or line.get("market") == "h2h"):
            continue
        f = (line.get("fair") or {}).get(o)
        if f is None:
            continue
        h = hist.setdefault(k, [])
        if not h or h[-1][0] != m:
            h.append([m, round(f, 4)])
    hist = {k: [x for x in v if m - x[0] <= 90] for k, v in hist.items()}
    hist = {k: v for k, v in hist.items() if v}
    n = 0
    for e in edges:
        k = _key(e["gameId"], e["market"], e["side"], e.get("point"), e.get("player"), e.get("team") if not e.get("player") else None)
        h = hist.get(k) or []
        old = [x for x in h if m - x[0] >= 20 and m - x[0] <= STEAM_MIN]
        if not old:
            continue
        mv = round(h[-1][1] - old[0][1], 4)
        e["fairMove"] = mv
        if mv >= STEAM_PTS:
            e["steam"] = True
            n += 1
    with open(fn, "w") as f:      # one line per key so git stores small deltas run to run
        f.write("{\n" + ",\n".join(f"{json.dumps(k)}:{json.dumps(v, separators=(',', ':'))}" for k, v in sorted(hist.items())) + "\n}\n")
    return n
