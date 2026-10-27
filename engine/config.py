"""Slate Ledger engine configuration.

Everything tunable lives here: which sports and markets we pull, which books
count as sharp, which venues Vince can actually bet in California, and the
credit budget rules that keep The Odds API usage under control.
"""

API_BASE = "https://api.the-odds-api.com/v4"

# Sports we cover. key -> short label used on the site.
SPORTS = {
    "basketball_nba": "NBA",
    "icehockey_nhl": "NHL",
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
    "baseball_mlb": "MLB",
    "basketball_ncaab": "NCAAB",
    "soccer_epl": "EPL",
    "soccer_uefa_champs_league": "UCL",
    "soccer_usa_mls": "MLS",
    "mma_mixed_martial_arts": "UFC",
}

# Game-line pulls: one request per sport covers every game.
GAME_MARKETS = ["h2h", "spreads", "totals"]
GAME_REGIONS = ["us", "us2", "us_ex", "eu"]

# Player props, pulled per event. Full menus; MOCK_PROP_MARKETS limits the
# free-tier test so the 500 credits last.
PROP_MARKETS = {
    "basketball_nba": [
        "player_points", "player_rebounds", "player_assists", "player_threes",
        "player_points_rebounds_assists", "player_points_rebounds",
        "player_points_assists", "player_rebounds_assists", "player_blocks",
        "player_steals", "player_blocks_steals", "player_turnovers",
        "player_double_double", "player_triple_double", "player_first_basket",
    ],
    "icehockey_nhl": [
        "player_points", "player_goals", "player_assists", "player_shots_on_goal",
        "player_power_play_points", "player_blocked_shots",
        "player_goal_scorer_anytime", "player_goal_scorer_first",
        "player_total_saves",
    ],
    "americanfootball_nfl": [
        "player_pass_yds", "player_pass_tds", "player_pass_completions",
        "player_pass_attempts", "player_pass_interceptions", "player_pass_longest_completion",
        "player_rush_yds", "player_rush_attempts", "player_rush_longest",
        "player_receptions", "player_reception_yds", "player_reception_longest",
        "player_rush_reception_yds", "player_anytime_td", "player_1st_td",
        "player_sacks", "player_tackles_assists", "player_kicking_points",
        "player_field_goals",
    ],
    "americanfootball_ncaaf": [
        "player_pass_yds", "player_rush_yds", "player_reception_yds",
        "player_receptions", "player_anytime_td",
    ],
    "baseball_mlb": [
        "batter_hits", "batter_total_bases", "batter_home_runs", "batter_rbis",
        "batter_runs_scored", "batter_hits_runs_rbis", "batter_singles",
        "batter_doubles", "batter_walks", "batter_strikeouts", "batter_stolen_bases",
        "pitcher_strikeouts", "pitcher_outs", "pitcher_hits_allowed",
        "pitcher_earned_runs", "pitcher_walks", "totals_1st_1_innings",
    ],
}
MOCK_PROP_MARKETS = {
    "basketball_nba": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    "icehockey_nhl": ["player_shots_on_goal", "player_points"],
    "americanfootball_nfl": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"],
    "baseball_mlb": ["pitcher_strikeouts", "batter_total_bases"],
}
PROP_REGIONS = ["us", "us_dfs", "us_ex", "eu"]
MOCK_PROP_REGIONS = ["us", "us_dfs", "us_ex"]

# ---------------------------------------------------------------------------
# Books
# kind: sharp | exchange | book | dfs
# weight: weight in the sharp consensus (0 = shown but not used for fair)
# venue: True when Vince can bet it from California (or it stands in for a
#        CA product, e.g. DraftKings for DK Predictions)
# fee: fee model applied to the displayed price to get the all-in price
BOOKS = {
    "pinnacle":       {"name": "Pinnacle",        "kind": "sharp",    "weight": 4.0},
    "betfair_ex_eu":  {"name": "Betfair Ex",      "kind": "exchange", "weight": 3.0},
    "matchbook":      {"name": "Matchbook",       "kind": "exchange", "weight": 2.5},
    "novig":          {"name": "Novig",           "kind": "exchange", "weight": 2.0, "venue": True},
    "prophetx":       {"name": "ProphetX",        "kind": "exchange", "weight": 2.0, "venue": True},
    "kalshi":         {"name": "Kalshi",          "kind": "exchange", "weight": 1.5, "venue": True, "fee": "kalshi"},
    "polymarket_us":  {"name": "Polymarket US",   "kind": "exchange", "weight": 1.5, "venue": True, "fee": "polymarket"},
    "polymarket":     {"name": "Polymarket",      "kind": "exchange", "weight": 1.0, "fee": "polymarket"},
    "betopenly":      {"name": "BetOpenly",       "kind": "exchange", "weight": 0.5},
    "limitless":      {"name": "Limitless",       "kind": "exchange", "weight": 0.5},
    "lowvig":         {"name": "LowVig",          "kind": "sharp",    "weight": 1.5},
    "betonlineag":    {"name": "BetOnline",       "kind": "sharp",    "weight": 1.5},
    "circasports":    {"name": "Circa",           "kind": "sharp",    "weight": 3.0},
    "draftkings":     {"name": "DraftKings",      "kind": "book",     "weight": 0.5, "venue": True, "venueNote": "stand-in for DK Predictions"},
    "fanduel":        {"name": "FanDuel",         "kind": "book",     "weight": 0.75, "venue": True, "venueNote": "stand-in for FanDuel Predicts"},
    "fliff":          {"name": "Fliff",           "kind": "book",     "weight": 0.0, "venue": True},
    "courtside":      {"name": "Courtside",       "kind": "book",     "weight": 0.0, "venue": True},
    "bovada":         {"name": "Bovada",          "kind": "book",     "weight": 0.25, "venue": True},
    "betmgm":         {"name": "BetMGM",          "kind": "book",     "weight": 0.25},
    "williamhill_us": {"name": "Caesars",         "kind": "book",     "weight": 0.25},
    "betrivers":      {"name": "BetRivers",       "kind": "book",     "weight": 0.25},
    "espnbet":        {"name": "theScore Bet",    "kind": "book",     "weight": 0.25},
    "fanatics":       {"name": "Fanatics",        "kind": "book",     "weight": 0.25},
    "prizepicks":     {"name": "PrizePicks",      "kind": "dfs",      "weight": 0.0, "venue": True},
    "underdog":       {"name": "Underdog",        "kind": "dfs",      "weight": 0.0, "venue": True},
    "pick6":          {"name": "DK Pick6",        "kind": "dfs",      "weight": 0.0, "venue": True},
    "dabble_us_dfs":  {"name": "Dabble",          "kind": "dfs",      "weight": 0.0, "venue": True},
    "betr_us_dfs":    {"name": "Betr",            "kind": "dfs",      "weight": 0.0, "venue": True},
}
DEFAULT_BOOK = {"kind": "other", "weight": 0.0}

def book(key, title=None):
    b = dict(DEFAULT_BOOK)
    b.update(BOOKS.get(key, {"name": title or key}))
    b.setdefault("name", title or key)
    b["key"] = key
    return b

# Pick'em break-even per leg (2-pick power at 3x). Used to judge DFS lines.
DFS_BREAKEVEN = 0.5774

# ---------------------------------------------------------------------------
# Data quality
MIN_DEC_PRICE = 1.015   # decimal prices at/under this (99c+) are placeholders with no liquidity
MIN_BOOK = 0.98         # a two-sided quote whose implied probs sum outside [MIN_BOOK, MAX_BOOK]
MAX_BOOK = 1.12         # is crossed or too wide to trust; it is shown but not used for the fair

# ---------------------------------------------------------------------------
# Signals
EDGE_LEAN = 0.015    # 1.5 pp over fair: worth a look
EDGE_STRONG = 0.03   # 3 pp: strong price
STALE_MINUTES = 20   # a book's market is stale if this far behind the freshest book

# ---------------------------------------------------------------------------
# Credit budget
MONTHLY_CREDITS = {"free": 500, "5m": 5_000_000}
MOCK_MAX_CREDITS = 260      # hard ceiling for one mock run
RESERVE_CREDITS = 40        # never go below this many remaining
