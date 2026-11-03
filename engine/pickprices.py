"""Real prices for our picks. A pick is graded at a price someone could actually have taken: each play-to venue's
price in the first odds pull at or after the pick was posted (raw payloads in snapshots/<date>/), all-in for Kalshi
and Polymarket. Pick'em venues: Underdog and Dabble at the feed's per-pick payout, PrizePicks standard at 1.732, DK Pick6
at its multiplier estimated from DraftKings' price (with DraftKings' margin, which is how Pick6 sets it). The best
venue that was playable then is the grading price (gradeSrc "posted"); a pick with no venue found keeps its good-at
price (gradeSrc "goodAt"). Used for new picks by picklive (first verified observation per venue) and once over the
picks already graded."""
import glob
import gzip
import json
import os
import re
import subprocess
from datetime import datetime

import config as C
import oddsmath as M

STD_LEG = 3 ** 0.5           # PrizePicks 2-pick 3x: 1.732 per leg
PICKEM = {"underdog", "dabble_us_dfs", "prizepicks", "pick6", "betr_us_dfs", "sleeper", "chalkboard"}


def pick6_mult(dk_dec_side, dk_dec_other):
    """Pick6's multiplier for a side from DraftKings' two prices: break-even of a standard 2-pick over DraftKings'
    implied chance WITH its margin (Pick6 follows the book's price, not the no-vig one), capped at 1x"""
    if not dk_dec_side or not dk_dec_other:
        return None
    q = 1 / dk_dec_side
    return round(max(0.35, min(1.0, C.DFS_BREAKEVEN / q)), 3)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _venue_prices(payload, market, side, point, player, home):
    """{venue: decimal price (all-in)} for one side in a raw Odds API event or lines payload"""
    out, dk = {}, {}
    for bm in payload.get("bookmakers") or []:
        key = bm["key"]
        for m in bm.get("markets") or []:
            if m["key"] != market:
                continue
            for o in m.get("outcomes") or []:
                if player and _norm(o.get("description")) != _norm(player):
                    continue
                pt = o.get("point")
                nm = o.get("name")
                if "spreads" in market and point is not None and pt is not None:
                    if _norm(nm) != _norm(side) or abs(pt - point) > 1e-6:
                        continue
                elif point is not None and pt is not None and abs(pt - point) > 1e-6:
                    continue
                if key == "draftkings" and player:
                    dk[nm] = o.get("price")
                if _norm(nm) != _norm(side) and not (player and nm == side):
                    continue
                price = o.get("price")
                if not price or price <= 1:
                    continue
                b = C.book(key)
                fee = b.get("fee")
                cost = M.all_in_prob(1 / price, fee) if fee else 1 / price
                out[key] = round(1 / cost, 4)
    if player and side in ("Over", "Under") and {"Over", "Under"} <= set(dk):
        mult = pick6_mult(dk[side], dk["Under" if side == "Over" else "Over"])
        if mult:
            out["pick6"] = round(STD_LEG * mult, 4)
    if "prizepicks" in out:
        out["prizepicks"] = round(STD_LEG, 4)
    return out


def _posted_at(data_dir, pick):
    fn = f"picks/{pick['id'][:10]}.json"
    try:
        r = subprocess.run(["git", "-C", data_dir, "log", "--format=%cI", "-S", f'"{pick["id"]}"', "--", fn], capture_output=True, text=True, timeout=60)
        ts = [x for x in r.stdout.split() if x]
        return ts[-1] if ts else None
    except Exception:
        return None


def posted_prices(data_dir, pick, posted=None):
    """{venue: decimal} from the first raw pull at/after posting that has this game and market, and when it was"""
    posted = posted or _posted_at(data_dir, pick)
    if not posted or not pick.get("gameId") or not pick.get("market"):
        return None, None
    t0 = datetime.fromisoformat(posted.replace("Z", "+00:00"))
    day = t0.strftime("%Y-%m-%d")
    files = []
    for d in sorted(glob.glob(os.path.join(data_dir, "snapshots", "*"))):
        if os.path.basename(d) < day:
            continue
        for fn in sorted(os.listdir(d)):
            stamp = datetime.strptime(f"{os.path.basename(d)} {fn[:6]}", "%Y-%m-%d %H%M%S").replace(tzinfo=t0.tzinfo)
            if stamp >= t0 and (pick["gameId"] in fn or fn.endswith("_lines.json.gz")):
                files.append((stamp, os.path.join(d, fn)))
        if len(files) > 400:
            break
    files.sort()
    commence = pick.get("commence")
    for stamp, fn in files:
        if commence and stamp.isoformat() >= commence.replace("Z", "+00:00"):
            break
        try:
            data = json.load(gzip.open(fn))
        except Exception:
            continue
        evs = data if isinstance(data, list) else [data]
        for ev in evs:
            if ev.get("id") != pick["gameId"]:
                continue
            px = _venue_prices(ev, pick["market"], pick["side"], pick.get("point"), pick.get("player"), ev.get("home_team"))
            if px:
                return px, stamp.isoformat()
    return None, None


def regrade(data_dir, log=print, only_missing=True):
    """grade every graded pick at its real posted price; returns how many changed"""
    n = 0
    for fn in sorted(glob.glob(os.path.join(data_dir, "picks", "*.json"))):
        picks = json.load(open(fn))
        ch = False
        for p in picks:
            if p.get("status") not in ("won", "lost", "push") or p.get("tier") == "likely":
                continue
            if only_missing and p.get("gradeSrc"):
                continue
            px, at = posted_prices(data_dir, p)
            venues = {v.get("venue") for v in p.get("playTo") or []}
            usable = {k: v for k, v in (px or {}).items() if k in venues}
            if usable:
                best = max(usable, key=usable.get)
                dec = usable[best]
                p["postedPrices"], p["postedAt"] = usable, at
                p["gradeSrc"], p["gradeVenue"] = "posted", best
            else:
                g = p.get("goodAt")
                dec = (1 + g / 100) if g and g > 0 else (1 + 100 / -g) if g else None
                p["gradeSrc"] = "goodAt"
            if dec:
                am = round((dec - 1) * 100) if dec >= 2 else round(-100 / (dec - 1))
                u = p.get("units") or 0
                p["gradedAt"] = am
                p["profit"] = round(u * (dec - 1), 3) if p["status"] == "won" else -u if p["status"] == "lost" else 0
                if p.get("closeFair") is not None:
                    p["clv"] = round(p["closeFair"] * dec - 1, 4)
            ch, n = True, n + 1
        if ch:
            json.dump(picks, open(fn, "w"), indent=1, ensure_ascii=False)
    log(f"pickprices: {n} picks graded at their posted prices")
    return n


def record_first(data_dir, live, now_iso):
    """the first real price we saw at each of an open pick's venues (picklive runs every pull): latest/pick_first_prices.json
    {pickId: {venue: {dec, at}}}; grading uses the best of them"""
    fn = os.path.join(data_dir, "latest", "pick_first_prices.json")
    try:
        st = json.load(open(fn))
    except Exception:
        st = {}
    n = 0
    for pid, x in (live or {}).items():
        cur = st.setdefault(pid, {})
        for v in x.get("venues") or []:
            am = v.get("now")
            if am is None or v.get("listed") is False or v["venue"] in cur:
                continue
            cur[v["venue"]] = {"dec": round((1 + am / 100) if am > 0 else (1 + 100 / -am), 4), "at": now_iso}
            n += 1
    json.dump(st, open(fn, "w"), separators=(",", ":"))
    return n


def first_prices(data_dir, pid):
    try:
        return json.load(open(os.path.join(data_dir, "latest", "pick_first_prices.json"))).get(pid) or {}
    except Exception:
        return {}
