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
SPLIT_PAGES = {
    "NBA": "https://data.vsin.com/nba/betting-splits/",
    "NHL": "https://data.vsin.com/nhl/betting-splits/",
    "NFL": "https://data.vsin.com/nfl/betting-splits/",
    "NCAAF": "https://data.vsin.com/college-football/betting-splits/",
    "NCAAB": "https://data.vsin.com/college-basketball/betting-splits/",
    "MLB": "https://data.vsin.com/mlb/betting-splits/",
    "WNBA": "https://data.vsin.com/wnba/betting-splits/",
    "TENNIS": "https://data.vsin.com/tennis/betting-splits/",
    "SOCCER": "https://data.vsin.com/draftkings/betting-splits/?view=socactive",
}


def _fetch(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def probe(data_dir, log=print):
    """save each splits page raw to the private data repo (latest/probe/) so the parser can be written against the real
    markup; logs sizes only"""
    out = os.path.join(data_dir, "latest", "probe")
    os.makedirs(out, exist_ok=True)
    for lg, url in SPLIT_PAGES.items():
        try:
            h = _fetch(url)
        except Exception as e:
            log(f"splits probe {lg}: {type(e).__name__}")
            continue
        open(os.path.join(out, f"vsin_{lg.lower()}.html"), "w").write(h)
        log(f"splits probe {lg}: {len(h)} bytes, {h.count('<tr')} rows, {h.count('%')} percent signs")
