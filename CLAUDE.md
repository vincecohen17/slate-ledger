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
- `engine/context.py` verified news Claude's pick runs write to the data repo `context/<YYYY-MM-DD>.json`
  (status / minutes / goalie / starter / note entries; schema in the module docstring) plus NFL kickoff weather
  (Open-Meteo); applied to the models before "logic agrees" is decided. NHL back-to-backs and NBA rest are in the models.
- `engine/tracker.py` model report card: snapshots model vs sharp fair before each game (`track/open/`), grades after
  (`track/graded/<sport>.jsonl`), fits per-market calibration a, b and model trust weight w (`calib/params.json`), and
  applies the calibration to every model probability on each run. Reports page shows the card.
- `engine/modelstats.py` deep model statistics from track/graded (live rows and the bt_ backtests, separately): per
  sport|kind|market and pooled groups, log loss/Brier of model vs closing market, skill with cluster-robust SEs (by game id
  where graded rows carry gid, else by day; t only with 6+ clusters), walk-forward calibration + blend (weekly refits when
  there are many days) and its out-of-sample gain, reliability bins with Wilson CIs, Murphy decomposition, disagreement
  buckets (ROI of the model's side at the closing no-vig price), daily series. stats/modelstats.json + RTDB pre/mstats
  (Reports > Models in depth). Recomputed when the graded files change. `oos_weights` then sets calib/params.json w to 0
  unless the walk-forward blend gain is 2+ SE (keeps the in-sample w as wInSample): the pick runs read that w.
- `engine/policytest.py` walk-forward test of the decision rules on the live record: edge muting (kept vs muted by results)
  and gated vs in-sample blend weights (log loss by sport, day-clustered SE); stored in mstats.policy and stats/policytest.json.
  Mode policytest runs it alone.
- `engine/pickprices.py` real prices for picks: the first real price seen at each play-to venue (picklive each run ->
  latest/pick_first_prices.json), else the first raw pull after posting (snapshots/, posting time from git history), else
  goodAt; grade_picks stores gradedAt, gradeSrc, gradeVenue. Pick6 multipliers are estimated from DraftKings' vigged price
  (break-even / implied chance, capped 1x), here and in build.py.
- `engine/audit.py` (mode audit): every bet, promo, wallet entry and pick with its missing fields to the private data repo
  latest/audit/. `ledger.backfill_settled` (hourly) fills settled bets' clv/closeFair/closeEst, legResults, structured legs
  and leg start times, and fairAtLog from the close when none was logged (fairAtLogSrc "close"); never results or profits.
  Pick'em-only numbers get an estimated close (the player's closing mean at that number, closeEst). Bets keep per-leg
  results in auto.legs / legResults {market, side, point, player, gameId, y, close, stat, est}.
- `engine/finals.py` finished games (ESPN summary fetched once per game: final score, line score, team stats, scoring
  plays, box scores) plus our pre-game numbers from `track/open` and how they did; data repo `finals/<date>.json`,
  Realtime Database `pre/fin` (three days). Bets grade as soon as their game is in finals. Collected in auto/rebuild runs before grading.
- `site/js/promo.js` promo engine: 13 promo kinds (KINDS), plain-words parser, dollar value per promo (bonus-bet
  conversion measured on the board), optimizer (main lines only, venue price at most 10% over fair), pick'em entries,
  discounted lines via `site/js/dist.js` (port of build.p_over; keep in sync), settlement for the ledger. Special picks with their
  own multiplier (Chalkboard free pick 1.78x) pay the product of every pick's multiplier (promoMult / otherMult). The reader
  (parsePromo) is checked against `tests/promo_corpus.json` (71 phrasings with the expected fields): keep it passing, add to it.
  The Promos page shows a live readout of what it read and flags what's missing before saving. Unused special picks show at
  the top of the Bet Lab (`promoLegs()`); with one in the slip the Lab prices the slip as that app's entry (multipliers
  multiplied, or the app's table; per-pick multipliers typed in) and logs it with the promo attached.
- `engine/ledger.py` grades after each game: Vince's pending bets (writes bets/{id}.auto {result, clv, closeFair}; the site
  settles them with the promo math) and our picks (status, profit in units, clv, closeFair in picks/<date>.json).
  CLV = closing sharp fair x decimal price - 1, from the pre-game snapshot (tracker keeps them in `track/closed/` 21 days).
  Bets and picks name the bet with gameId, market, side, point (that side's own line), player, team.
- `engine/livebets.py` every live run: each leg of Vince's open bets gets its current stat from the ESPN box score (all sports,
  MLB included), projected finish and chance, written to `live/{gameId}` as betLegs {"<betId>#<leg>"}; old bets without
  market/side are read from their labels (also used by ledger grading). Live bets (bet.live, logged from the Live tab,
  live edges, or any bet on a game in play, with liveState score/clock) get liveCheck {fair, clv, mins}: the live fair
  5-30 minutes after the bet, which grading uses as their CLV (the pre-game close says nothing about a live price).
  Open prop picks get betLegs too ("<pickId>#0"). A game that has ended but still has open bets or picks stays in the live
  feed (doc final: true, ESPN's final state via scoreboards(post=...), final stats with no time left) until they grade; its
  teams come from live_state liveMeta, else the pre-game snapshot track/open/<gameId>.json, else the bet's matchup.
  First-period player props (_q1, _p1, _h1/_1h): the box score's total is the period total until the window ends, then the
  value is locked (latest/period_locks.json); a lock read at the break is exact and grades the leg, else it's left to settle.
  Period yardage markets are priced with the full-game yardage shape (build.p_over / dist.js pOver). Bet corrections Vince
  asks for go in the private data repo fixes/bets.json (switch a leg's side, or add a leg), applied once by ledger.apply_fixes.
  On the site, a leg on a line no book prices (a pick'em app's own number) is estimated from the player's sharp mean.
- Watcher (`run.py watch()`, mode watch, the live lane, up to 50 min a job): every minute until Vince's open bets and prop
  picks are done, app open or not. Each pass is a full live pull while the site is on a live screen (presence.live within
  3 min; the site dispatches "watch" every minute there) and otherwise free: ESPN only, the bet games' small live/g/<id>/m
  pieces (score, clock, bet legs, projected finish) via rt.publish(partial=True), no Odds API credits. Auto runs start it
  (_watch_wanted: a pending bet or pick on a game started in the last 5 h or starting within 15 min, no watcher running).
- `engine/rt.py` Realtime Database (free, no daily write count; 10 GB/month download allowance): the board the engine
  publishes under `pre/` (status, edges, reports, g/<id> a game with main lines, ga/<id> alternate lines, p/<id>/<market>
  main prop rows, pa/<id>/<market> alternate rows, mu/<id> matchups, fin/board + fin/d/<key> finals) and in-play data
  under `live/` (board, g/<id>/{m,e,l,p/<market>}), plus the site's `presence` heartbeat. Values are strings: JSON or
  "z:" + base64 deflate (the site inflates with DecompressionStream); only changed pieces are written. Alternates load
  only for an opened game or the Lab's switch, so opening the board downloads ~1.5 MB. Firestore keeps bets, promos,
  picks, previews and private/dispatch. Rules: `database.rules.template.json` (the engine puts them on the database).
- `engine/edgecard.py` the edges' report card: every flagged edge logged (track/edgelog/open.json) with venue, price, fair,
  edge, logic and lead time, its fair and venue price refreshed to the close, then graded (track/edgecard/<sport>.jsonl):
  CLV at the flagged / last / best price and the result; summary stats/edgecard.json (by venue, line type, market, edge
  size, logic, timing, sport, venue x type, venue x sport) shown on Reports, and edges re-ranked by it (shrunk, n/(n+150)).
  Also opening lines: first fair seen per main line/prop row (track/opens.json) stamped as open/openPt/openAt on lines and edges.
  Only edges flagged before the start are logged; quotes over 50 decimal or 3x the fair are feed errors; each edge is graded
  once (track/edgelog/graded.json). Kinds are judged by results: gap (won - expected), zRes, roi; CLV on model-priced lines
  grades the model against itself. rerank uses gap (a kind 5 pts under its fair drops ~25%).
- `engine/opps.py` arbs and middles across Vince's venues (all-in prices; middles priced with the alt-line fairs: EV = 1 - S +
  P(middle)); suspect ones (8%+ or a leg 12+ pts off fair) are flagged. Published as RTDB `pre/opps`, Edges > Arbs & middles.
  Freshness is our pull time (refresh_state lines/events), not the book's last_update: a market older than 1.5 refresh
  cycles (min 45 min) is left out; each leg has age, exch (price moves with size) and holdTo (worst price that still works).
  free: a middle with no loss either way; ranked first, always alerted (priority 5).
  Size: odds calls ask the feed for includeBetLimits, so every exchange quote (Kalshi, Polymarket US, Novig, ProphetX) carries
  avail (dollars on offer at that price) on prices, best, edges, opps legs and pickslive venues. Opps get maxPay / maxStake /
  maxProfit (what both legs fill at their prices) and thin (under $50 in all): arbs prefer a pair with real size, thin ones
  rank lower and never alert; edges with avail under $25 don't alert. Sportsbooks don't publish limits (avail absent).
  Each leg carries market/side/point/team so a logged pair grades itself. `site/js/arbs.js`: "Log both legs" (or Ledger >
  Log arb / middle, or pairing two bets in a bet's Edit sheet) saves both legs as normal bets sharing `group` {id, kind, i,
  window?, pMiddle?}; `unify()` folds each pair into one row (net profit, total stake, expected profit from the legs' fairs)
  for every ledger stat and the Analysis tab, so the losing leg never counts as a lost bet or skews venues, CLV or Kelly.
- Prediction-market prices (Kalshi, Polymarket, DK Predictions, FanDuel Predicts) also show the app's cents (util centsTag,
  fee backed out); odds inputs accept 52c / 52% / .52 (oddsFromInput adds the fee).
- Promo funds: bonus_bet / free_entry promos have funds "own" (a set free bet or entry the app hands you: the wallet's bonus
  balance is untouched, winnings land as cash; wallet.freeFunded) or "bonus" (out of the app's bonus balance; wallet
  promos, and promos saved before the field). The reader sets it from the wording; the Promos form has "Paid from".
- `engine/alerts.py` phone alerts via ntfy (topic only in the private data repo config/alerts.json; shared to the owner-only
  Firestore private/alerts so the site's Phone alerts card can show setup and send a test): arbs/middles, strong new
  edges (logic agrees, outside quiet hours), optional bet results; each item once (latest/alerts_state.json).
- Site sizing: Firestore `settings/owner` {bankroll, kelly, maxPct, unit} (Ledger > Analysis) drives the suggested stake on
  edges (half for model/estimated fairs), the log sheet's default stake and units in dollars; parlays down to one leg show a
  hedge to lock a result and its expected cost.
- Sports on bets: site `leaguesOf()` / `sportKeys()` in store.js (each leg's game league, else a team name in the label; saved as
  bet.leagues); `ledger.backfill_leagues` fills older bets hourly from track/open snapshots, finals, or player/team names in
  refs. A multi-sport parlay counts in each of its sports and in "Multi-sport".
- Ledger > Analysis (`views/analysis.js`) and Our picks (`views/picksdeep.js`), shared pieces in `views/deep.js` (calendar,
  reliability chart, histogram, sortable tables, CSV) and math in `site/js/stats.js` (mean/SE, Wilson, bootstrap ROI,
  log loss, Brier, logistic calibration fit, p-values). Days are the Pacific date of the bet's last game. ROI is on money
  risked (bonus bets and free entries count in profit, not the ratio). Reports > Models in depth is `views/models.js`.
- Wallet (Ledger > Wallet, `site/js/wallet.js` + `views/wallet.js`): Firestore `wallet/{id}` entries {venue, type: balance |
  deposit | withdrawal | bonus, cash/bonus or amount, at, note}. From each balance check-in it replays deposits, withdrawals,
  bonus credits and every bet placed/settled at that venue (bonus-funded = promo kind bonus_bet/free_entry: stake from bonus,
  winnings to cash) to know what each app should hold; later check-ins that differ are "unaccounted". Money in = the first
  check-in (its cash, open cash stakes, and bonus where it counts) + deposits; profit = withdrawals + value now - money in,
  so check-in differences count (shown as adjustments, with bets settled and bonus credited - staked as the breakdown).
  settings/owner.bonusCash[app] marks apps whose bonus works like cash (counted as money; converting it isn't profit).
  DK Pick6 shares the DraftKings account (accountOf). Bonus funds become a promo per app (id wb-<app>, wallet: true) whose
  amount follows the wallet's bonus balance (syncWalletPromos).
- `engine/picklive.py` every run re-checks each open pick's playTo venues (current price, pick'em line still listed, in range vs
  goodAt) -> RTDB pre/pickslive and latest/pickslive.json; the Picks screen shows it. Pick'em payouts: Underdog/Dabble real,
  PrizePicks standard = fixed 1.732/leg, DK Pick6 multiplier estimated from DraftKings' price with its margin (dfs estMult in props).
- `engine/refs.py` ESPN team logos/colors and player headshots (cached daily in the data repo `refs/`).
  `engine/reports.py` per-book "where the books are off" stats (snapshot + cumulative in `stats/`).
- `site/` Firebase Hosting app, plain ES modules (Google sign-in; Firestore rules allow only the owner email,
  filled at deploy time from the private data repo `config/access.json`). Screens: Board, Game (overview,
  lines, props, pick), Edges, Picks, Bet Lab (parlays to 20 legs, same-game correlation), Promos (plain-words
  parser + optimizer), Ledger (bets; Analysis: skill vs luck, rolling CLV, calibration, venue x type heatmaps, timing,
  sizing vs Kelly, promo value, swings; Our picks incl. by pillars/model gap), Reports (edge report card, books, models). Math for the lab/promos lives in `site/js/lab.js`.
- Picks: Claude writes `picks/<YYYY-MM-DD>.json` (a list) to the data repo; every engine run syncs them to
  Firestore `picks/{id}`. Fields: id, date, gameId, league, matchup, commence, selection, player?, tier
  (bet|floor|likely), units, status, fair, fairAm, goodAt, greatAt, playTo[{venue,name,price,line?,inRange}],
  case (1-2 paragraphs, blank line between), pillars{model,advanced,matchup,role,context,market: support|neutral|against},
  risk (a paragraph on what could go wrong), marketFair, modelP, keyStats[{label,value,note?,tone}], confirm[],
  market (Odds API key), side, point (the side's own line), player?, team?, early? (posted the night before by the early look,
  earlyAt), checks {logic, model, price} and logicCase (bet tier needs all three; price-only edges are never picks); graded by the engine: status, profit, clv, closeFair
- `.github/workflows/pull.yml` engine run (manual or push to `engine/run-request`; live cron after the 5M upgrade).
  `deploy.yml` deploys site + rules on changes to `site/`, `firebase.json` or the rules template.
- Private data repo `vincecohen17/slate-ledger-data`: `latest/` (board.json, props.json, edges.json, log_<mode>.txt: the
  last run's log lines, last_error.json when a run failed),
  `snapshots/<date>/` raw API payloads (gz) for closes and backtests, `credits.jsonl`.

## Rules
- This repo is PUBLIC. Never commit keys, odds data, picks, bets or the owner's email. Logs print counts only.
- Keys live in GitHub secrets: ODDS_API_KEY, FIREBASE_SERVICE_ACCOUNT, DATA_REPO_TOKEN.
- Firestore docs store their body as a JSON string in `payload`; writes are skipped when the hash is unchanged.
- Firebase is on the free Spark plan. Firestore allows 20k writes a day: keep its writes low (one doc per game, one per
  game+prop market; far-off prop docs at most every 30 min). Anything that changes every minute goes to the Realtime Database.
- Kalshi/Polymarket prices are always shown all-in (ask + fee).
- Never place, edit or cancel bets or move money anywhere.
