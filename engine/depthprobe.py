"""One-off look at where order-book depth can come from, saved to the private data repo (probe/depth/). Logs print
status codes, counts and key names only.

1. The Odds API with includeSids / includeLinks / includeBetLimits for Kalshi, Novig, ProphetX and Polymarket US on one
   upcoming game per sport (does it carry the exchanges' own market ids, links or sizes?).
2. Kalshi's public market data (no login): sports series, a few open events with their markets, one order book.
3. Polymarket US: which public endpoints answer.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

DATA_DIR = os.environ.get("DATA_DIR", "data")
OUT = os.path.join(DATA_DIR, "probe", "depth")
os.makedirs(OUT, exist_ok=True)
UA = {"User-Agent": "Mozilla/5.0 (slate-ledger research)", "Accept": "application/json"}
KAL = "https://api.elections.kalshi.com/trade-api/v2"


def get(url, params=None):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return type(e).__name__, None


def save(name, obj):
    json.dump(obj, open(os.path.join(OUT, name + ".json"), "w"), indent=1)


def keys_of(x, depth=0):
    if isinstance(x, dict):
        return {k: keys_of(v, depth + 1) if depth < 3 else "…" for k, v in list(x.items())[:25]}
    if isinstance(x, list):
        return [keys_of(x[0], depth + 1)] if x else []
    return type(x).__name__


report = {}

# --- 1. The Odds API: do the exchange quotes carry ids, links or limits?
key = os.environ.get("ODDS_API_KEY")
if key:
    base = "https://api.the-odds-api.com/v4"
    now = datetime.now(timezone.utc)
    for sport, mk in (("basketball_nba", "h2h,spreads,totals,player_points"), ("americanfootball_nfl", "h2h,spreads,totals"),
                      ("icehockey_nhl", "h2h,spreads,totals")):
        st, evs = get(f"{base}/sports/{sport}/events", {"apiKey": key, "commenceTimeFrom": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                         "commenceTimeTo": (now + timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        if not evs:
            report[sport] = {"events": st}
            continue
        ev = evs[0]
        st, odds = get(f"{base}/sports/{sport}/events/{ev['id']}/odds", {"apiKey": key, "bookmakers": "kalshi,novig,prophetx,polymarket_us",
                       "markets": mk, "oddsFormat": "decimal", "includeSids": "true", "includeLinks": "true", "includeBetLimits": "true"})
        save(f"odds_{sport}", odds)
        books = (odds or {}).get("bookmakers") or []
        report[sport] = {"status": st, "books": [b.get("key") for b in books],
                         "shape": keys_of(books[0]) if books else None,
                         "sample": [{"book": b.get("key"), "link": b.get("link"), "sid": b.get("sid"),
                                     "market": {k: v for k, v in (b.get("markets") or [{}])[0].items() if k != "outcomes"},
                                     "outcome": (b.get("markets") or [{}])[0].get("outcomes", [None])[0]} for b in books]}
        print(f"odds api {sport}: {st}, books {[b.get('key') for b in books]}")

# --- 2. Kalshi public market data
st, ser = get(f"{KAL}/series", {"category": "Sports"})
series = (ser or {}).get("series") or []
save("kalshi_series", [{k: s.get(k) for k in ("ticker", "title", "category", "tags")} for s in series])
report["kalshi_series"] = {"status": st, "n": len(series)}
print(f"kalshi series: {st}, {len(series)} sports series")
picks = [s["ticker"] for s in series if any(w in (s.get("ticker") or "") for w in ("NBA", "NFL", "NHL", "MLB", "NCAAF"))][:40]
report["kalshi_series_pick"] = picks
evs_all = []
for t in picks[:14]:
    st, ev = get(f"{KAL}/events", {"series_ticker": t, "status": "open", "with_nested_markets": "true", "limit": 3})
    for e in (ev or {}).get("events") or []:
        evs_all.append({"series": t, "event_ticker": e.get("event_ticker"), "title": e.get("title"), "sub_title": e.get("sub_title"),
                        "markets": [{k: m.get(k) for k in ("ticker", "title", "subtitle", "yes_sub_title", "no_sub_title", "yes_ask", "yes_bid",
                                                            "no_ask", "no_bid", "yes_ask_dollars", "yes_bid_dollars", "floor_strike", "strike_type",
                                                            "liquidity", "liquidity_dollars", "open_interest", "volume", "custom_strike",
                                                            "expected_expiration_time")} for m in (e.get("markets") or [])[:6]]})
save("kalshi_events", evs_all)
report["kalshi_events"] = len(evs_all)
print(f"kalshi events sampled: {len(evs_all)}")
mt = next((m["ticker"] for e in evs_all for m in e["markets"] if m.get("ticker")), None)
if mt:
    st, ob = get(f"{KAL}/markets/{mt}/orderbook")
    save("kalshi_orderbook", {"ticker": mt, "status": st, "book": ob})
    report["kalshi_orderbook"] = {"status": st, "shape": keys_of(ob)}
    print(f"kalshi orderbook: {st}")

# --- 3. Polymarket US: which public endpoints answer
pm = {}
for u in ("https://gateway.polymarket.us/v1/markets?limit=2", "https://api.polymarket.us/v1/markets?limit=2",
          "https://api.polymarket.us/markets?limit=2", "https://clob.polymarket.us/markets", "https://polymarket.us/api/markets?limit=2",
          "https://gamma-api.polymarket.com/markets?limit=1", "https://clob.polymarket.com/book?token_id=0"):
    st, body = get(u)
    pm[u] = {"status": st, "shape": keys_of(body) if body is not None else None}
    print(f"polymarket probe {urllib.parse.urlparse(u).netloc}{urllib.parse.urlparse(u).path}: {st}")
report["polymarket"] = pm
save("report", report)
