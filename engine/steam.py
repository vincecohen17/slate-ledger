"""Steam scanner: catch a sharp move while Vince's venues are still at the old number.

The watcher calls scan() every STEAM_EVERY_MIN minutes for sports with a game starting within STEAM_AHEAD_H hours: one
odds call per sport for the main lines (h2h / spreads / totals) from the sharp books and the venues only (13 books = 2
credit units, so 6 credits a sport). It keeps the sharp consensus path per game and market for STEAM_WINDOW_MIN minutes;
when the consensus has moved STEAM_MOVE+ (chance of the side, points on one scale via market.SLOPE) since an earlier scan
in that window, every venue quote on the side it moved toward that still beats the NEW fair by STEAM_EDGE+ (all-in, fees
included) and hasn't updated since the move is a steam item. Items go to the Realtime Database live/steam (the site's
"Just moved" list) and to phone alerts; each item's window (first seen -> venue adjusted or edge gone) is logged to
track/steam/log.jsonl, so we learn how long each venue takes to follow. Spending is capped per day (STEAM_DAY_MAX)."""
import json
import os
from datetime import datetime, timedelta, timezone

import config as C
import oddsmath as M
import market as MK

SHARP = [k for k, v in C.BOOKS.items() if v.get("kind") == "sharp"]
VEN = [k for k, v in C.BOOKS.items() if v.get("venue") and v.get("kind") in ("exchange", "book")]
BOOKS = SHARP + VEN


def _cfg(name, default):
    return getattr(C, name, default)


EVERY = _cfg("STEAM_EVERY_MIN", 2)
AHEAD_H = _cfg("STEAM_AHEAD_H", 6)
WINDOW = _cfg("STEAM_WINDOW_MIN", 20)
MOVE = _cfg("STEAM_MOVE", 0.02)
EDGE = _cfg("STEAM_EDGE", 0.015)
DAY_MAX = _cfg("STEAM_DAY_MAX", 12000)
FRESH_MOVE_MIN = 12            # the sharp book's own update behind the move must be this recent


def _t(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _mins(a, b):
    return (a - b).total_seconds() / 60


def _state_fn(data_dir):
    return os.path.join(data_dir, "latest", "steam_state.json")


def load(data_dir):
    try:
        return json.load(open(_state_fn(data_dir)))
    except Exception:
        return {}


def save(data_dir, st):
    os.makedirs(os.path.join(data_dir, "latest"), exist_ok=True)
    json.dump(st, open(_state_fn(data_dir), "w"), separators=(",", ":"))


def _quotes(ev, mk):
    """book -> (last_update, {outcome name: (price, point)})"""
    out = {}
    for b in ev.get("bookmakers") or []:
        for m in b.get("markets") or []:
            if m.get("key") != mk:
                continue
            out[b["key"]] = (m.get("last_update") or b.get("last_update"),
                             {o["name"]: (o.get("price"), o.get("point")) for o in m.get("outcomes") or []})
    return out


def _side_a(ev, mk):
    return ev["home_team"] if mk in ("h2h", "spreads") else "Over"


def _side_b(ev, mk, names):
    if mk in ("h2h", "spreads"):
        return ev["away_team"]
    return "Under"


def _nv_a(prices, a, b, draw=None):
    try:
        inv = [1 / prices[a][0], 1 / prices[b][0]] + ([1 / prices[draw][0]] if draw else [])
    except Exception:
        return None
    s = sum(inv)
    if not 0.995 <= s <= 1.15:
        return None
    return inv[0] / s


def consensus(ev, mk, league):
    """sharp chance of side A at a reference number (Pinnacle's, else the most common), and the newest sharp update"""
    slope = MK._slope(league, mk)
    q = _quotes(ev, mk)
    a = _side_a(ev, mk)
    b = _side_b(ev, mk, None)
    draw = "Draw" if mk == "h2h" and any("Draw" in p for _, p in q.values()) else None
    sharp = {k: v for k, v in q.items() if k in SHARP and a in v[1] and b in v[1]}
    if not sharp:
        return None
    if mk != "h2h" and slope == 0 and mk == "spreads":
        return None            # fixed puck / run lines: a point move there is a different bet, not a move
    pts = {}
    for k, (_, p) in sharp.items():
        pts[p[a][1]] = pts.get(p[a][1], 0) + (C.BOOKS[k].get("weight") or 1)
    ref = sharp["pinnacle"][1][a][1] if "pinnacle" in sharp else max(pts, key=pts.get)
    acc, w, newest, lead = 0.0, 0.0, None, None
    for k, (upd, p) in sharp.items():
        pa = _nv_a(p, a, b, draw)
        if pa is None:
            continue
        pt = p[a][1]
        if mk == "spreads" and pt is not None and ref is not None:
            pa += slope * (ref - pt)
        elif mk == "totals" and pt is not None and ref is not None:
            pa += slope * (pt - ref)
        wt = C.BOOKS[k].get("weight") or 1
        acc += wt * pa
        w += wt
        if upd and (newest is None or upd > newest):
            newest, lead = upd, k
    if not w:
        return None
    return {"ref": ref, "p": acc / w, "upd": newest, "lead": lead, "slope": slope, "a": a, "b": b, "draw": draw}


def _fair_at(cons, mk, side, pt):
    """the side's chance at its own number, from the consensus chance of side A at the reference number"""
    pa, ref, k = cons["p"], cons["ref"], cons["slope"]
    if mk == "h2h":
        return pa if side == cons["a"] else 1 - pa
    if pt is None or ref is None:
        return None
    if mk == "spreads":         # side A = home; each side's own number
        return pa + k * (pt - ref) if side == cons["a"] else (1 - pa) + k * (pt + ref)
    return pa + k * (ref - pt) if side == "Over" else (1 - pa) + k * (pt - ref)


def _rebase(mk, p0, ref0, ref, k):
    """side A's chance at an earlier reference number, moved onto today's"""
    if ref0 is None or ref is None or ref0 == ref or not k or mk == "h2h":
        return p0
    return p0 + k * (ref - ref0) if mk == "spreads" else p0 + k * (ref0 - ref)


def _own(mk, side, cons, ref):
    if mk == "h2h" or ref is None:
        return None
    return ref if (side == cons["a"] or mk == "totals") else -ref


def _txt_pt(mk, side, pt):
    if mk == "h2h" or pt is None:
        return "ML"
    if mk == "totals":
        return f"{side} {pt:g}"
    return f"{side} {pt:+g}"


def _vq(ev, mk):
    """each venue's quotes for this market: {venue: {side: [price, point]}}"""
    return {v: {n: [x[0], x[1]] for n, x in p.items()} for v, (_, p) in _quotes(ev, mk).items() if v in VEN}


def scan(api, data_dir, now, sports, log=print):
    """one pass: returns (items, credits spent)"""
    st = load(data_dir)
    day = now.astimezone(timezone(timedelta(hours=-8))).strftime("%Y-%m-%d")
    if st.get("day") != day:
        st["day"], st["used"] = day, 0
    if st.get("used", 0) >= DAY_MAX:
        return st.get("items") or [], 0
    hist = st.get("hist") or {}
    open_items = {i["id"]: i for i in st.get("items") or []}
    items, spent = [], 0
    for s in sports:
        league = C.label(s)
        try:
            before = api.used_run
            data = api.odds(s, list(MK.MAIN), None, bookmakers=BOOKS,
                            commenceTimeTo=(now + timedelta(hours=AHEAD_H)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            spent += api.used_run - before
        except Exception as e:
            log(f"steam: {s} failed ({type(e).__name__})")
            continue
        for ev in data or []:
            if _t(ev["commence_time"]) <= now:
                continue
            for mk in MK.MAIN:
                cons = consensus(ev, mk, league)
                if not cons or cons["draw"]:
                    continue            # three-way soccer moneylines: left to the full runs
                key = f"{ev['id']}|{mk}"
                vq = _vq(ev, mk)
                h = [x for x in hist.get(key, []) if _mins(now, _t(x[0])) <= WINDOW]
                base = next((x for x in h if _mins(now, _t(x[0])) >= EVERY - 0.5), None)   # oldest scan in the window
                h.append([now.isoformat(), cons["ref"], round(cons["p"], 4), vq])
                hist[key] = h[-12:]
                if not base:
                    continue
                p0 = _rebase(mk, base[2], base[1], cons["ref"], cons["slope"])
                mv = cons["p"] - p0
                if abs(mv) < MOVE or not cons["upd"] or _mins(now, _t(cons["upd"])) > FRESH_MOVE_MIN:
                    continue
                side = cons["a"] if mv > 0 else cons["b"]
                for v, sides in vq.items():
                    if side not in sides:
                        continue
                    price, pt = sides[side]
                    old = (base[3].get(v) or {}).get(side)
                    # still at the old number: the same price on the same line as before the sharp move
                    if not price or price <= 1.01 or not old or old[1] != pt or abs(old[0] - price) > 0.01 * price:
                        continue
                    fair = _fair_at(cons, mk, side, pt)
                    if fair is None or not 0.03 < fair < 0.97:
                        continue
                    cost = M.all_in_prob(1 / price, C.BOOKS[v].get("fee"))
                    edge = fair - cost
                    if edge < EDGE or edge > 0.15:
                        continue
                    fb = fair - (mv if side == cons["a"] else -mv)
                    iid = f"{ev['id']}|{mk}|{side}|{pt}|{v}"
                    first = open_items.get(iid, {}).get("first") or now.isoformat()
                    lead = C.BOOKS.get(cons["lead"], {}).get("name", "Sharp books")
                    if mk != "h2h" and base[1] != cons["ref"]:
                        moved = f"{lead} moved {_txt_pt(mk, side, _own(mk, side, cons, base[1]))} to {_txt_pt(mk, side, _own(mk, side, cons, cons['ref']))}"
                    else:
                        moved = f"{lead} moved {side} {'ML' if mk == 'h2h' else _txt_pt(mk, side, pt)} from {100 * fb:.0f}% to {100 * fair:.0f}%"
                    upd = (ev.get("bookmakers") and next((m.get("last_update") for b in ev["bookmakers"] if b["key"] == v
                                                          for m in b.get("markets") or [] if m.get("key") == mk), None))
                    items.append({
                        "id": iid, "gameId": ev["id"], "league": league, "sport": s,
                        "matchup": f"{ev['away_team']} @ {ev['home_team']}", "commence": ev["commence_time"],
                        "market": mk, "side": side, "point": pt, "team": None,
                        "venue": v, "venueName": C.BOOKS[v]["name"], "dec": price, "am": M.american_from_decimal(price),
                        "cost": round(cost, 4), "fair": round(fair, 4), "fairBefore": round(fb, 4), "edge": round(edge, 4),
                        "move": round(abs(mv), 4), "lead": cons["lead"], "sharpAt": cons["upd"], "venueAt": upd, "since": base[0],
                        "venueAge": round(_mins(now, _t(upd)), 1) if upd else None, "first": first, "at": now.isoformat(),
                        "text": f"{moved}; {C.BOOKS[v]['name']} is still at {_txt_pt(mk, side, pt) if mk != 'h2h' else side + ' ML'} {M.american_from_decimal(price):+d}",
                    })
    now_ids = {i["id"] for i in items}
    closed = [i for k, i in open_items.items() if k not in now_ids]
    if closed:         # windows that closed: the venue moved, the edge went away or the game started
        os.makedirs(os.path.join(data_dir, "track", "steam"), exist_ok=True)
        with open(os.path.join(data_dir, "track", "steam", "log.jsonl"), "a") as f:
            for i in closed:
                f.write(json.dumps({**{k: i.get(k) for k in ("id", "gameId", "league", "market", "side", "point", "venue", "am",
                                                                 "fair", "fairBefore", "edge", "move", "lead", "first", "commence")},
                                    "last": i.get("at"), "window": round(_mins(_t(i["at"]), _t(i["first"])), 1)}) + "\n")
    hist = {k: v for k, v in hist.items() if v and _mins(now, _t(v[-1][0])) <= WINDOW + 5}
    st.update({"hist": hist, "items": items, "used": st.get("used", 0) + spent, "at": now.isoformat()})
    save(data_dir, st)
    return items, spent


def windows(data_dir, days=14):
    """how long windows stayed open, per venue (median minutes, n): shown beside the list"""
    fn = os.path.join(data_dir, "track", "steam", "log.jsonl")
    by = {}
    try:
        for line in open(fn):
            r = json.loads(line)
            by.setdefault(r["venue"], []).append(r.get("window") or 0)
    except Exception:
        return {}
    out = {}
    for v, w in by.items():
        w = sorted(w)
        out[v] = {"n": len(w), "med": w[len(w) // 2]}
    return out
