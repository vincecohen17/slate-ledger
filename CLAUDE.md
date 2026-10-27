# Slate Ledger — notes for Claude

Owner: Vince (Burbank, CA). Goal: beat the books with sharp-market pricing, sports logic and promos.

## Layout
- `engine/` Python. `run.py` pulls The Odds API, `build.py` computes sharp no-vig fair per line/prop,
  all-in venue costs (Kalshi/Polymarket fees), edges, stale flags, pick'em line judgments.
  `config.py` holds sports, markets, book weights, venues, budgets. Tests: `python engine/tests/test_build.py`.
- Models: `model_nba.py`, `model_nhl.py` (MoneyPuck + goalie GSAx), `model_nfl.py` (nflverse EPA + player shares),
  `model_cfb.py` (opponent-adjusted ridge ratings), `model_extra.py` (soccer Poisson, tennis Elo), `model_mlb.py` (Statcast per-PA odds-ratio model: platoon,
  park, weather, lineups; exact batter/pitcher prop distributions; F5 and team totals).
  `matchup.py` builds matchup docs, stamps model probabilities on lines/props, calibrates prop levels to market,
  and builds projected lineups. `stats.py` collects ESPN box scores/injuries (NBA, WNBA, NHL, NFL, CFB, nine soccer leagues), MoneyPuck, nflverse,
  ESPN tennis; `stats_mlb.py` collects Baseball Savant (custom/expected leaderboards, splits, arsenals, park factors)
  and MLB StatsAPI (schedule with lineups/probables/weather, rosters, handedness, game logs).
- `run.py` modes: auto (every 5 min, app-driven: presence/owner heartbeat from the site, staleness tiers, close
  capture on the paid plan; config.PLAN = auto reads the plan from the Odds API quota), refresh (a forced auto pull,
  used by Claude's pick runs), mock, rebuild (raw cache in latest/raw), stats, probe. Event pulls request the full
  market catalog (config.event_markets) and remember markets the API rejects (refresh_state badMarkets).
- `engine/refs.py` ESPN team logos/colors and player headshots (cached daily in the data repo `refs/`).
  `engine/reports.py` per-book "where the books are off" stats (snapshot + cumulative in `stats/`).
- `site/` Firebase Hosting app, plain ES modules (Google sign-in; Firestore rules allow only the owner email,
  filled at deploy time from the private data repo `config/access.json`). Screens: Board, Game (overview,
  lines, props, pick), Edges, Picks, Bet Lab (parlays to 20 legs, same-game correlation), Promos (plain-words
  parser + optimizer), Ledger (bets, P/L chart), Reports. Math for the lab/promos lives in `site/js/lab.js`.
- Picks: Claude writes `picks/<YYYY-MM-DD>.json` (a list) to the data repo; every engine run syncs them to
  Firestore `picks/{id}`. Fields: id, date, gameId, league, matchup, commence, selection, player?, tier
  (bet|floor|likely), units, status, fair, fairAm, goodAt, greatAt, playTo[{venue,name,price,line?,inRange}],
  case, pillars{model,advanced,matchup,role,context,market: support|neutral|against}, risk, confirm[], clv?, profit?
- `.github/workflows/pull.yml` engine run (manual or push to `engine/run-request`; live cron after the 5M upgrade).
  `deploy.yml` deploys site + rules on changes to `site/`, `firebase.json` or the rules template.
- Private data repo `vincecohen17/slate-ledger-data`: `latest/` (board.json, props.json, edges.json),
  `snapshots/<date>/` raw API payloads (gz) for closes and backtests, `credits.jsonl`.

## Rules
- This repo is PUBLIC. Never commit keys, odds data, picks, bets or the owner's email. Logs print counts only.
- Keys live in GitHub secrets: ODDS_API_KEY, FIREBASE_SERVICE_ACCOUNT, DATA_REPO_TOKEN.
- Firestore docs store their body as a JSON string in `payload`; writes are skipped when the hash is unchanged.
- Firebase is on the free Spark plan. Keep writes low (one doc per game, one per game+prop market).
- Kalshi/Polymarket prices are always shown all-in (ask + fee).
- Never place, edit or cancel bets or move money anywhere.
