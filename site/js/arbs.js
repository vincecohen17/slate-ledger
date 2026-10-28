// Arbs and middles in the ledger: both legs are logged together as ordinary bets that share a group
// {id, kind: "arb" | "middle", i, window?, pMiddle?}. Each leg still grades itself (the engine settles it like any bet),
// but every stat treats the pair as ONE result: net profit, total staked, expected profit from the fairs. That keeps
// a guaranteed loser leg out of the record, the venue tables, CLV, calibration and Kelly sizing.
import { S, saveBet, savePromo } from "./store.js";
import { esc, am, prob, money, sheet, toast, uid, decFromAm, todayPT, bookLogo, $, $$ } from "./util.js";
import { VENUES } from "./lab.js";
import { settleProfit, isBoost, promoOpen, singleUse } from "./promo.js";

const DONE = ["won", "lost", "push", "void"];
const promoOf = (b) => (b.promoId ? S.promos.get(b.promoId) : null);
const W = (b) => settleProfit(b, promoOf(b), "won"), L = (b) => settleProfit(b, promoOf(b), "lost");

// what the pair pays: one side wins, the other side wins, and (middles) both win
export function outcomes(legs) {
  const [a, b] = legs;
  const ifA = W(a) + L(b), ifB = L(a) + W(b), both = W(a) + W(b);
  return { ifA, ifB, both, worst: Math.min(ifA, ifB) };
}

// expected profit of the pair at the fairs when logged (linear, so the sum of the legs' EVs) and its variance:
// an arb has none; a middle swings between its worst case and both legs winning
function expected(legs, g) {
  if (legs.some((l) => l.fairAtLog == null || !l.dec)) return { ev: g.kind === "arb" ? outcomes(legs).worst : null, v: 0 };
  const ev = legs.reduce((s, l) => s + l.fairAtLog * W(l) + (1 - l.fairAtLog) * L(l), 0);
  if (g.kind !== "middle" || g.pMiddle == null) return { ev, v: 0 };
  const o = outcomes(legs), pm = g.pMiddle;
  return { ev, v: pm * (1 - pm) * (o.both - o.worst) ** 2 };
}

// one row per pair: a stand-in bet the stats can read like any other
export function combine(gid, legs) {
  legs = [...legs].sort((x, y) => (x.group.i ?? 0) - (y.group.i ?? 0));
  const g = legs[0].group, done = legs.every((l) => DONE.includes(l.status));
  const counted = legs.filter((l) => ["won", "lost", "push"].includes(l.status));
  const pl = Math.round(counted.reduce((s, l) => s + (l.profit || 0), 0) * 100) / 100;
  const status = !done ? "pending" : !counted.length ? "void" : pl > 0.005 ? "won" : pl < -0.005 ? "lost" : "push";
  const x = expected(legs, g), at = (k) => legs.map((l) => l[k]).filter(Boolean).sort();
  const kind = g.kind === "middle" ? "middle" : "arb";
  return {
    id: gid, isGroup: true, kind, group: g, legsB: legs, source: "arb",
    label: `${kind === "arb" ? "Arb" : "Middle"}: ${legs.map((l) => l.label).join(" / ")}`,
    venue: "arb", venueName: kind === "arb" ? "Arbs" : "Middles",
    league: legs[0].league, matchup: legs[0].matchup, commence: legs[0].commence, gameId: legs[0].gameId,
    date: legs[0].date, createdAt: at("createdAt")[0], settledAt: done ? at("settledAt").at(-1) : null,
    stake: legs.reduce((s, l) => s + (l.stake || 0), 0), profit: done ? pl : null, status,
    dec: null, am: null, clv: null, fairAtLog: null, promoId: null, live: legs.some((l) => l.live),
    expPl: x.ev, expVar: x.v,
  };
}

// bets with the pairs folded into one row each (a leg whose partner was deleted stays a normal bet)
export function unify(bets) {
  const by = new Map(), out = [];
  for (const b of bets) {
    const g = b.group?.id;
    if (!g) { out.push(b); continue; }
    if (!by.has(g)) by.set(g, []);
    by.get(g).push(b);
  }
  for (const [gid, legs] of by) legs.length >= 2 ? out.push(combine(gid, legs)) : out.push(...legs);
  return out;
}

// stake on the other leg that makes both outcomes pay the same, promos included (a bonus bet keeps no stake, a
// boost pays more): bisection, since what the other leg's stake does to each outcome only moves one way
function hedge(anchor, other) {
  const f = (s) => { const o = { ...other, stake: s }; return (W(anchor) + L(o)) - (L(anchor) + W(o)); };
  let lo = 0, hi = Math.max(1, anchor.stake * 60);
  if (f(lo) < 0) return 0;
  if (f(hi) > 0) return Math.round(hi * 100) / 100;
  for (let i = 0; i < 60; i++) { const m = (lo + hi) / 2; f(m) > 0 ? (lo = m) : (hi = m); }
  return Math.round(hi * 100) / 100;
}

// log both legs at once: from an Arbs & middles card (pre = the opportunity, pre.total = the split) or typed in
export function arbSheet(pre = {}) {
  const venues = VENUES.filter((v) => v.key !== "any" && !v.dfs);
  const fromBoard = !!pre.legs;
  const legs0 = fromBoard ? pre.legs : [{}, {}];
  const total = pre.total || 100;
  let kind = pre.kind || "arb", anchor = 0;
  const promoOpts = (sel) => `<option value="">No promo</option>${[...S.promos.values()].filter(promoOpen).map((p) => `<option value="${p.id}" ${p.id === sel ? "selected" : ""}>${esc(p.title || p.venueName || "Promo")}</option>`).join("")}`;
  const legHtml = (l, i) => `<div class="card ab-leg" data-i="${i}">
      <div class="row" style="gap:8px;align-items:center">${l.book ? bookLogo(l.book, l.name, 22) : `<b class="tiny muted">Leg ${i + 1}</b>`}<input class="ab-label grow" value="${esc(l.label || "")}" placeholder="${i ? "e.g. Under 224.5" : "e.g. Over 224.5"}"></div>
      <div class="fgrid" style="margin-top:8px">
        <label class="f">Venue<select class="ab-venue">${venues.map((v) => `<option value="${v.key}" ${v.key === l.book ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
        <label class="f">Odds<input class="ab-odds" inputmode="numeric" value="${l.am ?? ""}" placeholder="-110"></label>
        <label class="f">Stake $<input class="ab-stake" inputmode="decimal" value="${fromBoard ? Math.round(total * pre.stakes[i] * 100) / 100 : ""}" placeholder="50"></label>
        <label class="f">Promo<select class="ab-promo">${promoOpts("")}</select></label>
      </div>
      <label class="row lb-boost ab-bst" style="gap:8px;align-items:center;margin-top:6px" hidden><input type="checkbox" class="ab-boostin"><span class="tiny">odds above already include the boost</span></label>
      ${l.fair != null ? `<div class="tiny muted" style="margin-top:4px">fair ${prob(l.fair)} when found · ${esc(l.name || "")} ${am(l.am)}</div>` : ""}
    </div>`;
  sheet(`<h3>Log ${fromBoard ? (kind === "arb" ? "this arb" : "this middle") : "an arb or middle"}</h3>
    <div class="col" style="gap:10px">
      ${fromBoard ? `<div class="tiny muted">${esc(pre.league || "")} · ${esc(pre.matchup || "")}</div>` : `<div class="bar" style="margin:0">${["arb", "middle"].map((k) => `<button class="chip ${k === kind ? "on" : ""}" data-k="${k}">${k === "arb" ? "Arb (same line, both sides)" : "Middle (two numbers)"}</button>`).join("")}</div>`}
      ${legs0.map(legHtml).join("")}
      <label class="row lb-boost" style="gap:8px;align-items:center"><input type="checkbox" id="ab-auto" checked><span class="small">Size the other leg so both results pay the same (promos counted)</span></label>
      <div class="ab-pay card" style="padding:10px 12px"></div>
      <p class="note">Type the prices you actually got. Both legs save as normal bets and grade themselves${fromBoard ? "" : " (typed-in legs need a W/L tap)"}, but the ledger counts the pair as one result: its net profit, not a win and a loss.</p>
      <div class="row"><button class="btn" id="ab-save">Save both legs</button><button class="btn ghost" id="ab-cancel">Cancel</button></div>
    </div>`, (el, close) => {
    const rows = $$(".ab-leg", el);
    const read = () => rows.map((r) => {
      const odds = parseFloat($(".ab-odds", r).value), stake = parseFloat($(".ab-stake", r).value), promoId = $(".ab-promo", r).value || null;
      return { odds, stake, promoId, dec: isNaN(odds) ? null : decFromAm(odds), boostIn: !!$(".ab-boostin", r).checked, label: $(".ab-label", r).value.trim(), venue: $(".ab-venue", r).value };
    });
    const calc = () => {
      const L2 = read();
      rows.forEach((r, i) => { $(".ab-bst", r).hidden = !isBoost(S.promos.get(L2[i].promoId)); });
      if ($("#ab-auto", el).checked) {
        const a = L2[anchor], o = L2[1 - anchor];
        if (a.dec && o.dec && a.stake > 0) {
          const s = hedge(a, o);
          $(".ab-stake", rows[1 - anchor]).value = s;
          o.stake = s;
        }
      }
      if (L2.some((x) => !x.dec || isNaN(x.stake))) { $(".ab-pay", el).innerHTML = `<span class="tiny muted">Enter odds and stakes for both legs.</span>`; return; }
      const o = outcomes(L2), tot = L2[0].stake + L2[1].stake;
      const lab = (i) => esc(L2[i].label || `leg ${i + 1}`);
      $(".ab-pay", el).innerHTML = `<div class="col" style="gap:4px">
        <span class="small">If ${lab(0)} wins: <b class="num ${o.ifA >= 0 ? "pos" : "neg"}">${money(o.ifA)}</b></span>
        <span class="small">If ${lab(1)} wins: <b class="num ${o.ifB >= 0 ? "pos" : "neg"}">${money(o.ifB)}</b></span>
        ${kind === "middle" ? `<span class="small">If both win${pre.window ? ` (lands ${pre.window[0]} to ${pre.window[1]}${pre.pMiddle != null ? `, about ${prob(pre.pMiddle)}` : ""})` : ""}: <b class="num pos">${money(o.both)}</b></span>` : ""}
        <span class="tiny muted">${money(tot)} staked in all${kind === "arb" ? (o.worst >= 0 ? ` · ${money(o.worst)} locked either way` : ` · <span class="neg">not an arb at these prices</span>`) : ""}</span></div>`;
    };
    rows.forEach((r, i) => {
      $(".ab-stake", r).oninput = () => { anchor = i; calc(); };
      $(".ab-odds", r).oninput = calc;
      $(".ab-promo", r).onchange = calc;
      $(".ab-boostin", r).onchange = calc;
    });
    $("#ab-auto", el).onchange = calc;
    $$("[data-k]", el).forEach((b) => b.onclick = () => { kind = b.dataset.k; $$("[data-k]", el).forEach((x) => x.classList.toggle("on", x === b)); calc(); });
    calc();
    $("#ab-cancel", el).onclick = close;
    $("#ab-save", el).onclick = async () => {
      const L2 = read();
      if (L2.some((x) => !x.label || !x.dec || !(x.stake > 0))) { toast("Each leg needs a bet, odds and a stake"); return; }
      const gid = uid(), now = new Date().toISOString();
      const group = { id: gid, kind, n: 2, at: now, ...(pre.window ? { window: pre.window } : {}), ...(pre.pMiddle != null ? { pMiddle: pre.pMiddle } : {}) };
      const bets = L2.map((x, i) => {
        const src = legs0[i] || {}, v = venues.find((y) => y.key === x.venue);
        return { id: uid() + i, createdAt: now, date: todayPT(), label: x.label, venue: v.key, venueName: v.name, am: x.odds, dec: x.dec, stake: x.stake,
          promoId: x.promoId, boostIn: isBoost(S.promos.get(x.promoId)) ? x.boostIn : null, live: false, liveState: null, status: "pending",
          gameId: pre.gameId || null, league: pre.league || null, matchup: pre.matchup || null, commence: pre.commence || null,
          market: src.market || null, side: src.side ?? null, point: src.point ?? null, player: pre.player || null, team: src.team || null,
          fairAtLog: src.fair ?? null, legs: null, source: "arb", pickId: null, group: { ...group, i } };
      });
      const used = [...new Set(L2.map((x) => x.promoId).filter(Boolean))].map((id) => S.promos.get(id)).filter((p) => singleUse(p) && !p.used);
      try {
        await Promise.all(bets.map(saveBet));
        await Promise.all(used.map((p) => savePromo({ ...p, used: true, usedAt: now, usedBy: bets.find((b) => b.promoId === p.id)?.id })));
        toast(`${kind === "arb" ? "Arb" : "Middle"} logged: both legs${used.length ? " · promo marked used" : ""}`); close();
      }
      catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

// pair two bets already logged (e.g. legs logged one at a time before this existed)
export async function pairBets(a, b, kind) {
  const gid = uid(), at = new Date().toISOString();
  await Promise.all([a, b].map((x, i) => saveBet({ ...x, group: { id: gid, kind, n: 2, at, i } })));
}
export async function unpair(legs) {
  await Promise.all(legs.map((x) => { const y = { ...x }; delete y.group; return saveBet(y); }));
}
