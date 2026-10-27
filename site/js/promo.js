// Promo engine: every promo is described by what it pays (boost, bonus stake, refund, entry multiplier, fee waiver),
// valued in dollars against the sharp fair, and matched to the best bets on the live board.
import { S } from "./store.js";
import { catalog, priceAt, parlay, corr, VENUES, venueName } from "./lab.js";
import { marketInfo, todayPT } from "./util.js";
import { pOver } from "./dist.js";

export const decOf = (a) => a > 0 ? 1 + a / 100 : 1 + 100 / -a;
export const amOf = (D) => D >= 2 ? Math.round((D - 1) * 100) : Math.round(-100 / (D - 1));
const SPORTS = ["NBA", "WNBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB", "EPL", "UCL", "UEL", "MLS", "La Liga", "Bundesliga", "Serie A", "Ligue 1", "Liga MX", "ATP", "WTA", "UFC", "Boxing"];
export { SPORTS };
export const PICKEM = new Set(VENUES.filter((v) => v.dfs).map((v) => v.key));
export const FEE_VENUES = new Set(["kalshi", "polymarket_us", "novig", "prophetx"]);
export const BET_TYPES = [["ml", "Moneyline"], ["spread", "Spread"], ["total", "Totals"], ["prop", "Player props"]];

// Typical power-play payouts by number of picks. They change: every promo can override them.
export const PAYOUTS = {
  prizepicks: { 2: 3, 3: 5, 4: 10, 5: 20, 6: 37.5 }, underdog: { 2: 3, 3: 6, 4: 10, 5: 20, 6: 35 },
  pick6: { 2: 3, 3: 5, 4: 10, 5: 20, 6: 40 }, default: { 2: 3, 3: 5, 4: 10, 5: 20, 6: 35 },
};
export const payoutFor = (venue, n) => (PAYOUTS[venue] || PAYOUTS.default)[n] ?? null;

// ---------- promo types: which fields each needs (the form shows only these)
export const KINDS = [
  { key: "profit_boost", group: "Boosts", name: "Profit boost", blurb: "Extra % on what one bet wins",
    fields: ["boost", "stake", "maxWin", "maxLegs", "minOdds", "maxOdds", "sports", "betTypes"] },
  { key: "parlay_boost", group: "Boosts", name: "Parlay / combo boost", blurb: "Extra % on a parlay or combo trade's winnings",
    fields: ["boost", "stake", "maxWin", "minLegs", "maxLegs", "minLegOdds", "minOdds", "maxOdds", "sameGame", "onePerSport", "sports", "betTypes"] },
  { key: "odds_boost", group: "Boosts", name: "Boosted price on a set bet", blurb: "The app raised the price on one specific bet",
    fields: ["target", "boostedOdds", "origOdds", "stake"] },
  { key: "fee_free", group: "Boosts", name: "Fee-free or reduced fee", blurb: "Kalshi, Polymarket, Novig or ProphetX waives its fee",
    fields: ["feeOff", "stake", "sports", "betTypes"] },
  { key: "bonus_bet", group: "Bonus money", name: "Bonus bet / free bet", blurb: "Stake is not returned, only the winnings",
    fields: ["amount", "count", "minOdds", "maxOdds", "minLegs", "sports", "betTypes"] },
  { key: "no_sweat", group: "Bonus money", name: "No-sweat / bet insurance", blurb: "Lose and get your stake back as a bonus",
    fields: ["stake", "refundMax", "refundAs", "minOdds", "minLegs", "sports", "betTypes"] },
  { key: "parlay_insurance", group: "Bonus money", name: "Parlay insurance", blurb: "Refund when exactly one leg misses",
    fields: ["stake", "refundMax", "refundAs", "minLegs", "minLegOdds", "sports", "betTypes"] },
  { key: "bet_and_get", group: "Bonus money", name: "Bet $X, get $Y", blurb: "Place a qualifying bet, receive bonus bets",
    fields: ["qualStake", "amount", "condition", "minOdds", "sports", "betTypes"] },
  { key: "deposit_match", group: "Bonus money", name: "Deposit match", blurb: "Bonus on a deposit that must be wagered through",
    fields: ["amount", "deposit", "playthrough", "ptBase", "minOdds"] },
  { key: "pickem_boost", group: "Pick'em", name: "Pick'em payout boost", blurb: "Bigger multiplier on an entry",
    fields: ["picks", "mult", "boost", "stake", "sports"] },
  { key: "free_entry", group: "Pick'em", name: "Free entry", blurb: "An entry you don't pay for",
    fields: ["amount", "picks", "mult", "sports"] },
  { key: "discounted_line", group: "Pick'em", name: "Discounted line / special pick", blurb: "An easier line on one player",
    fields: ["target", "promoLine", "promoSide", "picks", "mult", "stake"] },
  { key: "custom", group: "Anything else", name: "Custom payout", blurb: "Describe what it pays on a win and on a loss",
    fields: ["stake", "stakeBack", "winBoost", "extraWin", "refundMax", "refundAs", "minOdds", "minLegs", "sports", "betTypes"] },
];
export const kindOf = (k) => KINDS.find((x) => x.key === (k === "dfs" ? "pickem_boost" : k)) || KINDS[0];

export const FIELDS = {
  boost: ["Boost %", "pct"], stake: ["Stake $ (max allowed)", "num"], maxWin: ["Max extra winnings $", "num"],
  minOdds: ["Min odds", "odds", "-200"], maxOdds: ["Max odds", "odds", "+300"], minLegs: ["Min legs", "int"], maxLegs: ["Max legs (1 = singles only)", "int"],
  minLegOdds: ["Min odds per leg", "odds"], amount: ["Bonus amount $", "num"], count: ["How many", "int"],
  refundMax: ["Max refund $", "num"], qualStake: ["Qualifying bet $", "num"], deposit: ["Your deposit $", "num"],
  playthrough: ["Playthrough (x)", "num"], picks: ["Picks in the entry", "int"], mult: ["Payout multiplier (x)", "num"],
  boostedOdds: ["Boosted odds", "odds"], origOdds: ["Original odds", "odds"], promoLine: ["Promo line", "num"],
  feeOff: ["Share of fee waived %", "pct"], winBoost: ["Extra % on winnings", "pct"], extraWin: ["Extra $ if it wins", "num"],
  expires: ["Expires", "date"],
};

export function blank(kind = "profit_boost", venue = "any") {
  return { kind, venue, boost: 0, stake: null, maxWin: null, minOdds: null, maxOdds: null, minLegs: 1, maxLegs: null, minLegOdds: null,
    amount: null, count: 1, refundMax: null, refundAs: "bonus", qualStake: null, condition: "any", deposit: null, playthrough: null,
    ptBase: "bonus", picks: null, mult: null, boostedOdds: null, origOdds: null, target: null, promoLine: null, promoSide: "Over",
    feeOff: 1, winBoost: 0, extraWin: 0, stakeBack: true, sameGame: "allowed", onePerSport: false, sports: [], betTypes: [], expires: null };
}
// promos saved by the old board used `type`; read them as the new kinds
export function normalize(p) {
  const n = { ...blank(), ...p };
  n.kind = p.kind || (p.type === "dfs" ? "pickem_boost" : p.type) || "profit_boost";
  if (p.maxStake != null && n.stake == null) n.stake = p.maxStake;
  if (p.bonus != null && n.amount == null) n.amount = p.bonus;
  n.sports = n.sports || []; n.betTypes = n.betTypes || [];
  return n;
}

// ---------- plain-words reader
const SPORT_WORDS = { nba: "NBA", wnba: "WNBA", nfl: "NFL", nhl: "NHL", mlb: "MLB", ncaaf: "NCAAF", cfb: "NCAAF", "college football": "NCAAF",
  ncaab: "NCAAB", cbb: "NCAAB", "college basketball": "NCAAB", "march madness": "NCAAB", epl: "EPL", "premier league": "EPL",
  "champions league": "UCL", ucl: "UCL", "europa league": "UEL", mls: "MLS", "la liga": "La Liga", laliga: "La Liga", bundesliga: "Bundesliga",
  "serie a": "Serie A", "ligue 1": "Ligue 1", "liga mx": "Liga MX", soccer: "EPL", ufc: "UFC", mma: "UFC", boxing: "Boxing", tennis: "ATP", atp: "ATP", wta: "WTA",
  basketball: "NBA", football: "NFL", hockey: "NHL", baseball: "MLB" };
const NUMW = { one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10 };
const num = (x) => x == null ? null : NUMW[x] ?? parseFloat(String(x).replace(/,/g, ""));
const ODDS = "([+-]\\d{3,5}|even|evens)";
const oddsNum = (x) => /even/.test(x) ? 100 : +x;

export function parsePromo(text) {
  const t = " " + text.toLowerCase().replace(/[–—]/g, "-").replace(/\s+/g, " ") + " ";
  const p = blank();
  p.text = text;
  // the longest alias that appears wins ("dk pick6" is Pick6, not DraftKings)
  let v = null, vl = 0;
  for (const x of VENUES) for (const a of x.aliases) if (t.includes(" " + a.trim()) && a.trim().length > vl) { v = x; vl = a.trim().length; }
  if (v) p.venue = v.key;
  const pick = PICKEM.has(p.venue);
  const m = (re) => t.match(re);

  // what kind of promo
  const pct = m(/(\d{1,3})\s*%\s*(?:profit\s*|odds\s*|parlay\s*|sgp\s*|combo\s*|trade\s*|payout\s*|winnings\s*)*boost/) || m(/boost(?:ed)?[a-z ]{0,30}?(\d{1,3})\s*%/) || m(/(\d{1,3})\s*%[a-z0-9 .'-]{0,30}?boost/) || m(/(\d{1,3})\s*%\s*(?:more|extra)\s*(?:on\s*)?(?:winnings|profit|payout)/);
  if (pct) p.boost = +pct[1] / 100;
  const ob = m(new RegExp(`(?:boosted|boost)?\\s*(?:from\\s*)?${ODDS}\\s*(?:to|->|→|now)\\s*${ODDS}`));
  if (/fee[- ]?free|no fees?|zero fees?|fees? waived|waive[sd]? (?:the )?fees?|no commission|0% commission|commission[- ]free|reduced (?:juice|fees?|vig|commission)|half (?:the )?fees?/.test(t)) {
    p.kind = "fee_free"; const h = m(/(\d{1,3})\s*%\s*(?:off|lower|reduced)?\s*(?:fees?|commission)/); p.feeOff = /half/.test(t) ? 0.5 : h && !/0%/.test(h[0]) ? +h[1] / 100 : 1;
  } else if (/deposit match|match(?:es)? your (?:first )?deposit|deposit bonus|first deposit|playthrough|rollover|wagering requirement|play-through/.test(t)) {
    p.kind = "deposit_match";
    const up = m(/up to \$\s*([\d,]+)/); if (up) p.amount = num(up[1]);
    const px = m(/(\d{1,2})\s*x\s*(?:playthrough|rollover|wagering|play-through)/) || m(/(?:playthrough|rollover|wager(?:ed|ing)?)[a-z ]{0,20}?(\d{1,2})\s*(?:x|times)/); if (px) p.playthrough = +px[1];
    if (/deposit (?:\+|and|plus) bonus|bonus (?:\+|and|plus) deposit/.test(t)) p.ptBase = "both";
  } else if (/(?:discount(?:ed)?|special|taco|reduced|flash) (?:line|pick|projection)|line (?:discount|drop)|discounted|(?:was|from) \d+(?:\.5)? (?:to|now) \d+(?:\.5)?/.test(t) && (pick || /line|pick/.test(t))) {
    p.kind = "discounted_line";
    const ln = m(/(?:to|now|at)\s*(\d+(?:\.\d)?)\b/) || m(/(\d+(?:\.\d)?)\s*(?:points|pts|rebounds|reb|assists|ast|pra|yards|yds|receptions|shots|sog|saves|strikeouts|ks|hits|threes|3pm|total bases)/) || m(/\b(\d+\.5)\b/);
    if (ln) p.promoLine = +ln[1];
    if (/\bunder\b|less/.test(t)) p.promoSide = "Under";
  } else if (/free (?:entry|play|lineup|pick'?em|squad)|bonus entry|entry credit|promo entry|free \$?\d+ (?:entry|lineup)/.test(t) && (pick || /entry|lineup/.test(t))) {
    p.kind = "free_entry";
  } else if (pick && (pct || /\d+(?:\.\d+)?\s*x\b|multiplier|pays? \d|payout boost/.test(t))) {
    p.kind = "pickem_boost";
  } else if (ob && !/insurance|no.?sweat/.test(t)) {
    p.kind = "odds_boost"; p.origOdds = oddsNum(ob[1]); p.boostedOdds = oddsNum(ob[2]);
  } else if (/parlay (?:insurance|protect)|(?:one|1) leg (?:loses|misses|fails)|if (?:only )?(?:one|1) (?:leg|pick|selection) (?:loses|misses|fails)|leg (?:loses|misses).{0,30}(?:back|refund)/.test(t)) {
    p.kind = "parlay_insurance"; p.minLegs = 3;
  } else if (/no.?sweat|bet insurance|insured|second chance|refund|money back|get (?:it|your (?:stake|bet|money)) back|bet back|risk.?free|safety net/.test(t)) {
    p.kind = "no_sweat";
  } else if (m(/(?:bet|wager|place|trade)\s*\$\s*(\d+)[a-z ,]{0,30}?get\s*\$\s*(\d+)/)) {
    p.kind = "bet_and_get"; const g = m(/(?:bet|wager|place|trade)\s*\$\s*(\d+)[a-z ,]{0,30}?get\s*\$\s*(\d+)/); p.qualStake = +g[1]; p.amount = +g[2];
    if (/(?:if|when) (?:it|your bet|the bet|your wager) (?:wins|cashes|hits)|win and get/.test(t)) p.condition = "win";
    else if (/(?:if|when) (?:it|your bet|the bet|your wager) (?:loses|doesn'?t win|misses)|lose and get/.test(t)) p.condition = "lose";
  } else if (/bonus bets?|free bets?|bet credits?|site credit|free \$\d+|token/.test(t) && !pct) {
    p.kind = "bonus_bet";
  } else if (/parlay|combo|sgp|same game|same-game|\blegs?\b|multi/.test(t) && !/profit boost|any (?:bet|wager)|single|straight/.test(t)) {
    p.kind = "parlay_boost"; p.minLegs = 2;
  } else if (/custom/.test(t)) p.kind = "custom";
  else p.kind = "profit_boost";

  // legs, picks and the entry
  const legs = m(/(\d{1,2}|two|three|four|five|six|seven|eight)\s*\+\s*(?:legs?|picks?|selections?|trades?|teams?)/) || m(/(?:at least|minimum(?: of)?|min\.?)\s*(\d{1,2}|two|three|four|five|six)\s*(?:legs?|picks?|selections?)/)
    || m(/(\d{1,2}|two|three|four|five|six)\s*(?:or more)\s*(?:legs?|picks?|selections?)/);
  if (legs) p.minLegs = num(legs[1]);
  const exact = m(/(\d{1,2}|two|three|four|five|six)[- ](?:pick|leg|man|player)\b/) || (PICKEM.has(p.venue) ? m(/(\d{1,2}|two|three|four|five|six)\s*(?:picks|selections|players)\b/) : null);
  if (pick) { p.picks = exact ? num(exact[1]) : legs ? num(legs[1]) : null; }
  else if (exact && !legs) p.minLegs = num(exact[1]);
  if (["parlay_boost", "parlay_insurance"].includes(p.kind) && p.minLegs < 2) p.minLegs = p.kind === "parlay_insurance" ? 3 : 2;
  const mx = m(/(\d{1,3}(?:\.\d+)?)\s*x\b(?! ?(?:playthrough|rollover|wagering))/); if (mx && pick) p.mult = +mx[1];
  if (/singles? only|straight bets? only|single bets? only|not valid on parlays|excludes? parlays/.test(t)) p.maxLegs = 1;
  if (/same.?game|sgp/.test(t)) p.sameGame = /no (?:same.?game|sgp)|excludes? (?:same.?game|sgp)/.test(t) ? "banned" : "required";
  if (/different games|separate games/.test(t)) p.sameGame = "banned";

  // money
  const stake = m(/(?:max(?:imum)?|up to)\s*(?:stake|wager|bet|trade|entry)?\s*(?:size\s*)?(?:of\s*)?\$\s*([\d,]+)\s*(?:stake|wager|bet|trade|entry)?/) || m(/\$\s*([\d,]+)\s*max(?:imum)?\s*(?:stake|wager|bet|trade|entry)?/) || m(/(?:on|for) (?:a |your )?\$\s*([\d,]+)\s*(?:bet|wager|trade|entry)/);
  if (stake && !["deposit_match", "bet_and_get"].includes(p.kind)) p.stake = num(stake[1]);
  const win = m(/max(?:imum)?\s*(?:extra|boosted|additional)?\s*(?:winnings|profit|payout)\s*(?:of\s*)?\$\s*([\d,]+)/) || m(/(?:extra|boosted) winnings (?:capped|limited) (?:at|to) \$\s*([\d,]+)/);
  if (win) p.maxWin = num(win[1]);
  const amt = m(/\$\s*([\d,]+)\s*(?:in\s*)?(?:bonus bets?|free bets?|bet credits?|site credit|bonus|free (?:entry|play|lineup)|entry|token)/) || m(/(?:bonus bet|free bet|free entry|bonus)\s*(?:of|worth)?\s*\$\s*([\d,]+)/);
  if (amt && ["bonus_bet", "free_entry", "deposit_match"].includes(p.kind) && p.amount == null) p.amount = num(amt[1]);
  const cnt = m(/(\d{1,2}|two|three|four|five)\s*x\s*\$\s*\d+/) || m(/(\d{1,2}|two|three|four|five)\s*(?:bonus bets|free bets|tokens|boosts)/);
  if (cnt) { p.count = num(cnt[1]); const each = m(/x\s*\$\s*(\d+)/); if (each) p.amount = +each[1]; }
  const ref = m(/(?:refund|back|insurance|no.?sweat)[a-z ]{0,40}?up to \$\s*([\d,]+)/) || m(/up to \$\s*([\d,]+)[a-z ]{0,30}(?:back|refund|bonus bets?)/);
  if (ref && ["no_sweat", "parlay_insurance"].includes(p.kind)) { p.refundMax = num(ref[1]); if (!p.stake) p.stake = p.refundMax; }
  if (/(?:back|refund(?:ed)?) (?:in|as) cash|cash refund|withdrawable/.test(t)) p.refundAs = "cash";
  if (p.kind === "deposit_match") { const dep = m(/deposit (?:of )?\$\s*([\d,]+)/); if (dep) p.deposit = num(dep[1]); if (!p.deposit && p.amount) p.deposit = p.amount; }

  // odds limits
  const between = m(new RegExp(`between\\s*${ODDS}\\s*and\\s*${ODDS}`));
  if (between) { p.minOdds = oddsNum(between[1]); p.maxOdds = oddsNum(between[2]); }
  const mo = m(new RegExp(`(?:min(?:imum)?\\s*(?:total\\s*)?odds|odds of|at odds of|priced at)\\s*(?:of\\s*)?${ODDS}`)) || m(new RegExp(`${ODDS}\\s*(?:odds\\s*)?or (?:longer|greater|higher|better)`));
  if (mo && !between) {
    const each = /each|per leg|per pick|every leg|per selection/.test(t.slice(mo.index, mo.index + 70));
    if (each) p.minLegOdds = oddsNum(mo[1]); else p.minOdds = oddsNum(mo[1]);
  }
  const xo = m(new RegExp(`(?:max(?:imum)?\\s*odds\\s*(?:of\\s*)?|odds up to\\s*)${ODDS}`)) || m(new RegExp(`${ODDS}\\s*or (?:shorter|less|lower)`));
  if (xo && !between) p.maxOdds = oddsNum(xo[1]);

  // sports, bet types, timing
  for (const [w, lg] of Object.entries(SPORT_WORDS)) if (new RegExp(`[^a-z]${w}[^a-z]`).test(t)) p.sports.push(lg);
  if (/major sports?/.test(t) && !p.sports.length) p.sports = ["NBA", "NFL", "NHL", "MLB"];
  if (/each (?:major )?sport|one (?:pick|leg|selection) (?:in|from|per) each|one per sport|different sports|across sports/.test(t)) p.onePerSport = true;
  p.sports = [...new Set(p.sports)];
  if (p.onePerSport && p.sports.length > p.minLegs) p.minLegs = p.sports.length;
  if (/player props?|props? only|prop bets?/.test(t)) p.betTypes.push("prop");
  if (/moneylines?|\bml\b|to win outright/.test(t)) p.betTypes.push("ml");
  if (/spreads?|point spread|puck ?line|run ?line/.test(t)) p.betTypes.push("spread");
  if (/\btotals?\b|over\/under|o\/u/.test(t) && !/total odds/.test(t)) p.betTypes.push("total");
  p.betTypes = [...new Set(p.betTypes)];
  const today = todayPT();
  if (/today|tonight|ends at midnight|11:59/.test(t)) p.expires = today;
  else if (/tomorrow/.test(t)) p.expires = new Date(Date.parse(today + "T12:00:00") + 864e5).toISOString().slice(0, 10);
  else { const d = m(/(?:expires?|ends?|by|through|until|valid (?:until|through))\s*(\d{1,2})\/(\d{1,2})/); if (d) p.expires = `${today.slice(0, 4)}-${d[1].padStart(2, "0")}-${d[2].padStart(2, "0")}`; }
  if (pick && ["free_entry", "discounted_line", "pickem_boost"].includes(p.kind) && !p.picks) p.picks = 2;
  if (pick && p.picks && !p.mult && p.kind !== "pickem_boost") p.mult = payoutFor(p.venue, p.picks);
  if (pick && p.kind === "pickem_boost" && pct && !p.mult && p.picks) p.mult = payoutFor(p.venue, p.picks);
  return p;
}

const oddsTxt = (a) => a == null ? "" : a > 0 ? `+${a}` : `${a}`;
export function describe(p) {
  const k = kindOf(p.kind), b = [];
  const pc = (x) => `${Math.round((x || 0) * 100)}%`;
  switch (k.key) {
    case "profit_boost": b.push(`${pc(p.boost)} profit boost`); break;
    case "parlay_boost": b.push(`${pc(p.boost)} boost on ${p.minLegs}+ leg ${p.sameGame === "required" ? "same-game parlays" : "parlays"}`); break;
    case "odds_boost": b.push(`boosted to ${oddsTxt(p.boostedOdds)}${p.origOdds != null ? ` from ${oddsTxt(p.origOdds)}` : ""}`); break;
    case "fee_free": b.push(p.feeOff >= 1 ? "no fee" : `${pc(p.feeOff)} of the fee waived`); break;
    case "bonus_bet": b.push(`${p.count > 1 ? `${p.count} × ` : ""}$${p.amount ?? "?"} bonus bet`); break;
    case "no_sweat": b.push(`refund up to $${p.refundMax ?? p.stake ?? "?"} as ${p.refundAs === "cash" ? "cash" : "bonus bets"} if it loses`); break;
    case "parlay_insurance": b.push(`${p.minLegs}+ leg parlay, refund up to $${p.refundMax ?? p.stake ?? "?"} if one leg misses`); break;
    case "bet_and_get": b.push(`bet $${p.qualStake ?? "?"}, get $${p.amount ?? "?"} in bonus bets${p.condition === "win" ? " if it wins" : p.condition === "lose" ? " if it loses" : ""}`); break;
    case "deposit_match": b.push(`$${p.amount ?? "?"} bonus${p.playthrough ? `, ${p.playthrough}x playthrough` : ""}`); break;
    case "pickem_boost": b.push(`${p.picks ?? "?"}-pick entry at ${p.mult ?? "?"}x${p.boost ? ` +${pc(p.boost)}` : ""}`); break;
    case "free_entry": b.push(`$${p.amount ?? "?"} free ${p.picks ?? "?"}-pick entry`); break;
    case "discounted_line": b.push(`${p.promoSide || "Over"} ${p.promoLine ?? "?"} on ${p.targetLabel || "a player"}`); break;
    case "custom": b.push(`custom: ${p.stakeBack ? "" : "stake not returned, "}${p.winBoost ? `+${pc(p.winBoost)} on wins, ` : ""}${p.extraWin ? `+$${p.extraWin} on wins, ` : ""}${p.refundMax ? `$${p.refundMax} back on a loss` : ""}`.replace(/, $/, "")); break;
  }
  if (p.sports?.length) b.push(`${p.onePerSport ? "one leg each: " : ""}${p.sports.join(", ")}`);
  if (p.betTypes?.length) b.push(p.betTypes.map((x) => BET_TYPES.find((y) => y[0] === x)?.[1]).join(", "));
  if (p.minOdds != null) b.push(`min ${oddsTxt(p.minOdds)}`);
  if (p.maxOdds != null) b.push(`max ${oddsTxt(p.maxOdds)}`);
  if (p.minLegOdds != null) b.push(`legs ${oddsTxt(p.minLegOdds)}+`);
  if (p.stake && !["no_sweat", "parlay_insurance"].includes(k.key)) b.push(`max $${p.stake}`);
  if (p.maxWin) b.push(`extra winnings up to $${p.maxWin}`);
  return b.join(" · ");
}

// ---------- the board, cached per data update
let cache = { stamp: null, legs: [] };
export function legs() {
  const stamp = `${S.status?.updated || ""}|${S.games.size}|${S.props.size}`;
  if (cache.stamp !== stamp) cache = { stamp, legs: catalog(), conv: new Map() };
  return cache.legs;
}
const upcoming = (l) => new Date(l.commence) > new Date();
const betType = (l) => {
  if (l.kind === "prop") return "prop";
  const k = marketInfo(l.market).kind;
  return k === "h2h" ? "ml" : k === "spreads" ? "spread" : k === "totals" || k === "team_totals" ? "total" : "other";
};
export function venueHasFeed(venue) {
  legs();
  if (!cache.feed) {
    cache.feed = new Set();
    for (const l of cache.legs) { if (l.pickem) cache.feed.add(l.pickem); for (const v of l.venues) cache.feed.add(v.book); }
  }
  return venue === "any" || cache.feed.has(venue);
}

function legOK(p, l) {
  if (!upcoming(l)) return false;
  if (p.sports?.length && !p.sports.includes(l.league)) return false;
  if (p.betTypes?.length && !p.betTypes.includes(betType(l))) return false;
  return true;
}
// a leg priced at the promo's venue (stand-in median price when the venue is not in the feed)
// Only bets whose sharp fair is solid: two-sided markets with 3+ books, and a venue price no more than 20% over fair
// (beyond that it is nearly always a stale quote or a mismatched line, not a real edge).
const solid = (l) => !l.est && l.main && l.nb >= 3 && l.fair > 0.04 && l.fair < 0.97 && !l.model;
function priced(p, l, opts = {}) {
  if (l.pickem || !solid(l)) return null;
  const pr = priceAt(l, p.venue, { standIn: opts.standIn ?? !venueHasFeed(p.venue) });
  if (!pr) return null;
  const cost = opts.feeFree ? pr.rawCost + (pr.cost - pr.rawCost) * (1 - (p.feeOff ?? 1)) : pr.cost;
  const D = 1 / cost;
  if (l.fair * D > 1.1) return null;
  if (p.minLegOdds != null && D < decOf(p.minLegOdds) - 1e-9) return null;
  return { leg: l, price: { ...pr, cost }, D, P: l.fair };
}

// best value of a $1 bonus bet at this venue right now (also what a bonus-bet refund is worth)
export function bonusConv(venue, minOdds = null) {
  const key = `${venue}|${minOdds}`;
  if (cache.conv?.has(key)) return cache.conv.get(key);
  // average of the ten best bonus-bet uses (one per game): the single best is flattered by noise
  const pp = { venue, sports: [], betTypes: [] }, perGame = new Map();
  for (const l of legs()) {
    if (!upcoming(l) || l.pickem) continue;
    const c = priced(pp, l);
    if (!c || c.P < 0.15 || (minOdds != null && c.D < decOf(minOdds))) continue;
    const v = c.P * (c.D - 1);
    if (v > (perGame.get(l.gameId) || 0)) perGame.set(l.gameId, v);
  }
  const top = [...perGame.values()].sort((a, b) => b - a).slice(0, 10);
  const out = top.length >= 3 ? Math.min(0.85, top.reduce((s, v) => s + v, 0) / top.length) : 0.65;
  cache.conv?.set(key, out);
  return out;
}

// value per dollar of a bonus bet at each price range (for the conversion chart)
export function convCurve(venue) {
  const buckets = [["-200 to -101", 1.5, 2], ["+100 to +199", 2, 3], ["+200 to +399", 3, 5], ["+400 to +799", 5, 9], ["+800 and up", 9, 1e9]];
  const pp = { venue, sports: [], betTypes: [] };
  const best = buckets.map(() => null);
  for (const l of legs()) {
    if (!upcoming(l) || l.pickem) continue;
    const c = priced(pp, l);
    if (!c) continue;
    const i = buckets.findIndex(([, lo, hi]) => c.D >= lo && c.D < hi);
    if (i < 0 || c.P < 0.08) continue;
    const v = c.P * (c.D - 1);
    if (!best[i] || v > best[i].v) best[i] = { v, c };
  }
  return buckets.map(([lab], i) => ({ label: lab, v: best[i]?.v ?? null, c: best[i]?.c }));
}

// ---------- dollars a promo is worth on one candidate bet
const pAll = (cs) => cs.reduce((x, c) => x * c.P, 1);
function oneMiss(cs) { let s = 0; cs.forEach((c, i) => { s += (1 - c.P) * cs.reduce((x, d, j) => x * (j === i ? 1 : d.P), 1); }); return s; }
function hitsDist(ps) { let d = [1]; for (const p of ps) { const n = new Array(d.length + 1).fill(0); d.forEach((v, k) => { n[k] += v * (1 - p); n[k + 1] += v * p; }); d = n; } return d; }
export function parseTable(txt) {   // "3:2.25, 2:1.25" -> {3: 2.25, 2: 1.25}
  const out = {}; String(txt || "").split(/[,;\s]+/).forEach((x) => { const m = x.match(/^(\d+):(\d+(?:\.\d+)?)x?$/); if (m) out[+m[1]] = +m[2]; });
  return Object.keys(out).length ? out : null;
}
function entryPay(p, cs) {     // expected multiplier of a pick'em entry
  const n = cs.length;
  const flex = parseTable(p.flex);
  if (flex) { const d = hitsDist(cs.map((c) => c.P)); return Object.entries(flex).reduce((s, [k, m]) => s + (d[+k] || 0) * m, 0); }
  const M = (p.mult || payoutFor(p.venue, n) || 0) * (1 + (p.kind === "pickem_boost" ? p.boost || 0 : 0));
  return pAll(cs) * M * cs.reduce((x, c) => x * (c.mult ?? 1), 1);      // per-pick payout adjustments (Underdog, Dabble)
}

export function value(p, cs, ctx = {}) {
  const P = pAll(cs), D = cs.reduce((x, c) => x * c.D, 1);
  const s = p.stake || 10;
  const conv = p.refundAs === "cash" ? 1 : (ctx.conv ?? bonusConv(p.venue));
  let ev = 0, risk = s, note = "";
  switch (kindOf(p.kind).key) {
    case "profit_boost": case "parlay_boost": {
      let extra = s * (D - 1) * (p.boost || 0);
      if (p.maxWin) extra = Math.min(extra, p.maxWin);
      ev = P * (s * (D - 1) + extra) - (1 - P) * s; break;
    }
    case "odds_boost": { const Db = p.boostedOdds != null ? decOf(p.boostedOdds) : D; ev = s * (P * Db - 1); break; }
    case "fee_free": ev = s * (P * D - 1); break;
    case "bonus_bet": { const a = p.amount || 10; ev = a * P * (D - 1); risk = 0; note = `${Math.round(P * (D - 1) * 100)}% of the bonus turned into cash value`; break; }
    case "no_sweat": { const r = Math.min(s, p.refundMax || s); ev = s * (P * D - 1) + (1 - P) * r * conv; break; }
    case "parlay_insurance": { const r = Math.min(s, p.refundMax || s); ev = s * (P * D - 1) + oneMiss(cs) * r * conv; break; }
    case "bet_and_get": {
      const q = p.qualStake || s, a = (p.amount || 0) * conv;
      ev = q * (P * D - 1) + a * (p.condition === "win" ? P : p.condition === "lose" ? 1 - P : 1); risk = q; break;
    }
    case "pickem_boost": ev = s * (entryPay(p, cs) - 1); break;
    case "free_entry": { const a = p.amount || 10; ev = a * entryPay(p, cs); risk = 0; break; }
    case "discounted_line": ev = s * (entryPay(p, cs) - 1); break;
    case "custom": {
      const win = s * (D - 1) * (1 + (p.winBoost || 0)) + (p.extraWin || 0);
      const r = Math.min(s, p.refundMax || 0) * conv;
      ev = P * win - (1 - P) * (p.stakeBack ? s : 0) + (1 - P) * r; risk = p.stakeBack ? s : 0; break;
    }
  }
  return { ev, P, D, risk, note };
}

// ---------- best bets for a promo
function oddsOK(p, D) {
  if (p.minOdds != null && D < decOf(p.minOdds) - 1e-9) return false;
  if (p.maxOdds != null && D > decOf(p.maxOdds) + 1e-9) return false;
  return true;
}
const keyOf = (cs) => cs.map((c) => c.leg.key).sort().join("~");

export function findTarget(key) { return key ? legs().find((l) => l.key === key) || null : null; }

// probability for a prop leg at any line (discounted lines, specials) from the sharp mean
export function pAtLine(leg, line, side) {
  if (!leg || leg.mean == null || line == null) return null;
  const po = pOver(leg.market, leg.mean, line, leg.sport);
  if (po == null) return null;
  return side === "Under" ? 1 - po : po;
}

export function optimize(p0, top = 8) {
  const p = normalize(p0);
  const k = kindOf(p.kind).key;
  const all = legs();
  const conv = bonusConv(p.venue);
  const ctx = { conv };
  const wrap = (cs, extra = {}) => ({ legs: cs, ...value(p, cs, ctx), ...extra });

  if (k === "deposit_match") return depositPlan(p);
  if (k === "odds_boost") {
    const l = findTarget(p.target);
    let P = l?.fair ?? null, est = false;
    if (P == null && p.origOdds != null) { P = 1 / decOf(p.origOdds) / 1.06; est = true; }   // assume a 6% margin on the original price
    if (P == null) return [];
    const c = { leg: l || { label: p.targetLabel || "Boosted bet", key: "boost" }, price: { am: p.boostedOdds }, D: decOf(p.boostedOdds ?? 100), P };
    return [wrap([c], { est })];
  }
  if (["pickem_boost", "free_entry", "discounted_line"].includes(k)) return pickemPlan(p, top, ctx);

  const minLegs = Math.max(1, p.minLegs || 1);
  const feeFree = k === "fee_free";
  let cands = all.filter((l) => legOK(p, l)).map((l) => priced(p, l, { feeFree })).filter(Boolean);

  // Boosts work on a single or a parlay unless the promo says otherwise (Max legs 1 = singles only): price both
  // and show the best of each. Other promos use their own leg rules.
  const mixed = k === "profit_boost" || k === "custom";
  const maxLegs = Math.max(minLegs, p.maxLegs || (mixed ? 3 : minLegs));
  const singlesOK = minLegs <= 1 && k !== "parlay_insurance";
  const parlaysOK = maxLegs >= 2 && (mixed || k === "parlay_boost" || k === "parlay_insurance" || minLegs >= 2);
  let singles = [], parlays = [];
  if (singlesOK) {
    const floor = k === "bonus_bet" ? 0.12 : k === "bet_and_get" ? 0.05 : 0.15;
    const all1 = cands.filter((c) => oddsOK(p, c.D) && c.P > floor).map((c) => wrap([c]));
    all1.sort((a, b) => b.ev - a.ev);
    // one option per game so the list shows real alternatives
    const seen = new Set();
    for (const o of all1) { const g = o.legs[0].leg.gameId + "|" + o.legs[0].leg.market; if (seen.has(g)) continue; seen.add(g); singles.push(o); if (singles.length >= top) break; }
  }
  if (parlaysOK) {
    const pc = cands.filter((c) => c.P * c.D > 0.9 && c.P > 0.2).sort((a, b) => b.P * b.D - a.P * a.D);
    parlays = p.sameGame === "required" ? sgpPlan(p, pc, Math.max(2, minLegs), top, ctx) : beamParlays(p, pc, Math.max(2, minLegs), maxLegs, top, ctx);
  }
  if (!singlesOK) return parlays;
  if (!parlaysOK) return singles;
  return [...singles.slice(0, Math.ceil(top / 2)), ...parlays.slice(0, Math.ceil(top / 2))].sort((a, b) => b.ev - a.ev).slice(0, top);
}

// parlays: beam search over legs from different games
function beamParlays(p, cands, minLegs, maxLegs, top, ctx) {
  const wrap = (cs) => ({ legs: cs, ...value(p, cs, ctx) });
  const perGame = new Map();
  for (const c of cands) { const g = c.leg.gameId; if (!perGame.has(g)) perGame.set(g, []); if (perGame.get(g).length < 3) perGame.get(g).push(c); }
  const pool = [...perGame.values()].flat().slice(0, 120);
  let beam = [{ cs: [], games: new Set(), sports: new Set() }], results = [];
  for (let depth = 1; depth <= maxLegs; depth++) {
    const next = [];
    for (const b of beam) for (const c of pool) {
      if (b.games.has(c.leg.gameId)) continue;
      if (p.onePerSport && b.sports.has(c.leg.league)) continue;
      const cs = [...b.cs, c];
      next.push({ cs, games: new Set([...b.games, c.leg.gameId]), sports: new Set([...b.sports, c.leg.league]) });
    }
    const uniq = new Map();
    for (const x of next) { const kk = keyOf(x.cs); if (!uniq.has(kk)) uniq.set(kk, x); }
    const scored = [...uniq.values()].map((x) => ({ ...x, s: depth >= minLegs ? value(p, x.cs, ctx).ev : x.cs.reduce((v, c) => v * c.P * c.D, 1) }));
    scored.sort((a, b) => b.s - a.s);
    beam = scored.slice(0, 70);
    if (depth >= minLegs) {
      for (const x of beam) {
        const D = x.cs.reduce((v, c) => v * c.D, 1);
        if (!oddsOK(p, D)) continue;
        if (p.onePerSport && p.sports.length && !p.sports.every((s) => x.sports.has(s))) continue;
        results.push(wrap(x.cs));
      }
    }
  }
  const seen = new Set();
  results = results.sort((a, b) => b.ev - a.ev).filter((r) => { const kk = keyOf(r.legs); if (seen.has(kk)) return false; seen.add(kk); return true; });
  return results.slice(0, top);
}

// same-game parlays: joint chance with the same-game correlation estimates; the app prices SGPs its own way
function sgpPlan(p, cands, n, top, ctx) {
  const byGame = new Map();
  for (const c of cands) { if (!byGame.has(c.leg.gameId)) byGame.set(c.leg.gameId, []); byGame.get(c.leg.gameId).push(c); }
  const out = [];
  for (const cs of byGame.values()) {
    const pool = cs.slice(0, 14);
    let beam = [[]];
    for (let d = 0; d < n; d++) {
      const next = [];
      for (const b of beam) for (const c of pool) {
        if (b.some((x) => x.leg.key === c.leg.key || (x.leg.market === c.leg.market && x.leg.player === c.leg.player))) continue;
        if (b.some((x) => Math.abs(corr(x.leg, c.leg)) > 0.12)) continue;     // apps price correlated SGP legs their own way
        next.push([...b, c]);
      }
      const sc = next.map((x) => { const j = parlay(x.map((c) => c.leg), []).joint; return { x, s: j * x.reduce((v, c) => v * c.D, 1) }; });
      sc.sort((a, b) => b.s - a.s); beam = sc.slice(0, 20).map((y) => y.x);
    }
    for (const x of beam.slice(0, 3)) {
      const joint = parlay(x.map((c) => c.leg), []).joint, ind = x.reduce((v, c) => v * c.P, 1);
      const scaled = x.map((c) => ({ ...c, P: c.P * Math.pow(joint / ind, 1 / x.length) }));
      if (!oddsOK(p, x.reduce((v, c) => v * c.D, 1))) continue;
      out.push({ legs: scaled, ...value(p, scaled, ctx), sgp: true });
    }
  }
  return out.sort((a, b) => b.ev - a.ev).slice(0, top);
}

// pick'em: best entry at the app (our chance at the app's line for each pick)
function pickemPlan(p, top, ctx) {
  const all = legs();
  const fed = all.some((l) => l.pickem === p.venue);
  // Each pick's payout: Underdog and Dabble send it (per-pick price vs the standard 1.732 of a 3x 2-pick).
  // PrizePicks and Pick6 set it in the app (0.6x, 0.8x...) and the feed only has a placeholder, so keep to near-even
  // picks, which those apps usually pay at 1x, and show the smallest multiplier each pick needs to be worth it.
  const STD = Math.sqrt(3);
  let pool = all.filter((l) => legOK(p, l) && (fed ? l.pickem === p.venue : (!l.pickem && l.kind === "prop" && l.main)))
    .map((l) => {
      const real = fed && !l.varPay && l.pickDec > 1;
      return { leg: l, price: { am: null }, D: 1, P: l.fair, mult: real ? l.pickDec / STD : 1, needs: l.varPay ? l.minMult ?? (1 / l.fair) / STD : null, varPay: !!l.varPay || !fed };
    })
    // varPay apps: near-even picks only (likely paid 1x); real per-pick payouts (Underdog, Dabble): any side the payout justifies
    .filter((c) => c.varPay ? c.P >= 0.5 && c.P <= 0.59 : c.P >= 0.3 && c.P * c.mult * STD > 0.9)
    .sort((a, b) => b.P * b.mult - a.P * a.mult);
  let fixed = null;
  if (p.kind === "discounted_line") {
    const l = findTarget(p.target);
    const P = pAtLine(l, p.promoLine, p.promoSide || "Over");
    if (P == null) return [];
    fixed = { leg: { ...l, label: `${l.player} ${p.promoSide || "Over"} ${p.promoLine} ${l.market.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ")} (promo line)` }, price: { am: null }, D: 1, P, promo: true };
    pool = pool.filter((c) => c.leg.player !== l.player);
  }
  const n = Math.max(fixed ? 1 : 2, p.picks || 2);
  // best picks, one per game; then alternatives swapping in the next best picks
  const seenG = new Set(fixed ? [fixed.leg.gameId] : []), base = fixed ? [fixed] : [];
  for (const c of pool) { if (base.length >= n) break; if (seenG.has(c.leg.gameId)) continue; seenG.add(c.leg.gameId); base.push(c); }
  if (base.length < n) return [];
  const opts = [base];
  const rest = pool.filter((c) => !base.includes(c) && !seenG.has(c.leg.gameId));
  for (let i = 0; i < Math.min(top - 1, rest.length); i++) {
    const alt = [...base]; alt[alt.length - 1 - (i % Math.max(1, n - (fixed ? 1 : 0)))] = rest[i];
    if (new Set(alt.map((c) => c.leg.gameId)).size === alt.length) opts.push(alt);
  }
  const res = opts.map((cs) => ({ legs: cs, ...value(p, cs, ctx), pick: true, fed }));
  const seen = new Set();
  return res.filter((r) => { const kk = keyOf(r.legs); if (seen.has(kk)) return false; seen.add(kk); return true; }).sort((a, b) => b.ev - a.ev).slice(0, top);
}

// deposit match: the bonus minus what the playthrough costs on the best-priced bets at the venue
function depositPlan(p) {
  const pp = { ...p, minLegs: 1 };
  const cands = legs().filter((l) => legOK(p, l)).map((l) => priced(pp, l)).filter((c) => c && oddsOK(p, c.D) && c.P > 0.3);
  cands.sort((a, b) => b.P * b.D - a.P * a.D);
  const seen = new Set(), best = [];
  for (const c of cands) { if (seen.has(c.leg.gameId)) continue; seen.add(c.leg.gameId); best.push(c); if (best.length >= 25) break; }
  // limits and line moves mean a big playthrough can't all go on the best prices: assume it costs at least 1%
  const ret = Math.min(0.99, best.length ? best.reduce((s, c) => s + c.P * c.D, 0) / best.length : 0.955);
  const amount = p.amount || 0, turnover = (p.playthrough || 1) * (p.ptBase === "both" ? amount + (p.deposit || amount) : amount);
  const cost = turnover * Math.max(0, 1 - ret);
  return best.slice(0, 8).map((c) => ({ legs: [c], ev: amount - cost, P: c.P, D: c.D, risk: turnover, deposit: { amount, turnover, cost, ret },
    note: `Wager $${Math.round(turnover)} to unlock it; at these prices that costs about $${cost.toFixed(2)}` }));
}

// ---------- settling a logged bet that used a promo
export function settleProfit(b, promo, status) {
  if (status === "push" || status === "void") return 0;
  const p = promo ? normalize(promo) : null, k = p ? kindOf(p.kind).key : null;
  const noStake = k === "bonus_bet" || k === "free_entry" || (k === "custom" && !p.stakeBack);
  if (status === "lost") return noStake ? 0 : -b.stake;
  let win = b.stake * (b.dec - 1);
  if (k === "profit_boost" || k === "parlay_boost" || k === "pickem_boost") {
    let extra = win * (p.boost || 0);
    if (p.maxWin) extra = Math.min(extra, p.maxWin);
    win += extra;
  }
  if (k === "custom") win = win * (1 + (p.winBoost || 0)) + (p.extraWin || 0);
  if (k === "free_entry") win = b.stake * b.dec;        // the entry was free: the whole payout is profit
  return Math.round(win * 100) / 100;
}

// ---------- find the bet a promo names (discounted lines, boosted prices) on the board
const STAT_WORDS = [[/pra|points ?\+ ?rebounds ?\+ ?assists|pts\+reb\+ast/, "player_points_rebounds_assists"], [/rebounds|\breb\b|boards/, "player_rebounds"],
  [/assists|\bast\b/, "player_assists"], [/threes|3-?pointers|3pm|three pointers/, "player_threes"], [/receiving yards|rec(?:eiving)? yds/, "player_reception_yds"],
  [/rushing yards|rush yds/, "player_rush_yds"], [/passing yards|pass yds/, "player_pass_yds"], [/receptions|catches/, "player_receptions"],
  [/shots on goal|\bsog\b|shots/, "player_shots_on_goal"], [/saves/, "player_total_saves"], [/strikeouts|\bks?\b/, "pitcher_strikeouts"],
  [/total bases/, "batter_total_bases"], [/\bhits\b/, "batter_hits"], [/points|pts/, "player_points"]];
export function autoTarget(p) {
  const t = " " + String(p.text || "").toLowerCase() + " ";
  const all = legs().filter((l) => !l.pickem && new Date(l.commence) > new Date());
  if (p.kind === "discounted_line") {
    const mk = STAT_WORDS.find(([re]) => re.test(t))?.[1];
    const cands = all.filter((l) => l.kind === "prop" && l.mean != null && l.player && (!mk || l.market === mk)
      && l.player.toLowerCase().split(" ").filter((w) => w.length > 2).slice(-1).every((w) => t.includes(w)));
    if (!cands.length) return p;
    const side = p.promoSide || "Over";
    const l = cands.filter((c) => c.side === side).sort((a, b) => (b.main ? 1 : 0) - (a.main ? 1 : 0))[0] || cands[0];
    return { ...p, target: l.key, targetLabel: l.player, promoSide: side, promoLine: p.promoLine ?? l.point };
  }
  if (p.kind === "odds_boost" && !/\band\b|\+|parlay|sgp/.test(t)) {
    const isML = /\bml\b|moneyline|to win/.test(t);
    const cands = all.filter((l) => l.kind === "line" && l.main && (isML ? l.market === "h2h" : true) && l.ref
      && [l.ref.short, l.ref.name, l.ref.abbr].filter(Boolean).some((n) => n.length > 2 && t.includes(" " + n.toLowerCase())));
    if (cands.length) { const l = cands[0]; return { ...p, target: l.key, targetLabel: l.label }; }
  }
  return p;
}
