# Slate Ledger

Live odds engine and private betting board.

- `engine/` pulls The Odds API on GitHub Actions, computes the sharp no-vig fair for every line and prop, prices every venue (fees included), flags edges and stale books, and writes the board to Firestore and snapshots to a private data repo.
- `site/` is the board (Firebase Hosting, Google sign-in, owner-only Firestore rules).
- Keys live in GitHub encrypted secrets. Logs print counts only.

Run a pull: Actions → pull-odds → Run workflow (mock = one capped pull).
