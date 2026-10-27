"""The Odds API client with credit accounting. Never logs the key."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config as C


class BudgetExceeded(Exception):
    pass


class APIError(RuntimeError):
    def __init__(self, msg, code=None, detail=""):
        super().__init__(msg)
        self.code, self.detail = code, detail


class OddsAPI:
    def __init__(self, key, max_credits=None, reserve=C.RESERVE_CREDITS):
        self.key = key
        self.max_credits = max_credits
        self.reserve = reserve
        self.used_run = 0
        self.remaining = None
        self.used_total = None
        self.calls = []

    def _get(self, path, params=None, est_cost=0):
        if est_cost:
            if self.remaining is not None and self.remaining - est_cost < self.reserve:
                raise BudgetExceeded("reserve")
            if self.max_credits is not None and self.used_run + est_cost > self.max_credits:
                raise BudgetExceeded("run cap")
        q = dict(params or {})
        q["apiKey"] = self.key
        url = f"{C.API_BASE}{path}?{urllib.parse.urlencode(q)}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    body = json.loads(r.read().decode())
                    h = r.headers
                    last = int(h.get("x-requests-last") or 0)
                    if h.get("x-requests-remaining") is not None:
                        self.remaining = int(float(h["x-requests-remaining"]))
                        self.used_total = int(float(h.get("x-requests-used") or 0))
                    self.used_run += last
                    self.calls.append({"path": path, "cost": last})
                    return body
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(2 + attempt * 3)
                    continue
                # message without the URL so the key never reaches a log
                try:
                    detail = e.read()[:400].decode("utf-8", "ignore").replace(self.key, "***")
                except Exception:
                    detail = ""
                raise APIError(f"Odds API {e.code} on {path}", e.code, detail) from None
            except urllib.error.URLError:
                if attempt < 2:
                    time.sleep(2)
                    continue
                raise RuntimeError(f"Odds API unreachable on {path}") from None

    # free endpoints
    def sports(self):
        return self._get("/sports")

    def events(self, sport, **kw):
        return self._get(f"/sports/{sport}/events", kw)

    # paid endpoints
    def odds(self, sport, markets, regions, bookmakers=None, **kw):
        p = {"markets": ",".join(markets), "oddsFormat": "decimal"}
        if bookmakers:
            p["bookmakers"] = ",".join(bookmakers)
            units = -(-len(bookmakers) // 10)
        else:
            p["regions"] = ",".join(regions)
            units = len(regions)
        p.update(kw)
        return self._get(f"/sports/{sport}/odds", p, est_cost=len(markets) * units)

    def event_odds(self, sport, event_id, markets, regions=None, bookmakers=None):
        p = {"markets": ",".join(markets), "oddsFormat": "decimal"}
        if bookmakers:
            p["bookmakers"] = ",".join(bookmakers)
            units = -(-len(bookmakers) // 10)
        else:
            p["regions"] = ",".join(regions)
            units = len(regions)
        # usage counts markets returned; the estimate assumes about 60% of requested markets come back
        return self._get(f"/sports/{sport}/events/{event_id}/odds", p, est_cost=max(1, int(len(markets) * 0.6)) * units)

    # historical (10 credits per market returned per unit)
    def historical_events(self, sport, at, **kw):
        return self._get(f"/historical/sports/{sport}/events", dict(date=at, **kw), est_cost=1)

    def historical_event_odds(self, sport, event_id, at, markets, bookmakers):
        p = {"date": at, "markets": ",".join(markets), "bookmakers": ",".join(bookmakers), "oddsFormat": "decimal"}
        units = -(-len(bookmakers) // 10)
        return self._get(f"/historical/sports/{sport}/events/{event_id}/odds", p, est_cost=int(10 * len(markets) * 0.7) * units)

    def event_odds_all(self, sport, event_id, markets, bookmakers, _depth=0):
        """Every market for one event. If the API rejects a market for this sport (422), drop the ones it names,
        or split the list in half until the bad ones are isolated. Returns (payload, rejected markets)."""
        try:
            return self.event_odds(sport, event_id, markets, bookmakers=bookmakers), []
        except APIError as e:
            if e.code != 422 or not markets:
                raise
            named = [m for m in markets if m in e.detail]
            if named:
                data, bad = self.event_odds_all(sport, event_id, [m for m in markets if m not in named], bookmakers, _depth + 1) \
                    if len(named) < len(markets) else (None, [])
                return data, named + bad
            if len(markets) == 1 or _depth >= 6:
                return None, list(markets)
            half = len(markets) // 2
            a, bad_a = self.event_odds_all(sport, event_id, markets[:half], bookmakers, _depth + 1)
            b, bad_b = self.event_odds_all(sport, event_id, markets[half:], bookmakers, _depth + 1)
            return merge_events(a, b), bad_a + bad_b


def merge_events(a, b):
    """Merge two event-odds payloads for the same event (bookmakers' market lists combined)."""
    if not a or not b:
        return a or b
    out = dict(a)
    books = {bm["key"]: dict(bm, markets=list(bm.get("markets", []))) for bm in a.get("bookmakers", [])}
    for bm in b.get("bookmakers", []):
        if bm["key"] in books:
            books[bm["key"]]["markets"] += bm.get("markets", [])
        else:
            books[bm["key"]] = bm
    out["bookmakers"] = list(books.values())
    return out
