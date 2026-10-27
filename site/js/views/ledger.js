// Ledger: Vince's bets and our picks, graded automatically after the games (result + closing line value), with the
// breakdowns and plain-words takeaways that say what to keep betting and what to stop.
import { S, saveBet, deleteBet } from "../store.js";
import { esc, am, pct, prob, money, fmtShortDay, bindTips, bookTag, todayPT, $, $$ } from "../util.js";
import { logSheet } from "./common.js";
import { settleProfit } from "../promo.js";
import { VENUES } from "../lab.js";
import { projectedCard } from "../projected.js";

let tab = "mine", filter = "pending", groupBy = "type";
const inflight = new Set();

const decA = (a) => a > 0 ? 1 + a / 100 : 1 + 100 / -a;
const profitFor = (b, status) => settleProfit(b, b.promoId ? S.promos.get(b.promoId) : null, status);

// the engine grades pending bets (bets/{id}.auto); apply it here, where the promo math lives
export function autoSettle() {
  for (const b of S.bets.values()) {
    if (b.status !== "pending" || !b.auto?.result || inflight.has(b.id)) continue;
    inflight.add(b.id);
    saveBet({ ...b, status: b.auto.result, profit: profitFor(b, b.auto.result), clv: b.auto.clv ?? null, closeFair: b.auto.closeFair ?? null,
              settledAt: b.auto.at || new Date().toISOString(), autoSettled: true }).finally(() => inflight.delete(b.id));
  }
}

// ---------- how each bet is classified
const DFS = new Set(VENUES.filter((v) => v.dfs).map((v) => v.key));
function betType(b) {
  if ((b.legs || []).length > 1) return "Parlay";
  if (DFS.has(b.venue)) return "Pick'em";
  const m = b.market || "";
  if (b.player || /^(player|batter|pitcher)_/.test(m)) return "Player prop";
  if (/^h2h/.test(m)) return "Moneyline";
  if (/spreads/.test(m)) return "Spread";
  if (/totals/.test(m)) return "Total";
  return b.market ? "Other" : "Unclassified";
}
const evAtLog = (b) => b.fairAtLog != null && b.dec ? b.fairAtLog * b.dec - 1 : null;
function oddsBand(b) {
  const d = b.dec || 0;
  return d < 1.67 ? "-150 or shorter" : d < 2.1 ? "-149 to +110" : d < 3 ? "+111 to +200" : d < 6 ? "+201 to +500" : "+500 and longer";
}
function edgeBand(b) {
  const e = evAtLog(b);
  return e == null ? "No fair logged" : e < 0 ? "Below fair (no edge)" : e < 0.03 ? "0–3% edge" : e < 0.06 ? "3–6% edge" : "6%+ edge";
}
const SOURCES = { edge: "Edges screen", pick: "Our picks", promo: "Promos", lab: "Bet Lab", manual: "Typed in" };
const GROUPS = {
  type: ["Bet type", betType], sport: ["Sport", (b) => b.league || "Unknown"], venue: ["Venue", (b) => b.venueName || b.venue],
  odds: ["Odds range", oddsBand], edge: ["Edge when logged", edgeBand], source: ["Where it came from", (b) => SOURCES[b.source] || b.source || "Typed in"],
  promo: ["Promo", (b) => b.promoId ? "With a promo" : "No promo"],
};

function summarize(list) {
  const g = list.filter((b) => ["won", "lost", "push"].includes(b.status));
  const staked = g.reduce((s, b) => s + (b.stake || 0), 0), pl = g.reduce((s, b) => s + (b.profit || 0), 0);
  const clv = g.filter((b) => b.clv != null);
  const exp = g.filter((b) => evAtLog(b) != null);
  return { n: g.length, w: g.filter((b) => b.status === "won").length, l: g.filter((b) => b.status === "lost").length, p: g.filter((b) => b.status === "push").length,
    staked, pl, roi: staked ? pl / staked : null, clv: clv.length ? clv.reduce((s, b) => s + b.clv, 0) / clv.length : null, nClv: clv.length,
    beat: clv.length ? clv.filter((b) => b.clv > 0).length / clv.length : null,
    expPl: exp.reduce((s, b) => s + b.stake * evAtLog(b), 0), nExp: exp.length };
}

// ---------- charts
function plChart(settled, expectedToo = true, fmt = money) {
  if (settled.length < 2) return `<div class="note">The chart appears after two settled bets.</div>`;
  const pts = []; let c = 0, e = 0;
  settled.forEach((b) => { c += b.profit || 0; e += evAtLog(b) != null ? b.stake * evAtLog(b) : 0; pts.push({ y: c, e, b }); });
  const W = 800, H = 230, P = { l: 52, r: 12, t: 14, b: 24 };
  const ys = pts.flatMap((p) => [p.y, expectedToo ? p.e : 0]), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), span = hi - lo || 1;
  const X = (i) => P.l + i * (W - P.l - P.r) / (pts.length - 1), Y = (v) => P.t + (hi - v) * (H - P.t - P.b) / span;
  const path = (k) => pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`).join("");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Running profit and expected profit">
    ${[lo, (lo + hi) / 2, hi].map((t) => `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${P.l - 6}" y="${Y(t) + 4}" text-anchor="end">${fmt(t)}</text>`).join("")}
    <line class="zero" x1="${P.l}" x2="${W - P.r}" y1="${Y(0)}" y2="${Y(0)}"/>
    ${expectedToo ? `<path d="${path("e")}" fill="none" stroke="var(--muted)" stroke-width="2" stroke-dasharray="5 4"/>` : ""}
    <path d="${path("y")}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map((p, i) => `<circle cx="${X(i)}" cy="${Y(p.y)}" r="9" fill="transparent" data-tip="${esc(`${fmtShortDay(p.b.settledAt || p.b.createdAt)} · ${p.b.label}<br>${fmt(p.b.profit)} · running ${fmt(p.y)}${expectedToo ? ` · expected ${fmt(p.e)}` : ""}`)}"/>`).join("")}
  </svg>
  <div class="row tiny muted" style="gap:14px"><span><i class="lg-line" style="background:var(--accent)"></i>${expectedToo ? "actual profit" : "units won"}</span>${expectedToo ? `<span><i class="lg-line dash"></i>expected from the prices you got (fair when logged)</span>` : ""}</div>`;
}

function groupTable(list, key) {
  const [, fn] = GROUPS[key];
  const by = new Map();
  list.forEach((b) => { const k = fn(b); if (!by.has(k)) by.set(k, []); by.get(k).push(b); });
  const rows = [...by.entries()].map(([k, bs]) => ({ k, ...summarize(bs) })).filter((r) => r.n).sort((a, b) => b.n - a.n);
  if (!rows.length) return `<div class="note">Nothing settled yet.</div>`;
  const maxRoi = Math.max(0.05, ...rows.map((r) => Math.abs(r.roi || 0)));
  return `<div style="overflow-x:auto"><table class="l10t lg-t"><thead><tr><th></th><th>Bets</th><th>Record</th><th>P/L</th><th style="min-width:120px">ROI</th><th>Avg CLV</th><th>Beat close</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${esc(r.k)}</td><td class="num">${r.n}</td><td class="num">${r.w}-${r.l}${r.p ? "-" + r.p : ""}</td>
      <td class="num ${r.pl >= 0 ? "pos" : "neg"}">${money(r.pl)}</td>
      <td><div class="lg-roi"><span class="lg-mid"></span><i class="${r.roi >= 0 ? "pos" : "neg"}" style="${r.roi >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(r.roi || 0) / maxRoi * 50).toFixed(0)}%"></i></div><span class="num tiny ${r.roi >= 0 ? "pos" : "neg"}">${r.roi != null ? pct(r.roi) : "–"}</span></td>
      <td class="num ${r.clv > 0 ? "pos" : r.clv < 0 ? "neg" : ""}">${r.clv != null ? pct(r.clv) : "–"}</td><td class="num">${r.beat != null ? Math.round(r.beat * 100) + "%" : "–"}</td></tr>`).join("")}
    </tbody></table></div>`;
}

// plain-words takeaways, only with enough bets behind them
function insights(list) {
  const out = [], all = summarize(list);
  const MIN = 8;
  if (all.nClv >= MIN) {
    if (all.clv > 0.01) out.push(["pos", `You're beating the closing line by ${pct(all.clv)} on average (${Math.round(all.beat * 100)}% of bets). That's the strongest sign your bets are good, whatever the results so far.`]);
    else if (all.clv < -0.01) out.push(["neg", `On average the price moved against you after you bet (${pct(all.clv)} vs the close). Bet earlier in the day, or only take prices the Edges screen shows above fair.`]);
    else out.push(["", `Your prices are about even with the close (${pct(all.clv)}). Fine, but the profit has to come from promos or sharper timing.`]);
  }
  if (all.nExp >= MIN) {
    const g = list.filter((b) => ["won", "lost"].includes(b.status) && b.fairAtLog != null);
    const sd = Math.sqrt(g.reduce((s, b) => s + (b.stake * b.dec) ** 2 * b.fairAtLog * (1 - b.fairAtLog), 0)) || 1;
    const diff = all.pl - all.expPl, z = diff / sd;
    out.push([Math.abs(z) < 1.5 ? "" : diff > 0 ? "pos" : "neg", `Results are ${money(Math.abs(diff))} ${diff >= 0 ? "above" : "below"} what your prices predicted (${money(all.expPl)} expected). ${Math.abs(z) < 1 ? "That's ordinary variance at this sample size." : Math.abs(z) < 2 ? "A noticeable swing, still within normal variance." : diff > 0 ? "Running hot: expect some of that to give back." : "Running cold: if your CLV is positive, keep going."}`]);
  }
  for (const key of ["type", "sport", "odds", "source", "venue"]) {
    const [lab, fn] = GROUPS[key];
    const by = new Map();
    list.forEach((b) => { const k = fn(b); if (!by.has(k)) by.set(k, []); by.get(k).push(b); });
    for (const [k, bs] of by) {
      const s = summarize(bs);
      if (s.nClv < MIN) continue;
      if (s.clv >= 0.02 && s.beat >= 0.55) out.push(["pos", `Keep betting ${k} (${lab.toLowerCase()}): ${pct(s.clv)} CLV over ${s.nClv} bets${s.roi != null ? `, ${pct(s.roi)} ROI so far` : ""}.`]);
      if (s.clv <= -0.02) out.push(["neg", `Rethink ${k} (${lab.toLowerCase()}): ${pct(s.clv)} CLV over ${s.nClv} bets means you're taking worse prices than the close.`]);
    }
  }
  const noEdge = list.filter((b) => ["won", "lost"].includes(b.status) && evAtLog(b) != null && evAtLog(b) < 0);
  if (noEdge.length >= 5) {
    const s = summarize(noEdge);
    out.push(["neg", `${noEdge.length} bets were below our fair price when you logged them (${money(s.pl)} so far). Stick to bets the site shows at or above fair.`]);
  }
  const long = list.filter((b) => ["won", "lost"].includes(b.status) && (b.dec || 0) >= 6);
  if (long.length >= MIN) {
    const s = summarize(long);
    if ((s.clv ?? 0) < 0) out.push(["neg", `Longshots (+500 and up): ${s.n} bets, ${money(s.pl)}, ${pct(s.clv)} CLV. These swing hard; size them small.`]);
  }
  return out;
}

function betRow(b) {
  const ev = evAtLog(b);
  return `<tr><td style="white-space:normal;min-width:210px"><div>${esc(b.label)}</div><div class="tiny muted">${esc(b.league || "")}${b.matchup ? " · " + esc(b.matchup) : ""}${b.date ? " · " + esc(b.date) : ""}${b.promoId ? " · promo" : ""}${b.source ? " · " + esc(SOURCES[b.source] || b.source) : ""}</div></td>
    <td>${bookTag(b.venue, b.venueName || b.venue, 15)}</td><td class="num">${am(b.am)}</td><td class="num">${money(b.stake)}</td>
    <td class="num ${ev > 0 ? "pos" : ev < 0 ? "neg" : ""}" title="our fair when logged">${b.fairAtLog != null ? `${prob(b.fairAtLog)}<br><span class="tiny">${pct(ev)}</span>` : "–"}</td>
    <td class="num ${b.clv > 0 ? "pos" : b.clv < 0 ? "neg" : ""}" title="value of your price at the closing fair">${b.clv != null ? pct(b.clv) : "–"}</td>
    <td>${b.status === "pending" ? `<span class="row" style="gap:4px;justify-content:flex-end">${["won", "lost", "push", "void"].map((s) => `<button class="btn sm ghost" data-set="${b.id}|${s}">${s[0].toUpperCase()}</button>`).join("")}</span>`
      : `<span class="pill ${b.status === "won" ? "pos" : b.status === "lost" ? "neg" : ""}">${b.status}${b.autoSettled ? " · auto" : ""}</span> <span class="num ${b.profit >= 0 ? "pos" : "neg"}">${money(b.profit)}</span>`}</td>
    <td><button class="x" data-del="${b.id}" title="delete">×</button></td></tr>`;
}

function mine(el) {
  const bets = [...S.bets.values()].sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  const settled = bets.filter((b) => ["won", "lost", "push"].includes(b.status)).sort((a, b) => (a.settledAt || "").localeCompare(b.settledAt || ""));
  const s = summarize(bets), pend = bets.filter((b) => b.status === "pending");
  const shown = bets.filter((b) => filter === "all" || (filter === "pending" ? b.status === "pending" : b.status !== "pending"));
  const ins = insights(bets);
  return `<div class="kpis" style="margin-top:12px">
      <div class="kpi"><div class="k">Profit / loss</div><div class="v ${s.pl >= 0 ? "pos" : "neg"}">${money(s.pl)}</div><div class="s">${s.n} settled · ${s.w}-${s.l}${s.p ? "-" + s.p : ""}</div></div>
      <div class="kpi"><div class="k">ROI</div><div class="v">${s.roi != null ? pct(s.roi) : "–"}</div><div class="s">on ${money(s.staked)} staked</div></div>
      <div class="kpi"><div class="k">Expected profit</div><div class="v ${s.expPl >= 0 ? "pos" : "neg"}">${s.nExp ? money(s.expPl) : "–"}</div><div class="s">from the prices you got</div></div>
      <div class="kpi"><div class="k">Avg CLV</div><div class="v ${s.clv > 0 ? "pos" : s.clv < 0 ? "neg" : ""}">${s.clv != null ? pct(s.clv) : "–"}</div><div class="s">beat the close: ${s.beat != null ? Math.round(s.beat * 100) + "%" : "–"}</div></div>
      <div class="kpi"><div class="k">Open</div><div class="v">${pend.length}</div><div class="s">${money(pend.reduce((x, b) => x + (b.stake || 0), 0))} at risk</div></div>
    </div>
    ${pend.length ? `<div class="sec-title">Open bets<span class="line"></span></div>${projectedCard({ items: pend, title: "Projected profit on open bets",
        settledToday: bets.filter((b) => ["won", "lost", "push"].includes(b.status) && b.settledAt && new Date(b.settledAt).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" }) === todayPT()).reduce((x, b) => x + (b.profit || 0), 0) })}` : ""}
    ${ins.length ? `<div class="sec-title">What the numbers say<span class="line"></span></div><div class="card col lg-ins">${ins.map(([t, x]) => `<div class="lg-in ${t}">${t === "pos" ? "▲" : t === "neg" ? "▼" : "●"} ${esc(x)}</div>`).join("")}</div>`
      : `<div class="card note" style="margin-top:12px">Takeaways appear once 8+ bets are settled with a closing price. Bets logged from the Edges, Picks, Promos and Lab screens grade themselves after the game; typed-in bets need a W/L tap.</div>`}
    <div class="sec-title">Running profit<span class="line"></span></div><div class="card">${plChart(settled)}</div>
    <div class="sec-title">Breakdown<span class="line"></span></div>
    <div class="bar">${Object.entries(GROUPS).map(([k, [lab]]) => `<button class="chip sm ${k === groupBy ? "on" : ""}" data-grp="${k}">${lab}</button>`).join("")}</div>
    <div class="card">${groupTable(bets, groupBy)}<p class="note">CLV is what your price was worth at the closing sharp fair: positive means you got a better number than the market settled on. Over a few hundred bets it predicts profit far better than wins and losses do.</p></div>
    <div class="row" style="justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px"><div class="bar" style="margin:0">${["pending", "settled", "all"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-flt="${f}">${f[0].toUpperCase() + f.slice(1)}</button>`).join("")}</div>
      <button class="btn" id="add-bet">Log a bet</button></div>
    ${shown.length ? `<div class="panel scroll" style="margin-top:8px"><table><thead><tr><th>Bet</th><th>Venue</th><th>Odds</th><th>Stake</th><th>Fair</th><th>CLV</th><th>Result</th><th></th></tr></thead><tbody>
      ${shown.map(betRow).join("")}</tbody></table></div>`
      : `<div class="empty"><h3>No ${filter === "all" ? "" : filter} bets</h3>Log bets from Edges, the Lab, Picks or Promos, or add one here.</div>`}`;
}

// ---------- our picks
function ours() {
  const all = [...S.picks.values()];
  const graded = all.filter((p) => ["won", "lost", "push"].includes(p.status)).sort((a, b) => (a.commence || "").localeCompare(b.commence || ""));
  const asBet = (p) => ({ ...p, stake: p.units || 0, profit: p.profit || 0, label: p.selection, settledAt: p.commence });
  const tiers = ["bet", "floor"].map((t) => ({ t, ...summarize(graded.filter((p) => p.tier === t).map(asBet)) }));
  const s = summarize(graded.map(asBet));
  const byLeague = new Map();
  graded.forEach((p) => { const k = p.league || "?"; if (!byLeague.has(k)) byLeague.set(k, []); byLeague.get(k).push(asBet(p)); });
  const tailed = [...S.bets.values()].filter((b) => b.pickId || b.source === "pick");
  const ts = summarize(tailed), own = summarize([...S.bets.values()].filter((b) => !(b.pickId || b.source === "pick")));
  const recent = [...all].filter((p) => p.tier !== "likely").sort((a, b) => (b.commence || "").localeCompare(a.commence || "")).slice(0, 30);
  const u = (x) => `${x >= 0 ? "+" : ""}${(x || 0).toFixed(2)}u`;
  const openPicks = all.filter((p) => ["open", "pending", undefined, null].includes(p.status) && p.tier !== "likely").map((p) => {
    const prices = (p.playTo || []).filter((v) => v.inRange && typeof v.price === "number").map((v) => v.price);
    const a = prices.length ? prices.reduce((x, y) => (decA(y) > decA(x) ? y : x)) : p.goodAt;
    return a == null ? null : { ...p, label: p.selection, dec: decA(a), am: a };
  }).filter(Boolean);
  return `<div class="kpis" style="margin-top:12px">
      <div class="kpi"><div class="k">Units</div><div class="v ${s.pl >= 0 ? "pos" : "neg"}">${u(s.pl)}</div><div class="s">${s.n} graded · ${s.w}-${s.l}${s.p ? "-" + s.p : ""}</div></div>
      <div class="kpi"><div class="k">ROI</div><div class="v">${s.roi != null ? pct(s.roi) : "–"}</div><div class="s">per unit risked</div></div>
      <div class="kpi"><div class="k">Avg CLV</div><div class="v ${s.clv > 0 ? "pos" : s.clv < 0 ? "neg" : ""}">${s.clv != null ? pct(s.clv) : "–"}</div><div class="s">beat the close: ${s.beat != null ? Math.round(s.beat * 100) + "%" : "–"}</div></div>
      <div class="kpi"><div class="k">Withdrawn</div><div class="v">${all.filter((p) => p.status === "void").length}</div><div class="s">before the game</div></div>
    </div>
    ${openPicks.length ? `<div class="sec-title">Open picks<span class="line"></span></div>${projectedCard({ items: openPicks, title: "Projected units on open picks", fmt: u, units: true })}` : ""}
    <div class="sec-title">Units over time<span class="line"></span></div><div class="card">${plChart(graded.map(asBet), false, u)}</div>
    <div class="sec-title">By tier and league<span class="line"></span></div>
    <div class="card"><div style="overflow-x:auto"><table class="l10t lg-t"><thead><tr><th></th><th>Picks</th><th>Record</th><th>Units</th><th>ROI</th><th>Avg CLV</th><th>Beat close</th></tr></thead><tbody>
      ${[...tiers.map((r) => [r.t === "bet" ? "Bet tier" : "Floor (0.25u)", r]), ...[...byLeague.entries()].map(([k, bs]) => [k, summarize(bs)])].filter(([, r]) => r.n).map(([k, r]) => `<tr><td>${esc(k)}</td><td class="num">${r.n}</td><td class="num">${r.w}-${r.l}${r.p ? "-" + r.p : ""}</td>
        <td class="num ${r.pl >= 0 ? "pos" : "neg"}">${u(r.pl)}</td><td class="num">${r.roi != null ? pct(r.roi) : "–"}</td><td class="num ${r.clv > 0 ? "pos" : r.clv < 0 ? "neg" : ""}">${r.clv != null ? pct(r.clv) : "–"}</td><td class="num">${r.beat != null ? Math.round(r.beat * 100) + "%" : "–"}</td></tr>`).join("") || `<tr><td colspan="7" class="muted">No graded picks yet: they grade themselves a few hours after each game.</td></tr>`}
    </tbody></table></div></div>
    ${tailed.length ? `<div class="sec-title">You vs the picks<span class="line"></span></div><div class="pr-ck">
      <div class="kpi"><div class="k">Bets tailing our picks</div><div class="v ${ts.pl >= 0 ? "pos" : "neg"}">${money(ts.pl)}</div><div class="s">${ts.n} settled · ROI ${ts.roi != null ? pct(ts.roi) : "–"} · CLV ${ts.clv != null ? pct(ts.clv) : "–"}</div></div>
      <div class="kpi"><div class="k">Your own bets</div><div class="v ${own.pl >= 0 ? "pos" : "neg"}">${money(own.pl)}</div><div class="s">${own.n} settled · ROI ${own.roi != null ? pct(own.roi) : "–"} · CLV ${own.clv != null ? pct(own.clv) : "–"}</div></div></div>` : ""}
    <div class="sec-title">Recent picks<span class="line"></span></div>
    <div class="panel scroll"><table><thead><tr><th>Pick</th><th>Tier</th><th>Graded at</th><th>Close</th><th>CLV</th><th>Result</th></tr></thead><tbody>
      ${recent.map((p) => `<tr><td style="white-space:normal;min-width:200px"><div>${esc(p.selection)}</div><div class="tiny muted">${esc(p.league || "")} · ${esc(p.date || "")}${p.finalStat != null ? ` · finished with ${p.finalStat}` : ""}</div></td>
        <td><span class="pill">${esc(p.tier)}</span> <span class="tiny">${p.units ?? 0}u</span></td><td class="num">${p.gradedAt != null ? am(p.gradedAt) : "–"}</td>
        <td class="num">${p.closeFair != null ? prob(p.closeFair) : "–"}</td><td class="num ${p.clv > 0 ? "pos" : p.clv < 0 ? "neg" : ""}">${p.clv != null ? pct(p.clv) : "–"}</td>
        <td>${["won", "lost", "push"].includes(p.status) ? `<span class="pill ${p.status === "won" ? "pos" : p.status === "lost" ? "neg" : ""}">${p.status}</span> <span class="num ${(p.profit || 0) >= 0 ? "pos" : "neg"}">${u(p.profit)}</span>` : `<span class="pill">${p.status === "void" ? "withdrawn" : esc(p.status || "open")}</span>`}</td></tr>`).join("")}
    </tbody></table></div>
    <p class="note">Picks grade at the best in-range price when posted (else the good-at price). Likely-list picks aren't graded: they were never bets.</p>`;
}

export function renderLedger(el) {
  autoSettle();
  el.innerHTML = `<div class="row" style="justify-content:space-between;margin-top:18px;flex-wrap:wrap;gap:8px"><h2 style="margin:0;font-size:18px">Ledger</h2>
      <div class="bar" style="margin:0"><button class="chip ${tab === "mine" ? "on" : ""}" data-tab="mine">My bets</button><button class="chip ${tab === "ours" ? "on" : ""}" data-tab="ours">Our picks</button></div></div>
    ${tab === "mine" ? mine(el) : ours()}`;
  $$("[data-tab]", el).forEach((b) => b.onclick = () => { tab = b.dataset.tab; renderLedger(el); });
  const ab = $("#add-bet", el); if (ab) ab.onclick = () => logSheet({});
  $$("[data-grp]", el).forEach((b) => b.onclick = () => { groupBy = b.dataset.grp; renderLedger(el); });
  $$("[data-flt]", el).forEach((b) => b.onclick = () => { filter = b.dataset.flt; renderLedger(el); });
  $$("[data-set]", el).forEach((b) => b.onclick = () => {
    const [id, st] = b.dataset.set.split("|"); const bet = S.bets.get(id);
    saveBet({ ...bet, status: st, profit: profitFor(bet, st), settledAt: new Date().toISOString() });
  });
  $$("[data-del]", el).forEach((b) => b.onclick = () => { if (b.dataset.c) deleteBet(b.dataset.del); else { b.dataset.c = 1; b.textContent = "?"; b.title = "tap again to delete"; } });
  bindTips(el);
}
