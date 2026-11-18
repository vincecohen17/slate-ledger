"""Maker scan (Nov 17): a cheap lane that keeps early markets and fringe sports priced for the maker-order board.

Vince (Nov 17): "only showing stuff that's like 10%+ EV if it fills ... attack early market and more fringe sports
because that's where the bigger make to take gaps are". The full runs price far-off games slowly (lines past 36 h every
4 h, and only while the site is open; no alternates or props past 36 h) and cover only the board's sports. This lane,
run from auto runs when due (paid plan):

  * lines: every EVERY_MIN (60) minutes, one odds call per sport for h2h / spreads / totals from the sharp books, the
    exchanges and Vince's sportsbooks only (BOOKS, 15 books = 2 credit units: 6 credits a sport). Board sports are pulled
    only when the board's own line pull is older than BOARD_STALE_MIN; every other active non-outright sport the feed
    carries (more soccer leagues, rugby, cricket, Aussie rules, handball, minor hockey and basketball ...) is a fringe
    sport. A fringe sport where none of Vince's exchanges quotes anything is skipped for NO_VENUE_H (24 h), so the
    discovery costs a few hundred credits a day and the rest only what has venue quotes. Fringe pulls are saved to
    snapshots/ as <sport>_lines (closes for CLV).
  * early alternates: every ALT_EVERY_MIN (240) minutes, alternate spreads / totals / team totals for ALT_SPORTS games
    EARLY_H (36) to makers.AHEAD_H (96) hours out (the full runs pull no event markets that far out), 6 credits a game.
  * spending: DAY_MAX credits a Pacific day (latest/makerscan_state.json), at most RUN_MAX in one pass.

docs() turns the saved payloads into game docs (build.build_game / add_extra_lines, so fairs, all-in costs and stale
flags are the board's own math) and merges them with the board's: a board game gets the scan's main lines when they
are newer than the board's line pull, plus the early alternates; fringe games are added. makers.run uses the merged
list for suggestions and for following its paper orders."""
import gzip
import json
import os
from datetime import datetime, timedelta, timezone

import config as C

EVERY_MIN = getattr(C, "MAKER_SCAN_EVERY_MIN", 60)
ALT_EVERY_MIN = getattr(C, "MAKER_SCAN_ALT_EVERY_MIN", 240)
DAY_MAX = getattr(C, "MAKER_SCAN_DAY_MAX", 10000)
RUN_MAX = 1500
BOARD_STALE_MIN = 45
EARLY_H, AHEAD_H = 36, 96
NO_VENUE_H = 24
ALT_SPORTS = ("americanfootball_nfl", "americanfootball_ncaaf", "basketball_nba", "icehockey_nhl")
ALT_MARKETS = ["alternate_spreads", "alternate_totals", "alternate_team_totals"]
MAIN = ["h2h", "spreads", "totals"]
VENUES = ("kalshi", "polymarket_us", "novig", "prophetx")
SHARP = [k for k, v in C.BOOKS.items() if v.get("kind") == "sharp"]
VEN = [k for k, v in C.BOOKS.items() if v.get("venue") and v.get("kind") in ("exchange", "book")]
BOOKS = list(dict.fromkeys(SHARP + ["betfair_ex_eu", "matchbook"] + VEN))
SKIP_GROUPS = {"Politics", "Golf"}


def _iso(s):
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _z(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mins(now, s):
    try:
        return (now - _iso(s)).total_seconds() / 60
    except Exception:
        return 1e9


def _dir(data_dir, *p):
    return os.path.join(data_dir, "latest", "raw", "makerscan", *p)


def _state_fn(data_dir):
    return os.path.join(data_dir, "latest", "makerscan_state.json")


def load(data_dir):
    try:
        return json.load(open(_state_fn(data_dir)))
    except Exception:
        return {}


def _save(data_dir, st):
    os.makedirs(os.path.dirname(_state_fn(data_dir)), exist_ok=True)
    json.dump(st, open(_state_fn(data_dir), "w"), separators=(",", ":"))


def _day(now):
    return now.astimezone(timezone(timedelta(hours=-8))).strftime("%Y-%m-%d")


def due(data_dir, now):
    st = load(data_dir)
    if st.get("day") == _day(now) and st.get("used", 0) >= DAY_MAX:
        return False
    return _mins(now, st.get("at")) >= EVERY_MIN - 2


def board_sport(s):
    return C.wanted(s)


def _put(data_dir, name, payload):
    os.makedirs(_dir(data_dir), exist_ok=True)
    json.dump(payload, open(_dir(data_dir, name), "w"), separators=(",", ":"))


def _snapshot(data_dir, now, sport, payload):
    d = os.path.join(data_dir, "snapshots", now.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    with gzip.open(os.path.join(d, f"{now.strftime('%H%M%S')}_{sport}_lines.json.gz"), "wt") as f:
        json.dump(payload, f, separators=(",", ":"))


def scan(api, data_dir, now, lines_at, log=print):
    """one pass. lines_at: the board's last line pull per sport (refresh_state lines). Returns credits spent."""
    from api import BudgetExceeded
    st = load(data_dir)
    if st.get("day") != _day(now):
        st["day"], st["used"] = _day(now), 0
    left = DAY_MAX - st.get("used", 0)
    if left <= 0:
        return 0
    api.max_credits = min(RUN_MAX, left)
    titles, no_venue = st.setdefault("titles", {}), st.setdefault("noVenue", {})
    alt_at = st.setdefault("altAt", {})
    try:
        sports = [x for x in api.sports() if x.get("active") and not x.get("has_outrights") and x.get("group") not in SKIP_GROUPS]
    except Exception as e:
        log(f"makerscan: sports list failed ({type(e).__name__})")
        return 0
    to = _z(now + timedelta(hours=AHEAD_H))
    n_board, n_fringe, n_found, n_alt, stop = 0, 0, 0, 0, False
    board_evs = {}
    for x in sorted(sports, key=lambda x: (not board_sport(x["key"]), x["key"])):
        s = x["key"]
        board = board_sport(s)
        titles[s] = x.get("title") or s
        if board and _mins(now, lines_at.get(s)) < BOARD_STALE_MIN:
            continue
        if not board and _mins(now, no_venue.get(s)) < NO_VENUE_H * 60:
            continue
        try:
            data = api.odds(s, MAIN, None, bookmakers=BOOKS, commenceTimeFrom=_z(now), commenceTimeTo=to)
        except BudgetExceeded:
            stop = True
            break
        except Exception as e:
            log(f"makerscan: {s} failed ({type(e).__name__})")
            continue
        data = [ev for ev in data or [] if ev.get("bookmakers")]
        has_venue = any(b["key"] in VENUES for ev in data for b in ev["bookmakers"])
        if board:
            n_board += 1
            board_evs[s] = data
        else:
            n_fringe += 1
            if not has_venue:
                no_venue[s] = _z(now)
                try:
                    os.remove(_dir(data_dir, f"{s}.json"))
                except OSError:
                    pass
                continue
            no_venue.pop(s, None)
            _snapshot(data_dir, now, s, data)
        n_found += has_venue
        _put(data_dir, f"{s}.json", {"at": _z(now), "sport": s, "title": titles[s], "board": board, "events": data})
    # early alternates for the big US sports: none of these are on the board this far out
    if not stop and _mins(now, st.get("altPass")) >= ALT_EVERY_MIN - 2:
        st["altPass"] = _z(now)
        for s in ALT_SPORTS:
            if s not in {x["key"] for x in sports}:
                continue
            try:
                evs = board_evs.get(s) or api.events(s, commenceTimeFrom=_z(now + timedelta(hours=EARLY_H)), commenceTimeTo=to)
            except Exception:
                continue
            for ev in evs or []:
                hrs = (_iso(ev["commence_time"]) - now).total_seconds() / 3600
                if not EARLY_H < hrs <= AHEAD_H or _mins(now, alt_at.get(ev["id"])) < ALT_EVERY_MIN - 2:
                    continue
                try:
                    data = api.event_odds(s, ev["id"], ALT_MARKETS, bookmakers=BOOKS)
                except BudgetExceeded:
                    stop = True
                    break
                except Exception:
                    alt_at[ev["id"]] = _z(now)
                    continue
                alt_at[ev["id"]] = _z(now)
                if data and data.get("bookmakers"):
                    _put(data_dir, f"ev_{ev['id']}.json", {"at": _z(now), "sport": s, "event": data})
                    n_alt += 1
            if stop:
                break
    # forget old files and state
    try:
        for fn in os.listdir(_dir(data_dir)):
            p = _dir(data_dir, fn)
            if (now.timestamp() - os.path.getmtime(p)) > 6 * 3600:
                os.remove(p)
    except OSError:
        pass
    st["altAt"] = {k: v for k, v in alt_at.items() if _mins(now, v) < 2 * 24 * 60}
    st["used"] = st.get("used", 0) + api.used_run
    st["at"] = _z(now)
    st["last"] = {"board": n_board, "fringe": n_fringe, "withVenue": n_found, "alts": n_alt, "credits": api.used_run,
                  "skipped": sum(1 for v in no_venue.values() if _mins(now, v) < NO_VENUE_H * 60), "budgetStop": stop}
    _save(data_dir, st)
    log(f"makerscan: {n_board} board + {n_fringe} fringe sports ({n_found} with exchange quotes), {n_alt} early alt pulls, "
        f"credits {api.used_run} (today {st['used']})")
    return api.used_run


def docs(data_dir, now, game_docs, lines_at=None):
    """the board's game docs merged with the scan's: newer main lines, early alternates, fringe games"""
    import build
    out = {d["id"]: d for d in game_docs}
    try:
        files = os.listdir(_dir(data_dir))
    except OSError:
        return game_docs
    lines_at = lines_at or {}
    changed = {}
    for fn in sorted(files):
        if fn.startswith("ev_"):
            continue
        try:
            pay = json.load(open(_dir(data_dir, fn)))
        except Exception:
            continue
        if _mins(now, pay.get("at")) > 2 * EVERY_MIN + 30:
            continue
        s = pay["sport"]
        label = C.label(s) if board_sport(s) else pay.get("title") or s
        newer = _mins(now, pay["at"]) < _mins(now, lines_at.get(s))
        for ev in pay.get("events") or []:
            if _iso(ev["commence_time"]) <= now:
                continue
            try:
                g = build.build_game(ev, label)
            except Exception:
                continue
            g["scanAt"] = pay["at"]
            if not pay.get("board"):
                g["fringe"] = True
            old = out.get(ev["id"])
            if old is None:
                out[ev["id"]] = changed[ev["id"]] = g
            elif newer:
                keep = [l for l in old.get("lines") or [] if l.get("market") not in MAIN]
                out[ev["id"]] = changed[ev["id"]] = dict(old, lines=[l for l in g["lines"]] + keep, scanAt=pay["at"])
    for fn in sorted(files):
        if not fn.startswith("ev_"):
            continue
        try:
            pay = json.load(open(_dir(data_dir, fn)))
        except Exception:
            continue
        ev = pay.get("event") or {}
        if _mins(now, pay.get("at")) > ALT_EVERY_MIN + 60 or ev.get("id") not in out:
            continue
        d = out[ev["id"]]
        if any(str(l.get("market", "")).startswith("alternate") for l in d.get("lines") or []):
            continue        # the board already has this game's alternates (inside 36 h)
        d = dict(d, lines=list(d.get("lines") or []))
        try:
            build.add_extra_lines(ev, d)
        except Exception:
            continue
        out[ev["id"]] = changed[ev["id"]] = d
    return list(out.values())
