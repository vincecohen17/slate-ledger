// Market behavior (engine market.py): how each game's lines moved, which book moved first, which of your venues haven't
// followed, and DraftKings' betting splits (share of bets vs share of money). Game page "Market" tab, the Edges page
// "Market moves" section, chips on edge cards, and the signals' report card on Reports.
import { S } from "../store.js";
import { esc, pct, prob, fmtTime, fmtDay, signPt, leagueLogo, oddsP, odds, $$ } from "../util.js";
import { labBtn } from "./common.js";

const MK = { h2h: "Moneyline", spreads: "Spread", totals: "Total" };
const BOOK = { pinnacle: "Pinnacle", circasports: "Circa", lowvig: "LowVig", betonlineag: "BetOnline", draftkings: "DraftKings",
  fanduel: "FanDuel", betmgm: "BetMGM", williamhill_us: "Caesars", espnbet: "theScore", fanatics: "Fanatics", betrivers: "BetRivers",
  hardrockbet: "Hard Rock", bovada: "Bovada", ballybet: "Bally", novig: "Novig", prophetx: "ProphetX", kalshi: "Kalshi",
  polymarket_us: "Polymarket US", fliff: "Fliff", courtside: "Courtside" };
const SHARP = new Set(["pinnacle", "circasports", "lowvig", "betonlineag"]);
const bk = (b) => BOOK[b] || b;
const tmin = (m) => new Date(m * 60000).toISOString();

function sideName(g, mk, side) {
  if (mk === "totals") return side === "A" ? "Over" : "Under";
  return side === "A" ? g.home : g.away;
}

function lineTxt(mk, pt, side) {
  if (mk === "h2h") return "";
  if (mk === "totals") return ` ${pt}`;
  const p = side === "A" ? pt : pt == null ? null : -pt;
  return ` ${signPt(p)}`;
}

// a small step chart of side A's chance over time (sharp consensus), number changes marked
function pathChart(path, mk) {
  if (!path || path.length < 2) return "";
  const W = 320, H = 70, P = 6;
  const t0 = path[0][0], t1 = Math.max(path[path.length - 1][0], t0 + 1);
  const ys = path.map((x) => x[2]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi - lo < 0.04) { const m = (hi + lo) / 2; lo = m - 0.02; hi = m + 0.02; }
  const X = (t) => P + (t - t0) * (W - 2 * P) / (t1 - t0), Y = (v) => P + (hi - v) * (H - 2 * P) / (hi - lo);
  let d = "";
  path.forEach((x, i) => { d += i ? `H${X(x[0]).toFixed(1)}V${Y(x[2]).toFixed(1)}` : `M${X(x[0]).toFixed(1)},${Y(x[2]).toFixed(1)}`; });
  const ptChanges = path.filter((x, i) => i && x[1] !== path[i - 1][1]);
  return `<svg class="mkt-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="sharp line path">
    <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>
    ${ptChanges.map((x) => `<circle cx="${X(x[0]).toFixed(1)}" cy="${Y(x[2]).toFixed(1)}" r="3" fill="var(--venue)"><title>number moved to ${esc(String(x[1]))} at ${fmtTime(tmin(x[0]))}</title></circle>`).join("")}
  </svg>`;
}

function splitBars(g, mk, sp) {
  if (!sp) return "";
  const row = (side) => {
    const s = sp[side];
    if (!s) return "";
    return `<div class="mkt-split"><span class="ellip">${esc(sideName(g, mk, side))}${s.pt != null ? " " + esc(mk === "totals" ? String(s.pt) : signPt(s.pt)) : ""}</span>
      <span class="mkt-bar" title="share of bets"><i style="width:${s.b}%"></i></span><b class="num">${s.b}%</b>
      <span class="mkt-bar money" title="share of money"><i style="width:${s.h}%"></i></span><b class="num">${s.h}%</b></div>`;
  };
  const thin = ["A", "B"].some((k) => sp[k] && [sp[k].h, sp[k].b].some((v) => v === 0 || v === 100));
  return `<div class="mkt-splits ${thin ? "thin" : ""}"><div class="mkt-split mkt-hd"><span class="tiny muted">DraftKings splits</span><span class="tiny muted">bets</span><span></span><span class="tiny muted">money</span><span></span></div>${row("B")}${row("A")}
    ${thin ? `<div class="tiny muted">Only a few bets in so far (100 / 0): no signal from these yet.</div>` : ""}</div>`;
}

export function chips(sig) {
  if (!sig) return "";
  const t = [];
  if (sig.mv != null && Math.abs(sig.mv) >= 0.015)
    t.push(`<span class="pill ${sig.mv > 0 ? "pos" : "neg"}" title="the sharp books' line since we first saw it, on one chance scale (number moves converted at the sport's points-to-chance rate)">${sig.mv > 0 ? "▲" : "▼"} ${(Math.abs(sig.mv) * 100).toFixed(1)} pts ${sig.mv > 0 ? "toward" : "away"}</span>`);
  if (sig.sl) t.push(`<span class="pill" title="a sharp book (Pinnacle, Circa, LowVig, BetOnline) moved first and the others followed">sharp books first</span>`);
  if (sig.rlm) t.push(`<span class="pill" title="the line moved toward this side though it holds only ${sig.b}% of DraftKings bets">reverse move · ${sig.b}% of bets</span>`);
  if (sig.div != null && sig.div >= 10) t.push(`<span class="pill" title="share of money well above share of bets: bigger bettors on this side (DraftKings)">money ${sig.h}% · bets ${sig.b}%</span>`);
  else if (sig.pub) t.push(`<span class="pill" title="DraftKings: a heavy majority of tickets">public · ${sig.b}% of bets</span>`);
  return t.join("");
}

export function edgeChips(e) {
  return e.mkt ? `<span class="tiny row" style="gap:4px;flex-wrap:wrap">${chips(e.mkt)}</span>` : "";
}

export function marketTab(g) {
  const m = g.mkt;
  if (!m) return `<div class="card muted">No movement history for this game yet: it builds from every price pull, and DraftKings' betting splits appear about a day before the start.</div>`;
  const out = [];
  for (const mk of ["spreads", "totals", "h2h"]) {
    const mv = m.mv?.[mk], sp = m.splits?.[mk];
    if (!mv && !sp) continue;
    const sA = m.sides?.[`${mk}|A`] || {}, sB = m.sides?.[`${mk}|B`] || {};
    const toward = mv && Math.abs(mv.mv) >= 0.015 ? (mv.mv > 0 ? "A" : "B") : null;
    const first = (mv?.first || []).map(([t, b]) => `<span class="${SHARP.has(b) ? "pos" : ""}">${esc(bk(b))}</span> <span class="faint">${fmtTime(tmin(t))}</span>`).join(" → ");
    out.push(`<div class="card">
      <div class="row" style="justify-content:space-between;align-items:baseline;gap:8px;flex-wrap:wrap"><b>${MK[mk]}</b>
        ${mv ? `<span class="small">open <b class="num">${esc(sideName(g, mk, "A"))}${lineTxt(mk, mv.open.pt, "A")}</b> ${oddsP(mv.open.p)} → now <b class="num">${lineTxt(mk, mv.now.pt, "A").trim() || ""}</b> ${oddsP(mv.now.p)}</span>` : ""}</div>
      ${mv ? pathChart(m.path?.[mk], mk) : ""}
      ${toward ? `<div class="small">Moved <b>${(Math.abs(mv.mv) * 100).toFixed(1)} pts toward ${esc(sideName(g, mk, toward))}</b>${mv.sharpLed ? " · <span class=\"pos\">a sharp book moved first</span>" : ""}${mv.sharpLed && mv.softLagMin > 0 ? ` · the big US books followed ${mv.softLagMin} min later` : ""}</div>` : mv ? `<div class="small muted">No real move since we first saw it (under 1.5 pts).</div>` : ""}
      ${first ? `<div class="tiny muted">First to move: ${first}</div>` : ""}
      ${mv?.lagging?.length ? `<div class="tiny"><span class="pill pos">still at the old number</span> ${mv.lagging.map(bk).map(esc).join(", ")}: the line moved, these venues haven't followed (check the Lines tab for the price)</div>` : ""}
      ${splitBars(g, mk, sp)}
      ${[["B", sB], ["A", sA]].map(([k, sg]) => { const c = chips(sg); return c ? `<div class="row" style="gap:4px;flex-wrap:wrap;align-items:center"><span class="tiny muted">${esc(sideName(g, mk, k))}</span>${c}</div>` : ""; }).join("")}
    </div>`);
  }
  const mc = S.reports?.marketcard;
  return `<div class="note small">How the market has behaved: the sharp books' line since we first saw it (Pinnacle, Circa, LowVig, BetOnline), who moved first by the books' own update times, and DraftKings' public betting splits. By the close a move is priced in; the value is a venue that hasn't followed yet. ${mc ? `Graded so far: ${mc.n} game sides over ${mc.games} games (Reports > Market signals).` : ""}</div>
    <div class="grid">${out.join("") || `<div class="card muted">Nothing yet.</div>`}</div>`;
}

// Edges page: the games where the market is saying something
export function marketMoves(limit = 14) {
  const G = S.mkt || {};
  const items = [];
  for (const [gid, m] of Object.entries(G)) {
    if (new Date(m.commence) < new Date()) continue;
    for (const [k, s] of Object.entries(m.sides || {})) {
      const [mk, side] = k.split("|");
      const score = (Math.max(0, s.mv || 0) * 100) + (s.rlm ? 3 : 0) + (s.sl ? 2 : 0) + (s.div >= 10 ? s.div / 10 : 0);
      if (score < 2 || !(s.mv > 0 || s.rlm || s.div >= 10)) continue;
      items.push({ gid, m, mk, side, s, score });
    }
  }
  items.sort((a, b) => b.score - a.score);
  if (!items.length) return "";
  return `<div class="card"><div class="row" style="justify-content:space-between"><b>Market moves</b><span class="tiny muted">sharp line moves, reverse moves and where the money is (DraftKings splits)</span></div>
    <div class="mkt-list">${items.slice(0, limit).map(({ gid, m, mk, side, s }) => {
      const g = { home: m.home, away: m.away };
      const mv = m.mv?.[mk];
      return `<a class="mkt-item" href="#/game/${gid}/market">
        <span class="tiny muted">${esc(m.league || "")} · ${fmtDay(m.commence)} ${fmtTime(m.commence)}</span>
        <span><b>${esc(sideName(g, mk, side))}</b> <span class="muted">${MK[mk]}${!mv ? "" : mk === "h2h" ? ` ${prob(side === "A" ? mv.open.p : 1 - mv.open.p)} → ${prob(side === "A" ? mv.now.p : 1 - mv.now.p)}` : ` ${esc(lineTxt(mk, mv.open.pt, side).trim())} → ${esc(lineTxt(mk, mv.now.pt, side).trim())}`}</span></span>
        <span class="row" style="gap:4px;flex-wrap:wrap">${chips(s)}${mv?.lagging?.length && s.mv > 0 ? `<span class="pill pos">not yet at ${esc(mv.lagging.slice(0, 3).map(bk).join(", "))}</span>` : ""}</span>
      </a>`;
    }).join("")}</div>
    <div class="tiny muted">Moves are already in our fair, so a venue still at the old number shows up as an edge. Whether these signals carry anything past that is graded on Reports > Market signals.</div></div>`;
}

// Reports: how sides with each signal did against their (fresh) close
export function marketReport(mc) {
  if (!mc) return "";
  const row = (r) => `<tr><td>${esc(r.k)}</td><td class="num">${r.n}</td><td class="num">${r.games ?? "–"}</td><td class="num">${prob(r.win, 1)}</td><td class="num">${prob(r.exp, 1)}</td>
    <td class="num ${r.gap > 0.01 ? "pos" : r.gap < -0.01 ? "neg" : ""}">${r.gap > 0 ? "+" : ""}${(r.gap * 100).toFixed(1)}</td><td class="num">${r.zG ?? "–"}</td>
    <td class="num ${r.roi > 0 ? "pos" : "neg"}">${pct(r.roi)}</td></tr>`;
  return `<div class="card"><b>Market signals report card</b>
    <div class="tiny muted">Every finished game's main-market sides with the market signals they had before the start, against their closing chance (the sharp books' last pre-game number, only when it came within 30 minutes of the start: ${mc.n} sides over ${mc.games} games; ${mc.staleClose ?? 0} left out for an older close). Gap = won minus the close's chance, z clustered by game. A real signal shows a gap that holds as games pile up; by the close most moves are priced in.</div>
    ${Object.entries(mc.dims || {}).filter(([, v]) => v.length).map(([name, v]) => `<div class="small" style="margin-top:8px"><b>${esc(name)}</b></div>
      <div style="overflow-x:auto"><table class="l10t" style="width:100%"><thead><tr><th>Bucket</th><th>Sides</th><th>Games</th><th>Won</th><th>Close said</th><th>Gap pts</th><th>z</th><th>ROI at close</th></tr></thead><tbody>${v.map(row).join("")}</tbody></table></div>`).join("")}
    ${Object.values(mc.dims || {}).some((v) => !v.length) ? `<div class="tiny muted">Splits buckets fill in as games with DraftKings splits finish.</div>` : ""}
  </div>`;
}

// ---------- why each edge (or pick) is on the board, in plain words
export const WHY = {
  lag: { icon: "⚡", label: "Sharp move, venue lagging", tone: "pos", tip: "The sharp books moved toward this side and this venue hasn't followed: the price you'd get is the old one. The kind of edge the market itself creates, and it lasts minutes." },
  move: { icon: "▲", label: "Line moved toward it", tone: "pos", tip: "The sharp line moved toward this side since open and this venue's price still beats where it settled." },
  static: { icon: "•", label: "Static price gap", tone: "", tip: "No market move behind it: this venue is simply off the sharp number. Static gaps have roughly held their fair on our record, no better." },
  against: { icon: "▼", label: "Line moved away", tone: "neg", tip: "The sharp line moved away from this side. The gap is real at today's price, but it's the side the market is leaving." },
  logic: { icon: "◆", label: "Sports logic", tone: "", tip: "A case from the matchup, roles, news and numbers, priced against the sharp market." },
};

export function reasons(e) {
  const r = [], now = Date.now();
  if (e.ctx?.length) r.push({ t: `Verified news in the model: ${e.ctx[0]}${e.ctx.length > 1 ? ` (+${e.ctx.length - 1} more)` : ""}` });
  if (e.logic === "agrees" && e.modelP != null) r.push({ tone: "pos", t: `Our model agrees: it gives this ${prob(e.modelP, 1)}, the sharp fair ${prob(e.fair, 1)}.` });
  else if (e.logic === "disagrees" && e.modelP != null) r.push({ tone: "neg", t: `Our model disagrees: it gives this only ${prob(e.modelP, 1)}.` });
  const c = (e.lean || [])[0];
  if (c) r.push({ tone: "pos", t: `On the playbook: ${c.k} edges have returned ${pct(c.roi)} over ${c.n} graded.` });
  const m = e.mkt || {};
  if (m.rlm) r.push({ tone: "pos", t: `Reverse line move: the line went this way with only ${m.b}% of DraftKings bets on it.` });
  if (m.div != null && m.div >= 10) r.push({ t: `DraftKings: ${m.h}% of the money but ${m.b}% of the bets (bigger bettors on this side).` });
  else if (m.pub) r.push({ t: `Public side: ${m.b}% of DraftKings bets.` });
  if (e.commence && !e.live) {
    const mins = (new Date(e.commence) - now) / 60000;
    if (mins > 0 && mins <= 60) r.push({ tone: "pos", t: `Starts in ${Math.round(mins)} min: flagged prices this close to the start have done best on our record.` });
    else if (mins >= 180 && mins <= 720) r.push({ tone: "neg", t: `Starts in ${Math.round(mins / 60)} h: prices flagged this early have done worst on our record; it may still improve or vanish.` });
  }
  if ((e.avoid || []).length) r.push({ tone: "neg", t: `The record says skip: ${e.avoid[0].k} has lost to the close.` });
  if (e.muted) r.push({ tone: "neg", t: `This kind of edge has lost to its fair (${e.muted}).` });
  return r;
}

export function whyBlock(e, { compact = false } = {}) {
  const w = e.why, k = w ? WHY[w.k] : null, rs = reasons(e);
  if (!k && !rs.length) return "";
  if (compact) return `<span class="why-line">${k ? `<b class="${k.tone}" title="${esc(k.tip)}">${k.icon} ${esc(k.label)}</b> ` : ""}<span class="muted">${esc(w?.t || rs[0]?.t || "")}</span></span>`;
  return `<div class="why ${k ? `why-${w.k}` : ""}"><div class="why-h">${k ? `<b class="${k.tone}" title="${esc(k.tip)}">${k.icon} ${esc(k.label)}</b>` : `<b>Why it's here</b>`}</div>
    ${w ? `<div class="small">${esc(w.t)}</div>` : ""}
    ${rs.length ? `<ul class="why-l">${rs.map((x) => `<li class="${x.tone || ""}">${esc(x.t)}</li>`).join("")}</ul>` : ""}</div>`;
}

// the edges the market itself is pointing at: a sharp move the venue hasn't followed first, then moves toward the side
export function marketDriven(edges) {
  const now = Date.now();
  return (edges || []).filter((e) => !e.live && !e.muted && !(e.avoid || []).length && ["lag", "move"].includes(e.why?.k) && new Date(e.commence) > now
    && !(e.edge > 0.15) && !e.est)
    .sort((a, b) => (b.why.s - a.why.s) || (b.edge - a.edge));
}

// the board's edge for a bet spec (a pick, a Lab leg), at one venue or any
export function edgeFor(sp, venue = null) {
  const same = (e) => e.gameId === sp.gameId && (e.market === sp.market) && e.side === sp.side && (sp.point == null || e.point == null || Math.abs(e.point - sp.point) < 1e-6)
    && (e.player || null) === (sp.player || null);
  const es = (S.edges || []).filter((e) => same(e) && (!venue || venue === "any" || e.book === venue));
  return es.sort((a, b) => (b.why?.s ?? -2) - (a.why?.s ?? -2) || b.edge - a.edge)[0] || null;
}

// how each kind of reason has done if you'd bet every flagged edge with it: flat 1 unit at the flagged price, graded at the
// final; won vs expected against the fair at the flag, CLV calibrated, t clustered by game (engine edgecard dim "why")
const WHY_ORDER = ["Sharp move, venue lagging", "Line moved toward it", "Static price gap", "Line moved away"];
export function whyRecord(card, { compact = false } = {}) {
  const rows = (card?.why || []).slice().sort((a, b) => WHY_ORDER.indexOf(a.k) - WHY_ORDER.indexOf(b.k));
  if (compact) {
    const r = rows.find((x) => x.k === WHY_ORDER[0]);
    return r ? `<div class="tiny muted">Record so far if you'd bet every ⚡ edge at its flagged price: <b class="${r.roi > 0 ? "pos" : "neg"}">ROI ${pct(r.roi)}</b> over ${r.graded} graded (${r.games ?? "?"} games) · won ${r.gap > 0 ? "+" : ""}${(r.gap * 100).toFixed(1)} pts vs the close${r.roiT != null ? ` · t ${r.roiT}` : ""} · <a href="#/reports">by reason on Reports</a></div>`
      : `<div class="tiny muted">Their record (as if you'd bet every one at the flagged price) starts filling as today's flagged edges finish: Reports, "Market reason".</div>`;
  }
  if (!rows.length) return `<div class="card note">Every flagged edge now records why it was on the board (a sharp move a venue hadn't followed, a move toward it, a static gap, a move away) and is graded as if bet at its flagged price. The table fills in as today's edges finish.</div>`;
  return `<div class="card"><b>If you'd bet them: every flagged edge by its market reason</b>
    <div class="tiny muted">Flat 1 unit at the price when it was flagged, graded at the final. Won vs expected: wins minus what the fair at the flag said. t is clustered by game; under 2 is still noise.</div>
    <div style="overflow-x:auto"><table class="l10t" style="width:100%"><thead><tr><th style="text-align:left">Reason</th><th>Graded</th><th>Games</th><th>Won vs expected</th><th>ROI</th><th>t</th><th>CLV cal.</th><th>Lately ROI</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td style="text-align:left">${esc(r.k)}</td><td class="num">${r.graded}</td><td class="num">${r.games ?? "–"}</td>
      <td class="num ${r.gap > 0.01 ? "pos" : r.gap < -0.01 ? "neg" : ""}">${r.gap > 0 ? "+" : ""}${(r.gap * 100).toFixed(1)} pts</td><td class="num ${r.roi > 0 ? "pos" : "neg"}">${pct(r.roi)}</td>
      <td class="num">${r.roiT ?? "–"}</td><td class="num">${r.clvCal != null ? pct(r.clvCal) : "–"}</td><td class="num">${r.roiR != null ? pct(r.roiR) : "–"}</td></tr>`).join("")}</tbody></table></div></div>`;
}


// ---------- Just moved: the steam scanner (engine steam.py, every 2 minutes from the watcher) found a sharp move with one of
// Vince's venues still at the old price. Minutes matter here, so each item shows how long ago the sharp book moved and how
// long the window has been open, plus how long that venue's windows usually stay open.
export function steamItems(league = "All") {
  const st = S.steam;
  if (!st || !st.at || Date.now() - new Date(st.at) > 6 * 60000) return [];          // a scan older than 6 minutes is stale
  return (st.items || []).filter((i) => (league === "All" || i.league === league) && new Date(i.commence) > new Date())
    .sort((a, b) => b.edge - a.edge);
}
const ago = (t) => { const m = Math.max(0, Math.round((Date.now() - new Date(t)) / 60000)); return m < 1 ? "just now" : `${m} min ago`; };
const ptTxt = (i) => i.market === "h2h" ? `${i.side} ML` : i.market === "totals" ? `${i.side} ${i.point}` : `${i.side} ${signPt(i.point)}`;
export function steamSpec(i) {
  return { label: ptTxt(i), gameId: i.gameId, league: i.league, matchup: i.matchup, commence: i.commence, market: i.market, side: i.side,
    point: i.market === "h2h" ? null : i.point, team: i.market === "h2h" || i.market === "spreads" ? i.side : null, am: i.am, venue: i.venue,
    venueName: i.venueName, fair: i.fair, source: "steam", why: "lag", whyText: i.text };
}
export function steamList(items, { compact = false } = {}) {
  if (!items.length) return "";
  const win = S.steam?.windows || {};
  return `<div class="mkt-list">${items.map((i) => {
    const w = win[i.venue];
    return `<div class="mkt-item steam"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap;min-width:0">
        <a href="#/game/${esc(i.gameId)}/market" style="color:inherit;text-decoration:none;min-width:0"><b>${esc(ptTxt(i))}</b> <span class="muted">at ${esc(i.venueName)}</span> ${odds(i.am)}</a>
        <span class="pill pos">+${(100 * i.edge).toFixed(1)}% vs new fair</span></div>
      <div class="small">⚡ ${esc(i.text)}</div>
      <div class="tiny muted">Sharp move ${ago(i.sharpAt || i.first)} · window open ${ago(i.first).replace(" ago", "")}${w && w.n >= 3 ? ` · ${esc(i.venueName)} usually follows in ~${Math.max(1, Math.round(w.med))} min (${w.n} seen)` : ""}
        · fair now ${oddsP(i.fair)} (was ${prob(i.fairBefore)}) · ${esc(i.matchup)} · ${fmtTime(i.commence)}</div>
      ${compact ? "" : `<div class="row" style="gap:6px;flex-wrap:wrap"><button class="btn sm" data-log='${esc(JSON.stringify(steamSpec(i)))}'>Log</button>${labBtn({ gameId: i.gameId, market: i.market, side: i.side, point: i.market === "h2h" ? null : i.point }, { venue: i.venue })}</div>`}
    </div>`; }).join("")}</div>`;
}
