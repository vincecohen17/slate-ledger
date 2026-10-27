import { S, savePromo, deletePromo, loadAllProps } from "../store.js";
import { esc, am, prob, fmtTime, money, uid, toast, todayPT, bookLogo, $, $$ } from "../util.js";
import { VENUES, venueName } from "../lab.js";
import { KINDS, FIELDS, SPORTS, BET_TYPES, PICKEM, FEE_VENUES, kindOf, blank, normalize, parsePromo, describe, optimize, legs,
  bonusConv, convCurve, findTarget, pAtLine, autoTarget, decOf, amOf, payoutFor, venueHasFeed } from "../promo.js";
import { betImage, logSheet } from "./common.js";

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
const worth = (p, opts) => Math.max(0, opts[0]?.ev || 0) * (kindOf(p.kind).key === "bonus_bet" ? (p.count || 1) : 1);
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

// ---------- the form: only the fields this kind of promo needs
function field(p, f) {
  const [lab, type, ph] = FIELDS[f] || [f, "num"];
  let val = p[f];
  if (type === "pct") val = val ? Math.round(val * 100) : "";
  if (type === "odds" && val != null && val !== "") val = val > 0 ? `+${val}` : val;
  return `<label class="f">${esc(lab)}<input data-k="${f}" data-t="${type}" ${type === "date" ? 'type="date"' : 'inputmode="decimal"'} value="${esc(val ?? "")}" placeholder="${esc(ph || "")}"></label>`;
}
const sel = (k, cur, opts, lab) => `<label class="f">${lab}<select data-k="${k}" data-t="str">${opts.map(([v, t]) => `<option value="${v}" ${String(cur) === String(v) ? "selected" : ""}>${t}</option>`).join("")}</select></label>`;

function form(p) {
  const kind = kindOf(p.kind), F = new Set(kind.fields);
  const groups = [...new Set(KINDS.map((k) => k.group))];
  const venues = VENUES.filter((v) => (["pickem_boost", "free_entry", "discounted_line"].includes(kind.key) ? v.dfs : true));
  const inputs = [...F].filter((f) => FIELDS[f] && f !== "expires").map((f) => field(p, f));
  if (F.has("refundAs")) inputs.push(sel("refundAs", p.refundAs, [["bonus", "Bonus bets"], ["cash", "Cash"]], "Refund paid as"));
  if (F.has("condition")) inputs.push(sel("condition", p.condition, [["any", "Always"], ["win", "Only if it wins"], ["lose", "Only if it loses"]], "Bonus arrives"));
  if (F.has("ptBase")) inputs.push(sel("ptBase", p.ptBase, [["bonus", "Bonus only"], ["both", "Deposit + bonus"]], "Playthrough on"));
  if (F.has("sameGame")) inputs.push(sel("sameGame", p.sameGame, [["allowed", "Any games"], ["banned", "Different games only"], ["required", "Same-game parlay only"]], "Games"));
  if (F.has("promoSide")) inputs.push(sel("promoSide", p.promoSide, [["Over", "Over / More"], ["Under", "Under / Less"]], "Side"));
  if (F.has("stakeBack")) inputs.push(sel("stakeBack", p.stakeBack, [["true", "Yes (cash bet)"], ["false", "No (bonus stake)"]], "Stake returned on a win"));
  if (["pickem_boost", "free_entry", "discounted_line"].includes(kind.key)) inputs.push(`<label class="f">Flex payouts (optional)<input data-k="flex" data-t="str" value="${esc(p.flex || "")}" placeholder="3:2.25, 2:1.25"></label>`);
  inputs.push(field(p, "expires"));
  const tgt = F.has("target") ? findTarget(p.target) : null;
  const preview = (() => {
    try {
      const o = optimize({ ...p, id: "draft" }, 1)[0];
      if (!o) return `<span class="muted">Nothing on the board fits yet</span>`;
      return `Worth about <b class="${o.ev >= 0 ? "pos" : "neg"}">${money(o.ev)}</b> · best: ${esc(o.legs.map((c) => c.leg.label).join(" + ").slice(0, 120))}`;
    } catch (e) { return ""; }
  })();
  return `<div class="card col pr-form" style="gap:12px;margin-top:12px">
    <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px"><b>${p.id ? "Edit promo" : "Check what I read"}</b><span class="tiny muted">${esc(describe(p))}</span></div>
    <div class="fgrid">
      <label class="f">App<select data-k="venue" data-t="str">${venues.map((x) => `<option value="${x.key}" ${x.key === p.venue ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label>
      <label class="f">Type<select data-k="kind" data-t="str">${groups.map((g) => `<optgroup label="${g}">${KINDS.filter((k) => k.group === g).map((k) => `<option value="${k.key}" ${k.key === kind.key ? "selected" : ""}>${k.name}</option>`).join("")}</optgroup>`).join("")}</select></label>
      ${inputs.join("")}
    </div>
    <div class="tiny muted">${esc(kind.blurb)}${FEE_VENUES.has(p.venue) || kind.key !== "fee_free" ? "" : " · fee waivers only matter on Kalshi, Polymarket, Novig and ProphetX"}${!venueHasFeed(p.venue) && p.venue !== "any" ? ` · ${esc(venueName(p.venue))} isn't in the odds feed, so I use a typical sportsbook price (or the sharp line for pick'em) as a stand-in` : ""}</div>
    ${F.has("target") ? `<div class="col" style="gap:6px"><span class="tiny muted">${kind.key === "discounted_line" ? "Which player prop?" : "Which bet?"}</span>
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
      ${pick ? "" : `<span class="num small">${c.price?.am != null ? am(c.price.am) : c.price?.standIn ? `~${am(amOf(c.D))}` : am(amOf(c.D))}</span>`}
      <span class="tiny muted" title="our chance">${prob(c.P)}</span></div>`).join("");
  const bar = `<div class="pr-evb"><i class="${o.ev >= 0 ? "pos" : "neg"}" style="width:${Math.min(100, Math.abs(o.ev) / max * 100).toFixed(0)}%"></i></div>`;
  if (dep) return `<div class="row pr-leg">${legsH.replace(/^<div class="row pr-leg">/, "").replace(/<\/div>$/, "")}<button class="btn sm ghost" data-take="${esc(p.id)}|${i}">Log</button></div>`;
  const head = pick ? `<span>${o.legs.length}-pick · hits all ${prob(o.P, 1)}${p.flex ? " · flex" : ` · pays ${p.mult || payoutFor(p.venue, o.legs.length) || "?"}x`}</span>`
    : `<span>${o.legs.length > 1 ? `${o.legs.length} legs at ` : ""}<b class="num">${am(amOf(o.D))}</b> · wins ${prob(o.P, 1)}${o.sgp ? " · same-game estimate" : ""}${o.est ? " · chance estimated from the original price" : ""}</span>`;
  return `<div class="pl pr-opt">${legsH}
    <div class="pl-b" style="margin-top:4px;gap:8px;align-items:center">${head}<span class="row" style="gap:8px"><span class="${o.ev >= 0 ? "pos" : "neg"}"><b>${money(o.ev)}</b> EV</span><button class="btn sm ghost" data-take="${esc(p.id)}|${i}">Log</button></span></div>${bar}
    ${o.note ? `<div class="tiny muted">${esc(o.note)}</div>` : ""}</div>`;
}
function depSummary(d) {
  const tiles = [["Bonus", money(d.amount), ""], ["To wager", money(d.turnover), "playthrough"], ["Expected cost", money(-d.cost), `${Math.round((1 - d.ret) * 1000) / 10}% of each $ wagered`], ["Net value", money(d.amount - d.cost), ""]];
  return `<div class="pr-ck">${tiles.map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v ${k === "Net value" ? "pos" : ""}">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`).join("")}</div>
    <div class="tiny muted">Clear the playthrough on bets priced closest to fair at this app, like these:</div>`;
}

function promoCard(p) {
  const opts = best(p), k = kindOf(p.kind).key, st = status(p);
  const bets = [...S.bets.values()].filter((b) => b.promoId === p.id);
  const pl = bets.reduce((s, b) => s + (b.profit ?? 0), 0);
  const w = worth(p, opts), o = opts[0];
  const usesBonus = ["bonus_bet", "no_sweat", "parlay_insurance", "bet_and_get"].includes(k);
  const max = Math.max(0.01, ...opts.map((x) => Math.abs(x.ev)));
  return `<div class="card col pr-card ${st === "used" || st === "expired" ? "pr-dim" : ""}" style="gap:10px">
    <div class="row" style="justify-content:space-between;gap:8px"><b class="row ellip" style="gap:6px">${bookLogo(p.venue, venueName(p.venue), 20)}${esc(p.title || venueName(p.venue))}</b>
      <span class="pill ${st === "active" ? "pos" : st === "today" ? "warn" : st === "expired" ? "neg" : ""}">${st === "today" ? "ends today" : st}</span></div>
    <div class="row" style="gap:6px;flex-wrap:wrap"><span class="pill">${esc(kindOf(p.kind).name)}</span><span class="small">${esc(describe(p))}</span></div>
    <div class="pr-worth"><div><div class="pr-big ${w > 0 ? "pos" : "muted"}">${o ? money(w) : "–"}</div><div class="tiny muted">${o ? "expected value of the best use" : "nothing on the board fits right now"}</div></div>
      ${o ? `<div class="tiny pr-best"><span class="muted">Best:</span> ${esc(o.legs.map((c) => c.leg.label).join(" + ").slice(0, 110))}</div>` : ""}</div>
    ${usesBonus ? `<div class="tiny muted">Bonus bets here convert at about <b>${Math.round(bonusConv(p.venue) * 100)}%</b> right now</div>` : ""}
    ${bets.length ? `<div class="tiny">${bets.length} bet${bets.length > 1 ? "s" : ""} logged · P/L <span class="${pl >= 0 ? "pos" : "neg"}">${money(pl)}</span></div>` : ""}
    <div class="row" style="flex-wrap:wrap"><button class="btn sm" data-open="${esc(p.id)}">${open.has(p.id) ? "Hide bets" : "See best bets"}</button><button class="btn sm ghost" data-edit="${esc(p.id)}">Edit</button>
      <button class="btn sm ghost" data-used="${esc(p.id)}">${p.used ? "Mark active" : "Mark used"}</button><button class="btn sm ghost" data-del="${esc(p.id)}">Delete</button></div>
    ${open.has(p.id) ? `<div class="col" style="gap:8px">${usesBonus ? convChart(p) : ""}
      ${!venueHasFeed(p.venue) && p.venue !== "any" ? `<div class="note">${esc(venueName(p.venue))} isn't in the odds feed: prices shown are typical sportsbook prices. Check the app's price before betting.</div>` : ""}
      ${VENUES.find((v) => v.key === p.venue)?.proxy ? `<div class="note">${esc(venueName(p.venue))} prices are read from the matching sportsbook; confirm the price in the app.</div>` : ""}
      ${k === "deposit_match" && opts[0]?.deposit ? depSummary(opts[0].deposit) : ""}
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
      ${tops.length ? `<div class="tiny muted">Best prices now: ${tops.map((v) => `${esc(v.name)} ${am(v.am)}`).join(" · ")}</div>` : ""}`;
  } else {
    out = `<input id="ck-q" placeholder="Search any bet on the board: 'Tatum points', 'Kings ML', 'Over 6.5 Sharks'" value="${esc(checker.q)}">${pickList(search(checker.q, { n: 10 }), "data-check")}`;
  }
  return `<div class="card col" style="gap:10px"><div class="tiny muted">For specials, boosted prices, discounted lines or anything else: pick the bet, type the app's price or line, see if it's worth it.</div>${out}</div>`;
}

export function renderPromos(el) {
  loadAllProps();
  const list = [...S.promos.values()].map(normalize);
  const rank = { today: 0, active: 1, used: 2, expired: 3 };
  list.sort((a, b) => rank[status(a)] - rank[status(b)] || worth(b, best(b)) - worth(a, best(a)));
  const live = list.filter((p) => ["active", "today"].includes(status(p)));
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
      <div class="row" style="flex-wrap:wrap"><button class="btn" id="pr-read">Read it</button><span class="tiny muted">or start from a type:</span>
        ${groups.map((g) => KINDS.filter((k) => k.group === g).map((k) => `<button class="chip sm" data-new="${k.key}" title="${esc(k.blurb)}">${esc(k.name)}</button>`).join("")).join("")}</div>
    </div>
    ${draft ? form(draft) : ""}
    <div class="sec-title">Your promos<span class="line"></span></div>
    ${list.length ? `<div class="grid pr-grid">${list.map(promoCard).join("")}</div>` : `<div class="empty"><h3>No promos saved</h3>Paste one above and I'll value it and find the best bet for it on the live board.</div>`}
    <div class="sec-title">Check any bet<span class="line"></span></div>
    ${checkerCard()}`;
  bind(el);
}

function bind(el) {
  const rerender = () => renderPromos(el);
  $("#pr-read", el).onclick = () => { const t = $("#pr-text", el).value.trim(); if (!t) return; draft = autoTarget(parsePromo(t)); targetQ = ""; rerender(); };
  $$("[data-new]", el).forEach((b) => b.onclick = () => { draft = blank(b.dataset.new, PICKEM.has(draft?.venue) ? draft.venue : "any"); targetQ = ""; if (["pickem_boost", "free_entry", "discounted_line"].includes(b.dataset.new)) draft.venue = "prizepicks"; rerender(); });
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
      p.title = draft.titleSet ? draft.title : `${venueName(p.venue)} · ${kindOf(p.kind).name}`;
      delete p.type;
      try { await savePromo(p); results.delete(p.id); open.add(p.id); draft = null; toast("Promo saved"); } catch (e) { toast("Could not save: " + e.message); }
    };
  }
  $$("[data-open]", el).forEach((b) => b.onclick = () => { const id = b.dataset.open; open.has(id) ? open.delete(id) : open.add(id); rerender(); });
  $$("[data-take]", el).forEach((b) => b.onclick = () => {
    const [id, i] = b.dataset.take.split("|");
    const p = normalize(S.promos.get(id)), o = best(p)[+i], k = kindOf(p.kind).key;
    const one = o.legs.length === 1 ? o.legs[0].leg : null;
    const pick = ["pickem_boost", "free_entry", "discounted_line"].includes(k);
    const mult = pick ? (p.mult || payoutFor(p.venue, o.legs.length)) : null;
    const stake = ["bonus_bet", "free_entry"].includes(k) ? p.amount : k === "bet_and_get" ? p.qualStake : p.stake;
    const odds = pick ? (mult ? amOf(mult) : "") : k === "odds_boost" ? p.boostedOdds : amOf(o.D);
    logSheet({ label: o.legs.map((c) => c.leg.label).join(" + "), am: odds, stake: stake || "", fair: o.P,
      venue: p.venue !== "any" ? p.venue : undefined, promoId: p.id, gameId: one?.gameId, league: one?.league, matchup: one?.matchup,
      commence: one?.commence, legs: o.legs.map((c) => ({ label: c.leg.label, gameId: c.leg.gameId, fair: c.P })), source: "promo" });
  });
  $$("[data-edit]", el).forEach((b) => b.onclick = () => { draft = normalize(S.promos.get(b.dataset.edit)); draft.titleSet = false; rerender(); scrollTo(0, 0); });
  $$("[data-used]", el).forEach((b) => b.onclick = () => { const p = S.promos.get(b.dataset.used); savePromo({ ...p, used: !p.used }); });
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
