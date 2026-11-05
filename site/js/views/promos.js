import { S, savePromo, deletePromo, loadAllProps, saveBet, saveWallet , savePromoReq, deletePromoReq } from "../store.js";
import { esc, am, prob, fmtTime, money, uid, toast, todayPT, bookLogo, sheet, $, $$, odds as odds3 } from "../util.js";
import { VENUES, venueName, legSpec } from "../lab.js";
const draftLine = (p, c) => p.promoLine ?? legSpec(c.leg).point;
import { KINDS, FIELDS, SPORTS, BET_TYPES, PICKEM, FEE_VENUES, kindOf, blank, normalize, parsePromo, describe, optimize, legs,
  bonusConv, convCurve, findTarget, pAtLine, autoTarget, decOf, amOf, payoutFor, venueHasFeed, fundsOf , missingFields } from "../promo.js";
const vname = (p) => (p.venue === "other" && p.venueOther ? p.venueOther : venueName(p.venue));
import { betImage, logSheet } from "./common.js";
import { labWithPromo, addToLab } from "./lab.js";
import { syncWalletPromos } from "../wallet.js";

let draft = null, open = new Set(), checker = { q: "", key: null, odds: "", line: "", boost: "", mult: "" }, targetQ = "";
const results = new Map();        // promo id -> {stamp, opts}
const stamp = () => `${S.status?.updated || ""}|${S.games.size}|${S.props.size}`;

function best(p) {
  const r = results.get(p.id);
  if (r && r.stamp === stamp()) return r.opts;
  let opts = [];
  try { opts = optimize(p); } catch (e) { console.error(e); }
  results.set(p.id, { stamp: stamp(), opts });
  return opts;
}
const worth = (p, opts) => kindOf(p.kind).key === "manual" ? Math.max(0, +p.value || 0)
  : Math.max(0, opts[0]?.ev || 0) * (kindOf(p.kind).key === "bonus_bet" ? (p.count || 1) : 1);
let showOld = false;
const status = (p) => p.used ? "used" : p.expires && p.expires < todayPT() ? "expired" : p.expires === todayPT() ? "today" : "active";

// ---------- search the board (for odds boosts, discounted lines and the bet checker)
function search(q, { props = false, n = 8 } = {}) {
  const toks = q.toLowerCase().split(/\s+/).filter(Boolean);
  if (!toks.length) return [];
  const out = [];
  for (const l of legs()) {
    if (l.pickem || new Date(l.commence) < new Date()) continue;
    if (props && (l.kind !== "prop" || l.mean == null)) continue;
    const s = `${l.label} ${l.matchup} ${l.league}`.toLowerCase();
    if (toks.every((t) => s.includes(t))) out.push(l);
    if (out.length > 1500) break;
  }
  // closest match first: fewest words in the bet's market that the search didn't ask for, then main lines
  const extra = (l) => (l.market || "").replace(/^(player|batter|pitcher)_/, "").split("_").filter((w) => w.length > 2 && !toks.some((t) => w.startsWith(t) || t.startsWith(w))).length;
  out.sort((a, b) => extra(a) - extra(b) || (b.main ? 1 : 0) - (a.main ? 1 : 0) || Math.abs(a.fair - 0.5) - Math.abs(b.fair - 0.5));
  return out.slice(0, n);
}
const pickList = (list, attr) => list.length ? `<div class="pr-hits">${list.map((l) => `<button class="pr-hit" ${attr}="${esc(l.key)}">
    ${betImage({ gameId: l.gameId, label: l.label, player: l.player, headshot: l.headshot }, 22)}<span class="grow ellip">${esc(l.label)}</span>
    <span class="tiny muted">${esc(l.league)} · ${fmtTime(l.commence)}</span></button>`).join("")}</div>` : "";

// ---------- what I read, in a sentence, and what's still missing (so a promo never saves half understood)
const x2 = (v) => `${+(+v).toFixed(2)}x`;
// a promo being written (no createdAt) defaults to its own free bet; one saved before the choice existed was bonus balance
const fundsNow = (p) => (p.wallet ? "bonus" : p.funds || (p.createdAt ? "bonus" : "own"));
const fundsNote = (p) => (fundsNow(p) === "own" ? " Its own free bet: your bonus balance isn't touched, the winnings land as cash." : " Paid from your bonus balance in the app.");
function explain(p) {
  const k = kindOf(p.kind).key, ven = p.venue && p.venue !== "any" ? venueName(p.venue) : "any app";
  const pc = (v) => `${Math.round((v || 0) * 100)}%`, d = (v) => (v == null ? "?" : `$${v}`), o = (v) => (v == null ? "?" : v > 0 ? `+${v}` : `${v}`);
  const who = p.targetLabel || "a player";
  const stat = (() => { const t = findTarget(p.target); return t?.market ? t.market.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ") : ""; })();
  switch (k) {
    case "discounted_line": return `${ven} special pick: ${who} ${p.promoSide || "Over"} ${p.promoLine ?? "?"}${stat ? ` ${stat}` : ""}${p.promoMult ? `, paying ${x2(p.promoMult)} on its own. Pair it with ${Math.max(1, (p.picks || 2) - 1)} more pick${(p.picks || 2) > 2 ? "s" : ""}; the entry pays ${x2(p.promoMult)} × the other pick's multiplier` : `, in a ${p.picks || 2}-pick entry paying ${p.mult || payoutFor(p.venue, p.picks || 2) || "?"}x`}.`;
    case "pickem_boost": return `${ven}: a ${p.picks ?? "?"}-pick entry paying ${p.mult ? `${p.mult}x` : "?"}${p.boost ? ` plus ${pc(p.boost)}` : ""}${p.stake ? `, up to $${p.stake}` : ""}.`;
    case "free_entry": return `${ven}: a free ${d(p.amount)} ${p.picks ?? 2}-pick entry (you keep the whole payout, nothing at risk).${fundsNote(p)}`;
    case "profit_boost": return `${ven}: ${pc(p.boost)} more on what a bet wins${p.stake ? `, on up to $${p.stake}` : ""}${p.maxLegs === 1 ? ", singles only" : ""}.`;
    case "parlay_boost": return `${ven}: ${pc(p.boost)} more on a ${p.minLegs}+ leg ${p.sameGame === "required" ? "same-game parlay" : "parlay"}${p.stake ? `, on up to $${p.stake}` : ""}.`;
    case "odds_boost": return `${ven}: ${p.targetLabel || "a set bet"} boosted to ${o(p.boostedOdds)}${p.origOdds != null ? ` (normally ${o(p.origOdds)})` : ""}${p.stake ? `, up to $${p.stake}` : ""}.`;
    case "fee_free": return `${ven}: ${p.feeOff >= 1 ? "no fee" : `${pc(p.feeOff)} of the fee waived`} on trades.`;
    case "bonus_bet": return `${ven}: ${p.count > 1 ? `${p.count} ${fundsNow(p) === "own" ? "free" : "bonus"} bets of ${d(p.amount)}` : `a ${d(p.amount)} ${fundsNow(p) === "own" ? "free" : "bonus"} bet`} (the stake isn't returned, only the winnings).${p.wallet ? "" : fundsNote(p)}`;
    case "no_sweat": return `${ven}: if the bet loses you get up to ${d(p.refundMax ?? p.stake)} back as ${p.refundAs === "cash" ? "cash" : "bonus bets"}.`;
    case "parlay_insurance": return `${ven}: a ${p.minLegs}+ leg parlay that refunds up to ${d(p.refundMax ?? p.stake)} if exactly one leg misses.`;
    case "bet_and_get": return `${ven}: bet ${d(p.qualStake)} and get ${d(p.amount)} in bonus bets${p.condition === "win" ? " if it wins" : p.condition === "lose" ? " if it loses" : ", win or lose"}.`;
    case "deposit_match": return `${ven}: a ${d(p.amount)} deposit bonus${p.playthrough ? ` you must wager ${p.playthrough}x` : ""}.`;
    default: return describe(p);
  }
}
const missing = (p) => missingFields(p);
function readout(p) {
  const miss = missing(p);
  return `<div class="pr-read"><div class="row" style="gap:8px;flex-wrap:wrap;align-items:center"><span class="pill s">${esc(kindOf(p.kind).name)}</span>${p.venue && p.venue !== "any" ? `<span class="lg-name">${bookLogo(p.venue, venueName(p.venue), 16)}${esc(venueName(p.venue))}</span>` : ""}</div>
    <div class="small" style="margin-top:4px">${esc(explain(p))}</div>
    ${miss.length ? `<div class="tiny neg" style="margin-top:4px">Still need: ${esc(miss.map((x) => x[1]).join(", "))}. Set it up and fill ${miss.length > 1 ? "those" : "it"} in.</div>` : `<div class="tiny pos" style="margin-top:4px">Got everything I need. Set it up to see what it's worth and the best plays.</div>`}</div>`;
}

// ---------- the form: only the fields this kind of promo needs
function field(p, f, miss = new Set()) {
  const [lab, type, ph] = FIELDS[f] || [f, "num"];
  let val = p[f];
  if (type === "pct") val = val ? Math.round(val * 100) : "";
  if (type === "odds" && val != null && val !== "") val = val > 0 ? `+${val}` : val;
  return `<label class="f ${miss.has(f) ? "miss" : ""}" ${type === "str" ? 'style="grid-column:1 / -1"' : ""}>${esc(lab)}<input data-k="${f}" data-t="${type}" ${type === "date" ? 'type="date"' : type === "str" ? "" : 'inputmode="decimal"'} value="${esc(val ?? "")}" placeholder="${esc(ph || "")}"></label>`;
}
const sel = (k, cur, opts, lab) => `<label class="f">${lab}<select data-k="${k}" data-t="str">${opts.map(([v, t]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;

function form(p) {
  const kind = kindOf(p.kind), F = new Set(kind.fields);
  const groups = [...new Set(KINDS.map((k) => k.group))];
  const venues = VENUES.filter((v) => (["pickem_boost", "free_entry", "discounted_line"].includes(kind.key) ? v.dfs : true));
  const miss = missing(p), M = new Set(miss.map((x) => x[0]));
  // a pick with its own multiplier pays by multiplying, so the entry's table payout doesn't apply
  const inputs = [...F].filter((f) => FIELDS[f] && f !== "expires" && !(f === "mult" && kind.key === "discounted_line" && p.promoMult)).map((f) => field(p, f, M));
  if (F.has("refundAs")) inputs.push(sel("refundAs", p.refundAs, [["bonus", "Bonus bets"], ["cash", "Cash"]], "Refund paid as"));
  if (F.has("condition")) inputs.push(sel("condition", p.condition, [["any", "Always"], ["win", "Only if it wins"], ["lose", "Only if it loses"]], "Bonus arrives"));
  if (F.has("ptBase")) inputs.push(sel("ptBase", p.ptBase, [["bonus", "Bonus only"], ["both", "Deposit + bonus"]], "Playthrough on"));
  if (F.has("sameGame")) inputs.push(sel("sameGame", p.sameGame, [["allowed", "Any games"], ["banned", "Different games only"], ["required", "Same-game parlay only"]], "Games"));
  if (F.has("promoSide")) inputs.push(sel("promoSide", p.promoSide, [["Over", "Over / More"], ["Under", "Under / Less"]], "Side"));
  if (["bonus_bet", "free_entry"].includes(kind.key) && !p.wallet) inputs.push(sel("funds", fundsNow(p), [["own", "Its own free bet"], ["bonus", "My bonus balance"]], "Paid from"));
  if (kind.key === "manual") {
    inputs.push(sel("credit", p.credit || "cash", [["cash", "Cash"], ["bonus", "Bonus funds (go to the Wallet)"], ["other", "Something else"]], "Pays out as"));
    if (p.venue === "other") inputs.unshift(`<label class="f ${M.has("venueOther") ? "miss" : ""}">App name<input data-k="venueOther" data-t="str" value="${esc(p.venueOther || "")}" placeholder="e.g. Chime, Rakuten, DraftKings Casino"></label>`);
  }
  if (F.has("stakeBack")) inputs.push(sel("stakeBack", p.stakeBack, [["true", "Yes (cash bet)"], ["false", "No (bonus stake)"]], "Stake returned on a win"));
  if (["pickem_boost", "free_entry", "discounted_line"].includes(kind.key)) inputs.push(`<label class="f">Flex payouts (optional)<input data-k="flex" data-t="str" value="${esc(p.flex || "")}" placeholder="3:2.25, 2:1.25"></label>`);
  inputs.push(field(p, "expires"));
  const tgt = F.has("target") ? findTarget(p.target) : null;
  const preview = (() => {
    if (kind.key === "manual") return `<span class="muted">Counted at the profit you expect until you log what came in. Logging the result puts it in your Ledger (cash) or Wallet (bonus funds) so it counts in your profit.</span>`;
    try {
      const opts = optimize({ ...p, id: "draft" }, 3);
      if (!opts.length) return `<span class="muted">Nothing on the board fits yet</span>`;
      const tg = kind.key === "discounted_line" ? findTarget(p.target) : null;
      const same = tg && p.promoLine != null && +p.promoLine === +tg.point;
      return `${same ? `<div class="tiny neg" style="margin-bottom:6px">The promo line is the same as the regular line (${tg.point}). Type the discounted number the app shows.</div>` : ""}
        <div>Worth about <b class="${opts[0].ev >= 0 ? "pos" : "neg"}">${money(opts[0].ev)}</b>. Suggested plays:</div>
        <ol class="pr-sug">${opts.map((o) => `<li><span class="grow">${esc(o.legs.map((c) => `${c.leg.label} (${prob(c.P)}${c.needs ? `, needs ≥ ${c.needs.toFixed(2)}x` : c.mult && Math.abs(c.mult - 1) > 0.01 ? `, pays ${c.mult.toFixed(2)}x` : ""})`).join(" + "))}</span>
          <b class="${o.ev >= 0 ? "pos" : "neg"}">${money(o.ev)}</b></li>`).join("")}</ol>`;
    } catch (e) { return ""; }
  })();
  return `<div class="card col pr-form" style="gap:12px;margin-top:12px">
    <div class="col" style="gap:4px"><b>${p.id ? "Edit promo" : "Check what I read"}</b><div class="small">${esc(explain(p))}</div>
      ${miss.length ? `<div class="tiny neg">Still need: ${esc(miss.map((x) => x[1]).join(", "))} (outlined below).</div>` : ""}</div>
    <div class="fgrid">
      <label class="f ${M.has("venue") ? "miss" : ""}">App<select data-k="venue" data-t="str">${venues.map((x) => `<option value="${x.key}" ${x.key === p.venue ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">Type<select data-k="kind" data-t="str">${groups.map((g) => `<optgroup label="${g}">${KINDS.filter((k) => k.group === g).map((k) => `<option value="${k.key}" ${k.key === kind.key ? "selected" : ""}>${k.name}</option>`).join("")}</optgroup>`).join("")}</select></label>
      ${inputs.join("")}
    </div>
    <div class="tiny muted">${esc(kind.blurb)}${FEE_VENUES.has(p.venue) || kind.key !== "fee_free" ? "" : " · fee waivers only matter on Kalshi, Polymarket, Novig and ProphetX"}${!venueHasFeed(p.venue) && p.venue !== "any" ? ` · ${esc(venueName(p.venue))} isn't in the odds feed, so I use a typical sportsbook price (or the sharp line for pick'em) as a stand-in` : ""}</div>
    ${F.has("target") ? `<div class="col ${M.has("target") ? "miss" : ""}" style="gap:6px"><span class="tiny muted">${kind.key === "discounted_line" ? "Which player prop?" : "Which bet?"}</span>
      ${tgt ? `<div class="row pr-tgt">${betImage({ gameId: tgt.gameId, label: tgt.label, player: tgt.player, headshot: tgt.headshot }, 26)}<b class="grow">${esc(tgt.label)}</b><span class="tiny muted">sharp ${prob(tgt.fair, 1)}${tgt.mean != null ? ` · avg ${tgt.mean}` : ""}</span><button class="btn sm ghost" data-untarget>Change</button></div>`
        : `<input id="pr-tq" placeholder="Search a player, team or bet" value="${esc(targetQ)}">${pickList(search(targetQ, { props: kind.key === "discounted_line" }), "data-target")}`}</div>` : ""}
    ${F.has("sports") ? `<div class="row" style="flex-wrap:wrap;gap:6px"><span class="tiny muted">Sports</span>${SPORTS.map((s) => `<button class="chip sm ${p.sports.includes(s) ? "on" : ""}" data-sp="${s}">${s}</button>`).join("")}
      ${F.has("onePerSport") ? `<button class="chip sm ${p.onePerSport ? "on" : ""}" data-ops>One leg per sport</button>` : ""}</div>` : ""}
    ${F.has("betTypes") ? `<div class="row" style="flex-wrap:wrap;gap:6px"><span class="tiny muted">Bet types</span>${BET_TYPES.map(([k, t]) => `<button class="chip sm ${p.betTypes.includes(k) ? "on" : ""}" data-bt="${k}">${t}</button>`).join("")}<span class="tiny faint">none picked = any</span></div>` : ""}
    <div class="pr-prev">${preview}</div>
    <div class="row"><button class="btn" id="pr-save">Save promo</button><button class="btn ghost" id="pr-cancel">Cancel</button></div>
  </div>`;
}

// ---------- results
function convChart(p) {
  const cur = convCurve(p.venue);
  const max = Math.max(0.01, ...cur.map((c) => c.v || 0));
  return `<div class="pr-conv"><div class="tiny muted">What $1 of bonus bet is worth at each price range here (best bet in each range)</div>
    ${cur.map((c) => `<div class="pr-cv"><span class="tiny">${c.label}</span><div class="pr-cvb"><i style="width:${((c.v || 0) / max * 100).toFixed(0)}%"></i></div>
      <span class="num tiny">${c.v != null ? (c.v >= 1 ? "100%+" : Math.round(c.v * 100) + "%") : "–"}</span></div>`).join("")}</div>`;
}
function optRow(p, o, i, max) {
  const k = kindOf(p.kind).key;
  const pick = o.pick, dep = o.deposit;
  const legsH = o.legs.map((c) => `<div class="row pr-leg">${betImage({ gameId: c.leg.gameId, label: c.leg.label, player: c.leg.player, headshot: c.leg.headshot }, 24)}
      <span class="grow ellip">${esc(c.leg.label)}${c.promo ? ` <span class="pill pos">promo</span>` : ""}</span>
      ${o.perPick ? (c.promo ? `<span class="pill pos" title="the promo pick's own multiplier">${p.promoMult}x</span>` : c.perX != null ? `<span class="pill" title="this pick's own multiplier">${c.perX.toFixed(2)}x</span>` : "")
        : pick && c.mult != null && Math.abs(c.mult - 1) > 0.01 ? `<span class="pill" title="this pick's payout vs a standard pick">${c.mult.toFixed(2)}x</span>` : ""}
      ${c.capped ? `<span class="tiny muted" title="the line is nearly certain; the 2% left is an early exit or a DNP that doesn't void">capped at 98%</span>` : ""}
      ${pick && c.needs ? `<span class="pill warn" title="skip this pick if the app shows a lower multiplier">needs ≥ ${c.needs.toFixed(2)}x</span>` : ""}
      ${pick ? "" : `<span class="num small">${c.price?.am != null ? odds3(c.price.am) : c.price?.standIn ? `~${odds3(amOf(c.D))}` : odds3(amOf(c.D))}</span>`}
      <span class="tiny muted" title="our chance">${prob(c.P)}</span></div>`).join("");
  const bar = `<div class="pr-evb"><i class="${o.ev >= 0 ? "pos" : "neg"}" style="width:${Math.min(100, Math.abs(o.ev) / max * 100).toFixed(0)}%"></i></div>`;
  if (dep) return `<div class="row pr-leg">${legsH.replace(/^<div class="row pr-leg">/, "").replace(/<\/div>$/, "")}<button class="btn sm ghost" data-take="${esc(p.id)}|${i}">Log</button></div>`;
  const head = pick ? `<span>${o.legs.length}-pick · hits all ${prob(o.P, 1)}${p.flex ? " · flex" : o.perPick ? ` · pays ${o.payX.toFixed(2)}x (${o.legs.map((c) => (c.promo ? p.promoMult : c.perX ?? p.otherMult ?? 1.73).toFixed(2) + "x").join(" × ")})${o.assumed ? ` <span class="tiny muted" title="the feed doesn't carry this app's multipliers; type the other pick's real one in the promo">other pick assumed at 1.73x</span>` : ""}` : ` · pays ${o.payX ? `${(+o.payX).toFixed(2)}x` : `${p.mult || payoutFor(p.venue, o.legs.length) || "?"}x`}${k === "bonus_bet" ? " · bonus funds: only the winnings come back" : ""}`}</span>`
    : `<span>${o.legs.length > 1 ? `${o.legs.length}-leg parlay at ` : "Single at "}<b class="num">${odds3(amOf(o.D))}</b> · wins ${prob(o.P, 1)}${o.sgp ? " · same-game estimate" : ""}${o.est ? " · chance estimated from the original price" : ""}</span>`;
  return `<div class="pl pr-opt">${legsH}
    <div class="pl-b" style="margin-top:4px;gap:8px;align-items:center">${head}<span class="row" style="gap:8px"><span class="${o.ev >= 0 ? "pos" : "neg"}"><b>${money(o.ev)}</b> EV</span><button class="btn sm ghost" data-takelab="${esc(p.id)}|${i}" title="open these legs in the Bet Lab with this promo attached">Lab</button><button class="btn sm ghost" data-take="${esc(p.id)}|${i}">Log</button></span></div>${bar}
    ${o.note ? `<div class="tiny muted">${esc(o.note)}</div>` : ""}</div>`;
}
function depSummary(d) {
  const tiles = [["Bonus", money(d.amount), ""], ["To wager", money(d.turnover), "playthrough"], ["Expected cost", money(-d.cost), `${Math.round((1 - d.ret) * 1000) / 10}% of each $ wagered`], ["Net value", money(d.amount - d.cost), ""]];
  return `<div class="pr-ck">${tiles.map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v ${k === "Net value" ? "pos" : ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("")}</div>
    <div class="tiny muted">Clear the playthrough on bets priced closest to fair at this app, like these:</div>`;
}

function manualCard(p, st) {
  const bets = [...S.bets.values()].filter((b) => b.promoId === p.id);
  const got = bets.reduce((s2, b) => s2 + (b.profit ?? 0), 0);
  const credits = [...S.wallet.values()].filter((w) => w.promoId === p.id);
  return `<div class="card col pr-card ${st === "used" || st === "expired" ? "pr-dim" : ""}" style="gap:10px">
    <div class="row" style="justify-content:space-between;gap:8px"><b class="row ellip" style="gap:6px">${p.venue !== "other" ? bookLogo(p.venue, venueName(p.venue), 20) : ""}${esc(p.title || vname(p))}</b>
      <span class="pill ${st === "active" ? "pos" : st === "today" ? "warn" : st === "expired" ? "neg" : ""}">${st === "today" ? "ends today" : st}</span></div>
    <div class="row" style="gap:6px;flex-wrap:wrap"><span class="pill">Other promo</span><span class="small">${esc(describe(p))}</span></div>
    <div class="pr-worth"><div><div class="pr-big ${(+p.value || 0) > 0 ? "pos" : "muted"}">${p.value != null && p.value !== "" ? money(+p.value) : "–"}</div><div class="tiny muted">expected profit, as you entered it${p.cost ? ` · ${money(+p.cost)} at stake or tied up` : ""}</div></div></div>
    ${bets.length || credits.length ? `<div class="tiny">Logged: ${bets.length ? `${bets.length} result${bets.length > 1 ? "s" : ""} in the Ledger, <span class="${got >= 0 ? "pos" : "neg"}">${money(got)}</span>` : ""}${bets.length && credits.length ? " · " : ""}${credits.length ? `${money(credits.reduce((a, w) => a + (+w.amount || 0), 0))} bonus funds in the Wallet` : ""}</div>` : ""}
    <div class="row" style="flex-wrap:wrap"><button class="btn sm" data-mres="${esc(p.id)}">Log what came in</button><button class="btn sm ghost" data-edit="${esc(p.id)}">Edit</button>
      <button class="btn sm ghost" data-used="${esc(p.id)}">${p.used ? "Mark active" : "Mark used"}</button><button class="btn sm ghost" data-del="${esc(p.id)}">Delete</button></div>
  </div>`;
}

// what a manual promo actually brought in: cash goes to the Ledger as a settled result (stake = what it cost, profit = what
// came in minus that), bonus funds go to the Wallet as a credit at that app (and become usable bonus there)
function manualResult(p) {
  const ven = VENUES.filter((v) => v.key !== "any");
  sheet(`<h3>Log what came in</h3>
    <div class="col" style="gap:10px"><div class="small">${esc(p.title || describe(p))}</div>
      <div class="fgrid">
        <label class="f">It cost you $<input id="mr-cost" inputmode="decimal" value="${esc(p.cost ?? 0)}"></label>
        <label class="f">You got back $ (in total)<input id="mr-got" inputmode="decimal" placeholder="0 if nothing" value=""></label>
        <label class="f">Paid as<select id="mr-as"><option value="cash" ${p.credit !== "bonus" ? "selected" : ""}>Cash</option><option value="bonus" ${p.credit === "bonus" ? "selected" : ""}>Bonus funds</option></select></label>
        <label class="f">App<select id="mr-ven">${ven.map((v) => `<option value="${v.key}" ${v.key === p.venue ? "selected" : ""}>${esc(v.key === "other" && p.venueOther ? p.venueOther : v.name)}</option>`).join("")}</select></label>
        <label class="f">Date<input id="mr-date" type="date" value="${todayPT()}"></label>
      </div>
      <label class="row lb-boost" style="gap:8px;align-items:center"><input type="checkbox" id="mr-used" checked><span class="small">Mark the promo used</span></label>
      <div class="mr-out card" style="padding:10px 12px"></div>
      <div class="row"><button class="btn" id="mr-save">Save</button><button class="btn ghost" id="mr-cancel">Cancel</button></div></div>`, (el, close) => {
    const rd = () => ({ cost: parseFloat($("#mr-cost", el).value) || 0, got: parseFloat($("#mr-got", el).value), as: $("#mr-as", el).value, venue: $("#mr-ven", el).value });
    const show = () => { const r = rd();
      $(".mr-out", el).innerHTML = isNaN(r.got) ? `<span class="tiny muted">Enter what came back (0 if it lost or never paid).</span>`
        : r.as === "bonus" ? `<span class="small">${money(r.got)} goes into your Wallet as bonus funds at this app${r.cost ? `; the ${money(r.cost)} it cost is logged as a ${money(-r.cost)} result` : ""}. It counts as profit once you turn it into cash.</span>`
        : `<span class="small">Logged in your Ledger as <b class="${r.got - r.cost >= 0 ? "pos" : "neg"}">${money(r.got - r.cost)}</b>${r.cost ? ` (${money(r.got)} back on ${money(r.cost)})` : ""}.</span>`; };
    ["mr-cost", "mr-got"].forEach((i) => $("#" + i, el).oninput = show); $("#mr-as", el).onchange = show; show();
    $("#mr-cancel", el).onclick = close;
    $("#mr-save", el).onclick = async () => {
      const r = rd();
      if (isNaN(r.got)) { toast("Enter what came back (0 if nothing)"); return; }
      const v = VENUES.find((x) => x.key === r.venue), vn = r.venue === "other" ? (p.venueOther || "Other app") : v?.name || r.venue;
      const day = $("#mr-date", el).value || todayPT(), at = new Date(`${day}T12:00:00-08:00`).toISOString();
      const label = p.what || p.title || "Promo";
      try {
        const cashBack = r.as === "bonus" ? 0 : r.got;
        if (r.cost || r.as !== "bonus") {          // the money side: what it cost against what came back as cash
          const profit = Math.round((cashBack - r.cost) * 100) / 100, dec = r.cost > 0 && cashBack > r.cost ? cashBack / r.cost : null;
          await saveBet({ id: uid(), createdAt: new Date().toISOString(), date: day, settledAt: at, label, venue: r.venue, venueName: vn,
            stake: r.cost, am: dec ? amOf(dec) : null, dec: dec || 1, status: profit >= 0 ? "won" : "lost", profit,
            promoId: p.id, manual: true, source: "promo-manual", leagues: ["Other"] });
        }
        if (r.as === "bonus" && r.got > 0) await saveWallet({ id: uid(), venue: r.venue, type: "bonus", amount: r.got, at, note: label, promoId: p.id });
        if ($("#mr-used", el).checked && !p.used) await savePromo({ ...S.promos.get(p.id), used: true, usedAt: new Date().toISOString() });
        toast(r.as === "bonus" ? "Added to your Wallet" : "Logged in your Ledger"); close();
      } catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

function promoCard(p) {
  if (kindOf(p.kind).key === "manual") return manualCard(p, status(p));
  const opts = best(p), k = kindOf(p.kind).key, st = status(p);
  const bets = [...S.bets.values()].filter((b) => b.promoId === p.id);
  const pl = bets.reduce((s, b) => s + (b.profit ?? 0), 0);
  const w = worth(p, opts), o = opts[0];
  const usesBonus = ["bonus_bet", "no_sweat", "parlay_insurance", "bet_and_get"].includes(k) && !PICKEM.has(p.venue);
  const max = Math.max(0.01, ...opts.map((x) => Math.abs(x.ev)));
  return `<div class="card col pr-card ${st === "used" || st === "expired" ? "pr-dim" : ""}" style="gap:10px">
    <div class="row" style="justify-content:space-between;gap:8px"><b class="row ellip" style="gap:6px">${bookLogo(p.venue, venueName(p.venue), 20)}${esc(p.title || venueName(p.venue))}</b>
      <span class="pill ${st === "active" ? "pos" : st === "today" ? "warn" : st === "expired" ? "neg" : ""}">${st === "today" ? "ends today" : st}</span></div>
    <div class="row" style="gap:6px;flex-wrap:wrap"><span class="pill">${esc(kindOf(p.kind).name)}</span><span class="small">${esc(describe(p))}</span></div>
    <div class="pr-worth"><div><div class="pr-big ${w > 0 ? "pos" : "muted"}">${o ? money(w) : "–"}</div><div class="tiny muted">${o ? "expected value of the best use" : "nothing on the board fits right now"}</div></div>
      ${o ? (() => {
        const s1 = opts.find((x) => x.legs.length === 1), pN = opts.find((x) => x.legs.length > 1);
        const line = (lab, x) => `<div class="tiny pr-best"><span class="muted">${lab}:</span> ${esc(x.legs.map((c) => c.leg.label).join(" + ").slice(0, 110))} <b class="${x.ev >= 0 ? "pos" : "neg"}">${money(x.ev)}</b></div>`;
        if (o.pick && k === "bonus_bet") {      // bonus funds at a pick'em app: the best entry of each size, with how often it hits
          const by = [2, 3, 4, 5].map((n) => opts.find((x) => x.legs.length === n)).filter(Boolean);
          return `<div class="col" style="gap:2px">${by.map((x) => `<div class="tiny pr-best"><span class="muted">${x.legs.length}-pick, hits ${prob(x.P, 1)}, pays ${(+x.payX).toFixed(1)}x:</span> <b class="${x.ev >= 0 ? "pos" : "neg"}">${money(x.ev)}</b> <span class="muted">(${Math.round(x.ev / (p.amount || 10) * 100)}% of the bonus)</span></div>`).join("")}
            <div class="tiny muted">Longer entries keep more of the bonus on average but hit far less often, and errors in each pick's chance compound. Fewer picks is the steadier way to turn it into cash.</div></div>`;
        }
        return s1 && pN && !o.pick ? `<div class="col" style="gap:2px">${line("Best single", s1)}${line(`Best ${pN.legs.length}-leg parlay`, pN)}</div>` : line("Best", o);
      })() : ""}</div>
    ${usesBonus ? `<div class="tiny muted">Bonus bets here convert at about <b>${Math.round(bonusConv(p.venue) * 100)}%</b> right now</div>` : ""}
    ${bets.length ? `<div class="tiny">${bets.length} bet${bets.length > 1 ? "s" : ""} logged · P/L <span class="${pl >= 0 ? "pos" : "neg"}">${money(pl)}</span></div>` : ""}
    <div class="row" style="flex-wrap:wrap"><button class="btn sm" data-open="${esc(p.id)}">${open.has(p.id) ? "Hide bets" : "See best bets"}</button><button class="btn sm ghost" data-edit="${esc(p.id)}">Edit</button>
      ${k === "discounted_line" && st !== "used" && st !== "expired" && p.target ? `<button class="btn sm ghost" data-lab="${esc(p.id)}" title="pair it with any pick you like and log it">Build your own in the Bet Lab</button>` : ""}
      ${p.wallet ? `<span class="tiny muted">From your Wallet: the amount follows your bonus balance there</span>` : `<button class="btn sm ghost" data-used="${esc(p.id)}">${p.used ? "Mark active" : "Mark used"}</button><button class="btn sm ghost" data-del="${esc(p.id)}">Delete</button>`}</div>
    ${open.has(p.id) ? `<div class="col" style="gap:8px">${usesBonus ? convChart(p) : ""}
      ${!venueHasFeed(p.venue) && p.venue !== "any" ? `<div class="note">${esc(venueName(p.venue))} isn't in the odds feed: prices shown are typical sportsbook prices. Check the app's price before betting.</div>` : ""}
      ${VENUES.find((v) => v.key === p.venue)?.proxy ? `<div class="note">${esc(venueName(p.venue))} prices are read from the matching sportsbook; confirm the price in the app.</div>` : ""}
      ${k === "deposit_match" && opts[0]?.deposit ? depSummary(opts[0].deposit) : ""}
      ${opts.some((o) => o.perPick) ? `<div class="note">${esc(venueName(p.venue))} pays each pick its own multiplier and the entry pays them multiplied together (the promo pick's ${p.promoMult}x included). ${p.otherMult ? `Other picks use the ${p.otherMult}x you entered.` : `The feed doesn't carry this app's multipliers, so the other pick is assumed at 1.73x: any pick the app pays at or above its "needs" number keeps the entry worth playing. Edit the promo with the real multiplier for an exact EV.`}</div>`
      : opts.some((o) => o.pick && o.legs.some((c) => c.needs)) ? `<div class="note">${esc(venueName(p.venue))} sets a payout multiplier on each pick that the odds feed doesn't carry. These suggestions stick to near-even picks the app usually pays at 1x; if the app shows a pick below its "needs" number, swap it out.</div>` : ""}
      ${opts.length ? opts.slice(0, 6).map((x, i) => optRow(p, x, i, max)).join("") : `<div class="note">Nothing on the board meets every rule yet. More games and props arrive with each pull.</div>`}</div>` : ""}
  </div>`;
}

// ---------- check any bet
function checkerCard() {
  const l = findTarget(checker.key);
  let out = "";
  if (l) {
    const line = checker.line !== "" ? +checker.line : l.point;
    const side = l.side;
    const P = l.kind === "prop" && l.mean != null && line != null && line !== l.point ? pAtLine(l, line, side === "Under" ? "Under" : "Over") : l.fair;
    const odds = checker.odds !== "" ? +checker.odds : l.best?.am ?? null;
    const boost = (+checker.boost || 0) / 100;
    const mult = checker.mult !== "" ? +checker.mult : null;
    let ev = null, be = null, verdict = "";
    if (mult) { be = 1 / mult; ev = P * mult - 1; }
    else if (odds != null && !isNaN(odds)) { const D = decOf(odds); be = 1 / (1 + (D - 1) * (1 + boost)); ev = P * (1 + (D - 1) * (1 + boost)) - 1; }
    if (ev != null) verdict = ev > 0.03 ? `<span class="pill pos">good bet</span>` : ev > 0 ? `<span class="pill">thin edge</span>` : `<span class="pill neg">pass</span>`;
    const tops = [...l.venues].filter((v) => !v.stale).sort((a, b) => a.cost - b.cost).slice(0, 5);
    out = `<div class="row pr-tgt">${betImage({ gameId: l.gameId, label: l.label, player: l.player, headshot: l.headshot }, 30)}<div class="col grow"><b>${esc(l.label)}</b><span class="tiny muted">${esc(l.matchup)} · ${fmtTime(l.commence)}</span></div><button class="btn sm ghost" data-uncheck>Change</button></div>
      <div class="fgrid">
        <label class="f">Your odds<input id="ck-odds" inputmode="numeric" value="${esc(checker.odds)}" placeholder="${l.best?.am != null ? am(l.best.am) : "-110"}"></label>
        ${l.kind === "prop" && l.mean != null ? `<label class="f">Your line<input id="ck-line" inputmode="decimal" value="${esc(checker.line)}" placeholder="${l.point ?? ""}"></label>` : ""}
        <label class="f">Boost %<input id="ck-boost" inputmode="numeric" value="${esc(checker.boost)}" placeholder="0"></label>
        <label class="f">Pick'em multiplier (per pick)<input id="ck-mult" inputmode="decimal" value="${esc(checker.mult)}" placeholder="e.g. 1.73"></label>
      </div>
      <div class="pr-ck">
        <div class="kpi"><div class="k">Our chance${line !== l.point ? ` at ${line}` : ""}</div><div class="v">${prob(P, 1)}</div><div class="s">sharp fair${l.ourModel != null ? ` · model ${prob(l.ourModel)}` : ""}</div></div>
        <div class="kpi"><div class="k">Break-even</div><div class="v">${be != null ? prob(be, 1) : "–"}</div><div class="s">at your price</div></div>
        <div class="kpi"><div class="k">Value per $10</div><div class="v ${ev > 0 ? "pos" : "neg"}">${ev != null ? money(ev * 10) : "–"}</div><div class="s">${verdict}</div></div>
      </div>
      ${tops.length ? `<div class="tiny muted">Best prices now: ${tops.map((v) => `${esc(v.name)} ${odds3(v.am)}`).join(" · ")}</div>` : ""}`;
  } else {
    out = `<input id="ck-q" placeholder="Search any bet on the board: 'Tatum points', 'Kings ML', 'Over 6.5 Sharks'" value="${esc(checker.q)}">${pickList(search(checker.q, { n: 10 }), "data-check")}`;
  }
  return `<div class="card col" style="gap:10px"><div class="tiny muted">For specials, boosted prices, discounted lines or anything else: pick the bet, type the app's price or line, see if it's worth it.</div>${out}</div>`;
}

// Claude's promo desk: requests written in words, their status, Claude's questions and what it set up
function deskHtml() {
  const reqs = [...S.promoreq.values()].filter((r) => r.at && Date.now() - Date.parse(r.at) < 7 * 864e5).sort((a, b) => (b.at || "").localeCompare(a.at || "")).slice(0, 6);
  if (!reqs.length) return "";
  const pill = { waiting: ["", "waiting for Claude"], question: ["s", "Claude has a question"], done: ["pos", "set up"], failed: ["neg", "couldn't set up"] };
  return `<div class="card col" style="gap:8px;margin-top:10px"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><b>Promo desk</b><span class="tiny muted">Claude checks every hour, 8 AM to 11 PM</span></div>
    ${reqs.map((r) => { const [c, t] = pill[r.status] || ["", r.status], last = (r.thread || []).filter((x) => x.from === "claude").at(-1);
      const p = r.promoId && S.promos.get(r.promoId);
      return `<div class="pd-row col" style="gap:4px;border-top:1px solid var(--line);padding-top:8px">
        <div class="row" style="gap:8px;align-items:flex-start"><span class="pill ${c}">${esc(t)}</span><span class="small grow" style="min-width:0">${esc(r.text)}</span><button class="x" data-pddel="${r.id}" title="remove">×</button></div>
        ${last ? `<div class="small" style="padding-left:6px;border-left:2px solid var(--accent)">${esc(last.text)}</div>` : ""}
        ${p ? `<div class="tiny muted">Saved as <b>${esc(p.title || kindOf(p.kind).name)}</b> below${r.status === "done" ? "" : ""}</div>` : ""}
        ${r.status === "question" ? `<div class="row" style="gap:6px"><input class="grow" data-pdans="${r.id}" placeholder="Your answer"><button class="btn sm" data-pdsend="${r.id}">Answer</button></div>` : ""}
      </div>`; }).join("")}</div>`;
}

export function renderPromos(el) {
  loadAllProps();
  syncWalletPromos();          // bonus funds in the Wallet show up here as a promo
  const list = [...S.promos.values()].map(normalize);
  const rank = { today: 0, active: 1, used: 2, expired: 3 };
  list.sort((a, b) => rank[status(a)] - rank[status(b)] || worth(b, best(b)) - worth(a, best(a)));
  const live = list.filter((p) => ["active", "today"].includes(status(p)));
  const old = list.filter((p) => !["active", "today"].includes(status(p))).sort((a, b) => (b.usedAt || b.expires || b.createdAt || "").localeCompare(a.usedAt || a.expires || a.createdAt || ""));
  const total = live.reduce((s, p) => s + worth(p, best(p)), 0);
  const settled = [...S.bets.values()].filter((b) => b.promoId && b.profit != null);
  const plv = settled.reduce((s, b) => s + b.profit, 0);
  const groups = [...new Set(KINDS.map((k) => k.group))];
  el.innerHTML = `<div class="kpis" style="margin-top:18px">
      <div class="kpi"><div class="k">Active promos</div><div class="v">${live.length}</div><div class="s">${list.filter((p) => status(p) === "today").length} end today</div></div>
      <div class="kpi"><div class="k">Worth right now</div><div class="v pos">${money(total)}</div><div class="s">expected value, best use of each</div></div>
      <div class="kpi"><div class="k">Promo bets settled</div><div class="v">${settled.length}</div></div>
      <div class="kpi"><div class="k">Promo P/L</div><div class="v ${plv >= 0 ? "pos" : "neg"}">${money(plv)}</div></div></div>
    <div class="card col" style="gap:8px;margin-top:12px">
      <b>Add a promo</b>
      <textarea id="pr-text" placeholder="Paste it from the app, e.g. 'Kalshi: no fees on NFL trades this weekend' · 'Bet $25 get $100 in bonus bets' · 'PrizePicks: LeBron 18.5 points discounted, more only' · 'FanDuel 30% SGP boost, 3+ legs, max $25'"></textarea>
      <div id="pr-live"></div>
      <div class="row" style="flex-wrap:wrap"><button class="btn" id="pr-read">Set it up</button><button class="btn ghost" id="pr-claude" title="Claude reads it, programs it in exactly as written (adding a new promo type if none fits) and checks the math">Send to Claude</button><span class="tiny muted">or start from a type:</span>
        ${groups.map((g) => KINDS.filter((k) => k.group === g).map((k) => `<button class="chip sm" data-new="${k.key}" title="${esc(k.blurb)}">${esc(k.name)}</button>`).join("")).join("")}</div>
    </div>
    ${deskHtml()}
    ${draft ? form(draft) : ""}
    <div class="sec-title">Your promos<span class="line"></span></div>
    ${live.length ? `<div class="grid pr-grid">${live.map(promoCard).join("")}</div>` : `<div class="empty"><h3>${old.length ? "No promos to use right now" : "No promos saved"}</h3>Paste one above and I'll value it and find the best bet for it on the live board.</div>`}
    ${old.length ? `<div class="row" style="justify-content:space-between;margin-top:14px;gap:8px;flex-wrap:wrap"><button class="chip ${showOld ? "on" : ""}" id="pr-old">${showOld ? "Hide" : "Show"} used and expired (${old.length})</button><span class="tiny muted">kept so bets that used them still settle with the promo</span></div>
      ${showOld ? `<div class="grid pr-grid" style="margin-top:10px">${old.map(promoCard).join("")}</div>` : ""}` : ""}
    <div class="sec-title">Check any bet<span class="line"></span></div>
    ${checkerCard()}`;
  bind(el);
}

function bind(el) {
  const rerender = () => renderPromos(el);
  const tx = $("#pr-text", el), live = $("#pr-live", el);
  let tm = null;
  if (tx) tx.oninput = () => { clearTimeout(tm); tm = setTimeout(() => { const t = tx.value.trim(); live.innerHTML = t.length > 6 ? readout(autoTarget(parsePromo(t))) : ""; }, 250); };
  $("#pr-read", el).onclick = () => { const t = $("#pr-text", el).value.trim(); if (!t) return; draft = autoTarget(parsePromo(t)); targetQ = ""; rerender(); };
  $$("[data-new]", el).forEach((b) => b.onclick = () => { draft = blank(b.dataset.new, PICKEM.has(draft?.venue) ? draft.venue : "any"); targetQ = ""; if (["pickem_boost", "free_entry", "discounted_line"].includes(b.dataset.new)) draft.venue = "prizepicks"; if (b.dataset.new === "manual") { draft.venue = "other"; draft.credit = "cash"; } rerender(); });
  if (draft) {
    $$("[data-k]", el).forEach((i) => i.onchange = () => {
      const k = i.dataset.k, t = i.dataset.t, v = i.value.trim();
      if (t === "pct") draft[k] = (parseFloat(v) || 0) / 100;
      else if (t === "odds") draft[k] = v === "" ? null : /even/i.test(v) ? 100 : parseFloat(v);
      else if (t === "num" || t === "int") draft[k] = v === "" ? null : parseFloat(v);
      else if (k === "stakeBack") draft[k] = v === "true";
      else draft[k] = v || null;
      if (k === "kind" && PICKEM.has(draft.venue) !== ["pickem_boost", "free_entry", "discounted_line"].includes(v)) draft.venue = ["pickem_boost", "free_entry", "discounted_line"].includes(v) ? "prizepicks" : "any";
      if ((k === "picks" || k === "venue") && PICKEM.has(draft.venue) && draft.picks && !draft.mult) draft.mult = payoutFor(draft.venue, draft.picks);
      rerender();
    });
    $$("[data-sp]", el).forEach((b) => b.onclick = () => { const s = b.dataset.sp; draft.sports = draft.sports.includes(s) ? draft.sports.filter((x) => x !== s) : [...draft.sports, s]; rerender(); });
    $$("[data-bt]", el).forEach((b) => b.onclick = () => { const s = b.dataset.bt; draft.betTypes = draft.betTypes.includes(s) ? draft.betTypes.filter((x) => x !== s) : [...draft.betTypes, s]; rerender(); });
    const ops = $("[data-ops]", el); if (ops) ops.onclick = () => { draft.onePerSport = !draft.onePerSport; rerender(); };
    const tq = $("#pr-tq", el);
    if (tq) { tq.oninput = () => { targetQ = tq.value; const box = tq.nextElementSibling; const html = pickList(search(targetQ, { props: draft.kind === "discounted_line" }), "data-target");
      if (box?.classList.contains("pr-hits")) box.outerHTML = html || "<div class=pr-hits></div>"; else tq.insertAdjacentHTML("afterend", html); bindTargets(); }; }
    const bindTargets = () => $$("[data-target]", el).forEach((b) => b.onclick = () => {
      const l = findTarget(b.dataset.target); draft.target = l.key; draft.targetLabel = l.player || l.label;
      if (draft.kind === "discounted_line") { draft.promoSide = l.side === "Under" ? "Under" : "Over"; if (draft.promoLine == null) draft.promoLine = l.point; }
      if (draft.kind === "odds_boost" && draft.origOdds == null && l.best?.am != null) draft.origOdds = l.best.am;
      rerender();
    });
    bindTargets();
    const ut = $("[data-untarget]", el); if (ut) ut.onclick = () => { draft.target = null; rerender(); };
    $("#pr-cancel", el).onclick = () => { draft = null; rerender(); };
    $("#pr-save", el).onclick = async () => {
      const p = { ...draft, id: draft.id || uid(), createdAt: draft.createdAt || new Date().toISOString() };
      if (["bonus_bet", "free_entry"].includes(kindOf(p.kind).key) && !p.wallet) p.funds = fundsNow(draft);
      p.title = draft.titleSet ? draft.title : kindOf(p.kind).key === "manual" ? `${vname(p)} · ${(p.what || "Other promo").slice(0, 48)}` : `${venueName(p.venue)} · ${kindOf(p.kind).name}`;
      delete p.type;
      try { await savePromo(p); results.delete(p.id); open.add(p.id); draft = null; toast("Promo saved"); } catch (e) { toast("Could not save: " + e.message); }
    };
  }
  $$("[data-open]", el).forEach((b) => b.onclick = () => { const id = b.dataset.open; open.has(id) ? open.delete(id) : open.add(id); rerender(); });
  const pc = $("#pr-claude", el);
  if (pc) pc.onclick = async () => {
    const text = ($("#pr-text", el)?.value || "").trim();
    if (!text) { toast("Write the promo first"); return; }
    const r = { id: uid(), text, at: new Date().toISOString(), status: "waiting", thread: [{ from: "vince", text, at: new Date().toISOString() }] };
    try { await savePromoReq(r); $("#pr-text", el).value = ""; toast("Sent to Claude's promo desk"); } catch (e) { toast("Could not send: " + e.message); }
  };
  $$("[data-pdsend]", el).forEach((b) => b.onclick = async () => {
    const r = S.promoreq.get(b.dataset.pdsend), inp = $(`[data-pdans="${b.dataset.pdsend}"]`, el), ans = (inp?.value || "").trim();
    if (!r || !ans) return;
    await savePromoReq({ ...r, status: "waiting", thread: [...(r.thread || []), { from: "vince", text: ans, at: new Date().toISOString() }] });
    toast("Answer sent");
  });
  $$("[data-pddel]", el).forEach((b) => b.onclick = () => deletePromoReq(b.dataset.pddel));
  $$("[data-take]", el).forEach((b) => b.onclick = () => {
    const [id, i] = b.dataset.take.split("|");
    const p = normalize(S.promos.get(id)), o = best(p)[+i], k = kindOf(p.kind).key;
    const one = o.legs.length === 1 ? o.legs[0].leg : null;
    const pick = ["pickem_boost", "free_entry", "discounted_line"].includes(k) || !!o.pick;
    const mult = pick ? (o.payX || p.mult || payoutFor(p.venue, o.legs.length)) : null;      // a bonus-funded entry logs at its full payout
    const stake = ["bonus_bet", "free_entry"].includes(k) ? p.amount : k === "bet_and_get" ? p.qualStake : p.stake;
    const odds = pick ? (mult ? amOf(mult) : "") : k === "odds_boost" ? p.boostedOdds : amOf(o.D);
    logSheet({ label: o.legs.map((c) => c.leg.label).join(" + "), am: odds, stake: stake || "", fair: o.P,
      venue: p.venue !== "any" ? p.venue : undefined, promoId: p.id, gameId: one?.gameId, league: one?.league, matchup: one?.matchup,
      commence: one?.commence, ...(one ? { market: one.market, side: one.side, point: legSpec(one).point, player: one.player || null, team: one.team || null } : {}),
      legs: o.legs.map((c) => ({ ...legSpec(c.leg), point: c.promo ? draftLine(p, c) : legSpec(c.leg).point, fair: c.P })), source: "promo" });
  });
  $$("[data-edit]", el).forEach((b) => b.onclick = () => { draft = normalize(S.promos.get(b.dataset.edit)); draft.titleSet = false; rerender(); scrollTo(0, 0); });
  $$("[data-used]", el).forEach((b) => b.onclick = () => { const p = S.promos.get(b.dataset.used); savePromo({ ...p, used: !p.used, usedAt: !p.used ? new Date().toISOString() : null }).then(() => toast(p.used ? "Back in your promos" : "Marked used: moved to Used and expired")); });
  $$("[data-lab]", el).forEach((b) => b.onclick = () => { labWithPromo(b.dataset.lab); location.hash = "#/lab"; });
  $$("[data-mres]", el).forEach((b) => b.onclick = () => manualResult(normalize(S.promos.get(b.dataset.mres))));
  $$("[data-takelab]", el).forEach((b) => b.onclick = () => {
    const [id, i] = b.dataset.takelab.split("|");
    const p = normalize(S.promos.get(id)), o = best(p)[+i], k = kindOf(p.kind).key;
    if (!o) return;
    const specs = o.legs.filter((c) => !c.promo).map((c) => ({ ...legSpec(c.leg), pickem: c.leg.pickem || null }));
    const boost = ["profit_boost", "parlay_boost"].includes(k) && p.boost ? Math.round(p.boost * 100) : 0;
    const stake = ["bonus_bet", "free_entry"].includes(k) ? p.amount : k === "bet_and_get" ? p.qualStake : p.stake;
    addToLab(specs, { replace: true, promoLeg: o.legs.some((c) => c.promo) ? p.id : null, venue: p.venue !== "any" ? p.venue : undefined, promoId: p.id, boost, stake: stake || undefined });
  });
  const po = $("#pr-old", el); if (po) po.onclick = () => { showOld = !showOld; rerender(); };

  $$("[data-del]", el).forEach((b) => b.onclick = () => { if (b.dataset.confirm) { deletePromo(b.dataset.del); open.delete(b.dataset.del); } else { b.dataset.confirm = 1; b.textContent = "Tap again to delete"; } });
  // bet checker
  const cq = $("#ck-q", el);
  if (cq) cq.oninput = () => { checker.q = cq.value; const box = cq.nextElementSibling; const html = pickList(search(checker.q, { n: 10 }), "data-check");
    if (box?.classList.contains("pr-hits")) box.outerHTML = html || "<div class=pr-hits></div>"; else cq.insertAdjacentHTML("afterend", html); bindChecks(); };
  const bindChecks = () => $$("[data-check]", el).forEach((b) => b.onclick = () => { checker = { ...checker, key: b.dataset.check, odds: "", line: "", boost: "", mult: "" }; rerender(); });
  bindChecks();
  const un = $("[data-uncheck]", el); if (un) un.onclick = () => { checker.key = null; rerender(); };
  [["#ck-odds", "odds"], ["#ck-line", "line"], ["#ck-boost", "boost"], ["#ck-mult", "mult"]].forEach(([s, k]) => { const i = $(s, el); if (i) i.onchange = () => { checker[k] = i.value.trim(); rerender(); }; });
}
