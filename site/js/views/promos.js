import { S, savePromo, deletePromo, loadAllProps } from "../store.js";
import { esc, am, pct, prob, fmtTime, money, amFromDec, uid, toast, todayPT, $, $$ } from "../util.js";
import { VENUES, venueName, parsePromo, describePromo, catalog, optimize } from "../lab.js";
import { betImage, logSheet } from "./common.js";

const SPORTS = ["NBA", "NFL", "NHL", "MLB", "NCAAF", "NCAAB", "EPL", "UCL", "MLS", "UFC"];
const TYPES = [["profit_boost", "Profit boost (single)"], ["parlay_boost", "Parlay / combo boost"], ["bonus_bet", "Bonus bet (stake not returned)"],
               ["no_sweat", "No-sweat / refund as bonus"], ["dfs", "Pick'em promo"]];
let draft = null, running = null;

function form(p) {
  const v = (k) => p[k] ?? "";
  return `<div class="card col" style="gap:10px;margin-top:12px">
    <div class="row" style="justify-content:space-between"><b>${p.id ? "Edit promo" : "Check what I read"}</b><span class="tiny muted">${esc(describePromo(p))}</span></div>
    <div class="fgrid">
      <label class="f">Venue<select data-k="venue">${VENUES.map((x) => `<option value="${x.key}" ${x.key === p.venue ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">Type<select data-k="type">${TYPES.map(([k, t]) => `<option value="${k}" ${k === p.type ? "selected" : ""}>${t}</option>`).join("")}</select></label>
      <label class="f">Boost %<input data-k="boostPct" inputmode="numeric" value="${p.boost ? Math.round(p.boost * 100) : ""}"></label>
      <label class="f">Min legs<input data-k="minLegs" inputmode="numeric" value="${v("minLegs")}"></label>
      <label class="f">Min total odds<input data-k="minOdds" inputmode="numeric" value="${v("minOdds")}" placeholder="e.g. -200"></label>
      <label class="f">Min odds per leg<input data-k="minLegOdds" inputmode="numeric" value="${v("minLegOdds")}"></label>
      <label class="f">Max stake $<input data-k="maxStake" inputmode="decimal" value="${v("maxStake")}"></label>
      <label class="f">Max extra winnings $<input data-k="maxWin" inputmode="decimal" value="${v("maxWin")}"></label>
      <label class="f">Bonus amount $<input data-k="bonus" inputmode="decimal" value="${v("bonus")}"></label>
      <label class="f">Expires<input data-k="expires" type="date" value="${v("expires")}"></label>
    </div>
    <div class="row" style="flex-wrap:wrap;gap:6px"><span class="tiny muted">Sports:</span>${SPORTS.map((s) => `<button class="chip ${p.sports.includes(s) ? "on" : ""}" data-sp="${s}">${s}</button>`).join("")}
      <button class="chip ${p.onePerSport ? "on" : ""}" data-ops>One leg per sport</button></div>
    <div class="row"><button class="btn" id="pr-save">Save promo</button><button class="btn ghost" id="pr-cancel">Cancel</button></div>
  </div>`;
}

function promoCard(p) {
  const bets = [...S.bets.values()].filter((b) => b.promoId === p.id);
  const pl = bets.reduce((s, b) => s + (b.profit ?? 0), 0);
  const expired = p.expires && p.expires < todayPT();
  return `<div class="card col" style="gap:8px">
    <div class="row" style="justify-content:space-between"><b>${esc(p.title || venueName(p.venue))}</b>
      <span class="pill ${p.used ? "" : expired ? "neg" : "pos"}">${p.used ? "used" : expired ? "expired" : "active"}</span></div>
    <div class="small">${esc(describePromo(p))}</div>
    ${p.text ? `<div class="tiny muted">"${esc(p.text.slice(0, 220))}"</div>` : ""}
    ${bets.length ? `<div class="tiny">${bets.length} bet${bets.length > 1 ? "s" : ""} · P/L <span class="${pl >= 0 ? "pos" : "neg"}">${money(pl)}</span></div>` : ""}
    <div class="row" style="flex-wrap:wrap"><button class="btn sm" data-opt="${p.id}">Find best bet</button><button class="btn sm ghost" data-edit="${p.id}">Edit</button>
      <button class="btn sm ghost" data-used="${p.id}">${p.used ? "Mark active" : "Mark used"}</button><button class="btn sm ghost" data-del="${p.id}">Delete</button></div>
    ${running?.id === p.id ? running.html : ""}
  </div>`;
}

function optHtml(p, opts) {
  if (!opts.length) return `<div class="note">Nothing on the current board meets every requirement. More games and props come in with each pull.</div>`;
  const stake = p.type === "bonus_bet" ? (p.bonus || p.maxStake || 10) : (p.maxStake || 10);
  const proxy = VENUES.find((v) => v.key === p.venue)?.proxy;
  return `<div class="col" style="gap:8px;margin-top:6px">${proxy ? `<div class="note">${esc(venueName(p.venue))} prices are read from the matching sportsbook; confirm the price in the app before betting.</div>` : ""}
    ${opts.map((o, i) => `<div class="pl">
      ${o.legs.map((c) => `<div class="row" style="padding:3px 0">${betImage({ gameId: c.leg.gameId, label: c.leg.label, player: c.leg.player, headshot: c.leg.headshot }, 26)}
        <span class="grow ellip">${esc(c.leg.label)}</span><span class="num small">${am(c.price.am)}</span><span class="tiny muted">${prob(c.leg.fair)}</span></div>`).join("")}
      <div class="pl-b" style="margin-top:6px"><span>Price <b class="num">${am(amFromDec(o.D))}</b> · fair ${prob(o.P, 1)}</span>
        <span class="${o.ev > 0 ? "pos" : "neg"}">EV ${pct(o.ev)} · ${money(o.ev * stake)} on $${stake}</span></div>
      <div style="margin-top:6px"><button class="btn sm ghost" data-take="${i}">Log this</button></div></div>`).join("")}</div>`;
}

export function renderPromos(el) {
  loadAllProps();
  const list = [...S.promos.values()].sort((a, b) => (a.used ? 1 : 0) - (b.used ? 1 : 0) || (b.createdAt || "").localeCompare(a.createdAt || ""));
  const settled = [...S.bets.values()].filter((b) => b.promoId && b.profit != null);
  el.innerHTML = `<div class="card col" style="gap:8px;margin-top:18px">
      <b>Describe a promo</b>
      <textarea id="pr-text" placeholder="Paste or describe it, e.g. 'DK Predictions: 10% boost on a combo trade with 4+ picks, one pick in each major sport, max $25'"></textarea>
      <div class="row"><button class="btn" id="pr-read">Read it</button><span class="tiny muted">I pull out the venue, boost, legs, sports and limits. Fix anything I misread before saving.</span></div>
    </div>
    ${draft ? form(draft) : ""}
    <div class="kpis" style="margin-top:14px"><div class="kpi"><div class="k">Active promos</div><div class="v">${list.filter((p) => !p.used).length}</div></div>
      <div class="kpi"><div class="k">Promo bets settled</div><div class="v">${settled.length}</div></div>
      <div class="kpi"><div class="k">Promo P/L</div><div class="v ${settled.reduce((s, b) => s + b.profit, 0) >= 0 ? "pos" : "neg"}">${money(settled.reduce((s, b) => s + b.profit, 0))}</div></div></div>
    <div class="sec-title">Your promos<span class="line"></span></div>
    ${list.length ? `<div class="grid">${list.map(promoCard).join("")}</div>` : `<div class="empty"><h3>No promos saved</h3>Describe one above and I'll find the best bet for it on the live board.</div>`}`;

  const rerender = () => renderPromos(el);
  $("#pr-read", el).onclick = () => { const t = $("#pr-text", el).value.trim(); if (!t) return; draft = parsePromo(t); rerender(); };
  if (draft) {
    $$("[data-k]", el).forEach((i) => i.onchange = () => {
      const k = i.dataset.k, val = i.value.trim();
      if (k === "boostPct") draft.boost = (parseFloat(val) || 0) / 100;
      else if (["venue", "type", "expires"].includes(k)) draft[k] = val;
      else draft[k] = val === "" ? null : parseFloat(val);
      rerender();
    });
    $$("[data-sp]", el).forEach((b) => b.onclick = () => { const s = b.dataset.sp; draft.sports = draft.sports.includes(s) ? draft.sports.filter((x) => x !== s) : [...draft.sports, s]; rerender(); });
    $("[data-ops]", el).onclick = () => { draft.onePerSport = !draft.onePerSport; rerender(); };
    $("#pr-cancel", el).onclick = () => { draft = null; rerender(); };
    $("#pr-save", el).onclick = async () => {
      const p = { ...draft, id: draft.id || uid(), createdAt: draft.createdAt || new Date().toISOString(), title: `${venueName(draft.venue)} · ${describePromo(draft).split(" · ")[0]}` };
      try { await savePromo(p); draft = null; toast("Promo saved"); } catch (e) { toast("Could not save: " + e.message); }
    };
  }
  $$("[data-opt]", el).forEach((b) => b.onclick = () => {
    const p = S.promos.get(b.dataset.opt);
    const opts = optimize(p, catalog());
    running = { id: p.id, opts, html: optHtml(p, opts) };
    rerender();
  });
  $$("[data-take]", el).forEach((b) => b.onclick = () => {
    const p = S.promos.get(running.id), o = running.opts[+b.dataset.take];
    const one = o.legs.length === 1 ? o.legs[0].leg : null;
    logSheet({ label: o.legs.map((c) => c.leg.label).join(" + "), am: amFromDec(o.D), stake: p.maxStake || p.bonus || "", fair: o.P,
               venue: p.venue !== "any" ? p.venue : undefined, promoId: p.id, gameId: one?.gameId, league: one?.league, matchup: one?.matchup,
               commence: one?.commence, legs: o.legs.map((c) => ({ label: c.leg.label, gameId: c.leg.gameId, fair: c.leg.fair })), source: "promo" });
  });
  $$("[data-edit]", el).forEach((b) => b.onclick = () => { draft = { ...S.promos.get(b.dataset.edit) }; rerender(); scrollTo(0, 0); });
  $$("[data-used]", el).forEach((b) => b.onclick = () => { const p = S.promos.get(b.dataset.used); savePromo({ ...p, used: !p.used }); });
  $$("[data-del]", el).forEach((b) => b.onclick = () => { if (b.dataset.confirm) deletePromo(b.dataset.del); else { b.dataset.confirm = 1; b.textContent = "Tap again to delete"; } });
}
