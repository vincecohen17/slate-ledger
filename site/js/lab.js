// Bet-lab math: leg catalog, same-game correlation, parlay pricing, promo parsing + optimizer.
import { S } from "./store.js";
import { mktName, signPt } from "./util.js";

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
        if (l.market === "h2h" || l.market === "h2h_3way") { label = l.market === "h2h" ? `${o} ML` : `${o} (regulation)`; dir = o === g.home ? 1 : o === g.away ? -1 : 0; }
        else if (l.market === "spreads") { const pt = o === g.home ? l.point : -l.point; label = `${o} ${signPt(pt)}`; dir = o === g.home ? 1 : -1; }
        else { label = `${o} ${l.point}`; ou = o === "Over" ? 1 : -1; }
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
          last10: r.last10, ourModel: r.model?.[o] ?? null });
        legs.push(leg);
        for (const d of r.dfs || []) {
          if (d.side === o && d.p != null) legs.push({ ...leg, key: leg.key + "|" + d.book, pickem: d.book, pickemName: d.name, label: `${label} (${d.name})` });
        }
      }
    }
  }
  return legs;
}

function mkLeg(g, matchup, line, o, label, extra) {
  const venues = (line.quotes || []).filter((q) => q.prices[o]).map((q) => ({
    book: q.book, name: q.name, venue: q.venue, stale: q.stale,
    am: q.prices[o].allInAm ?? q.prices[o].am, cost: q.prices[o].cost, edge: q.prices[o].edge,
  }));
  return {
    key: `${g.id}|${extra.market}|${line.player || ""}|${line.point ?? ""}|${o}`,
    gameId: g.id, league: g.league, commence: g.commence, matchup, label, side: o, point: line.point,
    fair: line.fair[o], fairAm: line.fairAm?.[o], venues, best: line.bestVenue?.[o] || null,
    ref: extra.kind === "line" ? (extra.dir === 1 ? g.homeRef : extra.dir === -1 ? g.awayRef : null) : null,
    ...extra,
  };
}

export function priceAt(leg, venueKey) {
  if (!venueKey || venueKey === "any") return leg.best ? { am: leg.best.am, cost: leg.best.cost, name: leg.best.name } : null;
  const v = leg.venues.find((x) => x.book === venueKey && !x.stale);
  return v ? { am: v.am, cost: v.cost, name: v.name } : null;
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
    const teamA = a.market !== "totals", teamB = b.market !== "totals";
    if (teamA && teamB && (!a.dir || !b.dir)) return 0;
    if (teamA && teamB) return .85 * a.dir * b.dir;
    if (!teamA && !teamB) return .9 * a.ou * b.ou;
    return 0;
  }
  if (lineA !== lineB) {
    const [l, p] = lineA ? [a, b] : [b, a];
    const sc = SCORING.test(p.market);
    if (l.market === "totals") {
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

// ---------- promo parsing from plain words
const SPORT_WORDS = { nba: "NBA", nfl: "NFL", nhl: "NHL", mlb: "MLB", ncaaf: "NCAAF", "college football": "NCAAF",
  ncaab: "NCAAB", "college basketball": "NCAAB", epl: "EPL", "premier league": "EPL", "champions league": "UCL", mls: "MLS", ufc: "UFC" };

export function parsePromo(text) {
  const t = " " + text.toLowerCase().replace(/\s+/g, " ") + " ";
  const p = { text, type: "profit_boost", venue: "any", boost: 0, maxStake: null, maxWin: null,
              minLegs: 1, maxLegs: null, minOdds: null, minLegOdds: null, sports: [], onePerSport: false, conv: 0.7, bonus: null };
  const v = VENUES.find((x) => x.aliases.some((a) => t.includes(" " + a.trim())));
  if (v) p.venue = v.key;
  const boost = t.match(/(\d{1,3})\s*%\s*(profit\s*|odds\s*|parlay\s*|combo\s*|trade\s*)*boost/) || t.match(/boost[a-z ]{0,25}?(\d{1,3})\s*%/);
  if (boost) p.boost = +boost[1] / 100;
  const legs = t.match(/(\d{1,2})\s*\+\s*(legs?|picks?|selections?|trades?)/) || t.match(/(?:at least|minimum of|min\.?)\s*(\d{1,2})\s*(legs?|picks?|selections?)/) || t.match(/(\d{1,2})\s*(?:or more)\s*(legs?|picks?|selections?)/);
  if (legs) p.minLegs = +legs[1];
  if (/parlay|combo|sgp|same game|legs?\b/.test(t) && p.minLegs < 2) p.minLegs = 2;
  const stake = t.match(/(?:max(?:imum)?|up to)\s*(?:stake|wager|bet)?\s*(?:of\s*)?\$\s*(\d+)/) || t.match(/\$\s*(\d+)\s*max/);
  if (stake) p.maxStake = +stake[1];
  const win = t.match(/max(?:imum)?\s*(?:extra\s*)?winnings?\s*(?:of\s*)?\$\s*(\d+)/);
  if (win) p.maxWin = +win[1];
  const mo = t.match(/(?:min(?:imum)?\s*odds|odds of)\s*(?:of\s*)?([+-]\d{3,4})/);
  if (mo) { if (/each|per|every/.test(t.slice(mo.index, mo.index + 60))) p.minLegOdds = +mo[1]; else p.minOdds = +mo[1]; }
  if (/no.?sweat|second chance|refund|insurance|money back/.test(t)) p.type = "no_sweat";
  else if (/bonus bet|free bet|bet \$?\d+.{0,10}get/.test(t) && !boost) p.type = "bonus_bet";
  else if (/discount|% off|free (pick|entry)|taco/.test(t)) p.type = "dfs";
  else if (p.minLegs >= 2) p.type = "parlay_boost";
  const bb = t.match(/get \$?\s*(\d+)/); if (bb && p.type === "bonus_bet") p.bonus = +bb[1];
  for (const [w, lg] of Object.entries(SPORT_WORDS)) if (t.includes(" " + w)) p.sports.push(lg);
  if (/major sport/.test(t) && !p.sports.length) p.sports = ["NBA", "NFL", "NHL", "MLB"];
  if (/each (major )?sport|one (pick|leg|selection) (in|from|per) each|one per sport|different sports/.test(t)) p.onePerSport = true;
  p.sports = [...new Set(p.sports)];
  if (p.onePerSport && p.sports.length > p.minLegs) p.minLegs = p.sports.length;
  return p;
}

export function describePromo(p) {
  const bits = [];
  if (p.type === "profit_boost") bits.push(`${Math.round(p.boost * 100)}% profit boost`);
  if (p.type === "parlay_boost") bits.push(`${Math.round(p.boost * 100)}% boost on ${p.minLegs}+ leg parlays`);
  if (p.type === "bonus_bet") bits.push(`bonus bet${p.bonus ? ` $${p.bonus}` : ""} (stake not returned)`);
  if (p.type === "no_sweat") bits.push("refund as bonus bet if it loses");
  if (p.type === "dfs") bits.push("pick'em promo");
  if (p.sports.length) bits.push(`${p.onePerSport ? "one leg each: " : "sports: "}${p.sports.join(", ")}`);
  if (p.minOdds != null) bits.push(`min odds ${p.minOdds > 0 ? "+" : ""}${p.minOdds}`);
  if (p.minLegOdds != null) bits.push(`min ${p.minLegOdds > 0 ? "+" : ""}${p.minLegOdds} per leg`);
  if (p.maxStake) bits.push(`max stake $${p.maxStake}`);
  if (p.maxWin) bits.push(`max extra winnings $${p.maxWin}`);
  return bits.join(" · ");
}

// ---------- promo optimizer
const amToDec = (a) => a > 0 ? 1 + a / 100 : 1 + 100 / -a;

export function optimize(promo, legsAll, top = 8) {
  const venue = promo.venue || "any";
  const minLegDec = promo.minLegOdds != null ? amToDec(promo.minLegOdds) : 1;
  let cands = legsAll.filter((l) => !l.pickem && new Date(l.commence) > new Date()).map((l) => {
    const pr = priceAt(l, venue);
    if (!pr) return null;
    const dec = 1 / pr.cost;
    if (dec < minLegDec) return null;
    if (promo.sports.length && !promo.sports.includes(l.league)) return null;
    return { leg: l, price: pr, dec, ratio: l.fair * dec };
  }).filter(Boolean);

  const value = (P, D) => {
    if (promo.type === "bonus_bet") return P * (D - 1);                     // per $1 of bonus
    if (promo.type === "no_sweat") return P * (D - 1) - (1 - P) + (1 - P) * promo.conv;
    let extra = (D - 1) * promo.boost;
    if (promo.maxWin && promo.maxStake) extra = Math.min(extra, promo.maxWin / promo.maxStake);
    return P * (1 + (D - 1) + extra) - 1;
  };
  const minDec = promo.minOdds != null ? amToDec(promo.minOdds) : 1;

  if ((promo.minLegs || 1) <= 1 && promo.type !== "parlay_boost") {
    return cands.filter((c) => c.dec >= minDec)
      .map((c) => ({ legs: [c], P: c.leg.fair, D: c.dec, ev: value(c.leg.fair, c.dec) }))
      .sort((a, b) => b.ev - a.ev).slice(0, top);
  }
  // parlays: beam search over legs from different games (keeps legs independent)
  const n = Math.max(2, promo.minLegs || 2);
  cands = cands.filter((c) => c.ratio > 0.9).sort((a, b) => b.ratio - a.ratio);
  const bestPerGame = new Map();
  for (const c of cands) { const k = c.leg.gameId; if (!bestPerGame.has(k)) bestPerGame.set(k, []); if (bestPerGame.get(k).length < 4) bestPerGame.get(k).push(c); }
  const pool = [...bestPerGame.values()].flat().slice(0, 90);
  let beam = [{ legs: [], P: 1, D: 1, games: new Set(), sports: new Set() }];
  for (let depth = 0; depth < n; depth++) {
    const next = [];
    for (const b of beam) for (const c of pool) {
      if (b.games.has(c.leg.gameId)) continue;
      if (promo.onePerSport && b.sports.has(c.leg.league)) continue;
      const legs = [...b.legs, c];
      if (legs.length > 1 && legs.at(-2).leg.key > c.leg.key && !promo.onePerSport) continue; // order-free dedupe
      next.push({ legs, P: b.P * c.leg.fair, D: b.D * c.dec, games: new Set([...b.games, c.leg.gameId]), sports: new Set([...b.sports, c.leg.league]) });
    }
    next.forEach((x) => { x.score = depth === n - 1 ? value(x.P, x.D) : x.P * x.D; });
    next.sort((a, b) => b.score - a.score);
    const seenD = new Set();
    beam = next.filter((b) => { const k = b.legs.map((c) => c.leg.key).sort().join("~"); if (seenD.has(k)) return false; seenD.add(k); return true; }).slice(0, 60);
  }
  const seen = new Set();
  beam = beam.filter((b) => { const k = b.legs.map((c) => c.leg.key).sort().join("~"); if (seen.has(k)) return false; seen.add(k); return true; });
  let out = beam.filter((b) => b.D >= minDec);
  if (promo.onePerSport && promo.sports.length) out = out.filter((b) => promo.sports.every((s) => b.sports.has(s)));
  return out.map((b) => ({ legs: b.legs, P: b.P, D: b.D, ev: value(b.P, b.D) })).sort((a, b) => b.ev - a.ev).slice(0, top);
}
