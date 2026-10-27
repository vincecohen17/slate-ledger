"""Finished games stay on the site: final score, line score, team stats, scoring plays and full box scores from ESPN
(summary fetched once per game), plus what our pre-game numbers said (track/open snapshot) and how that turned out.

Data repo: finals/<YYYY-MM-DD>.json  {key: doc}   (PT date of the game; last 3 days kept on Firestore)
Firestore: finals/{key} one doc per game, board/finals the list for the board and Live screens.
"""
import glob
import json
import os
from datetime import datetime, timedelta, timezone

import config as C
import live as LV
import refs as R

ESPN = LV.ESPN
SOCCER = ("soccer_",)
GROUPS_KEEP = 8          # box-score stat groups per team (NFL has many)
ATHLETES_KEEP = 16       # players per group


def _pt(dt):
    return (dt - timedelta(hours=7)).date()


def _path(sport):
    try:
        return LV._path(sport)
    except Exception:
        return None


def _num(v):
    try:
        return float(str(v).replace("%", "").split("-")[0].split("/")[0])
    except Exception:
        return None


def _box(s):
    """generic box score: per team, the stat groups ESPN lists (labels + rows), starters first"""
    out = {}
    for side in (s.get("boxscore") or {}).get("players", []) or []:
        abbr = side["team"]["abbreviation"]
        groups = []
        for grp in (side.get("statistics") or [])[:GROUPS_KEEP]:
            labels = grp.get("labels") or grp.get("keys") or []
            rows = []
            for a in (grp.get("athletes") or [])[:ATHLETES_KEEP]:
                if a.get("didNotPlay") or not a.get("stats"):
                    continue
                ath = a.get("athlete") or {}
                rows.append({"n": ath.get("shortName") or ath.get("displayName"), "full": ath.get("displayName"),
                             "pos": (ath.get("position") or {}).get("abbreviation"), "st": bool(a.get("starter")),
                             "img": (ath.get("headshot") or {}).get("href"), "v": a.get("stats")})
            if rows:
                groups.append({"name": grp.get("text") or grp.get("name") or "", "labels": labels, "rows": rows,
                               "tot": grp.get("totals") or None})
        out[abbr] = groups
    return out


def _team_stats(s, home_abbr):
    """[{k, h, a}] display values of every team stat ESPN gives, both teams"""
    by = {}
    for t in (s.get("boxscore") or {}).get("teams", []) or []:
        side = "h" if t["team"]["abbreviation"] == home_abbr else "a"
        for st in t.get("statistics") or []:
            k = st.get("label") or st.get("name")
            if not k or st.get("displayValue") in (None, ""):
                continue
            by.setdefault(k, {"k": k})[side] = st["displayValue"]
    return [v for v in by.values() if "h" in v and "a" in v][:24]


def _plays(s):
    out = []
    for p in (s.get("scoringPlays") or [])[:40]:
        out.append({"t": p.get("text"), "per": (p.get("period") or {}).get("number"), "clk": (p.get("clock") or {}).get("displayValue"),
                    "team": (p.get("team") or {}).get("abbreviation"), "hs": p.get("homeScore"), "as": p.get("awayScore")})
    if not out:      # soccer: key events that scored
        for p in (s.get("keyEvents") or [])[:60]:
            if p.get("scoringPlay"):
                out.append({"t": p.get("text") or (p.get("type") or {}).get("text"), "clk": (p.get("clock") or {}).get("displayValue"),
                            "team": (p.get("team") or {}).get("abbreviation") or (p.get("team") or {}).get("displayName")})
    return out


def _side(c):
    t = c.get("team") or {}
    return {"abbr": t.get("abbreviation"), "name": t.get("displayName") or t.get("name"), "logo": t.get("logo") or ((t.get("logos") or [{}])[0].get("href")),
            "color": t.get("color"), "score": _num(c.get("score")), "winner": bool(c.get("winner")),
            "line": [ls.get("displayValue") if ls.get("displayValue") is not None else ls.get("value") for ls in (c.get("linescores") or [])],
            "record": ((c.get("record") or [{}])[0] or {}).get("summary") if isinstance(c.get("record"), list) else None}


def _snapshots(data_dir):
    snaps = []
    for fn in glob.glob(os.path.join(data_dir, "track", "open", "*.json")):
        try:
            snaps.append(json.load(open(fn)))
        except Exception:
            pass
    return snaps


def _pre(snap):
    """what we said before the game: moneyline, main spread and total (model and sharp fair, home / over side)"""
    if not snap:
        return None
    out = {}
    for kind in ("h2h", "spreads", "totals"):
        rs = [r for r in snap["rows"] if r["mk"] == kind and r.get("p") is not None and not r.get("player")]
        if not rs:
            continue
        r = max(rs, key=lambda x: (x.get("w") or 0))
        out[kind] = {"pt": r.get("pt"), "side": r["side"], "p": r["p"], "f": r["f"]}
    return out or None


def _match(snaps, games, sport, home, away, when):
    """our game id for an ESPN game: same sport, same teams (names or abbreviations), start within a day"""
    hn, an = {R.norm(home.get("name") or ""), R.norm(home.get("abbr") or "")}, {R.norm(away.get("name") or ""), R.norm(away.get("abbr") or "")}
    def ok(h, a, ha, aa, t):
        try:
            close = abs((datetime.fromisoformat(t.replace("Z", "+00:00")) - when).total_seconds()) < 4 * 3600      # series play the same teams on back-to-back days
        except Exception:
            close = False
        return close and ({R.norm(h), R.norm(ha or "")} & hn) and ({R.norm(a), R.norm(aa or "")} & an)
    snap = next((x for x in snaps if x.get("sport") == sport and ok(x["home"], x["away"], x.get("ha"), x.get("aa"), x["commence"])), None)
    if snap:
        return snap["id"], snap
    g = next((x for x in games if x.get("sport") == sport and ok(x["home"], x["away"], (x.get("homeRef") or {}).get("abbr"),
                                                                     (x.get("awayRef") or {}).get("abbr"), x["commence"])), None)
    return (g["id"] if g else None), None


def _doc(sport, e, s, snaps, games):
    hdr = (s.get("header") or {}).get("competitions", [{}])[0] if s else e["competitions"][0]
    comp = {c["homeAway"]: c for c in hdr.get("competitors", [])}
    if "home" not in comp or "away" not in comp:
        return None
    home, away = _side(comp["home"]), _side(comp["away"])
    st = (hdr.get("status") or e.get("status") or {}).get("type") or {}
    when = datetime.fromisoformat((e.get("date") or hdr.get("date")).replace("Z", "+00:00"))
    gid, snap = _match(snaps, games, sport, home, away, when)
    pre = _pre(snap)
    res = None
    if pre and home["score"] is not None and away["score"] is not None:
        hs, as_ = home["score"], away["score"]
        res = {}
        if "h2h" in pre and hs != as_:
            res["h2h"] = 1 if hs > as_ else 0
        if "spreads" in pre and pre["spreads"]["pt"] is not None and hs + pre["spreads"]["pt"] != as_:
            res["spreads"] = 1 if hs + pre["spreads"]["pt"] > as_ else 0
        if "totals" in pre and pre["totals"]["pt"] is not None and hs + as_ != pre["totals"]["pt"]:
            res["totals"] = 1 if hs + as_ > pre["totals"]["pt"] else 0
    return {"key": f"{sport}|{e['id']}", "espnId": e["id"], "gameId": gid, "sport": sport, "league": C.label(sport),
            "commence": when.isoformat().replace("+00:00", "Z"), "date": str(_pt(when)), "status": st.get("shortDetail") or st.get("description") or "Final",
            "home": home, "away": away, "teamStats": _team_stats(s, home["abbr"]) if s else [], "plays": _plays(s) if s else [],
            "box": _box(s) if s else {}, "pre": pre, "result": res,
            "venue": ((hdr.get("venue") or (s or {}).get("gameInfo", {}).get("venue") or {}).get("fullName"))}


def _tennis_doc(sport, ev, c, games=()):
    comp = c.get("competitors", [])
    if len(comp) != 2:
        return None
    pl = []
    for x in comp:
        a = x.get("athlete") or {}
        pl.append({"name": a.get("displayName"), "abbr": a.get("shortName") or a.get("displayName"), "winner": bool(x.get("winner")),
                   "line": [int(float(ls.get("value") or 0)) for ls in x.get("linescores") or []],
                   "logo": (a.get("flag") or {}).get("href")})
    if not all(p["name"] for p in pl):
        return None
    when = datetime.fromisoformat((c.get("date") or ev.get("date")).replace("Z", "+00:00"))
    sets = [sum(1 for i, g in enumerate(p["line"]) if g > (pl[1 - j]["line"][i] if i < len(pl[1 - j]["line"]) else 0)) for j, p in enumerate(pl)]
    home, away = dict(pl[0], score=sets[0]), dict(pl[1], score=sets[1])
    names = {R.norm(pl[0]["name"]), R.norm(pl[1]["name"])}
    g = next((x for x in games if str(x.get("sport", "")).startswith("tennis_") and {R.norm(x["home"]), R.norm(x["away"])} == names), None)
    if g and R.norm(g["home"]) != R.norm(home["name"]):
        home, away = away, home         # keep our board's home/away order
    return {"key": f"{sport}|{c.get('id')}", "espnId": c.get("id"), "gameId": g["id"] if g else None, "sport": sport, "league": C.label(sport),
            "commence": when.isoformat().replace("+00:00", "Z"), "date": str(_pt(when)), "status": ((c.get("status") or {}).get("type") or {}).get("shortDetail") or "Final",
            "home": home, "away": away, "teamStats": [], "plays": [], "box": {}, "pre": None, "result": None, "tennis": True,
            "event": ev.get("name")}


def collect(data_dir, sports, games, now, every_min=10, log=print):
    """refresh finals for today and yesterday (PT); returns {key: doc} for the last 3 days"""
    d = os.path.join(data_dir, "finals")
    os.makedirs(d, exist_ok=True)
    sp = os.path.join(d, "state.json")
    try:
        state = json.load(open(sp))
    except Exception:
        state = {}
    today = _pt(now)
    days = [today - timedelta(days=i) for i in range(3)]
    store = {}
    for day in days:
        try:
            store[str(day)] = json.load(open(os.path.join(d, f"{day}.json")))
        except Exception:
            store[str(day)] = {}
    last = state.get("at")
    due = not last or (now - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() >= every_min * 60
    if due:
        snaps = _snapshots(data_dir)
        paths_done, new = set(), 0
        for sport in sports:
            path = _path(sport)
            if not path:
                continue
            for day in days[:2]:
                if state.get("done", {}).get(f"{path}|{day}"):
                    continue
                key = f"{path}|{day}"
                if key in paths_done:
                    continue
                paths_done.add(key)
                try:
                    sb = LV._get(f"{ESPN}/{path}/scoreboard?dates={day.strftime('%Y%m%d')}" + ("&groups=80" if sport == "americanfootball_ncaaf" else ""))
                except Exception:
                    continue
                evs = sb.get("events", [])
                all_post = bool(evs)
                for ev in evs:
                    if path.startswith("tennis/"):
                        for gr in ev.get("groupings") or [{"competitions": ev.get("competitions", [])}]:
                            if "doubles" in str((gr.get("grouping") or {}).get("slug", "")).lower():
                                continue
                            for c in gr.get("competitions", []):
                                if ((c.get("status") or {}).get("type") or {}).get("state") != "post":
                                    all_post = all_post and ((c.get("status") or {}).get("type") or {}).get("state") == "post"
                                    continue
                                k = f"{sport}|{c.get('id')}"
                                if any(k in v for v in store.values()):
                                    continue
                                doc = _tennis_doc(sport, ev, c, games)
                                if doc and doc["date"] in store:
                                    store[doc["date"]][k] = doc
                                    new += 1
                        continue
                    stt = ((ev.get("competitions") or [{}])[0].get("status") or ev.get("status") or {}).get("type") or {}
                    if stt.get("state") != "post":
                        all_post = False
                        continue
                    if (stt.get("name") or "").endswith(("POSTPONED", "CANCELED")):
                        continue
                    k = f"{sport}|{ev['id']}"
                    if any(k in v for v in store.values()):
                        continue
                    try:
                        s = LV._get(f"{ESPN}/{path}/summary?event={ev['id']}")
                    except Exception:
                        s = None
                    try:
                        doc = _doc(sport, ev, s, snaps, games)
                    except Exception as ex:
                        log(f"finals: {C.label(sport)} game skipped ({type(ex).__name__})")
                        doc = None
                    if doc and doc["date"] in store:
                        store[doc["date"]][k] = doc
                        new += 1
                if all_post and day < today:
                    state.setdefault("done", {})[key] = True
        for day in days:
            json.dump(store[str(day)], open(os.path.join(d, f"{day}.json"), "w"), separators=(",", ":"))
        state["at"] = now.isoformat().replace("+00:00", "Z")
        state["done"] = {k: v for k, v in state.get("done", {}).items() if k.split("|")[1] >= str(days[-1])}
        json.dump(state, open(sp, "w"))
        log(f"finals: {new} new, {sum(len(v) for v in store.values())} kept")
    out = {}
    for v in store.values():
        out.update(v)
    return out


def summary(docs):
    """the list the board and Live screens show (no box scores)"""
    rows = []
    for x in docs.values():
        rows.append({k: x.get(k) for k in ("key", "gameId", "league", "sport", "commence", "date", "status", "pre", "result", "tennis", "event")}
                    | {"home": {k: x["home"].get(k) for k in ("abbr", "name", "logo", "color", "score", "winner")},
                       "away": {k: x["away"].get(k) for k in ("abbr", "name", "logo", "color", "score", "winner")}})
    rows.sort(key=lambda r: r["commence"], reverse=True)
    return rows[:300]
