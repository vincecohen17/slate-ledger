"""Market behavior: how the lines moved, who moved them, and where the money is.

Built from what we already pull plus DraftKings' public betting splits:
  movement  every main line (h2h / spreads / totals) per book over time, from the raw pulls in snapshots/ (each book's
            own last_update stamps the change): open, now, the path, and which book moved first
  splits    DraftKings tickets % and handle % per side (published by VSiN, data.vsin.com/<league>/betting-splits/)
  signals   per game side: move since open (in no-vig points), sharp-led steam (a sharp book moved first and the rest
            followed), reverse line movement (the line moved toward the side with the minority of tickets), money vs
            tickets (handle % well above tickets %: bigger bettors), consensus drift of the soft books
  grading   every signal is stamped on the side at the close and graded against the result and the close, clustered by
            game (edgecard.clus), so the site and the pick runs learn which ones actually predict anything

Nothing here is a bet by itself until its record says so.
"""
import gzip
import json
import os
import re
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "text/html,application/xhtml+xml"}
# DraftKings' own public splits (DK Network): server-rendered, every game, handle % and bets % for spread / total /
# moneyline. tb_eg is DraftKings' event group (league); pages of 10 games via tb_page
DK_SPLITS = "https://dknetwork.draftkings.com/draftkings-sportsbook-betting-splits/?tb_eg={eg}&tb_edate=n7days&tb_emt=0&tb_page={page}"
DK_GROUPS = {"NFL": 88808, "NBA": 42648, "NHL": 42133, "MLB": 84240, "NCAAF": 87637, "NCAAB": 92483, "WNBA": 94682,
             "EPL": 40253, "UCL": 40685, "UEL": 41410, "MLS": 89345}


def _fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def probe(data_dir, log=print):
    """save DraftKings' splits pages raw to the private data repo (latest/probe/) so the parser can be written against the
    real markup; logs sizes only"""
    out = os.path.join(data_dir, "latest", "probe")
    os.makedirs(out, exist_ok=True)
    for lg, eg in DK_GROUPS.items():
        for page in (1, 2):
            try:
                h = _fetch(DK_SPLITS.format(eg=eg, page=page))
            except Exception as e:
                log(f"splits probe {lg} p{page}: {type(e).__name__}")
                continue
            open(os.path.join(out, f"dk_{lg.lower()}_{page}.html"), "w").write(h)
            log(f"splits probe {lg} p{page}: {len(h)} bytes, {h.count('%')} percent signs, {h.count(' @ ')} games")


# ---------------------------------------------------------------- movement from the raw pulls
from datetime import datetime, timedelta, timezone
import glob

import config as C

MAIN = ("h2h", "spreads", "totals")
SHARP = {k for k, v in C.BOOKS.items() if v.get("kind") == "sharp"}      # Pinnacle, Circa, LowVig, BetOnline (exchanges quote back prices only: junk on thin markets)
SOFT = {"draftkings", "fanduel", "betmgm", "williamhill_us", "espnbet", "fanatics", "betrivers", "hardrockbet", "bovada", "ballybet"}
VENUES = {k for k, v in C.BOOKS.items() if v.get("venue")}
KEEP = SHARP | SOFT | VENUES
# rough chance per point of line near the middle (spread / total), to put point moves and price moves on one scale
SLOPE = {"NBA": (0.03, 0.022), "WNBA": (0.03, 0.022), "NCAAB": (0.03, 0.02), "NFL": (0.03, 0.025), "NCAAF": (0.025, 0.02),
         "NHL": (0.0, 0.09), "MLB": (0.0, 0.08), "ATP": (0.03, 0.03), "WTA": (0.03, 0.03)}
SOCCER_SLOPE = (0.0, 0.12)
MOVE_MIN = 0.015          # a move smaller than 1.5 points of chance is noise


def _slope(league, mk):
    s = SLOPE.get(league, SOCCER_SLOPE)
    return s[0] if mk == "spreads" else s[1] if mk == "totals" else 0.0


def _gdir(data_dir):
    d = os.path.join(data_dir, "track", "market", "g")
    os.makedirs(d, exist_ok=True)
    return d


def _tmin(iso):
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() // 60)
    except Exception:
        return None


def _entry(mk, m, home, away):
    """[point of side A, dec A, dec B (, dec draw)] for a book's market; side A = home (h2h, spreads) or Over (totals)"""
    o = {x["name"]: x for x in m.get("outcomes") or []}
    if mk == "totals":
        a, b = o.get("Over"), o.get("Under")
    else:
        a, b = o.get(home), o.get(away)
    if not a or not b or not a.get("price") or not b.get("price"):
        return None
    e = [a.get("point"), round(a["price"], 3), round(b["price"], 3)]
    if mk == "h2h" and o.get("Draw"):
        e.append(round(o["Draw"]["price"], 3))
    return e


def update(data_dir, now, log=print):
    """fold new raw line pulls (snapshots/<day>/*_lines.json.gz) into each game's book-by-book history"""
    st_fn = os.path.join(data_dir, "track", "market", "seen.json")
    try:
        seen = set(json.load(open(st_fn)))
    except Exception:
        seen = set()
    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in (1, 0)]
    files = []
    for d in days:
        files += sorted(glob.glob(os.path.join(data_dir, "snapshots", d, "*_lines.json.gz")))
    new = [f for f in files if os.path.relpath(f, data_dir) not in seen]
    if not new:
        return 0
    gd = _gdir(data_dir)
    docs = {}
    for fn in new:
        try:
            data = json.load(gzip.open(fn))
        except Exception:
            continue
        data = data if isinstance(data, list) else data.get("data") or []
        day = os.path.basename(os.path.dirname(fn))
        hhmmss = os.path.basename(fn)[:6]
        pull_t = _tmin(f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}Z")
        for g in data:
            gid = g.get("id")
            if not gid:
                continue
            doc = docs.get(gid)
            if doc is None:
                try:
                    doc = json.load(open(os.path.join(gd, f"{gid}.json")))
                except Exception:
                    doc = {"id": gid, "sport": g.get("sport_key"), "league": C.label(g.get("sport_key") or ""), "home": g.get("home_team"),
                           "away": g.get("away_team"), "commence": g.get("commence_time"), "b": {}}
                docs[gid] = doc
            doc["commence"] = g.get("commence_time") or doc["commence"]
            start = _tmin(doc["commence"])
            for bk in g.get("bookmakers") or []:
                if bk.get("key") not in KEEP:
                    continue
                for m in bk.get("markets") or []:
                    mk = m.get("key")
                    if mk not in MAIN:
                        continue
                    e = _entry(mk, m, doc["home"], doc["away"])
                    if not e:
                        continue
                    t = _tmin(m.get("last_update") or bk.get("last_update") or "") or pull_t
                    if t is None or (start and t >= start):        # pre-game only
                        continue
                    h = doc["b"].setdefault(bk["key"], {}).setdefault(mk, [])
                    if h and h[-1][1:] == e:
                        continue
                    if h and t < h[-1][0]:
                        continue
                    h.append([t] + e)
        seen.add(os.path.relpath(fn, data_dir))
    for gid, doc in docs.items():
        with open(os.path.join(gd, f"{gid}.json"), "w") as f:
            json.dump(doc, f, separators=(",", ":"))
    keep = {os.path.relpath(f, data_dir) for f in files}
    json.dump(sorted(seen & keep), open(st_fn, "w"), indent=0)
    log(f"market: {len(new)} pulls folded into {len(docs)} games")
    return len(new)


def _nv(e, mk):
    """no-vig chance of side A from a history entry [t, pt, decA, decB(, decD)]; None for a quote whose margin is
    implausible (placeholder or one-sided prices)"""
    inv = [1 / x for x in e[2:] if x and x > 1]
    if len(inv) != len(e) - 2 or not 0.995 <= sum(inv) <= 1.15:
        return None
    return (1 / e[2]) / sum(inv)


def _eq(e, mk, ref_pt, slope):
    """side A's chance at the reference number: the no-vig price plus the point difference on the slope"""
    p = _nv(e, mk)
    if p is None:
        return None
    pt = e[1]
    if mk == "spreads" and pt is not None and ref_pt is not None:
        p += slope * (ref_pt - pt)        # home -3 -> -4: home covers -3 more often
    elif mk == "totals" and pt is not None and ref_pt is not None:
        p += slope * (pt - ref_pt)
    return p


def _consensus(doc, mk, slope):
    """the sharp books' path for side A: [[t, point, chance at that point, chance at the opening number]]"""
    rows = []
    for bk in SHARP:
        for e in (doc["b"].get(bk) or {}).get(mk) or []:
            rows.append((e[0], bk, e))
    if not rows:
        return []
    rows.sort(key=lambda r: r[0])
    cur, path = {}, []
    ref = None
    need = 2 if len({r[1] for r in rows}) >= 2 else 1      # open once two sharp books are quoting (one if only one ever does)
    for t, bk, e in rows:
        if _nv(e, mk) is None:
            continue
        cur[bk] = e
        if len(cur) < need:
            continue
        w = {b: C.BOOKS.get(b, {}).get("weight", 1.0) or 1.0 for b in cur}
        pts = {}
        for b, x in cur.items():
            pts[x[1]] = pts.get(x[1], 0) + w[b]
        pt = max(pts, key=pts.get)
        at = [(w[b], _nv(x, mk)) for b, x in cur.items() if x[1] == pt and _nv(x, mk) is not None]
        if not at:
            continue
        p = sum(a * b for a, b in at) / sum(a for a, _ in at)
        if ref is None:
            ref = pt
        q = p + (slope * (ref - pt) if mk == "spreads" else slope * (pt - ref) if mk == "totals" else 0) if pt is not None and ref is not None else p
        if path and path[-1][1] == pt and abs(path[-1][2] - p) < 0.002:
            continue
        path.append([t, pt, round(p, 4), round(q, 4)])
    return path


def summarize(doc, now=None):
    """movement per main market: open, now, the move toward side A on one chance scale, who moved first, who hasn't"""
    out = {}
    for mk in MAIN:
        slope = _slope(doc.get("league"), mk)
        if mk == "spreads" and not slope:       # puck lines, run lines, handicaps: fixed +-1.5 that flips sides; h2h tells the move
            continue
        path = _consensus(doc, mk, slope)
        if len(path) < 1:
            continue
        o, n = path[0], path[-1]
        mv = round(n[3] - o[3], 4)
        r = {"open": {"t": o[0], "pt": o[1], "p": o[2]}, "now": {"t": n[0], "pt": n[1], "p": n[2]}, "mv": mv,
             "path": [[x[0], x[1], x[2]] for x in path[-40:]]}
        if abs(mv) >= MOVE_MIN:
            s = 1 if mv > 0 else -1
            ref = o[1]
            first = []
            lag = []
            for bk, ms in doc["b"].items():
                h = ms.get(mk) or []
                if not h:
                    continue
                e0 = _eq(h[0], mk, ref, slope)
                if e0 is None:
                    continue
                hit = next((x[0] for x in h if (q := _eq(x, mk, ref, slope)) is not None and (q - e0) * s >= abs(mv) / 2), None)
                if hit is not None:
                    first.append((hit, bk))
                elif bk in VENUES and h[-1][0] >= o[0]:
                    lag.append(bk)
            first.sort()
            r["first"] = [[t, b] for t, b in first[:4]]
            r["sharpLed"] = bool(first) and first[0][1] in SHARP
            soft = [t for t, b in first if b in SOFT]
            if first and soft:
                r["softLagMin"] = soft[0] - first[0][0]
            r["lagging"] = sorted(lag)
        out[mk] = r
    return out
