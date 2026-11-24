"""Where the money is (Nov 17): a running record of every arb, middle and maker order the board finds, so the reports can
say where each one pays, when to look and which venues, sports and markets carry them.

Vince (Nov 17): "Keep tracking all this so we can make reports on where make profit and where the best everything is".

Arbs and middles: opps.annotate keeps each open opportunity's life in latest/opps_seen.json; arb_ent stamps what we
need on it (kind, league, prop or line, venues, best margin while open, best margin while confirmed, money it could take,
hours to the start when first seen). When it leaves the board (a venue moved, the game started, or its prices aged
out) it is written to track/arbs/<Pacific day first seen>.jsonl. Its life is first to last seen (a lower bound: runs are minutes apart).

Maker orders: maker_census records every suggestion at 5%+ per Pacific day (track/makers/census/<day>.json: first and
last seen, best EV, venue, kind, league, fringe, hours to start, competitive), so the report can count how many 10%+
orders each venue / kind / lead time / sport / hour carries a day. The paper record (track/makers/log/<day>.jsonl: posted ->
filled / locked / cancelled / expired, then graded) gives fill rates and profit per order by the same cuts.

summarize() -> stats/opps_report.json (rep.oppreport, Reports > Where the money is). Rerun hourly or when something
closed or graded. Everything is clustered by game where it is a rate of return."""
import json
import math
import os
from datetime import datetime, timedelta, timezone

PT = timezone(timedelta(hours=-8))
ARB_BAR = 0.03      # Vince's arb target


def _iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _pday(t):
    return _iso(t).astimezone(PT).strftime("%Y-%m-%d")


def _phour(t):
    return _iso(t).astimezone(PT).hour


def _load(fn, default):
    try:
        return json.load(open(fn))
    except Exception:
        return default


def _jsonl(fn):
    out = []
    try:
        with open(fn) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return out


def _lead(h):
    if h is None:
        return "?"
    return "Under 1 h" if h < 1 else "1-6 h" if h < 6 else "6-24 h" if h < 24 else "1-3 days" if h < 72 else "3+ days"


LEADS = ["Under 1 h", "1-6 h", "6-24 h", "1-3 days", "3+ days"]


def _med(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


# ---------------------------------------------------------------------------------------------------- arbs and middles

def arb_ent(o, prev, ent, now):
    """stamp the record fields on an opps_seen entry (called by opps.annotate for every open opportunity)"""
    p = prev or {}
    val = o.get("profit") if o["kind"] == "arb" else o.get("ev")
    conf = bool(o.get("confirmed"))
    ent.update({
        "k": o["kind"], "gid": o.get("gameId"), "lg": o.get("league"), "mk": o.get("market"), "prop": bool(o.get("player")),
        "v": sorted({l["book"] for l in o.get("legs") or []}), "free": bool(o.get("free")) or p.get("free", False),
        "best": max(val or 0, p.get("best") or 0), "bestConf": max(val or 0, p.get("bestConf") or 0) if conf else p.get("bestConf"),
        "conf": conf or p.get("conf", False), "confPulls": p.get("confPulls", 0) + conf,
        "mp": max(o.get("maxProfit") or 0, p.get("mp") or 0) if conf else p.get("mp"),
        "ms": max(o.get("maxStake") or 0, p.get("ms") or 0) if conf else p.get("ms"),
        "lead": p.get("lead") if p.get("lead") is not None else (
            round((_iso(o["commence"]) - now).total_seconds() / 3600, 2) if o.get("commence") else None),
        "susp": bool(o.get("suspect")) and p.get("susp", True),
        "moving": bool(o.get("moving")) or p.get("moving", False),
        "off": max(p.get("off") or 0, max((l.get("off") or 0) for l in o.get("legs") or [{}])),
    })
    return ent


def arb_close(data_dir, closed, now):
    """opportunities that left the board -> track/arbs/log.jsonl"""
    rows = [dict(e, key=k, closed=now.isoformat(), life=round((_iso(e["last"]) - _iso(e["first"])).total_seconds() / 60, 1))
            for k, e in closed.items() if e.get("k")]
    if not rows:
        return 0
    os.makedirs(os.path.join(data_dir, "track", "arbs"), exist_ok=True)
    by = {}
    for r in rows:          # one file per Pacific day first seen, so no file grows past a day's worth
        by.setdefault(_pday(r["first"]), []).append(r)
    for d, rs in by.items():
        with open(os.path.join(data_dir, "track", "arbs", f"{d}.jsonl"), "a") as f:
            for r in rs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return len(rows)


def _arb_cell(rs, days):
    arbs = [r for r in rs if r["k"] == "arb"]
    c3 = [r for r in arbs if r.get("conf") and (r.get("bestConf") or 0) >= ARB_BAR]
    conf = [r for r in arbs if r.get("conf")]
    return {"n": len(arbs), "conf": len(conf), "c3": len(c3), "perDay": round(len(conf) / days, 1) if days else None,
            "c3PerDay": round(len(c3) / days, 2) if days else None,
            "best": round(max((r.get("bestConf") or 0) for r in conf), 4) if conf else None,
            "med": round(_med([r.get("bestConf") for r in conf]) or 0, 4) if conf else None,
            "life": _med([r.get("life") for r in conf]), "life3": _med([r.get("life") for r in c3]),
            "money3": round(sum(r.get("mp") or 0 for r in c3), 2),
            "stake3": _med([r.get("ms") for r in c3])}


def arb_summary(data_dir, now, days=14):
    rows = []
    adir = os.path.join(data_dir, "track", "arbs")
    first_day = (now.astimezone(PT) - timedelta(days=35)).strftime("%Y-%m-%d")
    for f in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
        if f.endswith(".jsonl") and f[:10] >= first_day:
            rows += _jsonl(os.path.join(adir, f))
    seen = _load(os.path.join(data_dir, "latest", "opps_seen.json"), {})
    rows += [dict(e, key=k, life=round((_iso(e["last"]) - _iso(e["first"])).total_seconds() / 60, 1), open=True)
             for k, e in seen.items() if e.get("k")]
    rows = [r for r in rows if not r.get("susp")]
    if not rows:
        return None
    # one row per opportunity per day: one that drops off the list for a run and comes back is the same one
    merged = {}
    for r in sorted(rows, key=lambda r: r["first"]):
        k = (r.get("key"), _pday(r["first"]))
        m = merged.get(k)
        if not m:
            merged[k] = dict(r)
            continue
        m["last"] = max(m["last"], r["last"])
        m["life"] = round((_iso(m["last"]) - _iso(m["first"])).total_seconds() / 60, 1)
        m["conf"] = m.get("conf") or r.get("conf")
        for f in ("best", "bestConf", "mp", "ms"):
            if r.get(f) is not None:
                m[f] = max(m.get(f) or 0, r[f])
        m["free"] = m.get("free") or r.get("free")
    rows = list(merged.values())
    cut = (now.astimezone(PT) - timedelta(days=days)).strftime("%Y-%m-%d")
    for r in rows:
        r["d"], r["h"] = _pday(r["first"]), _phour(r["first"])
    since = min(r["d"] for r in rows)
    rec = [r for r in rows if r["d"] >= cut]
    nd = max(1, len({r["d"] for r in rec}))
    by = lambda f, order=None: sorted(
        [{"k": k, **_arb_cell([r for r in rec if f(r) == k], nd)} for k in {f(r) for r in rec}],
        key=lambda c: (order.index(c["k"]) if order and c["k"] in order else -c["c3"] * 1000 - c["conf"]))
    day_rows = []
    for d in sorted({r["d"] for r in rows}):
        rs = [r for r in rows if r["d"] == d]
        c = _arb_cell(rs, 1)
        mids = [r for r in rs if r["k"] == "middle"]
        c.update(d=d, mid=len(mids), free=sum(1 for r in mids if r.get("free")),
                 midBest=round(max((r.get("best") or 0) for r in mids), 4) if mids else None)
        day_rows.append(c)
    pair = lambda r: " + ".join(r.get("v") or [])
    hours = []
    for h in range(24):
        rs = [r for r in rec if r["h"] == h]
        c = _arb_cell(rs, nd)
        hours.append({"h": h, "confPerDay": c["perDay"], "c3PerDay": c["c3PerDay"], "best": c["best"]})
    mids = [r for r in rec if r["k"] == "middle"]
    return {"since": since, "days": nd, "window": days, "all": _arb_cell(rec, nd), "byDay": day_rows[-30:], "byHour": hours,
            "byPair": by(pair)[:15], "byLeague": by(lambda r: r.get("lg") or "?")[:15],
            "byKind": by(lambda r: "Props" if r.get("prop") else "Game lines"),
            "byLead": by(lambda r: _lead(r.get("lead")), LEADS),
            "byVenue": sorted([{"k": v, **_arb_cell([r for r in rec if v in (r.get("v") or [])], nd)}
                               for v in {v for r in rec for v in r.get("v") or []}], key=lambda c: -c["c3"] * 1000 - c["conf"]),
            "middles": {"n": len(mids), "perDay": round(len(mids) / nd, 1), "free": sum(1 for r in mids if r.get("free")),
                        "medEv": _med([r.get("best") for r in mids]), "best": max((r.get("best") or 0) for r in mids) if mids else None}}


# ---------------------------------------------------------------------------------------------------- maker orders

def _cdir(data_dir, *p):
    return os.path.join(data_dir, "track", "makers", "census", *p)


def maker_census(data_dir, items, now):
    """every suggestion at 5%+ today, once per key: first / last seen, best EV"""
    os.makedirs(_cdir(data_dir), exist_ok=True)
    day = now.astimezone(PT).strftime("%Y-%m-%d")
    fn = _cdir(data_dir, f"{day}.json")
    cur = _load(fn, {})
    t = now.isoformat()
    for o in items:
        k = o.get("key") or "|".join(str(o.get(x)) for x in ("venue", "gameId", "market", "player", "point", "side"))
        e = cur.get(k)
        if e:
            e["l"], e["ev"], e["c"], e["n"] = t, max(e["ev"], round(o["ev"], 4)), e["c"] or bool(o.get("competitive")), e["n"] + 1
        else:
            cur[k] = {"f": t, "l": t, "ev": round(o["ev"], 4), "ev0": round(o["ev"], 4), "v": o["venue"], "k": o["kind"],
                      "lg": o.get("league"), "fr": bool(o.get("fringe")), "h": o.get("hrs"), "c": bool(o.get("competitive")),
                      "gid": o.get("gameId"), "n": 1}
    json.dump(cur, open(fn, "w"), separators=(",", ":"))
    for f in os.listdir(_cdir(data_dir)):          # 45 days kept; the summary keeps the daily counts
        if f.endswith(".json") and f[:10] < (now.astimezone(PT) - timedelta(days=45)).strftime("%Y-%m-%d"):
            os.remove(_cdir(data_dir, f))


def _where(fr, h):
    return "Fringe sports" if fr else "Early (36 h+)" if (h or 0) > 36 else "Board, inside 36 h"


def _census_day(rows):
    hi = [r for r in rows if r["ev"] >= 0.10]
    hc = [r for r in hi if r["c"]]
    return {"n5": len(rows), "n10": len(hi), "n10c": len(hc), "best": round(max((r["ev"] for r in hc), default=0), 4) or None,
            "games10c": len({r.get("gid") for r in hc})}


def census_summary(data_dir, now, days=7):
    try:
        files = sorted(f for f in os.listdir(_cdir(data_dir)) if f.endswith(".json"))
    except OSError:
        return None
    if not files:
        return None
    cache_fn = os.path.join(data_dir, "stats", "maker_census_days.json")
    cache = _load(cache_fn, {})
    today = now.astimezone(PT).strftime("%Y-%m-%d")
    recent_cut = (now.astimezone(PT) - timedelta(days=days)).strftime("%Y-%m-%d")
    rec = []
    by_day = []
    for f in files:
        d = f[:10]
        if d < recent_cut and d in cache:
            by_day.append(dict(cache[d], d=d))
            continue
        rows = list(_load(_cdir(data_dir, f), {}).values())
        s = _census_day(rows)
        if d != today:
            cache[d] = s
        by_day.append(dict(s, d=d))
        if d >= recent_cut:
            for r in rows:
                r["d"] = d
            rec += rows
    os.makedirs(os.path.dirname(cache_fn), exist_ok=True)
    json.dump(cache, open(cache_fn, "w"), separators=(",", ":"))
    nd = max(1, len({r["d"] for r in rec}))

    def cell(rs):
        hi = [r for r in rs if r["ev"] >= 0.10]
        hc = [r for r in hi if r["c"]]
        return {"n10": round(len(hi) / nd, 1), "n10c": round(len(hc) / nd, 1), "games10c": round(len({(r["d"], r.get("gid")) for r in hc}) / nd, 1),
                "medEv": _med([r["ev"] for r in hc]), "best": max((r["ev"] for r in hc), default=None),
                "life": _med([(_iso(r["l"]) - _iso(r["f"])).total_seconds() / 60 for r in hc])}

    def by(f, order=None):
        ks = {f(r) for r in rec}
        out = [{"k": k, **cell([r for r in rec if f(r) == k])} for k in ks]
        return sorted(out, key=lambda c: (order.index(c["k"]) if order and c["k"] in order else -c["n10c"]))
    lead = lambda r: _lead(r.get("h"))
    return {"days": nd, "byDay": by_day[-30:], "all": cell(rec),
            "byVenue": by(lambda r: r["v"]), "byKind": by(lambda r: r["k"], ["main", "alt", "prop"]),
            "byLead": by(lead, LEADS), "byWhere": by(lambda r: _where(r.get("fr"), r.get("h")), ["Board, inside 36 h", "Early (36 h+)", "Fringe sports"]),
            "byLeague": by(lambda r: r.get("lg") or "?")[:20], "byHour": [{"k": h, **cell([r for r in rec if _phour(r["f"]) == h])} for h in range(24)],
            "byVenueKind": by(lambda r: f"{r['v']} · {r['k']}")[:16]}


def _evband(e):
    return "5-10%" if e < 0.10 else "10-15%" if e < 0.15 else "15-20%" if e < 0.20 else "20%+"


def _clus(rows):
    """ROI with SE clustered by game (rows: pnl per $1)"""
    n = len(rows)
    if not n:
        return None, None
    roi = sum(r["pnl"] for r in rows) / n
    g = {}
    for r in rows:
        g.setdefault(r.get("gameId") or r.get("d"), []).append(r["pnl"] - roi)
    G = len(g)
    if G < 2:
        return roi, None
    se = math.sqrt(G / (G - 1) * sum(sum(v) ** 2 for v in g.values())) / n
    return roi, se


def maker_record(data_dir):
    import makers
    rows = makers.log_rows(data_dir)
    op = _load(os.path.join(data_dir, "track", "makers", "open.json"), {})
    if not rows and not op:
        return None

    def cell(rs, opn=()):
        filled = [r for r in rs if r.get("status") in ("filled", "locked")]
        done = len(rs)
        graded = [r for r in filled if r.get("pnl") is not None and not r.get("ungraded") and (r.get("y") is not None or r.get("status") == "locked")]
        roi, se = _clus(graded)
        clv = [r["clv"] for r in graded if r.get("clv") is not None]
        exp = [r["pFill"] for r in rs if r.get("pFill") is not None]
        fo = [e for e in opn if e.get("status") in ("filled", "locked")]      # filled, game not graded yet
        nf, decided = len(filled) + len(fo), done + len(fo)
        exp += [e["pFill"] for e in fo if e.get("pFill") is not None]
        return {"done": done, "resting": sum(1 for e in opn if e.get("status") == "resting"),
                "filled": nf, "locked": sum(1 for r in filled + fo if r.get("status") == "locked"), "pending": len(fo),
                "fillRate": round(nf / decided, 3) if decided else None, "predicted": round(sum(exp) / len(exp), 3) if exp else None,
                "graded": len(graded), "roi": round(roi, 4) if roi is not None else None, "roiSE": round(se, 4) if se else None,
                "roiT": round(roi / se, 2) if se else None, "clv": round(sum(clv) / len(clv), 4) if clv else None,
                "perOrder": round(sum(r["pnl"] for r in graded) / done, 4) if done and graded else None,
                "evPosted": round(sum(r.get("ev") or 0 for r in rs + fo) / decided, 4) if decided else None, "decided": decided}
    opn = list(op.values())

    def by(f, order=None):
        ks = {f(r) for r in rows} | {f(e) for e in opn}
        out = [{"k": k, **cell([r for r in rows if f(r) == k], [e for e in opn if f(e) == k])} for k in ks]
        return sorted(out, key=lambda c: (order.index(c["k"]) if order and c["k"] in order else -c["done"]))
    return {"all": cell(rows, opn), "byVenue": by(lambda r: r.get("venue")), "byKind": by(lambda r: r.get("kind"), ["main", "alt", "prop"]),
            "byLead": by(lambda r: _lead(r.get("hrs")), LEADS), "byLeague": by(lambda r: r.get("league") or "?")[:20],
            "byWhere": by(lambda r: _where(r.get("fringe"), r.get("hrs")), ["Board, inside 36 h", "Early (36 h+)", "Fringe sports"]),
            "byEv": by(lambda r: _evband(r.get("ev") or 0), ["5-10%", "10-15%", "15-20%", "20%+"]),
            "byComp": by(lambda r: "No sportsbook beats it" if r.get("competitive") else "A sportsbook is cheaper"),
            "byHour": by(lambda r: _phour(r["at"]) if r.get("at") else -1, list(range(24)))}


def summarize(data_dir, now, log=print):
    out = {"updated": now.isoformat(), "arbBar": ARB_BAR}
    for k, f in (("arbs", lambda: arb_summary(data_dir, now)), ("census", lambda: census_summary(data_dir, now)),
                 ("makers", lambda: maker_record(data_dir))):
        try:
            out[k] = f()
        except Exception as e:
            log(f"oppreport: {k} failed ({type(e).__name__}: {str(e)[:80]})")
            out[k] = None
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "stats", "opps_report.json"), "w"), separators=(",", ":"))
    return out
