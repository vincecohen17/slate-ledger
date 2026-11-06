import { whyBlock, edgeFor } from "./market.js";
import { S, loadAllProps, loadAllAlt } from "../store.js";
import { esc, am, pct, prob, fmtTime, amFromDec, decFromAm, amFromProb, both, bothP, spark, hitRate, bookLogo, money, $, $$, odds, oddsP, oddsTxt, oddsFromInput } from "../util.js";
import { catalog, parlay, priceAt, evPerDollar, legSpec, venueName } from "../lab.js";
import { betImage, logSheet } from "./common.js";
import { normalize, findTarget, pAtLine, promoOpen, payoutFor } from "../promo.js";
import { project, summary } from "../projected.js";

const L = { q: "", league: "All", legs: [], customAm: "", boost: 0, stake: 10, compare: [], promoId: null, note: "" };

// ---------- the slip survives leaving the Lab (and a reload): saved by leg key, rebuilt from the live catalog
const SKEY = "sl.labslip";
let pending = [];          // legs sent from other screens that weren't in the catalog yet (props still loading)
function saveSlip() {
  try { sessionStorage.setItem(SKEY, JSON.stringify({ legs: L.legs.map((x) => ({ key: x.leg.key, venue: x.venue, mult: x.mult, am: x.am, promoId: x.leg.promoId || null })),
    boost: L.boost, stake: L.stake, promoId: L.promoId, customAm: L.customAm, pending })); } catch (e) { /* storage blocked */ }
}
let restored = false;
function restoreSlip(cat) {
  if (restored) return;
  restored = true;
  try {
    const d = JSON.parse(sessionStorage.getItem(SKEY) || "null");
    if (!d) return;
    const byKey = new Map(cat.map((l) => [l.key, l]));
    const promos = promoLegs();
    L.legs = (d.legs || []).map((x) => ({ leg: x.promoId ? promos.find((p) => p.promoId === x.promoId) : byKey.get(x.key), venue: x.venue, mult: x.mult, am: x.am })).filter((x) => x.leg);
    Object.assign(L, { boost: d.boost || 0, stake: d.stake || 10, promoId: d.promoId || null, customAm: d.customAm || "" });
    pending = d.pending || [];
  } catch (e) { /* ignore */ }
}

// find the catalog leg for a bet named the way the ledger names it (game, market, side, that side's own line, player);
// a pick'em leg also names its app
function findLeg(cat, spec) {
  const N = (x) => (x || "").toLowerCase().replace(/[^a-z0-9]/g, "");
  return cat.find((l) => l.gameId === spec.gameId && l.market === spec.market && l.side === spec.side &&
    N(l.player) === N(spec.player) && (spec.pickem ? l.pickem === spec.pickem : !l.pickem) &&
    (spec.point == null || legSpec(l).point == null || Math.abs(+legSpec(l).point - +spec.point) < 1e-6) &&
    (!spec.team || !l.team || N(l.team) === N(spec.team)));
}
function placeLeg(cat, spec, opts = {}) {
  const leg = findLeg(cat, spec);
  if (!leg) return false;
  if (!L.legs.some((x) => x.leg.key === leg.key) && L.legs.length < 20) {
    const ven = opts.venue && leg.venues.some((v) => v.book === opts.venue && !v.stale) ? opts.venue : "any";
    L.legs.push({ leg, venue: ven });
  }
  return true;
}
// send a bet to the Lab from anywhere (edges, picks, game pages, promos): adds it to the slip and opens the Lab
export function addToLab(spec, opts = {}) {
  loadAllProps();
  const cat = catalog();
  restoreSlip(cat);
  if (opts.replace) { L.legs = []; L.promoId = null; L.boost = 0; L.customAm = ""; pending = []; }
  if (opts.promoLeg) labWithPromo(opts.promoLeg);
  if (opts.boost != null) L.boost = opts.boost;
  if (opts.stake) L.stake = opts.stake;
  if (opts.promoId) L.promoId = opts.promoId;
  if (opts.note) L.note = opts.note;
  for (const sp of Array.isArray(spec) ? spec : [spec]) if (!placeLeg(cat, sp, opts)) pending.push({ spec: sp, opts: { venue: opts.venue } });
  saveSlip();
  if (location.hash !== "#/lab") location.hash = "#/lab";
}

// ---------- the best bets right now: the board's edges (ranked by edge, the report card and the playbook), skipping kinds
// the record mutes or says to skip, as Lab legs priced at the edge's venue
function bestBets(cat) {
  const now = Date.now(), out = [], seen = new Set();
  for (const e of S.edges || []) {
    if (e.live || e.muted || e.avoid || new Date(e.commence) <= now) continue;
    if (["below", "beyond"].includes(e.supBand)) continue;
    // a price far past the fair is almost always a stale or wrong quote, and alt lines priced by our model or one-sided
    // estimated fairs are kinds the record says to distrust: none of them belong in "best"
    if (e.edge > 0.15 || e.model || e.est || (e.am != null && e.fair * (e.am > 0 ? 1 + e.am / 100 : 1 + 100 / -e.am) > 1.5)) continue;
    const leg = findLeg(cat, { gameId: e.gameId, market: e.market, side: e.side, point: e.point, player: e.player, team: e.team, pickem: e.pickem ? e.book : null });
    if (!leg || seen.has(leg.key + e.book)) continue;
    seen.add(leg.key + e.book);
    out.push({ e, leg: { ...leg, label: e.label || leg.label } });
    if (out.length >= 40) break;
  }
  return out;
}

// suggested builds at one venue from the best bets: the best single, and the best 2- and 3-leg parlays across different
// games (a parlay of +EV legs multiplies their edges: higher expected ROI, much lower chance to cash)
// long-run bankroll growth per bet at full Kelly: the right way to rank bets for ROI that compounds (a +49% EV long shot
// that cashes 18% grows a bankroll slower than a +6% EV bet that cashes 55%)
function growth(p, dec, boost = 0) {
  const bb = (dec - 1) * (1 + boost), f = (bb * p - (1 - p)) / bb;
  if (!(f > 0) || f >= 1) return 0;
  return p * Math.log(1 + f * bb) + (1 - p) * Math.log(1 - f);
}

function builds(best) {
  const byVenue = new Map();
  for (const x of best) {
    if (x.e.pickem || !x.leg.venues.some((v) => v.book === x.e.book)) continue;
    const pr = priceAt(x.leg, x.e.book);
    if (!pr) continue;
    const r = { ...x, dec: 1 / pr.cost, p: x.leg.fair, venue: x.e.book };
    r.ev = r.p * r.dec - 1;
    r.g = growth(r.p, r.dec);
    if (r.ev <= 0) continue;
    if (!byVenue.has(r.venue)) byVenue.set(r.venue, []);
    byVenue.get(r.venue).push(r);
  }
  const boost = L.boost / 100, out = [];
  for (const [venue, rs] of byVenue) {
    rs.sort((a, b) => b.g - a.g);
    out.push({ venue, legs: [rs[0]] });
    for (const n of [2, 3]) {
      const pick = [];
      for (const r of rs) { if (pick.length < n && !pick.some((q) => q.leg.gameId === r.leg.gameId)) pick.push(r); }
      if (pick.length === n) out.push({ venue, legs: pick });
    }
  }
  for (const o of out) {
    o.p = o.legs.reduce((a, r) => a * r.p, 1);
    o.dec = o.legs.reduce((a, r) => a * r.dec, 1);
    o.ev = evPerDollar(o.p, o.dec, boost);
    o.g = growth(o.p, o.dec, boost);
    const bb = (o.dec - 1) * (1 + boost);
    o.kelly = Math.max(0, (bb * o.p - (1 - o.p)) / bb);
  }
  const top = (n) => out.filter((o) => o.legs.length === n && o.g > 0).sort((a, b) => b.g - a.g)[0];
  return [top(1), top(2), top(3)].filter(Boolean);
}

// today's open bets as one distribution (the same math as Projected profit), with and without the bet in the slip
function dayOdds(extra) {
  const pend = [...S.bets.values()].filter((b) => b.status === "pending");
  const rows = pend.map((b) => project(b, b.promoId ? S.promos.get(b.promoId) : null)).filter(Boolean);
  const base = rows.length ? summary(rows) : null;
  let withIt = null;
  if (extra) withIt = summary([...rows.map((r) => ({ ...r })), { b: {}, p: extra.p, win: extra.win, lose: extra.lose, srcs: [], ev: extra.p * extra.win + (1 - extra.p) * extra.lose }]);
  return { n: rows.length, base, withIt };
}
let bankroll = 500;
try { bankroll = +localStorage.getItem("sl.bankroll") || 500; } catch (e) { /* storage blocked */ }

// ---------- unused special picks (free picks, discounted lines) as legs: put one in the slip and the slip becomes that
// app's pick'em entry, priced the app's way (the promo pick's own multiplier times the others', or the payout table)
const STD = Math.sqrt(3);
const mktW = (m) => (m || "").replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ");
export function promoLegs() {
  const out = [];
  for (const p0 of S.promos.values()) {
    const p = normalize(p0);
    if (p.kind !== "discounted_line" || !promoOpen(p0) || !p.target || p.promoLine == null) continue;
    const l = findTarget(p.target);
    if (!l || new Date(l.commence) < new Date()) continue;
    const side = p.promoSide || "Over", P0 = pAtLine(l, p.promoLine, side);
    if (P0 == null) continue;
    out.push({ ...l, key: "promo|" + p.id, promoId: p.id, side, point: p.promoLine, pickemLine: p.promoLine, ou: side === "Over" ? 1 : -1,
      fair: Math.min(P0, 0.98), venues: [], best: null, pickem: null,
      label: `${l.player} ${side} ${p.promoLine} ${mktW(l.market)}`, promoTag: `${venueName(p.venue)} ${p.promoMult ? `${p.promoMult}x ` : ""}promo pick` });
  }
  return out;
}
// start the slip from a promo (the Promos page's "Build your own" button)
export function labWithPromo(id) {
  const leg = promoLegs().find((l) => l.promoId === id);
  if (leg) { L.legs = L.legs.filter((x) => !x.leg.promoId); L.legs.unshift({ leg, venue: "promo" }); L.customAm = ""; L.entryX = ""; const st = S.promos.get(id)?.stake; if (st) L.stake = st; }
}
// the entry when a promo pick is in the slip: each leg's multiplier, the total, and what's assumed
function entry() {
  const pl = L.legs.find((x) => x.leg.promoId);
  if (!pl) return null;
  const p = normalize(S.promos.get(pl.leg.promoId) || {}), n = L.legs.length, per = !!p.promoMult;
  const mults = L.legs.map((x) => {
    if (x.leg.promoId) return { m: per ? p.promoMult : null };
    const typed = parseFloat(x.mult);
    if (!isNaN(typed) && typed > 0) return { m: typed, typed: true };
    if (x.leg.pickem === p.venue && x.leg.pickDec > 1 && !x.leg.varPay) return { m: x.leg.pickDec, feed: true };
    if (p.otherMult) return { m: p.otherMult, typed: true };
    return { m: STD, assumed: true };
  });
  const typedX = parseFloat(L.entryX);
  const table = p.mult || payoutFor(p.venue, n);
  const pays = !isNaN(typedX) && typedX > 1 ? typedX : per ? mults.reduce((a, x) => a * x.m, 1) : table;
  return { p, per, mults, pays, typed: !isNaN(typedX) && typedX > 1, assumed: per && mults.some((x) => x.assumed) };
}

function legPrice(x) {
  // a typed price in any form: American (-115), decimal (1.87x or 1.87) or percent / cents (53.5%, 54c, .535)
  if (x.venue === "custom") { const a = oddsFromInput(x.am); return isNaN(a) || !a ? null : { am: a, cost: 1 / decFromAm(a), name: "custom" }; }
  return priceAt(x.leg, x.venue);
}

function search(cat) {
  const q = L.q.trim().toLowerCase();
  let r = cat.filter((l) => (L.league === "All" || l.league === L.league) && new Date(l.commence) > new Date());
  if (q) {
    const words = q.split(/\s+/);
    r = r.filter((l) => words.every((w) => (l.label + " " + l.matchup).toLowerCase().includes(w)));
    r.sort((a, b) => (b.main ? 1 : 0) - (a.main ? 1 : 0) || (b.best?.edge ?? -1) - (a.best?.edge ?? -1));
  } else {
    r = r.filter((l) => l.best).sort((a, b) => (b.best.edge ?? -1) - (a.best.edge ?? -1));
  }
  return r.slice(0, 60);
}

function entryResult() {
  const E = entry(), legs = L.legs.map((x) => x.leg), r = parlay(legs, legs.map(() => null));
  const stake = L.stake, ev = r.joint * E.pays - 1;
  const need = (E.p.picks || 2);
  return `<div class="big-out">
      <div class="kpi"><div class="k">Hits all</div><div class="v">${prob(r.joint, 1)}</div><div class="s">${r.pairs.length ? `independent ${prob(r.indep, 1)}` : "picks independent"}</div></div>
      <div class="kpi"><div class="k">Entry pays</div><div class="v">${E.pays ? `${(+E.pays).toFixed(2)}x` : "–"}</div><div class="s">${E.typed ? "entered" : E.per ? L.legs.map((x, i) => `${E.mults[i].m.toFixed(2)}x`).join(" × ") : `${venueName(E.p.venue)} ${legs.length}-pick table`}</div></div>
      <div class="kpi"><div class="k">EV</div><div class="v ${ev > 0 ? "pos" : ev < 0 ? "neg" : ""}">${E.pays ? pct(ev) : "–"}</div><div class="s">${E.pays ? `${ev >= 0 ? "+" : ""}$${(ev * stake).toFixed(2)} on $${stake}` : ""}</div></div>
      <div class="kpi"><div class="k">Break-even</div><div class="v">${E.pays ? prob(1 / E.pays, 1) : "–"}</div><div class="s">chance the entry needs</div></div>
    </div>
    ${legs.length < need ? `<p class="note neg">This promo needs ${need} picks: add ${need - legs.length} more from the left.</p>` : ""}
    ${E.assumed && !E.typed ? `<p class="note">${esc(venueName(E.p.venue))} sets each pick's multiplier and the feed doesn't carry it: picks without one typed in are assumed at 1.73x. Type what the app shows next to each pick for the exact payout.</p>` : ""}
    <div class="fgrid" style="margin-top:12px">
      <label class="f">Entry pays (x), if the app shows the total<input id="lab-entryx" inputmode="decimal" value="${esc(L.entryX || "")}" placeholder="${E.pays ? (+E.pays).toFixed(2) : ""}"></label>
      <label class="f">Stake $<input id="lab-stake" inputmode="decimal" value="${stake}"></label>
    </div>
    <div class="row" style="margin-top:12px;flex-wrap:wrap"><button class="btn" id="lab-log">Log this entry</button><button class="btn ghost" id="lab-clear">Clear</button></div>`;
}

function result() {
  const legs = L.legs.map((x) => x.leg), prices = L.legs.map(legPrice);
  if (!legs.length) return `<div class="empty" style="padding:28px 8px">Add legs from the left. One leg prices a straight bet; two or more prices a parlay with same-game correlation.</div>`;
  if (entry()) return entryResult();
  const r = parlay(legs, prices);
  const custom = oddsFromInput(L.customAm, L.legs[0]?.venue);
  const dec = !isNaN(custom) && custom ? decFromAm(custom) : r.dec;
  const ev = dec ? evPerDollar(r.joint, dec, L.boost / 100) : null;
  const b = dec ? (dec - 1) * (1 + L.boost / 100) : 0;
  const kelly = dec && b > 0 ? Math.max(0, (b * r.joint - (1 - r.joint)) / b) : 0;
  const pairs = r.pairs.map((p) => `<span class="pill ${p.r > 0 ? "pos" : "neg"}" title="estimated correlation">${esc(legs[p.i].label.slice(0, 22))} ↔ ${esc(legs[p.j].label.slice(0, 22))} ${p.r > 0 ? "+" : ""}${p.r.toFixed(2)}</span>`).join("");
  return `<div class="big-out">
      <div class="kpi"><div class="k">Fair chance</div><div class="v">${prob(r.joint, 1)}</div><div class="s">${r.pairs.length ? `independent ${prob(r.indep, 1)}` : "legs independent"}</div></div>
      <div class="kpi"><div class="k">Fair odds</div><div class="v">${oddsP(r.joint)}</div><div class="s">break-even</div></div>
      <div class="kpi"><div class="k">Your price</div><div class="v">${dec ? odds(amFromDec(dec)) : "–"}</div><div class="s">${!isNaN(custom) ? "entered" : "legs multiplied"}${L.boost ? ` · +${L.boost}% boost` : ""}</div></div>
      <div class="kpi"><div class="k">EV</div><div class="v ${ev > 0 ? "pos" : ev < 0 ? "neg" : ""}">${ev != null ? pct(ev) : "–"}</div><div class="s">${ev != null ? `${ev >= 0 ? "+" : ""}$${(ev * L.stake).toFixed(2)} on $${L.stake}` : ""}</div></div>
      <div class="kpi"><div class="k">Kelly ¼</div><div class="v">$${(kelly / 4 * bankroll).toFixed(0)}</div><div class="s">of $${bankroll} bankroll</div></div>
    </div>
    ${pairs ? `<div class="corr">${pairs}</div><p class="note">Same-game legs move together; the fair chance above already adjusts for it (estimated coefficients).</p>` : ""}
    ${dec ? riskHtml(r, dec, prices) : ""}
    <div class="fgrid" style="margin-top:12px">
      <label class="f">Venue's parlay odds (optional) <span class="tiny muted">American, decimal or %</span><input id="lab-custom" autocomplete="off" value="${esc(L.customAm)}" placeholder="${dec ? `${am(amFromDec(r.dec || dec))} or ${(+(r.dec || dec).toFixed(2))}x` : "+250, 3.5x or 28.6%"}">
        ${L.customAm && !isNaN(oddsFromInput(L.customAm, L.legs[0]?.venue)) ? `<span class="tiny muted">= ${oddsTxt(oddsFromInput(L.customAm, L.legs[0]?.venue))}</span>` : L.customAm ? `<span class="tiny neg">couldn't read that price</span>` : ""}</label>
      <label class="f">Profit boost %<input id="lab-boost" inputmode="numeric" value="${L.boost || ""}" placeholder="0"></label>
      <label class="f">Stake $<input id="lab-stake" inputmode="decimal" value="${L.stake}"></label>
      <label class="f">Bankroll $<input id="lab-bank" inputmode="decimal" value="${bankroll}"></label>
    </div>
    <div class="row" style="margin-top:12px;flex-wrap:wrap"><button class="btn" id="lab-log">Log this bet</button><button class="btn ghost" id="lab-cmp">Save to compare</button><button class="btn ghost" id="lab-clear">Clear</button></div>`;
}

// how this bet changes today's chance to finish up, and (for a parlay) the same legs played as straight bets
function riskHtml(r, dec, prices) {
  const stake = L.stake || 0, boost = L.boost / 100;
  const win = stake * (dec - 1) * (1 + boost), lose = -stake;
  const D = dayOdds({ p: r.joint, win, lose });
  const pc = (x) => `${Math.round(x * 100)}%`;
  let h = `<div class="lab-risk"><div class="row" style="gap:14px;flex-wrap:wrap">
    <div><div class="tiny muted">Chance this bet cashes</div><b>${prob(r.joint, 1)}</b></div>
    ${D.base ? `<div><div class="tiny muted">Your ${D.n} open bet${D.n === 1 ? "" : "s"} finish up</div><b>${pc(D.base.up)}</b> <span class="tiny muted">EV ${money(D.base.ev)}</span></div>` : ""}
    <div><div class="tiny muted">${D.base ? "With this bet added" : "Finish up with this bet"}</div><b class="${D.base && D.withIt.up >= D.base.up ? "pos" : D.base ? "neg" : ""}">${pc(D.withIt.up)}</b> <span class="tiny muted">EV ${money(D.withIt.ev)}</span></div></div>`;
  const legs = L.legs.map((x) => x.leg);
  if (legs.length > 1 && prices.every(Boolean) && !L.customAm) {
    const each = stake / legs.length;
    const rows = legs.map((l, i) => ({ b: {}, p: l.fair, win: each * (1 / prices[i].cost - 1), lose: -each, srcs: [] }));
    const st = summary(rows), evS = rows.reduce((a, x) => a + x.p * x.win + (1 - x.p) * x.lose, 0);
    const evP = r.joint * win + (1 - r.joint) * lose;
    h += `<div class="small" style="margin-top:8px">As a parlay: EV <b class="${evP >= 0 ? "pos" : "neg"}">${money(evP)}</b>, cashes ${prob(r.joint, 1)}.
      The same legs as ${legs.length} straight bets of ${money(each)}: EV <b class="${evS >= 0 ? "pos" : "neg"}">${money(evS)}</b>, finish up ${pc(st.up)}.
      ${evP > evS && evS > 0 ? "The parlay earns more on average but cashes far less often." : evS >= evP ? "Straights earn as much or more and finish up more often." : ""}</div>`;
  }
  return h + `<p class="note" style="margin:6px 0 0">Open bets use their live chances (Projected profit math). More separate +EV bets, at shorter odds and smaller stakes each, raise the chance of a winning day; parlays and long shots lower it.</p></div>`;
}

function compareHtml() {
  if (!L.compare.length) return "";
  return `<div class="sec-title">Compare<span class="line"></span></div><div class="panel scroll"><table><thead><tr><th>Bet</th><th>Legs</th><th>Fair</th><th>Price</th><th>Boost</th><th>EV</th><th></th></tr></thead><tbody>
    ${L.compare.map((c, i) => `<tr><td style="white-space:normal;max-width:340px">${esc(c.label)}</td><td>${c.n}</td><td class="num">${prob(c.p, 1)}</td><td class="num">${odds(c.am)}</td><td class="num">${c.boost ? c.boost + "%" : "–"}</td>
      <td class="num ${c.ev > 0 ? "pos" : "neg"}">${pct(c.ev)}</td><td><button class="x" data-rmc="${i}">×</button></td></tr>`).join("")}</tbody></table></div>`;
}

function bestHtml(cat) {
  if (L.q) return "";
  const best0 = bestBets(cat).filter((x) => L.league === "All" || x.leg.league === L.league);
  const mdOf = (x) => ["lag", "move"].includes(x.e.why?.k);
  const md = best0.filter(mdOf).sort((a, b) => (b.e.why.s - a.e.why.s) || (b.e.edge - a.e.edge)), best = best0.filter((x) => !mdOf(x));
  const picks = [...S.picks.values()].filter((p) => p.status === "open" && new Date(p.commence) > new Date() && p.tier !== "likely");
  const pk = picks.map((p) => ({ p, leg: findLeg(cat, { gameId: p.gameId, market: p.market, side: p.side, point: p.point, player: p.player, team: p.team }) })).filter((x) => x.leg);
  const B = builds(best);
  const tag = (e) => [...(e.lean || []).slice(0, 1).map((c) => `<span class="pill pos" title="on the playbook">▲ ${esc(c.k)}</span>`),
    e.supBand && ["between", "near"].includes(e.supBand) ? `<span class="pill pos" title="our model sits between the price and the fair">model backs it</span>` : ""].join("");
  return `${pk.length ? `<div class="tiny muted" style="margin-top:10px">Our picks today</div><div class="results">${pk.map(({ p, leg }) => { const v = (p.playTo || []).find((x) => x.inRange);
      return `<button class="res" data-pick="${esc(p.id)}">${betImage({ gameId: leg.gameId, label: leg.label, player: leg.player, headshot: leg.headshot, league: leg.league }, 30)}
      <span class="col"><span class="ellip" style="font-weight:500">${esc(p.selection)}</span><span class="tiny muted ellip">${esc(p.tier)} · ${p.units}u · ${fmtTime(p.commence)}</span></span>
      <span class="col" style="align-items:flex-end"><span class="num small">${v ? odds(v.price) : "–"}</span><span class="tiny muted">${v ? esc(v.name || venueName(v.venue)) : "not in range"}</span></span></button>`; }).join("")}</div>` : ""}
    ${B.length ? `<div class="tiny muted" style="margin-top:10px">Suggested builds ${L.boost ? `(with your ${L.boost}% boost)` : "(best bets at one venue, different games)"} · ranked by long-run bankroll growth, not raw EV</div><div class="results">${B.map((o, i) => `<button class="res" data-build="${i}">
      <span class="col" style="min-width:0"><span class="ellip" style="font-weight:500">${o.legs.length === 1 ? "Best single" : `Best ${o.legs.length}-leg parlay`}: ${esc(o.legs.map((x) => x.leg.label).join(" + "))}</span>
      <span class="tiny muted">${bookLogo(o.venue, venueName(o.venue), 13)} ${esc(venueName(o.venue))} · cashes ${prob(o.p, 1)} · ¼ Kelly $${(o.kelly / 4 * bankroll).toFixed(0)}</span></span>
      <span class="col" style="align-items:flex-end"><span class="num small">${odds(amFromDec(o.dec))}</span><span class="tiny nowrap ${o.ev > 0 ? "pos" : "neg"}">EV ${pct(o.ev)}</span></span></button>`).join("")}</div>` : ""}
    ${md.length ? `<div class="tiny" style="margin-top:10px"><b class="pos">⚡ Driven by the market now</b> <span class="muted">· the sharp books moved toward these and the venue hasn't caught up (tap to add, then check the value below)</span></div><div class="results">${md.slice(0, 10).map(({ e, leg }) => `<button class="res" data-best="${esc(leg.key)}|${esc(e.book)}">${betImage({ gameId: leg.gameId, label: leg.label, player: leg.player, headshot: leg.headshot, league: leg.league }, 30)}
      <span class="col" style="min-width:0"><span class="ellip" style="font-weight:500">${esc(leg.label)}</span><span class="tiny muted ellip">${esc(leg.matchup)} · ${fmtTime(leg.commence)}</span>${whyBlock(e, { compact: true })}<span class="row" style="gap:4px;flex-wrap:wrap">${tag(e)}</span></span>
      <span class="col" style="align-items:flex-end"><span class="num small">${e.pickem ? "pick'em" : odds(e.am)}</span><span class="tiny muted">${bookLogo(e.book, e.bookName, 12)} ${esc(e.bookName)}</span><span class="tiny pos">value +${(e.edge * 100).toFixed(1)}%</span></span></button>`).join("")}</div>` : ""}
    ${best.length ? `<div class="tiny muted" style="margin-top:10px">Best bets now · ranked by edge and what has actually won (muted and skip kinds left out)</div><div class="results">${best.slice(0, 25).map(({ e, leg }) => `<button class="res" data-best="${esc(leg.key)}|${esc(e.book)}">${betImage({ gameId: leg.gameId, label: leg.label, player: leg.player, headshot: leg.headshot, league: leg.league }, 30)}
      <span class="col" style="min-width:0"><span class="ellip" style="font-weight:500">${esc(leg.label)}</span><span class="tiny muted ellip">${esc(leg.matchup)} · ${fmtTime(leg.commence)}</span>${whyBlock(e, { compact: true })}<span class="row" style="gap:4px;flex-wrap:wrap">${tag(e)}</span></span>
      <span class="col" style="align-items:flex-end"><span class="num small">${e.pickem ? "pick'em" : odds(e.am)}</span><span class="tiny muted">${bookLogo(e.book, e.bookName, 12)} ${esc(e.bookName)}</span><span class="tiny pos">value +${(e.edge * 100).toFixed(1)}%</span></span></button>`).join("")}</div>` : ""}`;
}

export function renderLab(el) {
  loadAllProps();
  const cat = catalog();
  restoreSlip(cat);
  if (pending.length) pending = pending.filter((x) => !placeLeg(cat, x.spec, x.opts));      // props that arrived since
  saveSlip();
  const leagues = ["All", ...new Set(cat.map((l) => l.league))];
  const res = search(cat), promos = promoLegs().filter((l) => !L.legs.some((x) => x.leg.key === l.key));
  el.innerHTML = `<div class="lab" style="margin-top:18px">
    <div class="card">
      <input id="lab-q" placeholder="Search a team, player or bet (e.g. brunson points, knicks)" value="${esc(L.q)}" autocomplete="off">
      <div class="bar" style="margin:10px 0 0">${leagues.map((lg) => `<button class="chip ${lg === L.league ? "on" : ""}" data-lg="${esc(lg)}">${esc(lg)}</button>`).join("")}</div>
      <div class="row tiny muted" style="margin-top:8px;justify-content:space-between;gap:8px;flex-wrap:wrap"><span>${L.q ? `${res.length} matches` : "Best values right now"} · tap to add</span>
        ${S.allAlt ? `<span>alt lines on for every game</span>` : `<button class="btn sm ghost" id="lab-alt" title="Main lines and props load for every game; alternates load for games you open. This loads every game's alternates (about 35 MB).">Search alt lines too</button>`}</div>
      ${promos.length ? `<div class="tiny muted" style="margin-top:10px">Your promo picks (unused)</div><div class="results lab-promos">${promos.map((l) => `<button class="res" data-addp="${esc(l.promoId)}">${betImage({ gameId: l.gameId, label: l.label, player: l.player, headshot: l.headshot, league: l.league }, 30)}
        <span class="col"><span class="ellip" style="font-weight:500">${esc(l.label)}</span><span class="tiny muted ellip">${esc(l.matchup)} · ${fmtTime(l.commence)}</span></span>
        <span class="col" style="align-items:flex-end"><span class="pill pos">${esc(l.promoTag)}</span><span class="tiny muted">fair ${prob(l.fair)}</span></span></button>`).join("")}</div>` : ""}
      ${bestHtml(cat)}
      ${L.q ? "" : `<div class="tiny muted" style="margin-top:10px">Everything else by edge</div>`}
      <div class="results">${res.map((l) => `<button class="res" data-add="${esc(l.key)}">${betImage({ gameId: l.gameId, label: l.label, player: l.player, headshot: l.headshot, league: l.league }, 30)}
        <span class="col"><span class="ellip" style="font-weight:500">${esc(l.label)}</span><span class="tiny muted ellip">${esc(l.matchup)} · ${fmtTime(l.commence)}${l.model ? " · model fair" : ""}</span></span>
        <span class="col" style="align-items:flex-end"><span class="num small">${l.pickem ? "pick'em" : both(l.best?.am)}</span><span class="tiny ${l.best?.edge > 0.005 ? "pos" : "muted"}">fair ${bothP(l.fair)}</span></span></button>`).join("")}</div>
    </div>
    <div class="card">
      <div class="row" style="justify-content:space-between"><b>Your bet</b><span class="tiny muted">${L.legs.length} leg${L.legs.length === 1 ? "" : "s"} · up to 20</span></div>
      ${L.promoId && S.promos.get(L.promoId) ? `<div class="tiny" style="margin:4px 0"><span class="pill pos">promo: ${esc(S.promos.get(L.promoId).title || "attached")}</span> logs with this promo attached</div>` : ""}
      ${pending.length ? `<div class="tiny muted" style="margin:4px 0">${pending.length} leg${pending.length > 1 ? "s" : ""} still loading from the board…</div>` : ""}
      <div>${L.legs.map((x, i) => {
        const E = entry();
        if (E) {
          const mm = E.mults[i];
          return `<div class="leg">${betImage({ gameId: x.leg.gameId, label: x.leg.label, player: x.leg.player, headshot: x.leg.headshot }, 30)}
            <span class="col"><span class="ellip" style="font-weight:500">${esc(x.leg.label)}</span><span class="tiny muted">our chance ${prob(x.leg.fair)}${x.leg.promoId ? ` · <span class="pill pos">${esc(x.leg.promoTag)}</span>` : ""}</span></span>
            ${x.leg.promoId ? (E.per ? `<span class="num small">${E.p.promoMult}x</span>` : "<span></span>") : E.per ? `<input data-mx="${i}" inputmode="decimal" value="${esc(x.mult || "")}" placeholder="${mm.m.toFixed(2)}x${mm.assumed ? "?" : ""}" title="the multiplier the app shows for this pick" style="width:78px;padding:5px 6px;font-size:12px">` : "<span></span>"}
            <button class="x" data-rm="${i}" aria-label="remove">×</button></div>`;
        }
        const pr = legPrice(x);
        const opts = x.leg.venues.filter((v) => !v.stale).sort((a, b) => a.cost - b.cost);
        return `<div class="leg">${betImage({ gameId: x.leg.gameId, label: x.leg.label, player: x.leg.player, headshot: x.leg.headshot }, 30)}
          <span class="col"><span class="ellip" style="font-weight:500">${esc(x.leg.label)}</span><span class="tiny muted">fair ${bothP(x.leg.fair)} · ${pr ? `${pr.book ? bookLogo(pr.book, pr.name, 13) + " " : ""}${esc(pr.name)} ${both(pr.am)}` : "no price"}${x.leg.ourModel != null ? ` · our model ${prob(x.leg.ourModel)}` : ""}</span>
            ${x.leg.last10?.length ? `<span class="l10">${spark(x.leg.last10, x.leg.point, { side: x.leg.side, w: 70, h: 20 })}${hitRate(x.leg.last10, x.leg.point, x.leg.side)}</span>` : ""}${(() => { const ev = pr && x.leg.fair != null ? x.leg.fair * decFromAm(pr.am) - 1 : null, ed = edgeFor(legSpec(x.leg), x.venue);
              return `<span class="tiny">${ev != null ? `<b class="${ev > 0 ? "pos" : "neg"}" title="our fair chance times the price you'd get, minus 1">value ${pct(ev)}</b>` : ""}</span>${ed?.why ? whyBlock(ed, { compact: true }) : ""}`; })()}</span>
          <select data-ven="${i}" style="padding:5px 6px;font-size:12px">${opts.map((v) => `<option value="${v.book}" ${v.book === x.venue ? "selected" : ""}>${esc(v.name)} ${oddsTxt(v.am)}</option>`).join("")}
            <option value="any" ${x.venue === "any" ? "selected" : ""}>best yours</option><option value="custom" ${x.venue === "custom" ? "selected" : ""}>custom…</option></select>
          <button class="x" data-rm="${i}" aria-label="remove">×</button></div>
          ${x.venue === "custom" ? `<div class="row" style="padding:4px 0 8px 40px"><input data-cam="${i}" autocomplete="off" value="${esc(x.am || "")}" placeholder="-115, 1.87x or 53.5%" style="max-width:170px">
            ${x.am ? (isNaN(oddsFromInput(x.am)) ? `<span class="tiny neg" style="margin-left:8px">couldn't read that price</span>` : `<span class="tiny muted" style="margin-left:8px">= ${oddsTxt(oddsFromInput(x.am))}</span>`) : ""}</div>` : ""}`;
      }).join("")}</div>
      ${result()}
    </div></div>${compareHtml()}`;

  let pend = false;
  const rerender = () => { if (pend) return; pend = true; setTimeout(() => { pend = false; renderLab(el); }); };
  const q = $("#lab-q", el);
  const la = $("#lab-alt", el); if (la) la.onclick = () => loadAllAlt();
  q.oninput = () => { L.q = q.value; clearTimeout(q._t); q._t = setTimeout(() => { renderLab(el); const n = $("#lab-q", el); n.focus(); n.setSelectionRange(n.value.length, n.value.length); }, 200); };
  $$("[data-lg]", el).forEach((b) => b.onclick = () => { L.league = b.dataset.lg; rerender(); });
  $$("[data-add]", el).forEach((b) => b.onclick = () => {
    if (L.legs.length >= 20) return;
    const leg = cat.find((l) => l.key === b.dataset.add);
    if (leg && !L.legs.some((x) => x.leg.key === leg.key)) L.legs.push({ leg, venue: "any" });
    rerender();
  });
  const num = (id, fn) => { const i = $(id, el); if (i) i.onchange = () => { fn(i.value); rerender(); }; };
  $$("[data-rm]", el).forEach((b) => b.onclick = () => { L.legs.splice(+b.dataset.rm, 1); rerender(); });
  $$("[data-addp]", el).forEach((b) => b.onclick = () => { labWithPromo(b.dataset.addp); rerender(); });
  $$("[data-best]", el).forEach((b) => b.onclick = () => {
    const [key, book] = b.dataset.best.split("|"), leg = cat.find((l) => l.key === key);
    if (leg && !L.legs.some((x) => x.leg.key === key) && L.legs.length < 20) L.legs.push({ leg, venue: leg.venues.some((v) => v.book === book && !v.stale) ? book : "any" });
    rerender();
  });
  $$("[data-pick]", el).forEach((b) => b.onclick = () => {
    const p = S.picks.get(b.dataset.pick); if (!p) return;
    const v = (p.playTo || []).find((x) => x.inRange);
    placeLeg(cat, { gameId: p.gameId, market: p.market, side: p.side, point: p.point, player: p.player, team: p.team }, { venue: v?.venue });
    rerender();
  });
  $$("[data-build]", el).forEach((b) => b.onclick = () => {
    const o = builds(bestBets(cat))[+b.dataset.build]; if (!o) return;
    L.legs = o.legs.map((x) => ({ leg: x.leg, venue: o.venue })); L.customAm = "";
    rerender();
  });
  $$("[data-mx]", el).forEach((i) => i.onchange = () => { L.legs[+i.dataset.mx].mult = i.value.replace(/x/i, "").trim(); rerender(); });
  num("#lab-entryx", (v) => { L.entryX = v.replace(/x/i, "").trim(); });
  $$("[data-ven]", el).forEach((s) => s.onchange = () => { L.legs[+s.dataset.ven].venue = s.value; rerender(); });
  $$("[data-cam]", el).forEach((i) => i.onchange = () => { L.legs[+i.dataset.cam].am = i.value; rerender(); });
  num("#lab-custom", (v) => { L.customAm = v.trim(); });
  num("#lab-boost", (v) => { L.boost = parseFloat(v) || 0; });
  num("#lab-stake", (v) => { L.stake = parseFloat(v) || 0; });
  num("#lab-bank", (v) => { bankroll = parseFloat(v) || 0; try { localStorage.setItem("sl.bankroll", bankroll); } catch (e) { /* ignore */ } });
  $$("[data-rmc]", el).forEach((b) => b.onclick = () => { L.compare.splice(+b.dataset.rmc, 1); rerender(); });
  const legs = L.legs.map((x) => x.leg), prices = L.legs.map(legPrice);
  const r = legs.length ? parlay(legs, prices) : null;
  const custom = oddsFromInput(L.customAm, L.legs[0]?.venue), dec = r ? (!isNaN(custom) && custom ? decFromAm(custom) : r.dec) : null;
  const label = legs.map((l) => l.label).join(" + ");
  if ($("#lab-clear", el)) $("#lab-clear", el).onclick = () => { L.legs = []; L.customAm = ""; L.boost = 0; L.entryX = ""; L.promoId = null; pending = []; saveSlip(); rerender(); };
  if ($("#lab-cmp", el)) $("#lab-cmp", el).onclick = () => {
    if (!dec) return;
    L.compare.push({ label, n: legs.length, p: r.joint, am: amFromDec(dec), boost: L.boost, ev: evPerDollar(r.joint, dec, L.boost / 100) });
    rerender();
  };
  const E = entry();
  if (E && $("#lab-log", el)) { $("#lab-log", el).onclick = () => {
    const re = parlay(legs, legs.map(() => null));
    logSheet({ label, am: E.pays ? amFromDec(E.pays) : "", stake: L.stake, fair: re.joint, venue: E.p.venue, promoId: E.p.id,
      ...(legs.length === 1 ? legSpec(legs[0]) : {}), label, legs: legs.map(legSpec), source: "promo" });
  }; return; }
  if ($("#lab-log", el)) $("#lab-log", el).onclick = () => {
    const one = legs.length === 1 ? legs[0] : null;
    const ed = one ? edgeFor(legSpec(one), L.legs[0]?.venue) : null;
    logSheet({ label, am: dec ? amFromDec(dec) : "", stake: L.stake, fair: r?.joint, gameId: one?.gameId, league: one?.league, why: ed?.why?.k || null, whyText: ed?.why?.t || null,
               matchup: one?.matchup, commence: one?.commence, ...(one ? legSpec(one) : {}), label,
               venue: L.legs[0]?.venue !== "any" && L.legs[0]?.venue !== "custom" ? L.legs[0]?.venue : undefined,
               legs: legs.map(legSpec), source: "lab", ...(L.promoId ? { promoId: L.promoId } : {}) });
  };
}
