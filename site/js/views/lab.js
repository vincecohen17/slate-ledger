import { S, loadAllProps, loadAllAlt } from "../store.js";
import { esc, am, pct, prob, fmtTime, amFromDec, decFromAm, amFromProb, both, bothP, spark, hitRate, bookLogo, $, $$ } from "../util.js";
import { catalog, parlay, priceAt, evPerDollar, legSpec, venueName } from "../lab.js";
import { betImage, logSheet } from "./common.js";
import { normalize, findTarget, pAtLine, promoOpen, payoutFor } from "../promo.js";

const L = { q: "", league: "All", legs: [], customAm: "", boost: 0, stake: 10, compare: [] };
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
  if (x.venue === "custom") { const a = parseFloat(x.am); return isNaN(a) ? null : { am: a, cost: 1 / decFromAm(a), name: "custom" }; }
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
  const custom = parseFloat(L.customAm);
  const dec = !isNaN(custom) ? decFromAm(custom) : r.dec;
  const ev = dec ? evPerDollar(r.joint, dec, L.boost / 100) : null;
  const b = dec ? (dec - 1) * (1 + L.boost / 100) : 0;
  const kelly = dec && b > 0 ? Math.max(0, (b * r.joint - (1 - r.joint)) / b) : 0;
  const pairs = r.pairs.map((p) => `<span class="pill ${p.r > 0 ? "pos" : "neg"}" title="estimated correlation">${esc(legs[p.i].label.slice(0, 22))} ↔ ${esc(legs[p.j].label.slice(0, 22))} ${p.r > 0 ? "+" : ""}${p.r.toFixed(2)}</span>`).join("");
  return `<div class="big-out">
      <div class="kpi"><div class="k">Fair chance</div><div class="v">${prob(r.joint, 1)}</div><div class="s">${r.pairs.length ? `independent ${prob(r.indep, 1)}` : "legs independent"}</div></div>
      <div class="kpi"><div class="k">Fair odds</div><div class="v">${am(amFromProb(r.joint))}</div><div class="s">break-even · ${prob(r.joint, 1)}</div></div>
      <div class="kpi"><div class="k">Your price</div><div class="v">${dec ? am(amFromDec(dec)) : "–"}</div><div class="s">${dec ? prob(1 / dec, 1) + " implied · " : ""}${!isNaN(custom) ? "entered" : "legs multiplied"}${L.boost ? ` · +${L.boost}% boost` : ""}</div></div>
      <div class="kpi"><div class="k">EV</div><div class="v ${ev > 0 ? "pos" : ev < 0 ? "neg" : ""}">${ev != null ? pct(ev) : "–"}</div><div class="s">${ev != null ? `${ev >= 0 ? "+" : ""}$${(ev * L.stake).toFixed(2)} on $${L.stake}` : ""}</div></div>
      <div class="kpi"><div class="k">Kelly ¼</div><div class="v">$${(kelly / 4 * bankroll).toFixed(0)}</div><div class="s">of $${bankroll} bankroll</div></div>
    </div>
    ${pairs ? `<div class="corr">${pairs}</div><p class="note">Same-game legs move together; the fair chance above already adjusts for it (estimated coefficients).</p>` : ""}
    <div class="fgrid" style="margin-top:12px">
      <label class="f">Venue's parlay odds (optional)<input id="lab-custom" inputmode="numeric" value="${esc(L.customAm)}" placeholder="${dec ? am(amFromDec(r.dec || dec)) : "+250"}"></label>
      <label class="f">Profit boost %<input id="lab-boost" inputmode="numeric" value="${L.boost || ""}" placeholder="0"></label>
      <label class="f">Stake $<input id="lab-stake" inputmode="decimal" value="${L.stake}"></label>
      <label class="f">Bankroll $<input id="lab-bank" inputmode="decimal" value="${bankroll}"></label>
    </div>
    <div class="row" style="margin-top:12px;flex-wrap:wrap"><button class="btn" id="lab-log">Log this bet</button><button class="btn ghost" id="lab-cmp">Save to compare</button><button class="btn ghost" id="lab-clear">Clear</button></div>`;
}

function compareHtml() {
  if (!L.compare.length) return "";
  return `<div class="sec-title">Compare<span class="line"></span></div><div class="panel scroll"><table><thead><tr><th>Bet</th><th>Legs</th><th>Fair</th><th>Price</th><th>Boost</th><th>EV</th><th></th></tr></thead><tbody>
    ${L.compare.map((c, i) => `<tr><td style="white-space:normal;max-width:340px">${esc(c.label)}</td><td>${c.n}</td><td class="num">${prob(c.p, 1)}</td><td class="num">${am(c.am)}</td><td class="num">${c.boost ? c.boost + "%" : "–"}</td>
      <td class="num ${c.ev > 0 ? "pos" : "neg"}">${pct(c.ev)}</td><td><button class="x" data-rmc="${i}">×</button></td></tr>`).join("")}</tbody></table></div>`;
}

export function renderLab(el) {
  loadAllProps();
  const cat = catalog();
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
      <div class="results">${res.map((l) => `<button class="res" data-add="${esc(l.key)}">${betImage({ gameId: l.gameId, label: l.label, player: l.player, headshot: l.headshot, league: l.league }, 30)}
        <span class="col"><span class="ellip" style="font-weight:500">${esc(l.label)}</span><span class="tiny muted ellip">${esc(l.matchup)} · ${fmtTime(l.commence)}${l.model ? " · model fair" : ""}</span></span>
        <span class="col" style="align-items:flex-end"><span class="num small">${l.pickem ? "pick'em" : both(l.best?.am)}</span><span class="tiny ${l.best?.edge > 0.005 ? "pos" : "muted"}">fair ${bothP(l.fair)}</span></span></button>`).join("")}</div>
    </div>
    <div class="card">
      <div class="row" style="justify-content:space-between"><b>Your bet</b><span class="tiny muted">${L.legs.length} leg${L.legs.length === 1 ? "" : "s"} · up to 20</span></div>
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
            ${x.leg.last10?.length ? `<span class="l10">${spark(x.leg.last10, x.leg.point, { side: x.leg.side, w: 70, h: 20 })}${hitRate(x.leg.last10, x.leg.point, x.leg.side)}</span>` : ""}</span>
          <select data-ven="${i}" style="padding:5px 6px;font-size:12px">${opts.map((v) => `<option value="${v.book}" ${v.book === x.venue ? "selected" : ""}>${esc(v.name)} ${am(v.am)}</option>`).join("")}
            <option value="any" ${x.venue === "any" ? "selected" : ""}>best yours</option><option value="custom" ${x.venue === "custom" ? "selected" : ""}>custom…</option></select>
          <button class="x" data-rm="${i}" aria-label="remove">×</button></div>
          ${x.venue === "custom" ? `<div class="row" style="padding:4px 0 8px 40px"><input data-cam="${i}" value="${esc(x.am || "")}" placeholder="odds e.g. -115" style="max-width:140px"></div>` : ""}`;
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
  const custom = parseFloat(L.customAm), dec = r ? (!isNaN(custom) ? decFromAm(custom) : r.dec) : null;
  const label = legs.map((l) => l.label).join(" + ");
  if ($("#lab-clear", el)) $("#lab-clear", el).onclick = () => { L.legs = []; L.customAm = ""; L.boost = 0; L.entryX = ""; rerender(); };
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
    logSheet({ label, am: dec ? amFromDec(dec) : "", stake: L.stake, fair: r?.joint, gameId: one?.gameId, league: one?.league,
               matchup: one?.matchup, commence: one?.commence, ...(one ? legSpec(one) : {}), label,
               venue: L.legs[0]?.venue !== "any" && L.legs[0]?.venue !== "custom" ? L.legs[0]?.venue : undefined,
               legs: legs.map(legSpec), source: "lab" });
  };
}
