"""The Odds API client with credit accounting. Never logs the key."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config as C


class BudgetExceeded(Exception):
    pass


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
                raise RuntimeError(f"Odds API {e.code} on {path}") from None
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
    def odds(self, sport, markets, regions, **kw):
        p = {"markets": ",".join(markets), "regions": ",".join(regions), "oddsFormat": "decimal"}
        p.update(kw)
        return self._get(f"/sports/{sport}/odds", p, est_cost=len(markets) * len(regions))

    def event_odds(self, sport, event_id, markets, regions):
        p = {"markets": ",".join(markets), "regions": ",".join(regions), "oddsFormat": "decimal"}
        return self._get(f"/sports/{sport}/events/{event_id}/odds", p,
                         est_cost=len(markets) * len(regions))
