# Slate Ledger — notes for Claude

Owner: Vince (Burbank, CA). Goal: beat the books with sharp-market pricing, sports logic and promos.

## Layout
- `engine/` Python. `run.py` pulls The Odds API, `build.py` computes sharp no-vig fair per line/prop,
  all-in venue costs (Kalshi/Polymarket fees), edges, stale flags, pick'em line judgments.
  `config.py` holds sports, markets, book weights, venues, budgets. Tests: `python engine/tests/test_build.py`.
- `site/` Firebase Hosting board (Google sign-in; Firestore rules allow only the owner email,
  filled at deploy time from the private data repo `config/access.json`).
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
