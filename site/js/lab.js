// Bet-lab math: leg catalog, same-game correlation, parlay pricing, promo parsing + optimizer.
import { S } from "./store.js";
import { mktName, signPt, marketInfo } from "./util.js";

// ---------- venues (promo venue -> engine book key used for its prices)
export const VENUES = [
  { key: "draftkings", name: "DK Predictions", proxy: true, aliases: ["dk predictions", "draftkings predictions", "draftkings", "dk "] },
  { key: "fanduel", name: "FanDuel Predicts", proxy: true, aliases: ["fanduel predicts", "fanduel", "fd "] },
  { key: "kalshi", name: "Kalshi", aliases: ["kalshi"] },
  { key: "polymarket_us", name: "Polymarket", aliases: ["polymarket"] },
  { key: "novig", name: "Novig", aliases: ["novig"] },
  { key: "prophetx", name: "ProphetX", aliases: ["prophetx", "prophet x"] },
  { key: "fliff", name: "Fliff", aliases: ["fliff"] },
  { key: "bovada", name: "Bovada", aliases: ["bovada"] },
  { key: "courtside", name: "Courtside", aliases: ["courtside"] },
  { key: "prizepicks", name: "PrizePicks", dfs: true, aliases: ["prizepicks", "prize picks"] },
  { key: "underdog", name: "Underdog", dfs: true, aliases: ["underdog"] },
  { key: "pick6", name: "DK Pick6", dfs: true, aliases: ["pick6", "pick 6"] },
  { key: "dabble_us_dfs", name: "Dabble", dfs: true, aliases: ["dabble"] },
  { key: "sleeper", name: "Sleeper", dfs: true, aliases: ["sleeper"] },
  { key: "betr_us_dfs", name: "Betr", dfs: true, aliases: ["betr"] },
  { key: "chalkboard", name: "Chalkboard", dfs: true, aliases: ["chalkboard"] },
  { key: "boom", name: "Boom Fantasy", dfs: true, aliases: ["boom"] },
  { key: "parlayplay", name: "ParlayPlay", dfs: true, aliases: ["parlayplay", "parlay play"] },
  { key: "onyx", name: "Onyx", dfs: true, aliases: ["onyx"] },
  { key: "bookmaker", name: "Bookmaker", aliases: ["bookmaker"] },
  { key: "any", name: "Any venue (best price)", aliases: [] },
];
export const venueName = (k) => VENUES.find((v) => v.key === k)?.name || k;

// ---------- leg catalog from the live board
const SCORING = /points|pass_yds|rush_yds|reception_yds|rush_reception|receptions|assists|threes|shots|total_bases|hits|rbis|runs|pass_tds|anytime_td|goal/;

function sideOfPlayer(row, g) {
  if (!row.team || !g) return 0;
  if (g.homeRef?.abbr === row.team) return 1;
  if (g.awayRef?.abbr === row.team) return -1;
  return 0;
}

export function catalog() {
  const legs = [];
  for (const g of S.games.values()) {
    const matchup = `${g.away} @ ${g.home}`;
    for (const l of g.lines) {
      if (!l.fair) continue;
      for (const o of l.outcomes) {
        let label, dir = 0, ou = 0;
        const info = marketInfo(l.market);
        const pre = info.period ? `${info.period} ` : "";
        if (info.kind === "h2h") { label = l.market === "h2h" ? `${o} ML` : `${pre}${o}${l.market.includes("3") ? " (3-way)" : " ML"}`; dir = o === g.home ? 1 : o === g.away ? -1 : 0; }
        else if (info.kind === "spreads") { const pt = o === g.home ? l.point : -l.point; label = `${pre}${o} ${signPt(pt)}`; dir = o === g.home ? 1 : -1; }
        else if (info.kind === "team_totals") { label = `${pre}${l.team} ${o} ${l.point}`; ou = o === "Over" ? 1 : -1; dir = 0; }
        else if (info.kind === "totals") { const sub = (l.market.match(/_(corners|cards)$/) || [])[1]; label = `${pre}${o} ${l.point}${sub ? " " + sub : ""}`; ou = o === "Over" ? 1 : -1; }
        else { label = `${info.label}: ${o}${l.point != null ? " " + l.point : ""}`; }
        if (info.period) { dir *= 0.6; ou *= 0.6; }   // periods move with the full game, not one-for-one
        legs.push(mkLeg(g, matchup, l, o, label, { kind: "line", market: l.market, dir, ou, main: !!l.isMain }));
      }
    }
  }
  for (const p of S.props.values()) {
    const g = S.games.get(p.id);
    const matchup = `${p.away} @ ${p.home}`;
    for (const r of p.rows) {
      if (!r.fair) continue;
      for (const o of Object.keys(r.fair)) {
        const label = `${r.player} ${o} ${r.point ?? ""} ${mktName(p.market)}`.replace(/\s+/g, " ");
        const leg = mkLeg(g || p, matchup, r, o, label, { kind: "prop", market: p.market, player: r.player,
          ou: o === "Over" || o === "Yes" ? 1 : -1, pside: sideOfPlayer(r, g), main: !!r.isMain, headshot: r.headshot, model: !!r.modelFair,
          last10: r.last10, ourModel: r.model?.[o] ?? null, mean: r.mean ?? null, sport: p.sport });
        legs.push(leg);
        for (const d of r.dfs || []) {
          if (d.side === o && d.p != null) legs.push({ ...leg, key: leg.key + "|" + d.book, pickem: d.book, pickemName: d.name, label: `${label} (${d.name})`, fair: d.p, pickemLine: d.point ?? r.point,
            pickDec: d.dec?.[o] ?? null, varPay: !!d.flex, minMult: d.minMult?.[o] ?? null });   // varPay: the app sets each pick's multiplier (feed price is a placeholder)
        }
      }
    }
  }
  return legs;
}

function mkLeg(g, matchup, line, o, label, extra) {
  const venues = (line.quotes || []).filter((q) => q.prices[o]).map((q) => ({
    book: q.book, name: q.name, venue: q.venue, stale: q.stale, kind: q.kind,
    am: q.prices[o].allInAm ?? q.prices[o].am, cost: q.prices[o].cost, edge: q.prices[o].edge,
    rawCost: q.prices[o].dec ? 1 / q.prices[o].dec : q.prices[o].cost,      // before exchange fees (for fee-free promos)
  }));
  return {
    key: `${g.id}|${extra.market}|${line.player || ""}|${line.point ?? ""}|${o}`,
    gameId: g.id, league: g.league, commence: g.commence, matchup, label, side: o, point: line.point,
    nb: line.nBooks ?? 0, est: Object.keys(line.fair || {}).length < 2,      // one-sided (Yes-only) fair is an estimate
    fair: line.fair[o], fairAm: line.fairAm?.[o], venues, best: line.bestVenue?.[o] || null,
    ref: extra.kind === "line" ? (extra.dir === 1 ? g.homeRef : extra.dir === -1 ? g.awayRef : null) : null,
    ...extra,
  };
}

export function priceAt(leg, venueKey, opts = {}) {
  if (!venueKey || venueKey === "any") return leg.best ? { am: leg.best.am, cost: leg.best.cost, name: leg.best.name, book: leg.best.book } : null;
  const v = leg.venues.find((x) => x.book === venueKey && !x.stale);
  if (v) return { am: v.am, cost: v.cost, rawCost: v.rawCost, name: v.name };
  if (opts.standIn) {
    // venue not in the odds feed: the median US sportsbook price as a stand-in (flagged on screen)
    const bk = leg.venues.filter((x) => x.kind === "book" && !x.stale).map((x) => x.cost).sort((a, b) => a - b);
    if (bk.length >= 2) { const c = bk[Math.floor(bk.length / 2)]; return { am: null, cost: c, rawCost: c, name: "stand-in", standIn: true }; }
  }
  return null;
}

// ---------- correlation (same game only; rule-of-thumb coefficients, labeled as estimates)
const SAME_PLAYER = {
  "points|points_rebounds_assists": .8, "points|points_assists": .75, "points|points_rebounds": .75,
  "points|threes": .55, "rebounds|points_rebounds_assists": .5, "assists|points_rebounds_assists": .45,
  "rebounds|points_rebounds": .6, "assists|points_assists": .6, "pass_yds|pass_tds": .55,
  "pass_yds|pass_completions": .7, "rush_yds|rush_attempts": .7, "reception_yds|receptions": .7,
  "rush_yds|rush_reception_yds": .7, "reception_yds|rush_reception_yds": .7, "shots_on_goal|points": .3,
  "shots_on_goal|goals": .35, "hits|total_bases": .75,
};
const short = (m) => m.replace(/^(player|batter|pitcher)_/, "");

export function corr(a, b) {
  if (a.gameId !== b.gameId) return 0;
  const lineA = a.kind === "line", lineB = b.kind === "line";
  if (lineA && lineB) {
    const ka = marketInfo(a.market).kind, kb = marketInfo(b.market).kind;
    const teamA = ka === "h2h" || ka === "spreads", teamB = kb === "h2h" || kb === "spreads";
    if (teamA && teamB) return a.dir && b.dir ? .85 * Math.sign(a.dir) * Math.sign(b.dir) * Math.min(1, Math.abs(a.dir * b.dir) * 1.2) : 0;
    if (!teamA && !teamB && a.ou && b.ou) return (ka === kb ? .9 : .6) * Math.sign(a.ou) * Math.sign(b.ou) * Math.min(1, Math.abs(a.ou * b.ou) * 1.2);
    return 0;
  }
  if (lineA !== lineB) {
    const [l, p] = lineA ? [a, b] : [b, a];
    const sc = SCORING.test(p.market);
    if (marketInfo(l.market).kind === "totals") {
      if (/pitcher_strikeouts/.test(p.market)) return -.15 * l.ou * p.ou;
      if (/total_saves/.test(p.market)) return .15 * l.ou * p.ou;
      return (sc ? .22 : .1) * l.ou * p.ou;
    }
    if (p.pside && sc) return .15 * l.dir * p.pside * p.ou;
    return 0;
  }
  // prop & prop
  if (a.player === b.player) {
    if (a.market === b.market) return .9 * a.ou * b.ou;
    const k1 = `${short(a.market)}|${short(b.market)}`, k2 = `${short(b.market)}|${short(a.market)}`;
    return (SAME_PLAYER[k1] ?? SAME_PLAYER[k2] ?? .3) * a.ou * b.ou;
  }
  const sameTeam = a.pside && a.pside === b.pside;
  const qbRec = (x, y) => /pass_yds|pass_completions/.test(x.market) && /reception_yds|receptions/.test(y.market);
  if (sameTeam && (qbRec(a, b) || qbRec(b, a))) return .45 * a.ou * b.ou;
  if (sameTeam && SCORING.test(a.market) && SCORING.test(b.market)) return -.05 * a.ou * b.ou;
  if (a.pside && b.pside && a.pside !== b.pside && short(a.market) === short(b.market) && SCORING.test(a.market)) return .08 * a.ou * b.ou;
  return 0;
}

// joint probability with pairwise Bernoulli-correlation adjustments
export function parlay(legs, prices) {
  const ps = legs.map((l) => l.fair);
  let indep = ps.reduce((x, y) => x * y, 1), joint = indep;
  const pairs = [];
  for (let i = 0; i < legs.length; i++) for (let j = i + 1; j < legs.length; j++) {
    const r = corr(legs[i], legs[j]);
    if (!r) continue;
    const pi = ps[i], pj = ps[j];
    const pij = Math.max(0, Math.min(Math.min(pi, pj), pi * pj + r * Math.sqrt(pi * (1 - pi) * pj * (1 - pj))));
    joint *= pij / (pi * pj);
    pairs.push({ i, j, r });
  }
  joint = Math.max(0, Math.min(joint, Math.min(...ps)));
  const decs = prices.map((p) => (p ? 1 / p.cost : null));
  const dec = decs.every((d) => d) ? decs.reduce((x, y) => x * y, 1) : null;
  return { indep, joint, pairs, dec };
}

export const evPerDollar = (p, dec, boost = 0) => p * (1 + (dec - 1) * (1 + boost)) - 1;
