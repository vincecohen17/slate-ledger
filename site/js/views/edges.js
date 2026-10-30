import { S, testAlert } from "../store.js";
import { esc, bookLogo, am, pct, prob, fmtTime, money, centsTag, toast, kUsd, $$ } from "../util.js";
import { edgeCard, bindLog } from "./common.js";
import { arbSheet } from "../arbs.js";

const F = { league: "All", type: "All", min: 0.015, venue: "All", logic: "All", tab: "edges" };

// arbs and middles across your venues (all-in prices), with the stake split for a chosen total
let oppTotal = 100;
let OPPS = [];
const availPill = (l, stake) => l.avail == null ? (l.exch ? ` <span class="pill" title="an exchange: the price shown is the best one on offer, often only for a small amount. A bigger stake can fill at worse prices; check the price for your size before placing">price moves with size</span>` : "")
  : ` <span style="text-transform:none" class="pill ${stake != null && stake > l.avail ? "neg" : l.avail < 25 ? "warn" : ""}" title="what the exchange has on offer at this exact price right now; past it you fill at worse prices">${kUsd(l.avail)} at this price</span>`;
function oppCard(o) {
  OPPS.push(o);
  const tot = oppTotal, st = o.stakes.map((x) => x * tot);
  const ret = o.legs.map((l, i) => st[i] * l.dec);
  const head = o.kind === "arb" ? `<span class="pill pos">arb +${(o.profit * 100).toFixed(2)}%</span>`
    : o.free ? `<span class="pill pos">no-risk middle · +${(o.worst * 100).toFixed(1)}% either way</span>` : `<span class="pill s">middle · EV ${pct(o.ev)}</span>`;
  const ageTxt = (a) => a == null ? "" : a < 1 ? "just pulled" : a < 60 ? `${Math.round(a)} min old` : `${(a / 60).toFixed(1)} h old`;
  return `<div class="card op-card ${o.suspect ? "op-sus" : ""}">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">${head}<span class="tiny muted">${esc(o.league)} · ${esc(o.matchup)} · ${fmtTime(o.commence)}</span></div>
    ${o.suspect ? `<div class="tiny neg" style="margin-top:4px">Too good to be true? A leg is far from the sharp fair: check the venue's rules (void on DNP, overtime) and that the price is live before placing either side.</div>` : ""}
    <div class="col" style="gap:6px;margin-top:8px">${o.legs.map((l, i) => `<div class="op-leg">${bookLogo(l.book, l.name, 22)}<span class="col" style="gap:0;min-width:0"><b class="op-lab">${esc(l.label)}</b><span class="tiny muted">${esc(l.name)}${l.fair != null ? ` · fair ${prob(l.fair)}` : ""}${l.age != null ? ` · <span class="${l.age > 10 ? "neg" : ""}" title="how long ago we pulled this price">${ageTxt(l.age)}</span>` : ""}</span>
        <span class="tiny op-hold">${l.holdTo != null ? `works to <b class="num">${am(l.holdTo)}</b>${centsTag(l.holdTo, l.book) ? ` (${centsTag(l.holdTo, l.book)})` : ""}` : ""}${availPill(l, st[i])}</span></span>
      <span class="col" style="align-items:flex-end;gap:0"><b class="num">${am(l.am)}</b>${centsTag(l.am, l.book) ? `<span class="tiny muted">${centsTag(l.am, l.book)} in app</span>` : ""}</span><span class="num op-stake">${money(st[i])}</span></div>`).join("")}</div>
    ${o.legs.some((l) => l.age > 10) ? `<div class="tiny neg" style="margin-top:6px">Some prices here are over 10 minutes old: refresh, and check the line is still up before placing either leg.</div>` : ""}
    <div class="tiny" style="margin-top:8px">${o.kind === "arb"
      ? `Either way you get back about <b>${money(Math.min(...ret))}</b> on ${money(tot)}: <b class="pos">${money(Math.min(...ret) - tot)}</b> locked.`
      : `If one side wins: ${money(Math.min(...ret) - tot)}. If the result lands ${o.window[0]} to ${o.window[1]} (about ${prob(o.pMiddle)}): <b class="pos">${money(ret[0] + ret[1] - tot)}</b>. Expected <b class="${o.ev >= 0 ? "pos" : "neg"}">${money(o.ev * tot * o.stakes[0] * o.legs[0].dec)}</b>.`}</div>
    ${o.maxStake != null ? `<div class="tiny ${o.thin ? "neg" : "muted"}" style="margin-top:6px">${o.thin ? "Thin: " : ""}at these prices the exchanges fill about <b>${money(o.maxStake)}</b> in all${o.kind === "arb" ? ` (locks <b class="pos">${money(o.maxProfit)}</b>)` : ""}${tot > o.maxStake + 0.5 ? `; past that the price gets worse` : ""}.</div>` : ""}
    <div class="row" style="justify-content:flex-end;gap:6px;margin-top:8px">${o.maxStake != null && Math.abs(tot - o.maxStake) > 1 && o.maxStake >= 10 ? `<button class="btn sm ghost" data-opmax="${Math.floor(o.maxStake)}" title="split the most these prices fill">Size to ${money(Math.floor(o.maxStake))}</button>` : ""}<button class="btn sm ghost" data-arb="${OPPS.length - 1}" title="log both legs as one ${o.kind} in the ledger">Log both legs</button></div></div>`;
}
let alertsOpen = false;
function alertsCard() {
  const a = S.alerts;
  if (!a?.topic) return `<div class="card note">Phone alerts get set up on the next engine run (a minute or two); then this shows how to turn them on.</div>`;
  const qs = (h) => `${((h + 11) % 12) + 1} ${h < 12 ? "AM" : "PM"}`;
  return `<div class="card col" style="gap:8px"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><b>📲 Phone alerts</b>
      <span class="row" style="gap:6px"><button class="btn sm" id="al-test">Send a test</button><button class="btn sm ghost" id="al-how">${alertsOpen ? "Hide" : "How to turn on"}</button></span></div>
    <div class="tiny muted">No-risk middles alert right away, loudest. Arbs (1%+), middles (2%+ EV) and strong new edges too; edges stay quiet ${qs(a.quietStart)} to ${qs(a.quietEnd)}.</div>
    ${alertsOpen ? `<ol class="small" style="margin:4px 0 0 18px;padding:0;line-height:1.6">
      <li>Install the free <b>ntfy</b> app (App Store or Google Play).</li>
      <li>Tap <b>+</b> (Subscribe to topic), type this topic exactly, and keep the server as ntfy.sh:<div class="row" style="gap:6px;margin:4px 0"><code class="al-topic">${esc(a.topic)}</code><button class="btn sm ghost" id="al-copy">Copy</button></div></li>
      <li>Allow notifications for ntfy when your phone asks (iPhone: Settings › Notifications › ntfy › Allow).</li>
      <li>Tap <b>Send a test</b> above: it should buzz within a few seconds.</li></ol>
      <div class="tiny muted">The topic name is the only key, so keep it to yourself. On a computer you can also open <a href="https://ntfy.sh/${encodeURIComponent(a.topic)}" target="_blank" rel="noopener">ntfy.sh/${esc(a.topic.slice(0, 14))}…</a> in a browser.</div>` : ""}
  </div>`;
}
function oppsHtml() {
  const L = (S.opps || []).filter((o) => new Date(o.commence) > new Date() && (F.league === "All" || o.league === F.league));
  const arbs = L.filter((o) => o.kind === "arb"), free = L.filter((o) => o.kind === "middle" && o.free && !o.suspect), mids = L.filter((o) => o.kind === "middle" && !(o.free && !o.suspect));
  return `${alertsCard()}<div class="row" style="gap:8px;align-items:center;flex-wrap:wrap;margin:10px 0 10px"><span class="small">Split a total of</span><input id="op-tot" inputmode="decimal" value="${oppTotal}" style="width:90px"><span class="tiny muted">stakes below are for this total; venues round to cents</span></div>
    <p class="note">An <b>arb</b> takes both sides at two venues for less than the payout: profit whatever happens. A <b>middle</b> takes both sides at different numbers: you lose a little if one side wins and win both if the result lands between; a <b>no-risk middle</b> can't lose either way. Prices move in seconds: place the leg on the exchange (or the one most likely to move) first, at your full size, and only then the other. "Works to" is the worst price each leg can get before the deal is gone.</p>
    ${free.length ? `<div class="sec-title">No-risk middles (${free.length})<span class="line"></span></div><div class="grid">${free.map(oppCard).join("")}</div>` : ""}
    <div class="sec-title">Arbs (${arbs.length})<span class="line"></span></div>${arbs.length ? `<div class="grid">${arbs.map(oppCard).join("")}</div>` : `<div class="card note">None open right now.</div>`}
    <div class="sec-title">${free.length ? "Other middles" : "Middles"} (${mids.length})<span class="line"></span></div>${mids.length ? `<div class="grid">${mids.map(oppCard).join("")}</div>` : `<div class="card note">None open right now.</div>`}`;
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
    OPPS = [];
    el.innerHTML = tabs + `<div class="bar" style="margin-top:0">${chips("league", ["All", ...new Set((S.opps || []).map((o) => o.league))])}</div>` + oppsHtml();
    $$("[data-tab]", el).forEach((b) => b.onclick = () => { F.tab = b.dataset.tab; renderEdges(el); });
    $$("[data-f]", el).forEach((b) => b.onclick = () => { F[b.dataset.f] = b.dataset.v; renderEdges(el); });
    const t = el.querySelector("#op-tot"); if (t) t.onchange = () => { oppTotal = +t.value || 100; renderEdges(el); };
    $$("[data-opmax]", el).forEach((b) => b.onclick = () => { oppTotal = +b.dataset.opmax; renderEdges(el); toast(`Stakes split for ${money(oppTotal)}`); });
    $$("[data-arb]", el).forEach((b) => b.onclick = () => arbSheet({ ...OPPS[+b.dataset.arb], total: oppTotal }));
    const at = el.querySelector("#al-test"); if (at) at.onclick = () => testAlert().then(() => toast("Test sent: check your phone")).catch((e) => toast("Couldn't send: " + e.message));
    const ah = el.querySelector("#al-how"); if (ah) ah.onclick = () => { alertsOpen = !alertsOpen; renderEdges(el); };
    const ac = el.querySelector("#al-copy"); if (ac) ac.onclick = () => navigator.clipboard?.writeText(S.alerts.topic).then(() => toast("Topic copied")).catch(() => toast("Select and copy the topic"));
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
