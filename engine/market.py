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


def _graded(data_dir):
    try:
        return set(json.load(open(os.path.join(data_dir, "track", "market", "graded.json"))))
    except Exception:
        return set()


def update(data_dir, now, log=print, days=None):
    """fold new raw line pulls (snapshots/<day>/*_lines.json.gz) into each game's book-by-book history (games already
    graded are skipped: their files are pruned once graded)"""
    st_fn = os.path.join(data_dir, "track", "market", "seen.json")
    try:
        seen = set(json.load(open(st_fn)))
    except Exception:
        seen = set()
    graded = _graded(data_dir)
    days = days or [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in (1, 0)]
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
            if not gid or gid in graded:
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
    recent = {os.path.relpath(f, data_dir) for d in [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in (1, 0)]
              for f in glob.glob(os.path.join(data_dir, "snapshots", d, "*_lines.json.gz"))}
    json.dump(sorted(seen & recent), open(st_fn, "w"), indent=0)
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


# ---------------------------------------------------------------- DraftKings betting splits
SPLITS_EVERY_MIN = 20


def _pct(s):
    m = re.search(r"(\d{1,3})\s*%", s or "")
    return int(m.group(1)) if m else None


ALIAS = {"utd": "united", "man": "manchester", "nycfc": "york", "spurs": "tottenham", "wolves": "wolverhampton", "st": "state"}


def _words(name):
    return {ALIAS.get(w, w) for w in re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split() if len(w) > 1 and w not in ("fc", "cf", "the", "of")}


def match_game(away, home, docs):
    """our game for DraftKings' 'AWAY @ HOME' names: the doc whose team words overlap most (nicknames match even when
    DraftKings abbreviates the city)"""
    best, bs = None, 0
    wa, wh = _words(away), _words(home)
    for d in docs:
        s = len(wa & _words(d["away"])) + len(wh & _words(d["home"]))
        if s > bs and len(wa & _words(d["away"])) and len(wh & _words(d["home"])):
            best, bs = d, s
    return best


def _txt(h):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h)).strip()


def parse_dk(html):
    """DraftKings splits page -> [{away, home, when, rows: [{mk, label, odds, handle, bets}]}]"""
    out = []
    for blk in re.split(r'<div class="tb-se[ "]', html)[1:]:
        tm = re.search(r'<h5[^>]*>(.*?)</h5>', blk, re.S)
        if not tm:
            continue
        title = _txt(tm.group(1))
        if " @ " in title:
            away, home = [x.strip() for x in title.split(" @ ", 1)]
        elif " vs " in title:                       # soccer: home first
            home, away = [x.strip() for x in title.split(" vs ", 1)]
        else:
            continue
        wm = re.search(r'</h5>\s*<span[^>]*>([^<]+)</span>', blk)
        g = {"away": away, "home": home, "when": _txt(wm.group(1)) if wm else None, "rows": []}
        for sec in re.split(r'text-brand-orange">', blk)[1:]:
            name = _txt(sec[:sec.find("</div>")])
            mk = {"moneyline": "h2h", "spread": "spreads", "puck line": "spreads", "run line": "spreads", "total": "totals"}.get(name.lower())
            if not mk:
                continue
            for row in re.split(r'<div class="tb-sodd', sec)[1:]:
                lab = re.search(r'tb-slipline[^>]*>(.*?)</div>', row, re.S)
                odd = re.search(r'tb-odd-s[^>]*>(.*?)</a>', row, re.S)
                pcts = re.findall(r'<div>\s*(\d{1,3})%', row)
                if not lab or len(pcts) < 2:
                    continue
                o = _txt(odd.group(1)).replace("\u2212", "-") if odd else ""
                g["rows"].append({"mk": mk, "label": _txt(lab.group(1)), "odds": int(o) if re.fullmatch(r"[+-]?\d+", o) else None,
                                  "handle": int(pcts[0]), "bets": int(pcts[1])})
        if g["rows"]:
            out.append(g)
    return out


def _side_of(row, doc):
    """which side of our game a DraftKings row is: 'A' (home / Over) or 'B' (away / Under), plus its number"""
    lab = row["label"]
    m = re.search(r"([+-]?\d+(?:\.\d)?)$", lab)
    pt = float(m.group(1)) if m and row["mk"] != "h2h" else None
    if row["mk"] == "totals":
        return ("A" if lab.lower().startswith("over") else "B" if lab.lower().startswith("under") else None), pt
    name = lab[: m.start()].strip() if m and row["mk"] != "h2h" else lab
    wa, wh, wn = _words(doc["away"]), _words(doc["home"]), _words(name)
    if wn & wh and not wn & wa:
        return "A", pt
    if wn & wa and not wn & wh:
        return "B", pt
    return None, pt


def fetch_splits(data_dir, docs, now, log=print, force=False):
    """DraftKings splits for every league on the board with games in the next 36 h; matched to our games and kept with
    their history (track/market/s/<gameId>.json: [[t, mk, side, handle, bets, point]]) -> {gameId: {mk: {A|B: {...}}}}"""
    st_fn = os.path.join(data_dir, "track", "market", "splits_at.json")
    try:
        last = json.load(open(st_fn)).get("t", 0)
    except Exception:
        last = 0
    tnow = int(now.timestamp() // 60)
    fn = os.path.join(data_dir, "latest", "splits.json")
    if not force and tnow - last < SPLITS_EVERY_MIN:
        try:
            return json.load(open(fn))
        except Exception:
            return {}
    by_lg = {}
    for d in docs:
        st = _tmin(d.get("commence") or "")
        if st and 0 < st - tnow < 36 * 60:
            by_lg.setdefault(d["league"], []).append(d)
    out, n_pages, n_games = {}, 0, 0
    sdir = os.path.join(data_dir, "track", "market", "s")
    os.makedirs(sdir, exist_ok=True)
    for lg, ds in by_lg.items():
        eg = DK_GROUPS.get(lg)
        if not eg:
            continue
        seen_first = None
        for page in range(1, 8):
            try:
                gs = parse_dk(_fetch(DK_SPLITS.format(eg=eg, page=page)))
            except Exception as e:
                log(f"splits {lg} p{page}: {type(e).__name__}")
                break
            n_pages += 1
            if not gs or (seen_first and gs[0]["away"] + gs[0]["home"] == seen_first):
                break
            seen_first = seen_first or gs[0]["away"] + gs[0]["home"]
            for g in gs:
                doc = match_game(g["away"], g["home"], ds)
                if not doc:
                    continue
                rec = out.setdefault(doc["id"], {"at": tnow})
                for r in g["rows"]:
                    side, pt = _side_of(r, doc)
                    if not side:
                        continue
                    rec.setdefault(r["mk"], {})[side] = {"h": r["handle"], "b": r["bets"], "pt": pt, "odds": r["odds"]}
                n_games += 1
            if len(gs) < 10:
                break
    for gid, rec in out.items():           # history: one row per change
        hf = os.path.join(sdir, f"{gid}.json")
        try:
            h = json.load(open(hf))
        except Exception:
            h = []
        for mk in MAIN:
            for side, v in (rec.get(mk) or {}).items():
                prev = next((x for x in reversed(h) if x[1] == mk and x[2] == side), None)
                if not prev or prev[3:6] != [v["h"], v["b"], v["pt"]]:
                    h.append([tnow, mk, side, v["h"], v["b"], v["pt"]])
        json.dump(h, open(hf, "w"), separators=(",", ":"))
    json.dump(out, open(fn, "w"), separators=(",", ":"))
    json.dump({"t": tnow}, open(st_fn, "w"))
    log(f"splits: {n_pages} DraftKings pages, {len(out)} of our games matched")
    return out


# ---------------------------------------------------------------- signals per game side
RLM_TICKETS = 40          # reverse line movement: the line moved toward a side holding no more than this share of bets
DIV_SHARP = 10            # handle % this far above bets %: the bigger bettors are on that side
PUBLIC = 70               # bets % at or above: a public side
CLOSE_OK_MIN = 30         # a graded side counts only when the last sharp quote came within this many minutes of the start


def side_signals(summ, spl, mk, side):
    """signals for one side ('A' home / Over, 'B' away / Under) of a main market"""
    r = (summ or {}).get(mk)
    sgn = 1 if side == "A" else -1
    out = {}
    if r:
        mv = round(r["mv"] * sgn, 4)
        out["mv"] = mv
        out["open"] = r["open"]["pt"]
        out["now"] = r["now"]["pt"]
        if abs(r["mv"]) >= MOVE_MIN:
            toward = mv > 0
            out["sl"] = bool(r.get("sharpLed")) and toward          # a sharp book moved first, toward this side
            if toward and r.get("lagging"):
                out["lag"] = r["lagging"]
    s = ((spl or {}).get(mk) or {}).get(side)
    # 100 / 0 splits come from a handful of early bets: shown, but no signal
    if s and not ({s["h"], s["b"]} & {0, 100}):
        out["h"], out["b"] = s["h"], s["b"]
        out["div"] = s["h"] - s["b"]
        if s["b"] >= PUBLIC:
            out["pub"] = True
        if "mv" in out and s["b"] <= RLM_TICKETS and out["mv"] >= MOVE_MIN:
            out["rlm"] = True
    return out


def tags(sig):
    """plain words for the chips"""
    t = []
    mv = sig.get("mv")
    if mv is not None and abs(mv) >= MOVE_MIN:
        t.append(f"moved {'toward' if mv > 0 else 'away'} {abs(mv) * 100:.1f} pts")
    if sig.get("sl"):
        t.append("sharp books moved first")
    if sig.get("rlm"):
        t.append(f"reverse line move ({sig['b']}% of bets)")
    if sig.get("div") is not None and sig["div"] >= DIV_SHARP:
        t.append(f"money {sig['h']}% vs bets {sig['b']}%")
    if sig.get("pub"):
        t.append(f"public side ({sig['b']}% of bets)")
    return t


def _edge_side(e, home, away):
    mk = re.sub(r"^alternate_", "", e.get("market") or "")
    mk = {"alternate_spreads": "spreads", "alternate_totals": "totals"}.get(e.get("market"), mk)
    if mk not in MAIN or e.get("player"):
        return None, None
    sd = e.get("side")
    if mk == "totals":
        return mk, "A" if sd == "Over" else "B" if sd == "Under" else None
    return mk, "A" if sd == home else "B" if sd == away else None


def run(data_dir, game_docs, edges, now, log=print):
    """each pre-game run: fold new pulls, fetch splits, summarize every board game's movement, stamp signals on its game
    docs (doc['mkt']) and edges (e['mkt'], e['mktTags']), and write latest/market.json for the pick runs"""
    try:
        update(data_dir, now, log)
    except Exception as e:
        log(f"market history failed: {type(e).__name__}: {str(e)[:80]}")
    gd = _gdir(data_dir)
    hist = {}
    for d in game_docs:
        try:
            hist[d["id"]] = json.load(open(os.path.join(gd, f"{d['id']}.json")))
        except Exception:
            pass
    try:
        spl = fetch_splits(data_dir, [h for h in hist.values()], now, log)
    except Exception as e:
        log(f"splits failed: {type(e).__name__}: {str(e)[:80]}")
        spl = {}
    board = {}
    for d in game_docs:
        h = hist.get(d["id"])
        summ = summarize(h) if h else {}
        sp = spl.get(d["id"]) or {}
        sides = {}
        for mk in MAIN:
            for side in ("A", "B"):
                sg = side_signals(summ, sp, mk, side)
                if sg:
                    sides[f"{mk}|{side}"] = sg
        if not summ and not sp:
            continue
        rec = {"mv": {mk: {k: v for k, v in r.items() if k != "path"} for mk, r in summ.items()},
               "path": {mk: r["path"] for mk, r in summ.items()}, "splits": sp, "sides": sides}
        d["mkt"] = rec
        board[d["id"]] = {"league": d.get("league"), "home": d["home"], "away": d["away"], "commence": d.get("commence"),
                          "mv": rec["mv"], "splits": sp, "sides": {k: {**v, "tags": tags(v)} for k, v in sides.items()}}
    n = 0
    byid = {d["id"]: d for d in game_docs}
    nw = {}
    for e in edges:
        d = byid.get(e.get("gameId"))
        try:
            w = explain(e, d, hist.get(e.get("gameId")), now)
        except Exception:
            w = None
        if w:
            e["why"] = w
            nw[w["k"]] = nw.get(w["k"], 0) + 1
        if not d or not d.get("mkt"):
            continue
        mk, side = _edge_side(e, d["home"], d["away"])
        if not side:
            continue
        sg = d["mkt"]["sides"].get(f"{mk}|{side}")
        if sg:
            e["mkt"] = {k: sg[k] for k in ("mv", "sl", "rlm", "div", "h", "b", "pub") if k in sg}
            t = tags(sg)
            if t:
                e["mktTags"] = t
                n += 1
    json.dump({"at": now.isoformat(), "games": board}, open(os.path.join(data_dir, "latest", "market.json"), "w"), separators=(",", ":"))
    log(f"market: {len(board)} games with movement or splits, {n} edges carry market signals; why: {nw}")
    return board


# ---------------------------------------------------------------- why each edge is on the board
WHY = {"lag": "Sharp move, venue lagging", "move": "Line moved toward it", "static": "Static price gap", "against": "Line moved away"}


def _pt_side(mk, side, ptA):
    if ptA is None or mk == "h2h":
        return None
    return ptA if (side == "A" or mk == "totals") else -ptA


def _num(mk, pt, p):
    """how a number reads: a point for spreads/totals, the chance for moneylines"""
    if mk == "h2h" or pt is None:
        return f"{p * 100:.0f}%"
    return f"{pt:+g}" if mk == "spreads" else f"{pt:g}"


def _hhmm(tmin_):
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(tmin_ * 60, timezone.utc).astimezone(ZoneInfo("America/Los_Angeles")).strftime("%-I:%M %p")
    except Exception:
        return ""


def explain(e, d, hist, now):
    """the market reason an edge is on the board, in plain words: {k: lag | move | static | against, t: text, s: strength,
    f: share of the sharp move this venue has followed}. lag = the sharp books moved toward this side and this venue
    hasn't followed (the tradeable kind); move = the line moved toward it; static = a gap with no move behind it;
    against = the market moved away from this side"""
    venue = (C.BOOKS.get(e.get("book")) or {}).get("name") or e.get("bookName") or e.get("book")
    if e.get("player") or not d:
        # props: the sharp fair at this number since we first saw it (engine opens), the venue's price still beats it
        if e.get("open") is None or e.get("fair") is None or (e.get("openPt") is not None and e.get("point") is not None and abs(e["openPt"] - e["point"]) > 1e-6):
            return {"k": "static", "s": 0, "t": f"Price gap with no tracked move behind it: {venue} is off the sharp number."}
        mv = e["fair"] - e["open"]
        if mv >= MOVE_MIN:
            return {"k": "move", "s": 1, "t": f"The sharp price on this moved {mv * 100:.1f} pts toward it since open ({e['open'] * 100:.0f}% to {e['fair'] * 100:.0f}%); {venue} hasn't caught up."}
        if mv <= -MOVE_MIN:
            return {"k": "against", "s": -1, "t": f"Careful: the sharp price moved {abs(mv) * 100:.1f} pts away from this since open ({e['open'] * 100:.0f}% to {e['fair'] * 100:.0f}%). The gap is real at today's price, but it's the side the market is leaving."}
        return {"k": "static", "s": 0, "t": f"Price gap with no market move behind it: {venue} is just off the sharp number."}
    mk, side = _edge_side(e, d["home"], d["away"])
    summ = (d.get("mkt") or {}).get("mv") or {}
    r = summ.get(mk) if mk else None
    if not r:
        return {"k": "static", "s": 0, "t": f"Price gap with no tracked move behind it: {venue} is off the sharp number."}
    sgn = 1 if side == "A" else -1
    mv = r["mv"] * sgn
    name = (d["home"] if side == "A" else d["away"]) if mk != "totals" else ("Over" if side == "A" else "Under")
    o_pt, n_pt = _pt_side(mk, side, r["open"]["pt"]), _pt_side(mk, side, r["now"]["pt"])
    o_p = r["open"]["p"] if side == "A" else 1 - r["open"]["p"]
    n_p = r["now"]["p"] if side == "A" else 1 - r["now"]["p"]
    path = f"{name} {_num(mk, o_pt, o_p)} to {_num(mk, n_pt, n_p)}" if mk != "h2h" else f"{name} from {o_p * 100:.0f}% to {n_p * 100:.0f}%"
    first = r.get("first") or []
    lead = f" ({(C.BOOKS.get(first[0][1]) or {}).get('name', first[0][1])} first, {_hhmm(first[0][0])})" if first else ""
    if abs(mv) < MOVE_MIN:
        return {"k": "static", "s": 0, "t": f"Price gap with no market move behind it: the sharp line hasn't moved (under 1.5 pts) and {venue} is just off it."}
    if mv < 0:
        return {"k": "against", "s": -1, "t": f"Careful: the sharp line moved {abs(mv) * 100:.1f} pts away from this side ({path}). The gap is real at today's price, but it's the side the market is leaving."}
    # how much of the move this venue has followed, from its own price history (main numbers only)
    f = None
    vh = ((hist or {}).get("b", {}).get(e.get("book")) or {}).get(mk) or []
    if vh and e.get("main", True):
        slope = _slope(d.get("league"), mk)
        ref = r["open"]["pt"]
        t0 = r["open"]["t"]
        before = [x for x in vh if x[0] <= t0] or vh[:1]
        q0, q1 = _eq(before[-1], mk, ref, slope), _eq(vh[-1], mk, ref, slope)
        if q0 is not None and q1 is not None:
            f = round(max(0.0, min(1.5, (q1 - q0) * sgn / abs(r["mv"]))), 2)
            vpt = _pt_side(mk, side, vh[-1][1])
    sharp = bool(r.get("sharpLed"))
    who = "Sharp books" if sharp else "The market"
    if f is not None and f < 0.5:
        still = f"is still at {_num(mk, vpt, o_p)}" if mk != "h2h" and vpt is not None else ("hasn't followed" if f < 0.15 else f"has followed only {f * 100:.0f}% of it")
        return {"k": "lag", "s": 3 if sharp else 2, "f": f,
                "t": f"{who} moved {path}{lead}, {mv * 100:.1f} pts toward this side; {venue} {still}."}
    return {"k": "move", "s": 1, "f": f, "t": f"{who} moved {path}{lead}, {mv * 100:.1f} pts toward this side; {venue}'s price still beats where it settled."}


# ---------------------------------------------------------------- grading the signals
def _result(mk, side, pt, f):
    hs, as_ = f["home"].get("score"), f["away"].get("score")
    if f.get("league") in ("ATP", "WTA") and mk != "h2h":       # games, not sets
        hs, as_ = sum(f["home"].get("line") or []), sum(f["away"].get("line") or [])
    if hs is None or as_ is None:
        return None
    if mk == "h2h":
        y = 1 if hs > as_ else 0 if hs < as_ else None
        return y if side == "A" else (None if y is None else 1 - y)
    if pt is None:
        return None
    if mk == "spreads":
        v = hs + pt - as_
    else:
        v = hs + as_ - pt
    y = 1 if v > 0 else 0 if v < 0 else None
    return y if side == "A" else (None if y is None else 1 - y)


def grade(data_dir, fin_by_gid, log=print):
    """every finished game with a movement history: one row per main-market side with its closing chance (the sharp
    consensus at the last pre-game quote), the result, and the signals at the close -> track/market/graded/<league>.jsonl"""
    gd = _gdir(data_dir)
    done_fn = os.path.join(data_dir, "track", "market", "graded.json")
    try:
        done = set(json.load(open(done_fn)))
    except Exception:
        done = set()
    odir = os.path.join(data_dir, "track", "market", "graded")
    os.makedirs(odir, exist_ok=True)
    n = 0
    for gid, f in fin_by_gid.items():
        if gid in done:
            continue
        try:
            doc = json.load(open(os.path.join(gd, f"{gid}.json")))
        except Exception:
            continue
        summ = summarize(doc)
        spl = {}
        try:
            for t, mk, side, hh, bb, pt in json.load(open(os.path.join(data_dir, "track", "market", "s", f"{gid}.json"))):
                spl.setdefault(mk, {})[side] = {"h": hh, "b": bb, "pt": pt}
        except Exception:
            pass
        rows = []
        for mk, r in summ.items():
            pA = r["now"]["p"]
            for side in ("A", "B"):
                p = pA if side == "A" else 1 - pA
                pt = r["now"]["pt"]
                spt = None if pt is None else (pt if side == "A" or mk == "totals" else -pt)
                y = _result(mk, side, pt, f)
                if y is None or not 0.02 < p < 0.98 or (mk == "h2h" and len(((doc["b"].get("pinnacle") or {}).get("h2h") or [[0, 0, 0, 0]])[-1]) > 4):
                    continue        # pushes, near-certain sides, 3-way moneylines
                sg = side_signals(summ, spl, mk, side)
                start = _tmin(doc.get("commence") or "")
                rows.append({"d": (f.get("date") or f.get("commence", "")[:10]), "gid": gid, "ca": (start - r["now"]["t"]) if start else None, "sport": doc.get("sport"), "league": doc.get("league"),
                             "mk": mk, "side": side, "pt": spt, "close": round(p, 4), "y": y, "pnl": round(y / p - 1, 4),
                             **{k: sg[k] for k in ("mv", "sl", "rlm", "div", "h", "b", "pub") if k in sg}})
        for fn in (os.path.join(gd, f"{gid}.json"), os.path.join(data_dir, "track", "market", "s", f"{gid}.json")):
            try:
                os.remove(fn)       # graded: the rows keep what matters; update() skips graded games
            except Exception:
                pass
        if rows:
            with open(os.path.join(odir, f"{(doc.get('league') or 'other').replace(' ', '_')}.jsonl"), "a") as fh:
                for x in rows:
                    fh.write(json.dumps(x, separators=(",", ":")) + "\n")
            n += len(rows)
        done.add(gid)
    json.dump(sorted(done), open(done_fn, "w"), indent=0)
    if n:
        log(f"market: graded {n} game sides")
    return n


BUCKETS = {
    "Line move toward the side": lambda r: None if r.get("mv") is None else "3+ pts toward" if r["mv"] >= 0.03 else "1.5-3 toward" if r["mv"] >= 0.015
                                  else "3+ pts away" if r["mv"] <= -0.03 else "1.5-3 away" if r["mv"] <= -0.015 else "flat",
    "Sharp books moved first": lambda r: "sharp-led, toward" if r.get("sl") else ("other move toward" if (r.get("mv") or 0) >= 0.015 else None),
    "Reverse line movement": lambda r: None if r.get("b") is None else "RLM side" if r.get("rlm") else "no RLM",
    "Money vs bets": lambda r: None if r.get("div") is None else "money 20+ over bets" if r["div"] >= 20 else "money 10-20 over" if r["div"] >= 10
                     else "bets 10+ over money" if r["div"] <= -10 else "even",
    "Public side (70%+ of bets)": lambda r: None if r.get("b") is None else "public 70%+" if r["b"] >= 70 else "contrarian 30%-" if r["b"] <= 30 else "middle",
}


def card(data_dir, log=print):
    """the signals' report card: for each bucket, how the sides did against their closing chance (gap = won - expected)
    and the flat return at the no-vig close, clustered by game -> stats/marketcard.json"""
    import edgecard as EC
    rows = []
    for fn in glob.glob(os.path.join(data_dir, "track", "market", "graded", "*.jsonl")):
        for line in open(fn):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    # fresh closes only (the last sharp quote within 30 min of the start): with an older close, a line that kept moving
    # after our last pull would look like "moves keep going"
    stale = sum(1 for r in rows if (r.get("ca") or 0) > CLOSE_OK_MIN)
    rows = [r for r in rows if not (r.get("ca") or 0) > CLOSE_OK_MIN]
    out = {"n": len(rows), "games": len({r["gid"] for r in rows}), "staleClose": stale, "dims": {}}
    for name, fb in BUCKETS.items():
        cells = {}
        for r in rows:
            k = fb(r)
            if k is not None:
                cells.setdefault(k, []).append(r)
        res = []
        for k, g in cells.items():
            c = EC.clus(g, base="close")
            res.append({"k": k, "n": len(g), "games": c.get("games"), "win": round(sum(r["y"] for r in g) / len(g), 4),
                        "exp": round(sum(r["close"] for r in g) / len(g), 4), "gap": round(sum(r["y"] - r["close"] for r in g) / len(g), 4),
                        "zG": c.get("zG"), "roi": round(sum(r["pnl"] for r in g) / len(g), 4), "roiT": c.get("roiT")})
        out["dims"][name] = sorted(res, key=lambda x: -x["n"])
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "stats", "marketcard.json"), "w"), indent=1)
    return out


def backfill(data_dir, now, log=print):
    """once: rebuild every game's movement from all saved pulls and grade every finished game (mode marketbackfill)"""
    days = sorted(os.path.basename(d) for d in glob.glob(os.path.join(data_dir, "snapshots", "*")))
    fin = {}
    for fn in glob.glob(os.path.join(data_dir, "finals", "*.json")):
        try:
            x = json.load(open(fn))
        except Exception:
            continue
        for d in (x if isinstance(x, list) else x.values()):
            if isinstance(d, dict) and d.get("gameId"):
                fin[d["gameId"]] = d
    for i in range(0, len(days), 3):        # a few days at a time: grade (and prune) as we go to keep memory flat
        update(data_dir, now, log, days=days[max(0, i - 1): i + 3])
        grade(data_dir, fin, log)
    c = card(data_dir, log)
    log(f"market backfill: {len(days)} days, {c['n']} graded sides over {c['games']} games")
    return c
