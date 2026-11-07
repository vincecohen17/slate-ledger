# Slate Ledger — notes for Claude

Owner: Vince (Burbank, CA). Goal: beat the books with sharp-market pricing, sports logic and promos.

## Layout
- `engine/` Python. `run.py` pulls The Odds API, `build.py` computes sharp no-vig fair per line/prop,
  all-in venue costs (Kalshi/Polymarket fees), edges, stale flags, pick'em line judgments.
  `config.py` holds sports, markets, book weights, venues, budgets. Prop count shapes: build.DISPERSION (variance = k x mean; NBA
  points 2.8 / PRA 3.2 since Nov 6, rebounds 1.3 since Nov 7, fit on alt-line outcomes; in-play remaining-stat chances, WNBA / NCAAB
  and double-doubles keep DISPERSION_PREV; our stat model's own mean uses DISPERSION_MODEL where it needs another width, rebounds 1.5,
  via p_over(..., model=True) in matchup); site/js/dist.js mirrors the market side. Tests: `python engine/tests/test_build.py`.
  Backtests: mode backtest (env BT_SPORTS=comma list reruns only those sports; the rest keep their saved rows).
- Models: `model_nba.py`, `model_nhl.py` (MoneyPuck + goalie GSAx), `model_nfl.py` (nflverse EPA + player shares; QB passing from
  the passer's own last-10 volume x efficiency), `model_cfb.py` (opponent-adjusted ridge ratings shrunk to FBS/FCS tiers), `model_extra.py` (soccer Poisson, tennis Elo), `model_mlb.py` (Statcast per-PA odds-ratio model: platoon,
  park, weather, lineups; exact batter/pitcher prop distributions; F5 and team totals).
  `matchup.py` builds matchup docs, stamps model probabilities on lines/props, calibrates prop levels to market,
  and builds projected lineups. `stats.py` collects ESPN box scores/injuries (NBA, WNBA, NHL, NFL, CFB, nine soccer leagues), MoneyPuck, nflverse,
  ESPN tennis; `stats_mlb.py` collects Baseball Savant (custom/expected leaderboards, splits, arsenals, park factors)
  and MLB StatsAPI (schedule with lineups/probables/weather, rosters, handedness, game logs).
- `run.py` modes: auto (every 5 min, app-driven: presence/owner heartbeat from the site, staleness tiers, close
  capture on the paid plan; config.PLAN = auto reads the plan from the Odds API quota), refresh (a forced auto pull,
  used by Claude's pick runs), mock, rebuild (raw cache in latest/raw), stats, probe. Event pulls request the full
  market catalog (config.event_markets) and remember markets the API rejects (refresh_state badMarkets).
- Hot games (run._hot -> latest/hot.json, each auto run): open picks, Vince's pending bets (Firestore, read at most every
  15 min, cached in refresh_state hotBets), arbs/middles (latest/opps.json, not suspect) and real edges (2%+, not muted /
  avoid / est / model, under 25%), up to HOT_MAX 60 (picks and bets first). Their sport's lines and their event markets
  refresh on config.HOT_STALE (4 min inside 1 h ... 90 min past 36 h; extended markets at 2x when something on the game
  sits there) whether or not the site is open, until the day has used HOT_DAY_SHARE of its paced cap; hot events go
  first in the queue. The watcher dispatches an auto run every HOT_ASK_MIN (5) min while hot games start within 18 h
  (and stays up for them); an auto run that finds everything fresh spends no credits.
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
  Mode policytest runs it alone. Also the logic override (mutingLogic: a 5%+ edge the model agrees with is un-muted where its
  league x agrees x 5%+ cell has held its fair over 100+ graded, e.logicBacked; one it disagrees with is muted where that cell
  has z <= -2; edgecard dim logicSize) and residual: a walk-forward residual model logit(close) + b . features per sport|kind
  on live rows that carry tracker features x {mm market mean, md/mr model mean, op open fair, opPt, nb, fw, disp books' no-vig
  sd}; reported only (Reports > Models), wired into fairs only if it beats the close with t >= 2.
- Model support (build.edges): support = (modelP - venue cost) / (fair - cost), where the model sits from the price (0) to our
  fair (1); supBand below (<0) | between (0-1) | near (1-1.5) | beyond (1.5+). On 5%+ edges between/near won, below and beyond lost
  (beyond = model and fair share an error, mostly model-shaped alt fairs). Rank weight between 1.5 / near 1.2 / beyond 0.6 /
  below 0.4 (else the logic flag's 1.5 / 0.4); alerts skip below/beyond. edgecard: graded rows carry gid, side, pt, player,
  mp and sup (supSrc "flag"; rows before Nov 3 were backfilled from the closing model, supSrc "close"); dims support,
  supportSize, supLeague; rerank's support override (league x band x 5%+ cells, same 100 / z -2 rule; supportBacked) wins
  over the logic override when an edge has a model number. policytest.logic_study -> mstats.policy.logic (logic x size with
  won-fair split into fair move + beat the close, support curve, sport/kind x band, matched agrees-disagrees, daily) and
  mutingSupport; site Reports > Model logic (`views/logic.js`). Model fixes go in the data repo stats/model_changes.json
  (before / after backtest log loss), shown at the top of Models in depth.
- Self-correcting fair (`engine/fairadj.py` -> calib/fairadj.json, applied in build._finish_line via build.FAIR_SHIFT, pre-game
  modes only): a logit shift for the tracked side (Over/Yes/home) per sport|kind and market, recency weighted (half-life 14 d,
  backtest rows a 0.15-weight prior), live only for sport|kinds whose walk-forward gain vs the raw fair is 2+ SE with 200+ scored rows that carried a shift (NBA props:
  overs win less than the even-margin de-vig says). Lines keep fairRaw / fairShift; tracker snapshots keep the raw fair as f
  (fits stay on raw) plus fa; ledger CLV uses fa. Refit when graded rows change. Edge card dim fairShift.
- Recency: edgecard _agg adds gapR / roiR / nR (half-life 5 days); rerank leans half on gapR. Playbook (`engine/playbook.py` ->
  stats/playbook.json, Reports data "playbook"): lean (cells with 150+ graded, positive long-run and recent ROI) and avoid
  (z <= -2.5, losing) across kind, venue x type, venue, timing, edge size, support, logic, sport, market, fair, venue x sport,
  plus plain rules (support, logic by sport, fair correction, model trust, picks). playbook.tag stamps e.lean / e.avoid on
  edges (rank x1.1 per lean up to 1.3, x0.8 per avoid down to 0.5; alerts skip avoid). Edges page: "What's working now" card,
  On the playbook / Model backs it filters, "The record says skip" section. The pick prompts read the playbook first.
  policytest.ai_study -> mstats.policy.ai: our picks by pillar / check / tier / pillar count / timing / price source, and edges
  and model skill in games with vs without verified context (Reports > AI flags and news).
- Steam (`edgecard.steam`, each run): track/fairhist.json keeps 90 minutes of sharp fair per main line/prop row; an edge whose
  fair moved toward its side 20-75 min ago gets fairMove (and steam: true at 2+ pts). The edge log keeps it ("steam") and the
  report card grades it (dim steam). No boost until it's proven. `leadlag.py` (mode leadlag -> stats/leadlag.json): which books
  move first and stale-after-steam vs static gaps from the raw snapshots (Nov 2026: too sparse to say).
- Market behavior (`engine/market.py`, each pre-game run via market.run, before the edge log): folds every raw pull
  (snapshots/<day>/*_lines.json.gz) into per-game book histories (track/market/g/<gameId>.json: book -> h2h/spreads/totals
  -> [[t (the book's own last_update, minutes), pointA, decA, decB(, draw)]], A = home / Over; only sharp, soft-US and venue
  books). summarize(): the sharp consensus path (Pinnacle, Circa, LowVig, BetOnline; opens once two quote; quotes with an
  implausible margin dropped; exchanges left out because back-only prices are junk on thin markets), open / now, mv = the
  move toward A on one chance scale (point moves at SLOPE per sport; spreads skipped where they're fixed puck/run lines),
  first movers, sharpLed, softLagMin, lagging (Vince's venues still at the old number). DraftKings' public splits from DK
  Network (DK_SPLITS, tb_eg per league, 10 games a page, every 20 min; parse_dk; matched to our games by team words with
  soccer aliases) -> latest/splits.json + history track/market/s/<gameId>.json. Per side signals (side_signals): mv, sl, rlm
  (moved toward a side with <= 40% of bets), div (money % - bets %), pub (70%+ of bets); 0/100 splits are thin and give no
  signal. Stamped on game docs (doc.mkt with path), edges (e.mkt, e.mktTags; logged on the edge card: dims mktMove,
  mktSplits, also playbook dims) and latest/market.json (the pick runs read it); RTDB pre/mkt (site S.mkt). Grading
  (market.grade on finals): every main-market side with its fresh close (last sharp quote <= 30 min before the start;
  older closes are counted as staleClose and left out, since a line that kept moving after our last pull looks like
  momentum) -> track/market/graded/<league>.jsonl, stats/marketcard.json (buckets clustered by game), Reports > Market
  signals. Graded games' files are pruned. Mode marketbackfill rebuilds from all saved pulls; splitsprobe saves raw pages.
  Nov 5 first read (88 games with fresh closes): moves are priced in by the close; the value is a venue that hasn't followed.
  Site: game page Market tab (views/market.js: sharp path chart, first movers, venues still at the old number, DK splits),
  Edges > Market moves.
  Why each edge is on the board (market.explain -> e.why {k, t, s, f}): lag = the sharp books moved toward the side and this
  venue's own history shows it followed under half (main numbers; "Sharp books moved X -3 to -4.5 (Pinnacle first, 2:14 PM);
  Novig is still at -3"), move = moved toward it (alts, props by their sharp fair since open, or a venue that partly
  followed), static = no move behind the gap, against = the market moved away. Shown first on every edge card (whyBlock with
  model / news / playbook / splits / timing reasons), pick cards (p.why from the run, else the board's edge now), the Board's
  "Driven by the market now" strip, Edges "Driven by the market right now" + Market-driven filter, the Lab (a market-driven
  group in Best bets; each slip leg shows its value at the chosen price and its reason). Logged: bets carry why / whyText
  (ledger group "Market reason"), the edge card logs wk (dim "why", also a playbook dim), picks group by why on Our picks;
  whyRecord shows the record by reason as if every flagged edge were bet flat at its flagged price (Edges, Reports).
  Alerts: "Sharp move, <venue> lagging" (lag edges, 2%+, not muted / skip / thin), and edge alerts carry the reason.
  Bet Lab price boxes (custom leg price, the venue's parlay odds) read any format via oddsFromInput and show the conversion.
- In-play on every screen: the live run (run.live, full pulls) also runs opps.find(live=True) on in-play games (no
  commence filter, ages 0) -> live board "opps" (top 40, o.live). Edges shows "Live now" (S.live.edges + liveGames edges,
  deduped, same filters) above the pre-game list, Arbs & middles a "Live now" section (LIVE pill, never alerted; the tab
  count includes them). The Lab catalog swaps in-play games' pre-game lines/props for their live ones (labels "LIVE · ",
  leg.live, state, now), lists open picks on games in play with live prices, and a "Live now" best-bets group (liveBets).
  Live screen: under each game card a "Log a live bet" row (views/live.js quickLog): ML and total at the best live price,
  More -> spread, team totals, a player-prop search (only the results box re-renders, so typing keeps focus) and
  "Something else" (a blank live bet on that game); every button opens logSheet with source "live", live true (score and
  clock saved as liveState). Picks: "Our picks in play" (projectedCard in units over open picks with a live game state,
  projected.pickAsBet shared with the Ledger) and a live strip on each pick card (score, the stat's progress from betLegs
  "<pickId>#0", live chance, expected units) until it grades.
  Live alt lines: for the game open on the site (presence.game) and games with open bets, the live run pulls every core
  event market (alternates, team totals, plus player props in the props sports) in one event call every
  LIVE_PROP_EVERY_MIN, keeps the payload (latest/live_props/<id>.ev.json, reused up to 20 min) and folds the lines into the
  live game (build.add_extra_lines). Game page: in play, Lines and Best bets use the live lines; every side has Log /
  Log live at the best price and each venue row logs at its price (ladder spec); "Log any line" (game.js anyLine: type,
  period, side, number) logs any number, listed or not, named with market (alternate_* when off the main number), side
  and own point so it grades from the score.
  Report pages are tabbed (deep.tabify: sections grouped by their .sec-title into a sticky tab bar, hide/show only,
  remembered per session): Reports (Edges, Books, Market, Model logic, AI & news, Models, Backtests), Ledger > Analysis
  (By day, CLV & fairs, Breakdowns, Sizing & promos, Bets), Ledger > Our picks (Open, Fast read, By day, Forecasts,
  Breakdowns, Every pick).
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
  (parsePromo) is checked against `tests/promo_corpus.json` (81 phrasings with the expected fields): keep it passing, add to it.
  The Promos page shows a live readout of what it read and flags what's missing before saving. Unused special picks show at
  the top of the Bet Lab (`promoLegs()`); with one in the slip the Lab prices the slip as that app's entry (multipliers
  multiplied, or the app's table; per-pick multipliers typed in) and logs it with the promo attached.
- `engine/ledger.py` grades after each game: Vince's pending bets (writes bets/{id}.auto {result, clv, closeFair}; the site
  settles them with the promo math) and our picks (status, profit in units, clv, closeFair in picks/<date>.json).
  CLV = closing sharp fair x decimal price - 1, from the pre-game snapshot (tracker keeps them in `track/closed/` 21 days).
  Bets and picks name the bet with gameId, market, side, point (that side's own line), player, team.
  Period game lines (_q1-_q4, _h1/_h2, _p1-_p3, 1st_N_innings; hockey _q1 = P1) grade from the final's line score
  (ledger._period_score); a 4th quarter or 2nd half that went to overtime is left ungraded (books differ on OT).
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
  Parlays across games or days: grade_bets writes legsDone {index: {y, stat, void}} on the open bet as each leg's game goes
  final, and the site shows those legs as won or lost (projected.legRaw). Bets carry lastGame (their last game's start,
  written at grading and backfilled hourly): the Pacific day of that game is the bet's day everywhere (analysis gameDay,
  the calendar, "today" on the projected card), however late it was graded. The Analysis calendar also shows each day's
  open bets' projected profit (≈). Clock: ESPN's displayClock without a colon is seconds (live._clock_min); reading "45.3"
  as minutes made every projection spike in the last minute of each period. NHL power-play points aren't in the box score:
  stats.add_pp_points counts them from the summary's power-play scoring plays (live box and daily stats); also mapped:
  NBA field goals and free throws, NHL time on ice.
- Watcher (`run.py watch()`, mode watch, the live lane, up to 50 min a job): every minute until Vince's open bets and prop
  picks are done, app open or not. Each pass is a full live pull while the site is on a live screen (presence.live within
  3 min; the site dispatches "watch" every minute there) and otherwise free: ESPN only, the bet games' small live/g/<id>/m
  pieces (score, clock, bet legs, projected finish) via rt.publish(partial=True), no Odds API credits. Auto runs start it
  (_watch_wanted: a pending bet or pick on a game started in the last 5 h or starting within WATCH_AHEAD_H = 6 h, no watcher
  running). Before the first game starts it waits (a check every 5 min, presence every minute); at 50 min it dispatches the
  next watcher itself (_dispatch_watch(chain=True)), because GitHub runs the */5 cron only every few hours (Nov 2026: ~6 h).
  Live sports: NBA, WNBA, NCAAB (D-I scoreboard groups=50, halves 2 x 20), NHL, NFL, NCAAF, MLB, soccer, tennis.
  The watcher also stays up while any board game starts within 6 h and: (a) closes: a game starting within CLOSE_ASK_MIN
  (12) minutes whose prices are older than CLOSE_STALE_MIN gets an auto run (_closer), so closes don't depend on the cron;
  (b) settlement: a watched game that leaves play (final) gets a rebuild run that collects the final and grades its bets
  and picks (_dispatch). NHL power-play points: nhlapi.py (api-web.nhle.com play-by-play: situationCode skaters, a pulled
  goalie's extra attacker excluded, shootouts excluded; scorer + both assisters), ESPN plays as fallback; mode ppprobe
  checks both against the NHL box score (Nov 5: 12 of 12 games exact).
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
  Significance is clustered by game (edgecard.clus: games, zG, roiT, roiSE; rows without gid cluster by day x sport): the same
  prop at several venues or an alt ladder is one bet, and row-level z ran 2-8x too big (Nov 5: all edges z -11.7 row-level,
  -4.1 clustered). Every confidence the site shows, the playbook (lean: 20+ games and clustered ROI t >= 1.5; avoid: zG <= -2)
  and the logic study (zG, clustered roiSE and matched z) use it; zRes / policytest z stay row-level only for the muting and
  override rules, whose thresholds the walk-forward test chose on that scale. Pick summaries (policytest.ai) count CLV on fresh
  closes only and carry clvCal.
- `engine/opps.py` arbs and middles across Vince's venues (all-in prices; middles priced with the alt-line fairs: EV = 1 - S +
  P(middle)); suspect ones (8%+ or a leg 12+ pts off fair) are flagged. Published as RTDB `pre/opps`, Edges > Arbs & middles.
  Freshness is our pull time (refresh_state lines/events), not the book's last_update: a market older than 1.5 refresh
  cycles (min 45 min) is left out; each leg has age, exch (price moves with size) and holdTo (worst price that still works).
  free: a middle with no loss either way; ranked first, always alerted (priority 5).
  Size: odds calls ask the feed for includeBetLimits, so every exchange quote (Kalshi, Polymarket US, Novig, ProphetX) carries
  avail (dollars on offer at that price) on prices, best, edges, opps legs and pickslive venues. Opps get maxPay / maxStake /
  maxProfit (what both legs fill at their prices) and thin (under $50 in all): arbs prefer a pair with real size, thin ones
  rank lower and never alert; edges with avail under $25 don't alert. Sportsbooks don't publish limits (avail absent).
  Each leg carries market/side/point/team so a logged pair grades itself. Reliability (`opps.annotate`, each run): opps keep
  key, pulls / seenMin (latest/opps_seen.json, consecutive pulls), legs carry bookAge (the book's own last update) and proxy
  (DK Predictions / FanDuel Predicts prices borrowed from their sportsbooks); Vince's check-ins (Firestore oppcheck/{id}:
  verdict real / fake, badLegs) pool into a real rate per venue x prop/line. confirmed = 2+ pulls, no proxy leg, book prices
  under 30 min old, not suspect or thin, no venue-kind with 3+ checks under 35% real; o.why lists what failed. Only confirmed
  opps rank first and alert; the Arbs tab shows the rest under "Unverified" with check-in buttons. `site/js/arbs.js`: "Log both legs" (or Ledger >
  Log arb / middle, or pairing two bets in a bet's Edit sheet) saves both legs as normal bets sharing `group` {id, kind, i,
  window?, pMiddle?}; `unify()` folds each pair into one row (net profit, total stake, expected profit from the legs' fairs)
  for every ledger stat and the Analysis tab, so the losing leg never counts as a lost bet or skews venues, CLV or Kelly.
- Prediction-market prices (Kalshi, Polymarket, DK Predictions, FanDuel Predicts) also show the app's cents (util centsTag,
  fee backed out); odds inputs accept 52c / 52% / .52 (oddsFromInput adds the fee) and decimal 2.5x / x2.5 / a bare number under 100.
- Every price on the site shows three ways, American · implied % · decimal ("+150 · 40.0% · 2.5x"): util odds(am) / oddsP(p)
  are the HTML (American, the other two small beside it; stacked in narrow cells by the .o3 CSS), oddsTxt / oddsTxtP the plain
  text for titles, options and textContent; fairs use oddsP(p) so the % is the exact no-vig chance. am() is American only,
  kept for inputs and placeholders. New price displays use these, never am() alone.
- Promo funds: bonus_bet / free_entry promos have funds "own" (a set free bet or entry the app hands you: the wallet's bonus
  balance is untouched, winnings land as cash; wallet.freeFunded) or "bonus" (out of the app's bonus balance; wallet
  promos, and promos saved before the field). The reader sets it from the wording; the Promos form has "Paid from".
- Non-sports promos: kind "manual" ("Other promo (log it yourself)": what, cost, value, pays out as cash | bonus; venue may
  be "other" with an app name). The reader sends non-sports wording (referral, sign-up, casino, spins, cashback, election,
  finance) there. No optimizer; "Log what came in" saves cash results as a settled ledger bet (manual: true, source
  promo-manual, league "Other") and bonus credits as a wallet bonus entry, and can mark the promo used. The Ledger's log
  sheet also takes an "Other app" venue; bets with no game, legs or pick are manual: true, and the engine never grades them.
  For those the sheet shows Category (Sports, or NONSPORT: Politics / Economics / Culture / Weather / Other -> league, leagues,
  nonSports), Result (open / won / lost / push / sold early) and Paid out $ (total back): profit = paid - stake, cashedOut,
  paidOut; odds optional once a payout is typed (a win's price = paid / stake). The ledger's W/L taps on them open the sheet
  prefilled instead of computing from the odds.
- CLV accuracy. Snapshots carry linesAt / eventAt (when their prices were pulled); grading stores closeAge (minutes from
  that pull to the start) on bets (auto.closeAge), picks and graded edges (ca). CLV counts only fresh closes (closeAge <= 30,
  or unknown on older rows) and never estimated closes (site stats.clvOk, edgecard CLOSE_OK_MIN). Mismatched closes (20+
  points from the fair at the flag: feed errors or the wrong side) are dropped everywhere (edgecard._ok, policytest,
  changes, closecal). Calibrated CLV (`engine/closecal.py` -> calib/closecal.json): fit per kind x line type (main / alt /
  model: a fair moved by our stat model or estimated) P = sigmoid(a + b logit(close)), shrunk by n/(n+500), and used only
  where walk-forward raw CLV overstated realized ROI by 2+ game-clustered SE and the correction closes the gap (Nov 5: on for
  model-priced props, raw +8.1% vs ROI -3.9%; main props raw +5.1% vs ROI +3.9% stay raw). The first version fit per kind
  only, so alt-line behavior dragged main-line bets (a 60.8% closing moneyline read as 48%; Vince's bets -14% instead of
  +3%). Bet legs carry t (grade_leg: main = the snapshot row with the most fairWeight for that market, alt otherwise, model
  when estimated); closecal.VERSION bumps make bets and picks recompute clvCal (clvCalV). clvCal on edges, bets, picks. Headline CLV on the site is the
  calibrated one with raw beside it.
- Change log (`engine/changes.py`): data repo stats/change_log.json (logic, picks, measure) + stats/model_changes.json
  (model fixes with backtests) -> stats/changes.json with each change's 14-day before/after on edges (won vs fair, CLV
  cal., ROI), picks (CLV, flat) and model skill vs the close, with t; mstats.changes + mstats.timeline (daily). Reports >
  Models in depth "What changed, and what happened after" (timeline with markers + table). Add an entry for every
  model, logic, pick-rule or measurement change.
- Promo desk (`engine/promodesk.py`): Promos > "Send to Claude" saves Firestore promoreq/{id} {text, status, thread};
  each auto / rebuild run writes waiting requests to the data repo promodesk/inbox.json (+ promos.json) and applies the
  desk's answers from promodesk/outbox/<reqId>.json {reqId, action create | question | failed, promo, text, atThread}
  (create saves promos/{id} with fromDesk). The hourly desk task builds promos with the site's own engine and checks them
  under Node: `node tests/node/promo_check.mjs promo <file>` (kind, missing fields via promo.missingFields, describe,
  settlement) and `... corpus` (the reader, 81/81). It may add a promo kind to site/js/promo.js when nothing fits, only
  with the corpus passing.
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
  Bet Lab links: `addToLab(spec, opts)` (views/lab.js) takes a bet named like the ledger (gameId, market, side, own point, player,
  team, pickem app) from any screen ("+ Lab" on edge cards, game sides and prop chips, "Open in Bet Lab" on picks, "Lab" on promo
  suggestions with the promo, boost and stake attached); the slip persists in sessionStorage (sl.labslip). The Lab lists our open
  picks, best bets (S.edges minus muted / skip / below-beyond / model or estimated fairs / quotes 15%+ or 1.5x past fair) and
  suggested builds per venue (best single, 2- and 3-leg across games) ranked by full-Kelly log growth, and shows the chance
  today's open bets finish up with and without the slip (projected.summary) plus a parlay-vs-straights comparison.
- Picks: Claude writes `picks/<YYYY-MM-DD>.json` (a list) to the data repo; every engine run syncs them to
  Firestore `picks/{id}`. Fields: id, date, gameId, league, matchup, commence, selection, player?, tier
  (bet|floor|likely), units, status, fair, fairAm, goodAt, greatAt, playTo[{venue,name,price,line?,inRange}],
  case (1-2 paragraphs, blank line between), pillars{model,advanced,matchup,role,context,market: support|neutral|against},
  risk (a paragraph on what could go wrong), marketFair, modelP, keyStats[{label,value,note?,tone}], confirm[],
  market (Odds API key), side, point (the side's own line), player?, team?, early? (posted the night before by the early look,
  earlyAt), checks {logic, model, price} and logicCase (bet tier needs all three; price-only edges are never picks), evidence {lean: [playbook cells], support}; graded by the engine: status, profit, clv, closeFair, clvCal, closeAge,
  flat (1u return at the graded price). Tiers: bet, floor (0.25u), lean (0.1u, tracked volume), likely (0u); every tier grades
  (likely too, since Nov 4). Pick runs (scheduled tasks): morning 7:37, pregame 1:53, and an evening run for late games and
  overnight tennis/soccer (needs Vince's approval to enable). They aim for 1 pick per 2 games across all sports (8+ a day), work
  niche sports with logic-led cases (never bet tier under 5 books), and read the fast read first: a sport or tier with 15+
  graded and CLV t <= -1.5 is cut to leans. Portfolio (runbook step 6b, Nov 7): a candidate scan per league x kind
  (game lines / props, counted by game not by flag), picks in rounds (best qualifier of each cell first, then by value),
  a day cap of half the bet/floor/lean picks per league x kind while another cell qualifies (60% for a cell the fast read
  says is working), at most 2 props per game, capped candidates to the likely list; messages open with the Mix line. The
  Picks page shows the day's mix (pill flagged past half). Ledger > Our picks "Fast read": CLV (the early signal) by sport and tier, t,
  beat-the-close, flat ROI, verdict (15+ picks, |t| >= 1.5) and picks still needed to reach t = 2.
- Scheduled Claude tasks (Pacific): seven pick runs: 4:41 early (European soccer + tennis near start, re-checks the
  evening's early picks), 7:37 morning, 10:43 midday, 1:53 pregame, 3:31 late pregame (4-6:30 PM starts), 5:41 evening
  (late games + overnight tennis/soccer), 9:17 night (overnight + tomorrow's early games). Each prompt is a short scope
  line plus "follow runbook/picks.md" in the private data repo: the shared pick procedure (data, market behavior step 3d,
  research, checks, tiers, venues, schema, publishing, message). Change pick rules there, not in the seven prompts; promo desk hourly at :23 from 8 AM to 11 PM; settlement and live audits twice a
  day, 1:17 AM and 12:17 PM (audit mode, recomputes results from finals, checks watcher/close capture/grading timeliness,
  fixes via engine or fixes/bets.json; the midday pass covers overnight/morning games and skips what the nightly one
  already fixed); nightly model and logic lab 2:47 AM (one improvement, shipped only if it beats out of sample by 2+ SE;
  notebook in the data repo lab/notes.md; logs to model_changes / change_log).
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
