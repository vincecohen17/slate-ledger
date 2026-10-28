// Ledger: Vince's bets and our picks, graded automatically after the games (result + closing line value), with the
// breakdowns and plain-words takeaways that say what to keep betting and what to stop.
import { S, saveBet, deleteBet, loadProps } from "../store.js";
import { esc, am, pct, prob, money, fmtShortDay, bindTips, bookTag, bookLogo, teamLogo, todayPT, toast, $, $$ } from "../util.js";
import { logSheet } from "./common.js";
import { settleProfit, isBoost } from "../promo.js";
import { VENUES } from "../lab.js";
import { projectedCard, betLegs } from "../projected.js";
import { betImage } from "./common.js";
import { analysis, bindAnalysis } from "./analysis.js";
import { unify, outcomes, arbSheet, unpair, findPairs, autoPair, pairBets } from "../arbs.js";

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
  if (b.isGroup) return b.kind === "arb" ? "Arb" : "Middle";
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
  if (b.isGroup) return "Arbs & middles";
  const d = b.dec || 0;
  return d < 1.67 ? "-150 or shorter" : d < 2.1 ? "-149 to +110" : d < 3 ? "+111 to +200" : d < 6 ? "+201 to +500" : "+500 and longer";
}
function edgeBand(b) {
  if (b.isGroup) return "Arbs & middles";
  const e = evAtLog(b);
  return e == null ? "No fair logged" : e < 0 ? "Below fair (no edge)" : e < 0.03 ? "0–3% edge" : e < 0.06 ? "3–6% edge" : "6%+ edge";
}
const SOURCES = { arb: "Arbs & middles", edge: "Edges screen", pick: "Our picks", promo: "Promos", lab: "Bet Lab", manual: "Typed in" };
const GROUPS = {
  type: ["Bet type", betType], sport: ["Sport", (b) => b.league || "Unknown"], venue: ["Venue", (b) => b.venueName || b.venue],
  odds: ["Odds range", oddsBand], edge: ["Edge when logged", edgeBand], source: ["Where it came from", (b) => SOURCES[b.source] || b.source || "Typed in"],
  promo: ["Promo", (b) => b.promoId ? "With a promo" : "No promo"],
  timing: ["Live or pre-game", (b) => b.live ? "Live (in-game)" : "Pre-game"],
};

function summarize(list) {
  const g = list.filter((b) => ["won", "lost", "push"].includes(b.status));
  const staked = g.reduce((s, b) => s + (b.stake || 0), 0), pl = g.reduce((s, b) => s + (b.profit || 0), 0);
  const clv = g.filter((b) => b.clv != null);
  const exp = g.filter((b) => evAtLog(b) != null || b.expPl != null);
  return { n: g.length, w: g.filter((b) => b.status === "won").length, l: g.filter((b) => b.status === "lost").length, p: g.filter((b) => b.status === "push").length,
    staked, pl, roi: staked ? pl / staked : null, clv: clv.length ? clv.reduce((s, b) => s + b.clv, 0) / clv.length : null, nClv: clv.length,
    beat: clv.length ? clv.filter((b) => b.clv > 0).length / clv.length : null,
    expPl: exp.reduce((s, b) => s + (b.isGroup ? b.expPl : b.stake * evAtLog(b)), 0), nExp: exp.length };
}

// ---------- charts
function plChart(settled, expectedToo = true, fmt = money) {
  if (settled.length < 2) return `<div class="note">The chart appears after two settled bets.</div>`;
  const pts = []; let c = 0, e = 0;
  settled.forEach((b) => { c += b.profit || 0; e += b.isGroup ? b.expPl || 0 : evAtLog(b) != null ? b.stake * evAtLog(b) : 0; pts.push({ y: c, e, b }); });
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

// a logo next to a breakdown row: the venue's, else the league's
function nameCell(key, k, list) {
  if (key === "venue") { const b = list.find((x) => (x.venueName || x.venue) === k); return `<span class="lg-name">${bookLogo(b?.venue || k, k, 18)}${esc(k)}</span>`; }
  if (key === "sport") { const g = [...S.games.values()].find((x) => x.league === k); return `<span class="lg-name">${g?.leagueLogo ? teamLogo({ logo: g.leagueLogo, abbr: k }, k, 18) : ""}${esc(k)}</span>`; }
  return esc(k);
}
const wl = (r) => r.w + r.l ? `<span class="lg-wl"><i class="w" style="width:${(r.w / (r.w + r.l) * 100).toFixed(0)}%"></i><i class="l" style="width:${(r.l / (r.w + r.l) * 100).toFixed(0)}%"></i></span>` : "";

function groupTable(list, key) {
  const [, fn] = GROUPS[key];
  const by = new Map();
  list.forEach((b) => { const k = fn(b); if (!by.has(k)) by.set(k, []); by.get(k).push(b); });
  const rows = [...by.entries()].map(([k, bs]) => ({ k, ...summarize(bs) })).filter((r) => r.n).sort((a, b) => b.n - a.n);
  if (!rows.length) return `<div class="note">Nothing settled yet.</div>`;
  const maxRoi = Math.max(0.05, ...rows.map((r) => Math.abs(r.roi || 0)));
  return `<div style="overflow-x:auto"><table class="l10t lg-t"><thead><tr><th></th><th>Bets</th><th>Record</th><th>P/L</th><th style="min-width:120px">ROI</th><th>Avg CLV</th><th>Beat close</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td>${nameCell(key, r.k, list)}</td><td class="num">${r.n}</td><td class="num">${wl(r)}${r.w}-${r.l}${r.p ? "-" + r.p : ""}</td>
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
  const lv = list.filter((b) => b.live && ["won", "lost", "push"].includes(b.status)), pg = list.filter((b) => !b.live && ["won", "lost", "push"].includes(b.status));
  if (lv.length >= 5) {
    const a = summarize(lv), c = summarize(pg);
    out.push([a.clv != null && a.clv > 0.01 ? "pos" : a.clv != null && a.clv < -0.01 ? "neg" : "", `Live bets: ${a.n} settled, ${money(a.pl)}${a.roi != null ? `, ${pct(a.roi)} ROI` : ""}${a.clv != null ? `, ${pct(a.clv)} vs the live fair a few minutes later` : ""}${c.roi != null ? ` (pre-game ROI ${pct(c.roi)})` : ""}. ${a.clv != null && a.clv < -0.01 ? "The live market is beating your timing: stick to live edges the site flags, and only when the price is still there." : a.clv != null && a.clv > 0.01 ? "Your live timing is beating the market. Keep it up." : ""}`]);
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

// boosted bets logged before the "odds already include the boost" switch existed: the boost may be counted twice
const needsCheck = (b) => b.promoId && isBoost(S.promos.get(b.promoId)) && b.boostIn == null && !b.checkedAt;

function betRow(b) {
  const ev = evAtLog(b), legs = betLegs(b), multi = legs.length > 1;
  const done = ["won", "lost", "push", "void"].includes(b.status);
  const win = b.dec && b.stake ? b.stake * (b.dec - 1) : null;
  return `<div class="lt ${esc(b.status || "pending")}">
    ${bookLogo(b.venue || "any", b.venueName || b.venue || "", 34)}
    <div class="col" style="gap:3px;min-width:0">
      <div class="row" style="gap:8px;min-width:0">${!multi ? betImage({ gameId: b.gameId || legs[0]?.gameId, label: legs[0]?.label || b.label, player: legs[0]?.player, league: b.league }, 22) : ""}<span class="lt-t ellip">${multi ? `${legs.length}-leg parlay` : esc(b.label)}</span></div>
      <div class="lt-m"><span><b class="num">${am(b.am)}</b></span><span>${money(b.stake)}${win != null && !done ? ` to win <b class="num">${money(win)}</b>` : ""}</span>
        <span>${esc(b.venueName || b.venue || "")}</span>${b.league ? `<span>${esc(b.league)}</span>` : ""}${b.promoId ? `<span class="pill s">${esc(S.promos.get(b.promoId)?.title || "promo")}${b.boostIn ? " · in odds" : ""}</span>` : ""}${needsCheck(b) ? `<span class="lt-chip neg" title="did the odds you typed already include the boost?">check boost</span>` : ""}
        ${b.fairAtLog != null ? `<span class="lt-chip ${ev > 0 ? "pos" : ev < 0 ? "neg" : ""}" title="our fair when you logged it">fair ${prob(b.fairAtLog)} · ${pct(ev)}</span>` : ""}
        ${b.live ? `<span class="pill live" title="placed during the game">● live${b.liveState?.text ? ` · ${esc(b.liveState.text)}` : ""}</span>` : ""}
        ${b.clv != null ? `<span class="lt-chip ${b.clv > 0 ? "pos" : b.clv < 0 ? "neg" : ""}" title="${b.live ? "your price at the live fair a few minutes after the bet" : "value of your price at the closing fair"}">CLV ${pct(b.clv)}</span>`
          : b.liveCheck ? `<span class="lt-chip ${b.liveCheck.clv > 0 ? "pos" : b.liveCheck.clv < 0 ? "neg" : ""}" title="the live fair ${Math.round(b.liveCheck.mins)} minutes after you bet: ${prob(b.liveCheck.fair)}">${Math.round(b.liveCheck.mins)}-min check ${pct(b.liveCheck.clv)}</span>`
          : b.live && b.status === "pending" ? `<span class="lt-chip" title="the live fair is checked 5 to 30 minutes after the bet">price check pending</span>` : ""}</div>
    </div>
    <div class="lt-r">${done ? `<span class="pill ${b.status === "won" ? "pos" : b.status === "lost" ? "neg" : ""}">${b.status}${b.autoSettled ? " · auto" : ""}</span><span class="lt-res ${b.profit >= 0 ? "pos" : "neg"}">${money(b.profit)}</span>`
      : `<span class="lt-act">${["won", "lost", "push", "void"].map((s) => `<button class="btn sm ghost" data-set="${b.id}|${s}" title="mark ${s}">${s[0].toUpperCase()}</button>`).join("")}</span>`}
      <span class="row" style="gap:4px"><button class="btn sm ghost" data-edit="${b.id}" title="check or fix odds, stake, promo">Edit</button><button class="x" data-del="${b.id}" title="delete">×</button></span></div>
    ${multi ? `<div class="lt-legs">${legs.map((l) => `<span class="lt-leg">${betImage({ gameId: l.gameId, label: l.label, player: l.player, league: b.league }, 18)}<span>${esc(l.label || "")}</span></span>`).join("")}</div>` : ""}
  </div>`;
}
// an arb or middle: one ticket, one result (net of both legs); each leg below with its own grade and edit
function legRow(b) {
  const done = ["won", "lost", "push", "void"].includes(b.status);
  return `<div class="lt-gleg">${bookLogo(b.venue || "any", b.venueName || "", 20)}<span class="ellip" title="${esc(b.label)}">${esc(b.label)}</span>
    <span class="num">${am(b.am)}</span><span class="num muted">${money(b.stake)}</span>
    <span class="lg-clv">${b.clv != null ? `<span class="lt-chip ${b.clv > 0 ? "pos" : b.clv < 0 ? "neg" : ""}" title="this leg's price at the closing fair">CLV ${pct(b.clv)}</span>` : ""}</span>
    <span class="lg-st">${done ? `<span class="pill ${b.status === "won" ? "pos" : b.status === "lost" ? "neg" : ""}">${b.status}${b.autoSettled ? " · auto" : ""}</span>`
      : `<span class="lt-act">${["won", "lost", "push", "void"].map((s) => `<button class="btn sm ghost" data-set="${b.id}|${s}" title="mark ${s}">${s[0].toUpperCase()}</button>`).join("")}</span>`}</span>
    <span class="row lg-ac" style="gap:4px"><button class="btn sm ghost" data-edit="${b.id}" title="check or fix odds, stake, promo">Edit</button><button class="x" data-del="${b.id}" title="delete this leg">×</button></span></div>`;
}
function groupRow(g) {
  const o = outcomes(g.legsB), done = g.status !== "pending", pl = g.legsB[0].player;
  const range = g.kind === "arb" ? `<span>${o.worst >= 0 ? "locks" : "worst"} <b class="num ${o.worst >= 0 ? "pos" : "neg"}">${money(o.worst)}</b></span>`
    : `<span><b class="num ${o.worst >= 0 ? "pos" : "neg"}">${money(o.worst)}</b> to <b class="num pos">${money(o.both)}</b></span>`;
  return `<div class="lt lt-grp ${esc(g.status)}">
    <span class="lt-gico">${g.legsB.map((l) => bookLogo(l.venue || "any", l.venueName || "", 26)).join("")}</span>
    <div class="col" style="gap:3px;min-width:0">
      <div class="row" style="gap:8px;min-width:0"><span class="pill ${g.kind === "arb" ? "pos" : "s"}">${g.kind}</span><span class="lt-t ellip">${esc(pl ? `${pl} · ${g.matchup || ""}` : g.matchup || g.label)}</span></div>
      <div class="lt-m"><span>${money(g.stake)} across ${g.legsB.length} venues</span>${g.league ? `<span>${esc(g.league)}</span>` : ""}${done ? "" : range}
        ${g.group.window ? `<span>middle ${esc(g.group.window[0])} to ${esc(g.group.window[1])}${g.group.pMiddle != null ? ` (~${prob(g.group.pMiddle)})` : ""}</span>` : ""}
        ${g.expPl != null ? `<span class="lt-chip ${g.expPl > 0 ? "pos" : g.expPl < 0 ? "neg" : ""}" title="what the pair was worth at the fairs when logged">expected ${money(g.expPl)}</span>` : ""}</div>
    </div>
    <div class="lt-r">${done ? `<span class="pill ${g.status === "won" ? "pos" : g.status === "lost" ? "neg" : ""}">${g.status === "won" ? "profit" : g.status === "lost" ? "loss" : g.status}</span><span class="lt-res ${(g.profit || 0) >= 0 ? "pos" : "neg"}">${money(g.profit)}</span>`
      : `<span class="pill">open</span>`}
      <button class="btn sm ghost" data-unlink="${esc(g.id)}" title="split back into two separate bets">Unlink</button></div>
    <div class="lt-glegs">${g.legsB.map(legRow).join("")}</div>
  </div>`;
}
function betList(list) {
  const by = new Map();
  list.forEach((b) => { const d = b.date || (b.createdAt || "").slice(0, 10); if (!by.has(d)) by.set(d, []); by.get(d).push(b); });
  return `<div class="lt-list">${[...by.entries()].map(([d, bs]) => {
    const pl = bs.filter((b) => ["won", "lost", "push"].includes(b.status)).reduce((x, b) => x + (b.profit || 0), 0);
    const any = bs.some((b) => ["won", "lost", "push"].includes(b.status));
    return `<div class="lt-day row" style="justify-content:space-between"><span>${d ? esc(fmtShortDay(d + "T12:00:00")) : "Undated"}</span>${any ? `<span class="num ${pl >= 0 ? "pos" : "neg"}">${money(pl)}</span>` : ""}</div>${bs.map((b) => b.isGroup ? groupRow(b) : betRow(b)).join("")}`;
  }).join("")}</div>`;
}

function mine(el) {
  const raw = [...S.bets.values()];
  const bets = unify(raw).sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || ""));
  const settled = bets.filter((b) => ["won", "lost", "push"].includes(b.status)).sort((a, b) => (a.settledAt || "").localeCompare(b.settledAt || ""));
  // arbs and middles are their own category: the betting record, ROI, CLV and takeaways are the bets alone
  const plain = bets.filter((b) => !b.isGroup), arbsL = bets.filter((b) => b.isGroup);
  const s = summarize(plain), sa = summarize(arbsL), pend = raw.filter((b) => b.status === "pending");
  const sugg = findPairs(raw).filter((p) => !p.auto);
  // money truly at risk: what each open bet loses if it loses (a bonus-funded stake was never yours), plus an open
  // middle's worst case if that's a loss. Open arbs and middles are shown apart: what's in them and what they lock.
  const openB = plain.filter((b) => b.status === "pending"), openG = arbsL.filter((g) => g.status === "pending");
  const lose = (b) => Math.max(0, -profitFor(b, "lost"));
  const gO = openG.map((g) => ({ g, o: outcomes(g.legsB) }));
  const atRisk = openB.reduce((x, b) => x + lose(b), 0) + gO.reduce((x, { o }) => x + Math.max(0, -o.worst), 0);
  const inArbs = openG.reduce((x, g) => x + (g.stake || 0), 0);
  const lockLo = gO.reduce((x, { o }) => x + o.worst, 0), lockHi = gO.reduce((x, { g, o }) => x + (g.kind === "middle" ? Math.max(o.both, o.worst) : Math.max(o.ifA, o.ifB)), 0);
  const shown = bets.filter((b) => filter === "all" || (filter === "live" ? b.live : filter === "pending" ? b.status === "pending" : b.status !== "pending"));
  const ins = insights(plain);
  return `<div class="kpis" style="margin-top:12px">
      <div class="kpi"><div class="k">Profit / loss</div><div class="v ${s.pl + sa.pl >= 0 ? "pos" : "neg"}">${money(s.pl + sa.pl)}</div><div class="s">${sa.n ? `bets ${money(s.pl)} · arbs ${money(sa.pl)}` : `${s.n} settled · ${s.w}-${s.l}${s.p ? "-" + s.p : ""}`}</div></div>
      <div class="kpi"><div class="k">ROI</div><div class="v">${s.roi != null ? pct(s.roi) : "–"}</div><div class="s">${s.w}-${s.l}${s.p ? "-" + s.p : ""} on ${money(s.staked)} staked${arbsL.length ? " · arbs apart" : ""}</div></div>
      <div class="kpi"><div class="k">Expected profit</div><div class="v ${s.expPl >= 0 ? "pos" : "neg"}">${s.nExp ? money(s.expPl) : "–"}</div><div class="s">from the prices you got</div></div>
      <div class="kpi"><div class="k">Avg CLV</div><div class="v ${s.clv > 0 ? "pos" : s.clv < 0 ? "neg" : ""}">${s.clv != null ? pct(s.clv) : "–"}</div><div class="s">beat the close: ${s.beat != null ? Math.round(s.beat * 100) + "%" : "–"}</div></div>
      <div class="kpi"><div class="k">At risk</div><div class="v">${money(atRisk)}</div><div class="s">${openB.length} open bet${openB.length === 1 ? "" : "s"}${openB.some((b) => lose(b) < (b.stake || 0) - 0.005) ? " · bonus stakes not counted" : ""}</div></div>
      ${openG.length ? `<div class="kpi"><div class="k">In open arbs</div><div class="v">${money(inArbs)}</div><div class="s">${openG.length} pair${openG.length === 1 ? "" : "s"} · ${Math.abs(lockHi - lockLo) < 0.01 ? `locks <b class="${lockLo >= 0 ? "pos" : "neg"}">${money(lockLo)}</b>` : `makes <b class="${lockLo >= 0 ? "pos" : "neg"}">${money(lockLo)}</b> to <b class="pos">${money(lockHi)}</b>`}</div></div>` : ""}
      ${sa.n ? `<div class="kpi"><div class="k">Arbs & middles</div><div class="v ${sa.pl >= 0 ? "pos" : "neg"}">${money(sa.pl)}</div><div class="s">${sa.n} settled pair${sa.n === 1 ? "" : "s"} · own category</div></div>` : ""}
    </div>
    ${sugg.length ? `<div class="card lg-chk"><b>${sugg.length === 1 ? "These two bets look" : `${sugg.length} pairs of bets look`} like ${sugg.every((p) => p.kind === "middle") ? "a middle" : "an arb or middle"}</b>
      <div class="small muted" style="margin:4px 0 8px">Paired, they count as one result in their own category, so the losing leg isn't a lost bet in your record.</div>
      <div class="col" style="gap:6px">${sugg.map((p, i) => `<div class="row lg-chkr" style="gap:8px;flex-wrap:wrap">${bookLogo(p.a.venue || "any", p.a.venueName || "", 22)}${bookLogo(p.b.venue || "any", p.b.venueName || "", 22)}
        <span class="grow ellip small" style="min-width:160px">${esc(p.a.label)} <span class="muted">${am(p.a.am)}</span> + ${esc(p.b.label)} <span class="muted">${am(p.b.am)}</span></span>
        <button class="btn sm ghost" data-pairs="${i}">Pair as ${p.kind}</button></div>`).join("")}</div></div>` : ""}
    ${(() => { const chk = raw.filter(needsCheck); return chk.length ? `<div class="card lg-chk"><b>${chk.length} boosted bet${chk.length > 1 ? "s" : ""} to double-check</b>
      <div class="small muted" style="margin:4px 0 8px">If you typed the price the app showed after the boost, the ledger added the boost a second time. Tap the one that's right for each bet.</div>
      <div class="col" style="gap:6px">${chk.map((b) => { const p = S.promos.get(b.promoId), st = ["won", "lost"].includes(b.status) ? b.status : "won";
        const a = settleProfit({ ...b, boostIn: true }, p, st), n = settleProfit({ ...b, boostIn: false }, p, st);
        return `<div class="row lg-chkr" style="gap:8px;flex-wrap:wrap">${bookLogo(b.venue || "any", b.venueName || "", 22)}<span class="grow ellip small" style="min-width:140px">${esc(b.label)} <span class="muted">${am(b.am)} · ${money(b.stake)}${b.status !== "pending" ? ` · ${b.status}` : ""}</span></span>
          <button class="btn sm ghost" data-bin="${b.id}|1">Boost was in the odds <span class="tiny muted">${st === "won" ? `wins ${money(a)}` : ""}</span></button>
          <button class="btn sm ghost" data-bin="${b.id}|0">Normal odds <span class="tiny muted">${st === "won" ? `wins ${money(n)}` : ""}</span></button></div>`; }).join("")}</div></div>` : ""; })()}
    ${pend.length ? `<div class="sec-title">Open bets<span class="line"></span></div>${projectedCard({ items: pend, title: "Projected profit on open bets",
        settledToday: raw.filter((b) => ["won", "lost", "push"].includes(b.status) && b.settledAt && new Date(b.settledAt).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" }) === todayPT()).reduce((x, b) => x + (b.profit || 0), 0) })}` : ""}
    ${ins.length ? `<div class="sec-title">What the numbers say<span class="line"></span></div><div class="card col lg-ins">${ins.map(([t, x]) => `<div class="lg-in ${t}">${t === "pos" ? "▲" : t === "neg" ? "▼" : "●"} ${esc(x)}</div>`).join("")}</div>`
      : `<div class="card note" style="margin-top:12px">Takeaways appear once 8+ bets are settled with a closing price. Bets logged from the Edges, Picks, Promos and Lab screens grade themselves after the game; typed-in bets need a W/L tap.</div>`}
    <div class="sec-title">Running profit<span class="line"></span></div><div class="card">${plChart(settled)}</div>
    <div class="sec-title">Breakdown<span class="line"></span></div>
    <div class="bar">${Object.entries(GROUPS).map(([k, [lab]]) => `<button class="chip sm ${k === groupBy ? "on" : ""}" data-grp="${k}">${lab}</button>`).join("")}</div>
    <div class="card">${groupTable(bets, groupBy)}<p class="note">CLV is what your price was worth at the closing sharp fair: positive means you got a better number than the market settled on. Over a few hundred bets it predicts profit far better than wins and losses do.</p></div>
    <div class="row" style="justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px"><div class="bar" style="margin:0">${["pending", "settled", "live", "all"].map((f) => `<button class="chip ${f === filter ? "on" : ""}" data-flt="${f}">${f === "live" ? "● Live bets" : f[0].toUpperCase() + f.slice(1)}</button>`).join("")}</div>
      <span class="row" style="gap:6px"><button class="btn ghost" id="add-arb" title="both legs of an arb or middle, counted as one result">Log arb / middle</button><button class="btn" id="add-bet">Log a bet</button></span></div>
    ${shown.length ? betList(shown)
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
  const ub = unify([...S.bets.values()]), tailed = ub.filter((b) => b.pickId || b.source === "pick");
  const ts = summarize(tailed), own = summarize(ub.filter((b) => !(b.pickId || b.source === "pick")));
  const recent = [...all].filter((p) => p.tier !== "likely").sort((a, b) => (b.commence || "").localeCompare(a.commence || "")).slice(0, 30);
  const u = (x) => `${x >= 0 ? "+" : ""}${(x || 0).toFixed(2)}u`;
  const openPicks = all.filter((p) => ["open", "pending", undefined, null].includes(p.status) && p.tier !== "likely").map((p) => {
    const ins = (p.playTo || []).filter((v) => v.inRange && typeof v.price === "number");
    const best = ins.length ? ins.reduce((x, y) => (decA(y.price) > decA(x.price) ? y : x)) : null;
    const a = best ? best.price : p.goodAt;
    return a == null ? null : { ...p, label: p.selection, dec: decA(a), am: a, venue: best?.venue, venueName: best?.name || best?.venue };
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
    ${graded.length ? (() => {
      const sup = (p) => Object.values(p.pillars || {}).filter((x) => x === "support").length;
      const grp = (fn) => { const m = new Map(); graded.forEach((p) => { const k = fn(p); if (!m.has(k)) m.set(k, []); m.get(k).push(asBet(p)); }); return [...m.entries()].map(([k, bs]) => [k, summarize(bs)]); };
      const edgeB = (p) => { const e = p.modelP != null && p.marketFair != null ? p.modelP - p.marketFair : null; return e == null ? "No model number" : e < 0.02 ? "Model +0-2 pts" : e < 0.05 ? "Model +2-5 pts" : "Model +5 pts or more"; };
      const kindB = (p) => (p.player ? "Props" : /^h2h/.test(p.market || "") ? "Moneylines" : /spreads/.test(p.market || "") ? "Spreads" : /totals/.test(p.market || "") ? "Totals" : "Other");
      const tbl = (title, rows) => `<div class="card" style="margin-top:10px"><b>${title}</b><div style="overflow-x:auto"><table class="l10t lg-t"><thead><tr><th></th><th>Picks</th><th>Record</th><th>Units</th><th>ROI</th><th>Avg CLV</th></tr></thead><tbody>
        ${rows.filter(([, r]) => r.n).map(([k, r]) => `<tr><td>${esc(k)}</td><td class="num">${r.n}</td><td class="num">${r.w}-${r.l}${r.p ? "-" + r.p : ""}</td><td class="num ${r.pl >= 0 ? "pos" : "neg"}">${u(r.pl)}</td><td class="num">${r.roi != null ? pct(r.roi) : "–"}</td><td class="num ${r.clv > 0 ? "pos" : r.clv < 0 ? "neg" : ""}">${r.clv != null ? pct(r.clv) : "–"}</td></tr>`).join("")}</tbody></table></div></div>`;
      return `<div class="sec-title">What makes a good pick<span class="line"></span></div>
        ${tbl("By how many of the six pillars supported it", grp((p) => `${sup(p)} of 6 support`).sort((a, b) => b[0].localeCompare(a[0])))}
        ${tbl("By how far our model was from the market", grp(edgeB))}
        ${tbl("By bet type", grp(kindB))}
        <p class="note">Once these fill in, they tell the pick runs which cases to trust: if 5-of-6 picks beat the close and 3-of-6 don't, the bar goes up.</p>`; })() : ""}
    <div class="sec-title">Recent picks<span class="line"></span></div>
    <div class="lt-list">${recent.map((p) => {
      const st = ["won", "lost", "push"].includes(p.status) ? p.status : p.status === "void" ? "void" : "pending";
      const v = (p.playTo || []).find((x) => x.inRange) || (p.playTo || [])[0];
      return `<div class="lt ${st}">${betImage({ gameId: p.gameId, label: p.selection, player: p.player, headshot: p.headshot, league: p.league }, 34)}
        <div class="col" style="gap:3px;min-width:0"><span class="lt-t">${esc(p.selection)}</span>
          <div class="lt-m"><span class="pill ${p.tier === "bet" ? "s" : ""}">${esc(p.tier)} · ${p.units ?? 0}u</span><span>${esc(p.league || "")}</span><span>${esc(p.date || "")}</span>
            ${v ? `<span class="lg-name">${bookLogo(v.venue, v.name || v.venue, 14)}${esc(v.name || v.venue)}</span>` : ""}
            ${p.gradedAt != null ? `<span>graded at <b class="num">${am(p.gradedAt)}</b></span>` : ""}
            ${p.clv != null ? `<span class="lt-chip ${p.clv > 0 ? "pos" : p.clv < 0 ? "neg" : ""}">CLV ${pct(p.clv)}</span>` : ""}
            ${p.finalStat != null ? `<span>finished with <b class="num">${p.finalStat}</b></span>` : ""}</div></div>
        <div class="lt-r">${["won", "lost", "push"].includes(p.status) ? `<span class="pill ${p.status === "won" ? "pos" : p.status === "lost" ? "neg" : ""}">${p.status}</span><span class="lt-res ${(p.profit || 0) >= 0 ? "pos" : "neg"}">${u(p.profit)}</span>`
          : `<span class="pill">${p.status === "void" ? "withdrawn" : esc(p.status || "open")}</span>`}</div></div>`;
    }).join("")}</div>
    <p class="note">Picks grade at the best in-range price when posted (else the good-at price). Likely-list picks aren't graded: they were never bets.</p>`;
}

export function renderLedger(el) {
  autoSettle();
  for (const b of S.bets.values()) if (b.status === "pending") for (const l of betLegs(b)) if (l.player && l.gameId) loadProps(l.gameId);
  for (const p of S.picks.values()) if (p.player && p.gameId && !["won", "lost", "push", "void"].includes(p.status)) loadProps(p.gameId);
  el.innerHTML = `<div class="row" style="justify-content:space-between;margin-top:18px;flex-wrap:wrap;gap:8px"><h2 style="margin:0;font-size:18px">Ledger</h2>
      <div class="bar" style="margin:0"><button class="chip ${tab === "mine" ? "on" : ""}" data-tab="mine">My bets</button><button class="chip ${tab === "analysis" ? "on" : ""}" data-tab="analysis">Analysis</button><button class="chip ${tab === "ours" ? "on" : ""}" data-tab="ours">Our picks</button></div></div>
    ${tab === "mine" ? mine(el) : tab === "analysis" ? analysis() : ours()}`;
  if (tab === "analysis") bindAnalysis(el, () => renderLedger(el));
  $$("[data-tab]", el).forEach((b) => b.onclick = () => { tab = b.dataset.tab; renderLedger(el); });
  const ab = $("#add-bet", el); if (ab) ab.onclick = () => logSheet({});
  autoPair([...S.bets.values()]);
  const sg = findPairs([...S.bets.values()]).filter((p) => !p.auto);
  $$("[data-pairs]", el).forEach((b) => b.onclick = () => { const p = sg[+b.dataset.pairs]; if (p) pairBets(p.a, p.b, p.kind).then(() => toast(`Paired as ${p.kind}`)).catch((e) => toast("Could not save: " + e.message)); });
  const aa = $("#add-arb", el); if (aa) aa.onclick = () => arbSheet({});
  $$("[data-unlink]", el).forEach((b) => b.onclick = () => {
    if (!b.dataset.c) { b.dataset.c = 1; b.textContent = "Unlink?"; return; }
    unpair([...S.bets.values()].filter((x) => x.group?.id === b.dataset.unlink)).then(() => toast("Split into two bets")).catch((e) => toast("Could not save: " + e.message));
  });
  $$("[data-grp]", el).forEach((b) => b.onclick = () => { groupBy = b.dataset.grp; renderLedger(el); });
  $$("[data-flt]", el).forEach((b) => b.onclick = () => { filter = b.dataset.flt; renderLedger(el); });
  $$("[data-set]", el).forEach((b) => b.onclick = () => {
    const [id, st] = b.dataset.set.split("|"); const bet = S.bets.get(id);
    saveBet({ ...bet, status: st, profit: profitFor(bet, st), settledAt: new Date().toISOString() });
  });
  $$("[data-edit]", el).forEach((b) => b.onclick = () => logSheet(S.bets.get(b.dataset.edit)));
  $$("[data-bin]", el).forEach((b) => b.onclick = () => {
    const [id, v] = b.dataset.bin.split("|"), bet = { ...S.bets.get(id), boostIn: v === "1", checkedAt: new Date().toISOString() };
    if (["won", "lost", "push", "void"].includes(bet.status)) bet.profit = profitFor(bet, bet.status);
    saveBet(bet);
  });
  $$("[data-del]", el).forEach((b) => b.onclick = () => { if (b.dataset.c) deleteBet(b.dataset.del); else { b.dataset.c = 1; b.textContent = "?"; b.title = "tap again to delete"; } });
  bindTips(el);
}
