import { S } from "../store.js";
import { esc, bookLogo, am, pct, prob, fmtTime, money, $$ } from "../util.js";
import { edgeCard, bindLog } from "./common.js";

const F = { league: "All", type: "All", min: 0.015, venue: "All", logic: "All", tab: "edges" };

// arbs and middles across your venues (all-in prices), with the stake split for a chosen total
let oppTotal = 100;
function oppCard(o) {
  const tot = oppTotal, st = o.stakes.map((x) => x * tot);
  const ret = o.legs.map((l, i) => st[i] * l.dec);
  const head = o.kind === "arb" ? `<span class="pill pos">arb +${(o.profit * 100).toFixed(2)}%</span>` : `<span class="pill s">middle · EV ${pct(o.ev)}</span>`;
  return `<div class="card op-card ${o.suspect ? "op-sus" : ""}">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">${head}<span class="tiny muted">${esc(o.league)} · ${esc(o.matchup)} · ${fmtTime(o.commence)}</span></div>
    ${o.suspect ? `<div class="tiny neg" style="margin-top:4px">Too good to be true? A leg is far from the sharp fair: check the venue's rules (void on DNP, overtime) and that the price is live before placing either side.</div>` : ""}
    <div class="col" style="gap:6px;margin-top:8px">${o.legs.map((l, i) => `<div class="op-leg">${bookLogo(l.book, l.name, 22)}<span class="col" style="gap:0;min-width:0"><b class="op-lab">${esc(l.label)}</b><span class="tiny muted">${esc(l.name)}${l.fair != null ? ` · fair ${prob(l.fair)}` : ""}</span></span>
      <b class="num">${am(l.am)}</b><span class="num op-stake">${money(st[i])}</span></div>`).join("")}</div>
    <div class="tiny" style="margin-top:8px">${o.kind === "arb"
      ? `Either way you get back about <b>${money(Math.min(...ret))}</b> on ${money(tot)}: <b class="pos">${money(Math.min(...ret) - tot)}</b> locked.`
      : `If one side wins: ${money(Math.min(...ret) - tot)}. If the result lands ${o.window[0]} to ${o.window[1]} (about ${prob(o.pMiddle)}): <b class="pos">${money(ret[0] + ret[1] - tot)}</b>. Expected <b class="${o.ev >= 0 ? "pos" : "neg"}">${money(o.ev * tot * o.stakes[0] * o.legs[0].dec)}</b>.`}</div></div>`;
}
function oppsHtml() {
  const L = (S.opps || []).filter((o) => new Date(o.commence) > new Date() && (F.league === "All" || o.league === F.league));
  const arbs = L.filter((o) => o.kind === "arb"), mids = L.filter((o) => o.kind === "middle");
  return `<div class="row" style="gap:8px;align-items:center;flex-wrap:wrap;margin:6px 0 10px"><span class="small">Split a total of</span><input id="op-tot" inputmode="decimal" value="${oppTotal}" style="width:90px"><span class="tiny muted">stakes below are for this total; venues round to cents</span></div>
    <p class="note">An <b>arb</b> takes both sides at two venues for less than the payout: profit whatever happens. A <b>middle</b> takes both sides at different numbers: you lose a little if one side wins and win both if the result lands between. Prices move in seconds: place the harder-to-get leg first.</p>
    <div class="sec-title">Arbs (${arbs.length})<span class="line"></span></div>${arbs.length ? `<div class="grid">${arbs.map(oppCard).join("")}</div>` : `<div class="card note">None open right now.</div>`}
    <div class="sec-title">Middles (${mids.length})<span class="line"></span></div>${mids.length ? `<div class="grid">${mids.map(oppCard).join("")}</div>` : `<div class="card note">None open right now.</div>`}`;
}
const isLine = (e) => !/^(player|batter|pitcher)_/.test(e.market || "");

export function renderEdges(el) {
  const leagues = ["All", ...new Set(S.edges.map((e) => e.league))];
  const venues = ["All", ...new Set(S.edges.map((e) => e.bookName))];
  const items = S.edges.filter((e) =>
    (F.logic === "All" || (F.logic === "Logic agrees" && e.logic === "agrees")) && (F.league === "All" || e.league === F.league) && (F.venue === "All" || e.bookName === F.venue) && e.edge >= F.min &&
    (F.type === "All" || (F.type === "Lines" && isLine(e)) || (F.type === "Props" && !isLine(e) && !e.pickem) || (F.type === "Pick'em" && e.pickem)) &&
    new Date(e.commence) > new Date(Date.now() - 5 * 60000));
  const bookOf = Object.fromEntries(S.edges.map((e) => [e.bookName, e.book]));
  const chips = (key, vals, fmt = (v) => v) => vals.map((v) => `<button class="chip ${F[key] === v ? "on" : ""}" data-f="${key}" data-v="${esc(v)}">${key === "venue" && bookOf[v] ? bookLogo(bookOf[v], v, 14) : ""}${esc(fmt(v))}</button>`).join("");
  const nOpp = (S.opps || []).filter((o) => new Date(o.commence) > new Date() && !o.suspect).length;
  const tabs = `<div class="bar"><button class="chip ${F.tab === "edges" ? "on" : ""}" data-tab="edges">Edges</button><button class="chip ${F.tab === "opps" ? "on" : ""}" data-tab="opps">Arbs & middles${nOpp ? ` <span class="pill pos">${nOpp}</span>` : ""}</button></div>`;
  if (F.tab === "opps") {
    el.innerHTML = tabs + `<div class="bar" style="margin-top:0">${chips("league", ["All", ...new Set((S.opps || []).map((o) => o.league))])}</div>` + oppsHtml();
    $$("[data-tab]", el).forEach((b) => b.onclick = () => { F.tab = b.dataset.tab; renderEdges(el); });
    $$("[data-f]", el).forEach((b) => b.onclick = () => { F[b.dataset.f] = b.dataset.v; renderEdges(el); });
    const t = el.querySelector("#op-tot"); if (t) t.onchange = () => { oppTotal = +t.value || 100; renderEdges(el); };
    return;
  }
  el.innerHTML = tabs + `<div class="bar">${chips("league", leagues)}<span class="spacer"></span>${chips("type", ["All", "Lines", "Props", "Pick'em"])}</div>
    <div class="bar" style="margin-top:0">${chips("logic", ["All", "Logic agrees"])}<span class="spacer"></span>${chips("venue", venues)}<span class="spacer"></span>
      ${[0.015, 0.03, 0.05].map((m) => `<button class="chip ${F.min === m ? "on" : ""}" data-min="${m}">${(m * 100).toFixed(1)}%+</button>`).join("")}</div>
    <p class="note">Prices at your venues that beat the sharp no-vig fair. Ranked by edge, boosted when our sports model agrees and cut when it disagrees; alt and pick'em lines count half until backtests prove them. "Logic agrees" is the shortlist.</p>
    ${items.length ? `<div class="grid tight">${items.slice(0, 150).map(edgeCard).join("")}</div>` : `<div class="empty"><h3>Nothing at this threshold</h3>Lower the minimum edge or check back after the next pull.</div>`}`;
  $$("[data-tab]", el).forEach((b) => b.onclick = () => { F.tab = b.dataset.tab; renderEdges(el); });
  $$("[data-f]", el).forEach((b) => b.onclick = () => { F[b.dataset.f] = b.dataset.v; renderEdges(el); });
  $$("[data-min]", el).forEach((b) => b.onclick = () => { F.min = +b.dataset.min; renderEdges(el); });
  bindLog(el);
}
