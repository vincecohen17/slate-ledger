"""The Methods board (Nov 17): every betting method we might use, each graded on its own record, so the pick runs adopt
the ones that work and drop the ones that don't.

A method is a plain rule that picks a side. Two kinds:
  - edge methods ride our flagged edges (track/edgecard: every edge logged at the venue price it was flagged at, graded
    after the game). One row per side (the venue with the biggest edge), so a prop flagged at five venues counts once.
  - market methods use the market's own behavior (DraftKings' public splits, line moves): fade the public, follow the
    sharp money, reverse line movement... Their earlier record comes from the market report card (every main-market side,
    graded at the closing no-vig price: does the signal beat the close?). From Nov 17 they also paper-bet 1u at the best
    price on Vince's venues in the last FLAG_MIN minutes before the start (track/methods/open.json -> graded.jsonl),
    which is the record that counts for adopting them: a signal has to beat the vig, not just the close.

Each record: sides, games, win rate, flat ROI with its SE clustered by game (one game's sides win or lose together),
CLV against the market close where we have it, the last 14 days, and the leagues. Status (games = distinct games):
  Adopted  100+ games on a playable record and ROI t >= 2, or CLV t >= 2 with ROI not negative
  Dropped  100+ games and ROI t <= -2 or CLV t <= -2
  Testing  everything else (Promising when 30+ games and ROI t >= 1.5)
A market method with fewer than 30 games of paper bets is Dropped when its close record lost with t <= -2 over 100+
games (it can't beat the vig if it can't beat the no-vig close) and Promising when that record won with t >= 2.
Only Adopted methods can carry a bet or floor pick (runbook/picks.md); Testing methods can post tracked leans.
stats/methods.json, Reports > Methods."""
import glob
import json
import math
import os
from datetime import datetime, timedelta, timezone

import config as C

FLAG_MIN = 90               # market methods flag in the last 90 minutes before the start (splits and lines near their close)
ADOPT_GAMES, ADOPT_T, DROP_T, PROMISE_GAMES, PROMISE_T = 100, 2.0, -2.0, 30, 1.5
CLOSE_OK_MIN = 30
RECENT_DAYS = 14

MOVE_MIN = 0.015


def _sup(lo, hi):
    return lambda r: r.get("sup") is not None and lo <= r["sup"] < hi


def _mkt_edge(r):
    """an edge priced off the market itself (not a model-shaped alt fair, not a one-sided estimate)"""
    return not r.get("est") and not r.get("model")


# key, name, what it is, source, rule
METHODS = [
    ("price_edge", "Price edge 3%+", "A venue at least 3% better than the sharp no-vig fair (market-priced lines and props).", "edges",
     lambda r: _mkt_edge(r) and r["edge"] >= 0.03 and r.get("kind") != "pick'em"),
    ("big_edge", "Price edge 5%+", "The same, 5% or more.", "edges",
     lambda r: _mkt_edge(r) and r["edge"] >= 0.05 and r.get("kind") != "pick'em"),
    ("model_backs", "Model backs the edge", "A 3%+ edge where our stat model sits between the price and the fair (or near the fair).", "edges",
     lambda r: not r.get("est") and r["edge"] >= 0.03 and _sup(0, 1.5)(r)),
    ("model_beyond", "Model past the fair", "A 3%+ edge where the model is even further out than our fair (they share an error?).", "edges",
     lambda r: not r.get("est") and r["edge"] >= 0.03 and _sup(1.5, 99)(r)),
    ("model_under", "Model against the edge", "A 3%+ edge the model doesn't back (model below the price).", "edges",
     lambda r: not r.get("est") and r["edge"] >= 0.03 and _sup(-99, 0)(r)),
    ("steam_follow", "Steam: fair just moved toward it", "The sharp fair moved 2+ points toward the side in the last hour and a venue still has 1.5%+.", "edges",
     lambda r: (r.get("steam") or 0) >= 0.02 and r["edge"] >= 0.015 and not r.get("est")),
    ("venue_lag", "Sharp move, venue lagging", "The sharp books moved and this venue's own history says it follows slowly.", "edges",
     lambda r: r.get("wk") == "lag"),
    ("moved_toward", "Line moved toward the edge", "A 1.5%+ edge on a side the market has been moving toward.", "edges",
     lambda r: r.get("wk") == "move" and r["edge"] >= 0.015),
    ("pickem_stale", "Stale pick'em line", "A pick'em app's line 3%+ off the market (Dabble, PrizePicks, Underdog...), at its per-pick payout.", "edges",
     lambda r: r.get("kind") == "pick'em" and r["edge"] >= 0.03),
    ("fade_public", "Fade the public", "The side with 30% or less of the bets at DraftKings.", "market",
     lambda s: s.get("b") is not None and s["b"] <= 30),
    ("follow_public", "Ride the public", "The side with 70%+ of the bets.", "market",
     lambda s: s.get("b") is not None and s["b"] >= 70),
    ("sharp_money", "Follow the sharp money", "Money % at least 10 points above bets % (bigger bettors on it).", "market",
     lambda s: s.get("div") is not None and s["div"] >= 10),
    ("tickets_money", "Tickets over money", "Bets % at least 10 points above money % (the side the big money is against).", "market",
     lambda s: s.get("div") is not None and s["div"] <= -10),
    ("rlm", "Reverse line movement", "The line moved toward a side with 40% or less of the bets.", "market",
     lambda s: bool(s.get("rlm"))),
    ("sharp_led", "Sharp books moved first", "A sharp-led move of 1.5+ points toward the side.", "market",
     lambda s: bool(s.get("sl")) and (s.get("mv") or 0) >= MOVE_MIN),
    ("chase_move", "Chase the move", "The side the line moved 3+ points toward since open.", "market",
     lambda s: (s.get("mv") or 0) >= 0.03),
    ("fade_move", "Fade the move", "The side the line moved 3+ points away from.", "market",
     lambda s: (s.get("mv") or 0) <= -0.03),
]
BY_KEY = {m[0]: m for m in METHODS}


def _p(*a):
    return os.path.join(*a)


def _iso(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00"))


def _load(fn, default):
    try:
        return json.load(open(fn))
    except Exception:
        return default


def _rows(pattern):
    out = []
    for fn in glob.glob(pattern):
        for line in open(fn):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


# ---------- market methods: paper bets at the best price on Vince's venues ----------

def _venues():
    return {b for b, v in C.BOOKS.items() if v.get("venue") and v.get("kind") != "dfs"}


def _main_line(doc, mk):
    ls = [l for l in doc.get("lines") or [] if l.get("market") == mk and l.get("fair")]
    return max(ls, key=lambda l: l.get("fairWeight") or 0) if ls else None


def observe(data_dir, game_docs, now, log=print):
    """flag market-method sides on games starting within FLAG_MIN minutes, at the best venue price now (first flag kept)"""
    fn = _p(data_dir, "track", "methods", "open.json")
    op = _load(fn, {})
    ven, n = _venues(), 0
    for d in game_docs:
        sides = ((d.get("mkt") or {}).get("sides")) or {}
        if not sides or not d.get("commence"):
            continue
        mins = (_iso(d["commence"]) - now).total_seconds() / 60
        if not 0 < mins <= FLAG_MIN:
            continue
        for key, sig in sides.items():
            mk, ab = key.split("|")
            hits = [m[0] for m in METHODS if m[3] == "market" and m[4](sig)]
            if not hits:
                continue
            l = _main_line(d, mk)
            if not l:
                continue
            outs = l.get("outcomes") or []
            if mk == "totals":
                side = "Over" if ab == "A" else "Under"
            else:
                side = d["home"] if ab == "A" else d["away"]
            okey = next((o for o in outs if o == side), None)
            if not okey or okey not in (l.get("fair") or {}):
                continue
            if not 0.2 <= l["fair"][okey] <= 0.8:
                continue        # lopsided moneylines: a 3% favorite's price says nothing about a method
            best = None
            for q in l.get("quotes") or []:
                if q.get("book") not in ven or q.get("stale"):
                    continue
                pr = (q.get("prices") or {}).get(okey) or {}
                cost = pr.get("cost") or (1 / pr["dec"] if pr.get("dec") else None)
                if cost and 0.01 < cost < 0.99 and (best is None or cost < best[1]):
                    best = (q["book"], cost)
            if not best:
                continue
            pt = l.get("point")
            if mk == "spreads" and pt is not None and ab == "B":
                pt = -pt
            for m in hits:
                k = f"{m}|{d['id']}|{mk}|{ab}"
                if k in op:
                    continue
                op[k] = {"m": m, "gid": d["id"], "sport": d.get("sport"), "league": d.get("league"), "commence": d["commence"],
                         "mk": mk, "ab": ab, "side": side, "pt": pt, "team": side if mk != "totals" else None,
                         "venue": best[0], "dec": round(1 / best[1], 4), "fair": l["fair"][okey], "t": now.isoformat(),
                         "sig": {x: sig.get(x) for x in ("mv", "h", "b", "div", "rlm", "sl") if sig.get(x) is not None}}
                n += 1
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    json.dump(op, open(fn, "w"), separators=(",", ":"))
    if n:
        log(f"methods: {n} market-method sides paper-bet")
    return n


def grade(data_dir, fin_by_gid, now, log=print):
    """grade paper bets whose game is over: result from the final, CLV against the market consensus at the close"""
    import closemkt
    import ledger
    fn = _p(data_dir, "track", "methods", "open.json")
    op = _load(fn, {})
    if not op:
        return 0
    out, n = [], 0
    for k, e in list(op.items()):
        start = _iso(e["commence"])
        if e["gid"] not in fin_by_gid and now - start < timedelta(hours=4):
            continue
        leg = {"gameId": e["gid"], "market": e["mk"], "side": e["side"], "point": e.get("pt"), "team": e.get("team")}
        try:
            g = ledger.grade_leg(data_dir, leg, fin_by_gid)
        except Exception:
            g = None
        if g is None:
            if now - start > timedelta(days=4):
                op.pop(k)               # never graded (no final): drop it
            continue
        if g.get("void"):
            op.pop(k)
            continue
        y = g.get("y")
        try:
            c = closemkt.leg_close(data_dir, leg, e["sport"], e["commence"])
        except Exception:
            c = {}
        close = c.get("p")
        out.append({"d": (start - timedelta(hours=8)).strftime("%Y-%m-%d"), "m": e["m"], "gid": e["gid"], "sport": e["sport"],
                    "league": e.get("league"), "mk": e["mk"], "side": e["side"], "pt": e.get("pt"), "venue": e["venue"],
                    "dec": e["dec"], "fair": e["fair"], "close": close, "ca": c.get("age"),
                    "clv": round(close * e["dec"] - 1, 4) if close else None,
                    "y": y, "pnl": 0.0 if y is None else round(y * e["dec"] - 1, 4), "sig": e.get("sig")})
        op.pop(k)
        n += 1
    if out:
        gfn = _p(data_dir, "track", "methods", "graded.jsonl")
        with open(gfn, "a") as f:
            for r in out:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
    json.dump(op, open(fn, "w"), separators=(",", ":"))
    if n:
        log(f"methods: {n} paper bets graded")
    return n


# ---------- the board ----------

def _ck(r):
    return r.get("gid") or f"{r.get('d')}|{r.get('sport')}"


def _stats(rows, now):
    """flat ROI and CLV with standard errors clustered by game"""
    g = [r for r in rows if r.get("pnl") is not None]
    n = len(g)
    if not n:
        return {"n": 0}
    cl = {}
    for r in g:
        c = cl.setdefault(_ck(r), [0.0, 0])
        c[0] += r["pnl"]
        c[1] += 1
    G = len(cl)
    roi = sum(r["pnl"] for r in g) / n
    out = {"n": n, "games": G, "roi": round(roi, 4),
           "win": round(sum(1 for r in g if r.get("y") == 1) / max(1, sum(1 for r in g if r.get("y") is not None)), 4)}
    if G >= 6:
        v = G / (G - 1) * sum((c[0] - roi * c[1]) ** 2 for c in cl.values())
        if v > 0:
            out["roiSE"] = round(math.sqrt(v) / n, 4)
            out["roiT"] = round(roi / (math.sqrt(v) / n), 2)
    cv = [r for r in g if r.get("clv") is not None and not ((r.get("ca") or 0) > CLOSE_OK_MIN)]
    if cv:
        m = sum(r["clv"] for r in cv) / len(cv)
        out["clv"], out["nClv"] = round(m, 4), len(cv)
        out["beat"] = round(sum(r["clv"] > 0 for r in cv) / len(cv), 3)
        cc = {}
        for r in cv:
            c = cc.setdefault(_ck(r), [0.0, 0])
            c[0] += r["clv"]
            c[1] += 1
        Gc = len(cc)
        if Gc >= 6:
            v = Gc / (Gc - 1) * sum((c[0] - m * c[1]) ** 2 for c in cc.values())
            if v > 0:
                out["clvT"] = round(m / (math.sqrt(v) / len(cv)), 2)
    cut = (now - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    rec = [r for r in g if (r.get("d") or "") >= cut]
    if rec:
        out["recent"] = {"n": len(rec), "games": len({_ck(r) for r in rec}), "roi": round(sum(r["pnl"] for r in rec) / len(rec), 4)}
    lg = {}
    for r in g:
        lg.setdefault(r.get("league") or "?", []).append(r)
    out["leagues"] = sorted(({"k": k, "n": len(v), "games": len({_ck(x) for x in v}), "roi": round(sum(x["pnl"] for x in v) / len(v), 4)}
                             for k, v in lg.items()), key=lambda x: -x["n"])[:6]
    days = {}
    for r in g:
        days.setdefault(r.get("d"), [0.0, 0])
        days[r.get("d")][0] += r["pnl"]
        days[r.get("d")][1] += 1
    out["daily"] = [[d, round(v[0], 3), v[1]] for d, v in sorted(days.items()) if d]
    return out


def _status(s, playable=True, use_clv=True):
    G, t, roi = s.get("games") or 0, s.get("roiT"), s.get("roi") or 0
    ct = s.get("clvT") if use_clv else None
    if not playable:
        return "signal"
    if G >= ADOPT_GAMES and ((t is not None and t <= DROP_T) or (ct is not None and ct <= DROP_T)):
        return "dropped"
    if G >= ADOPT_GAMES and ((t is not None and t >= ADOPT_T) or (ct is not None and ct >= ADOPT_T and roi >= 0)):
        return "adopted"
    if G >= PROMISE_GAMES and t is not None and t >= PROMISE_T:
        return "promising"
    return "testing"


def _dedupe(rows):
    """one row per side: the venue with the biggest edge"""
    best = {}
    for r in rows:
        k = (r.get("gid") or f"{r.get('d')}|{r.get('sport')}", r.get("mk"), r.get("side"), r.get("player"), r.get("pt"))
        if k not in best or r["edge"] > best[k]["edge"]:
            best[k] = r
    return list(best.values())


def summarize(data_dir, now, log=print):
    edges = [r for r in _rows(_p(data_dir, "track", "edgecard", "*.jsonl")) if _ok_edge(r)]
    mrows = [r for r in _rows(_p(data_dir, "track", "market", "graded", "*.jsonl")) if not ((r.get("ca") or 0) > CLOSE_OK_MIN)]
    paper = _rows(_p(data_dir, "track", "methods", "graded.jsonl"))
    out = {"updated": now.isoformat(), "rules": {"adoptGames": ADOPT_GAMES, "adoptT": ADOPT_T, "dropT": DROP_T, "flagMin": FLAG_MIN},
           "since": min((r.get("d") for r in edges if r.get("d")), default=None), "methods": []}
    for key, name, blurb, src, rule in METHODS:
        m = {"key": key, "name": name, "blurb": blurb, "src": src}
        if src == "edges":
            rows = _dedupe([r for r in edges if _safe(rule, r)])
            # the edge log's CLV is against our own fair at the close (model-shaped on alt lines), which runs well above
            # what these edges return: judged on results only
            m["live"] = _stats([{**r, "clv": None} for r in rows], now)
            m["status"] = _status(m["live"], use_clv=False)
        else:
            m["live"] = _stats([r for r in paper if r.get("m") == key], now)
            # earlier record: every main-market side with this signal, at its closing no-vig price (no vig to beat)
            m["close"] = _stats([r for r in mrows if _safe(rule, r)], now)
            if (m["live"].get("games") or 0) >= PROMISE_GAMES:
                m["status"] = _status(m["live"])
            else:       # not enough paper bets yet: the close record decides only the clear cases
                ct, cg = m["close"].get("roiT"), m["close"].get("games") or 0
                m["status"] = "dropped" if cg >= ADOPT_GAMES and ct is not None and ct <= DROP_T else \
                    "promising" if cg >= ADOPT_GAMES and ct is not None and ct >= ADOPT_T else "testing"
            m["closeRead"] = _read_close(m["close"])
        out["methods"].append(m)
    order = {"adopted": 0, "promising": 1, "testing": 2, "signal": 3, "dropped": 4}
    out["methods"].sort(key=lambda m: (order.get(m["status"], 5), -((m["live"].get("roi") or 0))))
    os.makedirs(_p(data_dir, "stats"), exist_ok=True)
    json.dump(out, open(_p(data_dir, "stats", "methods.json"), "w"), indent=1)
    ad = [m["name"] for m in out["methods"] if m["status"] == "adopted"]
    log(f"methods: {len(out['methods'])} on the board; adopted: {len(ad)}")
    return out


def _ok_edge(r):
    """graded, not a feed error (a quote over 50 decimal or 3x the fair's price), not a mismatched close (20+ points off)"""
    if r.get("pnl") is None or not r.get("dec") or not r.get("fair"):
        return False
    if r["dec"] > 50 or r["dec"] > 3 / max(r["fair"], 0.01):
        return False
    return not (r.get("close") is not None and abs(r["close"] - r["fair"]) >= 0.2)


def _read_close(s):
    t = s.get("roiT")
    if t is None:
        return None
    return "beats the close" if t >= 2 else "loses to the close" if t <= -2 else "no edge over the close"


def _safe(rule, r):
    try:
        return bool(rule(r))
    except Exception:
        return False


def compact(board):
    """the board without the daily series, for the reports payload"""
    if not board:
        return None
    b = json.loads(json.dumps(board))
    for m in b.get("methods") or []:
        for k in ("live", "close"):
            if isinstance(m.get(k), dict):
                d = m[k].pop("daily", None)
                if d:
                    m[k]["spark"] = [x[1] for x in d[-30:]]
    return b
