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

def label(sport):
    """Short league label; tennis tournaments come and go, so they are labelled by tour."""
    if sport in SPORTS:
        return SPORTS[sport]
    if sport.startswith("tennis_"):
        return "ATP" if "_atp_" in sport else "WTA"
    return sport


def wanted(sport):
    return sport in SPORTS or sport.startswith("tennis_atp") or sport.startswith("tennis_wta")


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


# ---------------------------------------------------------------------------
# Plan + app-driven refresh
PLAN = "free"            # "free" until the Odds API upgrade, then "5m"
PLANS = {
    # per-run and per-day credit ceilings; props only on the paid plan
    "free": {"run_cap": 40, "day_cap": 80, "props": False, "min_lines_age": 120},
    "5m": {"run_cap": 6000, "day_cap": 150_000, "props": True, "min_lines_age": 0},
}
ACTIVE_MINUTES = 7       # the site counts as open if it pinged within this many minutes
# staleness (minutes) by how soon the game starts: (starts within hours, max age)
LINES_STALE = [(3, 5), (12, 15), (36, 60), (9999, 240)]
PROPS_STALE = [(3, 10), (12, 30), (36, 180)]          # no props beyond 36h
CLOSE_WINDOW_MIN = 25    # always capture prices for games starting this soon (CLV / grading)
CLOSE_STALE_MIN = 8

# Books for event-level pulls: naming books costs 1 unit per 10 books, cheaper than whole regions.
EVENT_BOOKS = ["pinnacle", "betfair_ex_eu", "novig", "prophetx", "kalshi", "polymarket_us", "draftkings",
               "fanduel", "betonlineag", "lowvig",
               "prizepicks", "underdog", "pick6", "dabble_us_dfs", "bovada", "fliff", "betmgm",
               "williamhill_us", "espnbet", "courtside"]

# Every market The Odds API offers per sport (event endpoint). Cost counts only markets returned.
_P = lambda *k: list(k)
_ALT_GAME = ["alternate_spreads", "alternate_totals", "team_totals", "alternate_team_totals"]
_PERIODS_BB = [f"{m}_{p}" for p in ("q1", "q2", "q3", "q4", "h1", "h2") for m in ("h2h", "spreads", "totals")] + \
              [f"team_totals_{p}" for p in ("q1", "h1")] + [f"alternate_{m}_{p}" for p in ("q1", "h1") for m in ("spreads", "totals")]
_PERIODS_HK = [f"{m}_{p}" for p in ("p1", "p2", "p3") for m in ("h2h", "spreads", "totals")] + ["h2h_3_way", "h2h_3_way_p1", "btts"]
_INN = [f"{m}_1st_{n}_innings" for n in (1, 3, 5, 7) for m in ("h2h", "spreads", "totals")] + \
       ["alternate_spreads_1st_5_innings", "alternate_totals_1st_5_innings", "h2h_3_way_1st_5_innings"]
_NBA_P = ["player_points", "player_points_q1", "player_rebounds", "player_rebounds_q1", "player_assists", "player_assists_q1",
          "player_threes", "player_blocks", "player_steals", "player_blocks_steals", "player_turnovers",
          "player_points_rebounds_assists", "player_points_rebounds", "player_points_assists", "player_rebounds_assists",
          "player_field_goals", "player_frees_made", "player_frees_attempts", "player_first_basket", "player_first_team_basket",
          "player_double_double", "player_triple_double", "player_method_of_first_basket", "player_fantasy_points"]
_NBA_ALT = [m + "_alternate" for m in ("player_points", "player_rebounds", "player_assists", "player_blocks", "player_steals",
            "player_turnovers", "player_threes", "player_points_assists", "player_points_rebounds", "player_rebounds_assists",
            "player_points_rebounds_assists", "player_fantasy_points")]
_NFL_P = ["player_assists", "player_defensive_interceptions", "player_field_goals", "player_kicking_points", "player_pass_attempts",
          "player_pass_completions", "player_pass_interceptions", "player_pass_longest_completion", "player_pass_rush_yds",
          "player_pass_rush_reception_tds", "player_pass_rush_reception_yds", "player_pass_tds", "player_pass_yds", "player_pass_yds_q1",
          "player_pats", "player_receptions", "player_reception_longest", "player_reception_tds", "player_reception_yds",
          "player_rush_attempts", "player_rush_longest", "player_rush_reception_tds", "player_rush_reception_yds", "player_rush_tds",
          "player_rush_yds", "player_sacks", "player_solo_tackles", "player_tackles_assists", "player_tds_over", "player_1st_td",
          "player_anytime_td", "player_last_td"]
_NFL_ALT = [m + "_alternate" for m in ("player_assists", "player_field_goals", "player_kicking_points", "player_pass_attempts",
            "player_pass_completions", "player_pass_interceptions", "player_pass_longest_completion", "player_pass_rush_yds",
            "player_pass_rush_reception_tds", "player_pass_rush_reception_yds", "player_pass_tds", "player_pass_yds", "player_pats",
            "player_receptions", "player_reception_longest", "player_reception_tds", "player_reception_yds", "player_rush_attempts",
            "player_rush_longest", "player_rush_reception_tds", "player_rush_reception_yds", "player_rush_tds", "player_rush_yds",
            "player_sacks", "player_solo_tackles", "player_tackles_assists")]
_MLB_P = ["batter_home_runs", "batter_first_home_run", "batter_hits", "batter_total_bases", "batter_rbis", "batter_runs_scored",
          "batter_hits_runs_rbis", "batter_singles", "batter_doubles", "batter_triples", "batter_walks", "batter_strikeouts",
          "batter_stolen_bases", "batter_fantasy_score", "pitcher_strikeouts", "pitcher_record_a_win", "pitcher_hits_allowed",
          "pitcher_walks", "pitcher_earned_runs", "pitcher_outs"]
_MLB_ALT = ["batter_total_bases_alternate", "batter_home_runs_alternate", "batter_hits_alternate", "batter_rbis_alternate",
            "batter_walks_alternate", "batter_strikeouts_alternate", "batter_runs_scored_alternate", "batter_hits_runs_rbis_alternate",
            "batter_singles_alternate", "batter_doubles_alternate", "batter_triples_alternate", "batter_fantasy_score_alternate",
            "pitcher_hits_allowed_alternate", "pitcher_walks_alternate", "pitcher_earned_runs_alternate",
            "pitcher_strikeouts_alternate", "pitcher_outs_alternate"]
_NHL_P = ["player_points", "player_power_play_points", "player_assists", "player_blocked_shots", "player_shots_on_goal",
          "player_goals", "player_total_saves", "player_faceoffs_won", "player_time_on_ice", "player_goal_scorer_first",
          "player_goal_scorer_last", "player_goal_scorer_anytime"]
_NHL_ALT = [m + "_alternate" for m in ("player_points", "player_assists", "player_power_play_points", "player_goals",
            "player_shots_on_goal", "player_blocked_shots", "player_total_saves", "player_faceoffs_won", "player_time_on_ice")]
_SOC_P = ["player_assists", "player_first_goal_scorer", "player_goal_scorer_anytime", "player_last_goal_scorer", "player_shots",
          "player_shots_on_target", "player_to_receive_card", "player_to_receive_red_card", "player_shots_alternate",
          "player_shots_on_target_alternate", "player_assists_alternate"]
_SOC_G = ["btts", "draw_no_bet", "double_chance", "h2h_h1", "totals_h1", "spreads_h1", "btts_h1", "alternate_totals_corners",
          "alternate_spreads_corners", "alternate_totals_cards", "corners_1x2", "correct_score", "halftime_fulltime"]

EVENT_MARKETS = {
    "basketball_nba": _ALT_GAME + _PERIODS_BB + _NBA_P + _NBA_ALT,
    "basketball_ncaab": _ALT_GAME + [m for m in _PERIODS_BB if "_q" not in m] + _NBA_P[:12] + _NBA_ALT[:6],
    "americanfootball_nfl": _ALT_GAME + _PERIODS_BB + _NFL_P + _NFL_ALT,
    "americanfootball_ncaaf": _ALT_GAME + _PERIODS_BB + _NFL_P + _NFL_ALT[:12],
    "icehockey_nhl": _ALT_GAME + _PERIODS_HK + _NHL_P + _NHL_ALT,
    "baseball_mlb": _ALT_GAME + _INN + _MLB_P + _MLB_ALT,
    "soccer_epl": _SOC_G + _SOC_P, "soccer_uefa_champs_league": _SOC_G + _SOC_P, "soccer_usa_mls": _SOC_G + _SOC_P,
    "mma_mixed_martial_arts": [],
}
