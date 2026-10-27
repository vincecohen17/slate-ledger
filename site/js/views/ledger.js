import { S, saveBet, deleteBet } from "../store.js";
import { esc, am, pct, money, fmtShortDay, bindTips, $, $$ } from "../util.js";
import { logSheet } from "./common.js";

let filter = "pending";

function profitFor(b, status) {
  if (status === "push" || status === "void") return 0;
  const p = b.promoId ? S.promos.get(b.promoId) : null;
  if (status === "lost") return p?.type === "bonus_bet" ? 0 : -b.stake;
  let win = b.stake * (b.dec - 1);
  if (p && ["profit_boost", "parlay_boost"].includes(p.type)) {
    let extra = win * (p.boost || 0);
    if (p.maxWin) extra = Math.min(extra, p.maxWin);
    win += extra;
  }
  return Math.round(win * 100) / 100;
}

function plChart(settled) {
  if (settled.length < 2) return `<div class="note">The profit chart appears after two settled bets.</div>`;
  const pts = []; let c = 0;
  settled.forEach((b) => { c += b.profit; pts.push({ y: c, b }); });
  const W = 800, H = 220, P = { l: 48, r: 12, t: 12, b: 24 };
  const ys = pts.map((p) => p.y), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = hi - lo || 1;
  const X = (i) => P.l + i * (W - P.l - P.r) / (pts.length - 1), Y = (v) => P.t + (hi - v) * (H - P.t - P.b) / span;
  const ticks = [lo, (lo + hi) / 2, hi];
  const path = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Cumulative profit">
    ${ticks.map((t) => `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${P.l - 6}" y="${Y(t) + 4}" text-anchor="end">${money(t)}</text>`).join("")}
    <line class="zero" x1="${P.l}" x2="${W - P.r}" y1="${Y(0)}" y2="${Y(0)}"/>
    <path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.y)}" r="10" fill="transparent" data-tip="${esc(`${fmtShortDay(p.b.settledAt || p.b.createdAt)} · ${p.b.label}<br>${money(p.b.profit)} · running ${money(p.y)}`)}"/>`).join("")}
    <circle cx="${X(pts.length - 1)}" cy="${Y(c)}" r="4" fill="var(--accent)"/>
  </svg>`;
}

function hbars(rows) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.v)));
  return rows.map((r) => `<div class="hbar" data-tip="${esc(r.k)}: ${money(r.v)} on ${r.n} bets"><span class="ellip">${esc(r.k)}</span>
    <div class="eb" style="height:12px"><div class="l">${r.v < 0 ? `<i style="width:${Math.abs(r.v) / max * 100}%"></i>` : ""}</div><div class="r">${r.v > 0 ? `<i style="width:${r.v / max * 100}%"></i>` : ""}</div></div>
    <span class="num small ${r.v >= 0 ? "pos" : "neg"}" style="text-align:right">${money(r.v)}</span></div>`).join("");
}

export function renderLedger(el) {
  const bets = [...S.bets.values()].sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  const settled = bets.filter((b) => ["won", "lost", "push", "void"].includes(b.status)).sort((a, b) => (a.settledAt || "").localeCompare(b.settledAt || ""));
  const graded = settled.filter((b) => b.status !== "void");
  const staked = graded.reduce((s, b) => s + b.stake, 0), pl = graded.reduce((s, b) => s + (b.profit || 0), 0);
  const w = graded.filter((b) => b.status === "won").length, l = graded.filter((b) => b.status === "lost").length, p = graded.filter((b) => b.status === "push").length;
  const pend = bets.filter((b) => b.status === "pending");
  const clv = bets.filter((b) => b.clv != null);
  const byVenue = {};
  graded.forEach((b) => { const k = b.venueName || b.venue; byVenue[k] = byVenue[k] || { k, v: 0, n: 0 }; byVenue[k].v += b.profit || 0; byVenue[k].n++; });
  const shown = bets.filter((b) => filter === "all" || (filter === "pending" ? b.status === "pending" : b.status !== "pending"));

  el.innerHTML = `<div class="row" style="justify-content:space-between;margin-top:18px"><h2 style="margin:0;font-size:18px">Ledger</h2><button class="btn" id="add-bet">Log a bet</button></div>
    <div class="kpis" style="margin-top:12px">
      <div class="kpi"><div class="k">Profit / loss</div><div class="v ${pl >= 0 ? "pos" : "neg"}">${money(pl)}</div><div class="s">${graded.length} settled</div></div>
      <div class="kpi"><div class="k">ROI</div><div class="v">${staked ? pct(pl / staked) : "–"}</div><div class="s">on ${money(staked)} staked</div></div>
      <div class="kpi"><div class="k">Record</div><div class="v">${w}-${l}${p ? "-" + p : ""}</div><div class="s">win-loss${p ? "-push" : ""}</div></div>
      <div class="kpi"><div class="k">Avg CLV</div><div class="v">${clv.length ? pct(clv.reduce((s, b) => s + b.clv, 0) / clv.length) : "–"}</div><div class="s">beat the close: ${clv.length ? Math.round(clv.filter((b) => b.clv > 0).length / clv.length * 100) + "%" : "–"}</div></div>
      <div class="kpi"><div class="k">Open</div><div class="v">${pend.length}</div><div class="s">${money(pend.reduce((s, b) => s + b.stake, 0))} at risk</div></div>
    </div>
    <div class="sec-title">Running profit<span class="line"></span></div><div class="card">${plChart(settled.filter((b) => b.status !== "void"))}</div>
    ${Object.keys(byVenue).length ? `<div class="sec-title">By venue<span class="line"></span></div><div class="card">${hbars(Object.values(byVenue).sort((a, b) => b.v - a.v))}</div>` : ""}
    <div class="bar">${["pending", "settled", "all"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-flt="${f}">${f[0].toUpperCase() + f.slice(1)}</button>`).join("")}</div>
    ${shown.length ? `<div class="panel scroll"><table><thead><tr><th>Bet</th><th>Venue</th><th>Odds</th><th>Stake</th><th>Fair</th><th>Result</th><th></th></tr></thead><tbody>
      ${shown.map((b) => `<tr><td style="white-space:normal;min-width:200px"><div>${esc(b.label)}</div><div class="tiny muted">${esc(b.matchup || "")} ${b.date ? "· " + esc(b.date) : ""}${b.promoId ? " · promo" : ""}</div></td>
        <td>${esc(b.venueName || b.venue)}</td><td class="num">${am(b.am)}</td><td class="num">${money(b.stake)}</td>
        <td class="num">${b.fairAtLog != null ? Math.round(b.fairAtLog * 100) + "%" : "–"}</td>
        <td>${b.status === "pending" ? `<span class="row" style="gap:4px;justify-content:flex-end">${["won", "lost", "push", "void"].map((s) => `<button class="btn sm ghost" data-set="${b.id}|${s}">${s[0].toUpperCase()}</button>`).join("")}</span>`
          : `<span class="pill ${b.status === "won" ? "pos" : b.status === "lost" ? "neg" : ""}">${b.status}</span> <span class="num ${b.profit >= 0 ? "pos" : "neg"}">${money(b.profit)}</span>`}</td>
        <td><button class="x" data-del="${b.id}" title="delete">×</button></td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty"><h3>No ${filter === "all" ? "" : filter} bets</h3>Log bets from Edges, the Lab, Picks or Promos, or add one here.</div>`}
    <p class="note">Settle by hand for now (W / L / P / Void). Automatic grading and closing line value arrive with the grading phase.</p>`;

  $("#add-bet", el).onclick = () => logSheet({});
  $$("[data-flt]", el).forEach((b) => b.onclick = () => { filter = b.dataset.flt; renderLedger(el); });
  $$("[data-set]", el).forEach((b) => b.onclick = () => {
    const [id, st] = b.dataset.set.split("|"); const bet = S.bets.get(id);
    saveBet({ ...bet, status: st, profit: profitFor(bet, st), settledAt: new Date().toISOString() });
  });
  $$("[data-del]", el).forEach((b) => b.onclick = () => { if (b.dataset.c) deleteBet(b.dataset.del); else { b.dataset.c = 1; b.textContent = "?"; b.title = "tap again to delete"; } });
  bindTips(el);
}
