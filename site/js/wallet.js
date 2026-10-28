// The wallet: what's really in each app, and what you've really made there.
// Entries (Firestore wallet/{id}): {id, venue, type: "balance" | "deposit" | "withdrawal" | "bonus", cash?, bonus?,
// amount?, at, note?}. A balance check-in is what the app shows right now (cash and bonus). From the latest check-in the
// wallet replays everything since: deposits and withdrawals, bonus credits, every bet placed (cash bets take cash,
// bonus-funded bets take bonus) and every bet settled (a cash bet returns stake + profit; a bonus bet's winnings come back
// as cash; a push or void returns the stake to where it came from). So it always knows what each app should hold.
// The next check-in that differs is "unaccounted": an unlogged bet, a typo in odds or a stake, a credit not entered.
// Real profit since you started tracking = cash now + cash in open bets + withdrawn - deposited - what was there at the start.
import { S } from "./store.js";
import { settleProfit, kindOf, normalize } from "./promo.js";

const DONE = ["won", "lost", "push", "void"];
const t = (x) => (x ? Date.parse(x) : 0);
const promoOf = (b) => (b.promoId ? S.promos.get(b.promoId) : null);
export function bonusFunded(b) {
  const p = promoOf(b);
  return !!p && ["bonus_bet", "free_entry"].includes(kindOf(normalize(p).kind).key);
}
const profitOf = (b) => (b.profit != null ? b.profit : settleProfit(b, promoOf(b), b.status));

export function venueState(venue, now = Date.now()) {
  const entries = [...S.wallet.values()].filter((w) => w.venue === venue).sort((a, b) => t(a.at) - t(b.at));
  const bets = [...S.bets.values()].filter((b) => b.venue === venue && b.stake > 0);
  if (!entries.length && !bets.length) return null;
  const checks = entries.filter((e) => e.type === "balance");
  const start = checks[0] ? t(checks[0].at) : entries[0] ? t(entries[0].at) : null;
  // every event in time order: entries, bets placed, bets settled
  const ev = entries.map((e) => ({ at: t(e.at), e }));
  for (const b of bets) {
    ev.push({ at: t(b.createdAt), place: b });
    if (DONE.includes(b.status)) ev.push({ at: t(b.settledAt || b.createdAt) + 1, settle: b });
  }
  ev.sort((a, b) => a.at - b.at || (a.e?.type === "balance" ? 1 : 0) - (b.e?.type === "balance" ? 1 : 0));
  let cash = 0, bonus = 0, started = start == null, dep = 0, wd = 0, opening = null, unacc = 0, unaccBonus = 0, bonusIn = 0;
  const log = [];
  const openAt = (at) => bets.filter((b) => !bonusFunded(b) && t(b.createdAt) <= at && (!DONE.includes(b.status) || t(b.settledAt || b.createdAt) + 1 > at)).reduce((x, b) => x + b.stake, 0);
  for (const x of ev) {
    if (x.at > now) break;
    if (x.e) {
      const e = x.e;
      if (e.type === "balance") {
        const first = opening == null;
        const dc = first ? null : (+e.cash || 0) - cash, db = first ? null : (+e.bonus || 0) - bonus;
        if (first) { opening = (+e.cash || 0) + openAt(x.at); started = true; }
        else { unacc += dc; unaccBonus += db; }
        log.push({ ...e, first, diffCash: dc, diffBonus: db, expCash: first ? null : cash, expBonus: first ? null : bonus });
        cash = +e.cash || 0; bonus = +e.bonus || 0;
        continue;
      }
      if (e.type === "deposit") { cash += +e.amount || 0; if (opening != null) dep += +e.amount || 0; }
      if (e.type === "withdrawal") { cash -= +e.amount || 0; if (opening != null) wd += +e.amount || 0; }
      if (e.type === "bonus") { bonus += +e.amount || 0; bonusIn += +e.amount || 0; }
      log.push(e);
      continue;
    }
    if (x.place) { if (bonusFunded(x.place)) bonus -= x.place.stake; else cash -= x.place.stake; continue; }
    if (x.settle) {
      const b = x.settle, pr = profitOf(b) || 0;
      if (bonusFunded(b)) { if (b.status === "push" || b.status === "void") bonus += b.stake; else cash += Math.max(0, pr); }
      else cash += b.stake + pr;
    }
  }
  const openCash = bets.filter((b) => b.status === "pending" && !bonusFunded(b)).reduce((x, b) => x + b.stake, 0);
  const openBonus = bets.filter((b) => b.status === "pending" && bonusFunded(b)).reduce((x, b) => x + b.stake, 0);
  // what the ledger says you made here since tracking started (settled bets)
  const ledger = bets.filter((b) => DONE.includes(b.status) && start != null && t(b.settledAt || b.createdAt) >= start).reduce((x, b) => x + (profitOf(b) || 0), 0);
  const real = opening != null ? cash + openCash + wd - dep - opening : null;
  return { venue, cash, bonus, openCash, openBonus, dep, wd, opening, real, ledger, unacc, unaccBonus, bonusIn, start,
    checkedAt: checks.length ? checks.at(-1).at : null, tracked: opening != null, log: log.reverse(),
    nBets: bets.filter((b) => start != null && t(b.createdAt) >= start).length };
}

export function walletAll() {
  const venues = new Set([...[...S.wallet.values()].map((w) => w.venue)]);
  return [...venues].map((v) => venueState(v)).filter(Boolean);
}
