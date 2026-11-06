import { WHY } from "./market.js";
// Ledger > Our picks, in depth: units by day (calendar), the forecasts behind each pick scored against the results
// (our fair vs the sharp market vs the model: log loss, Brier, calibration, reliability), a breakdown explorer over
// every attribute a pick carries, and the full pick table (sortable, CSV). Picks grade at real venue prices (the best
// first seen at a play-to venue, else the first raw pull after posting); older ones may still show good-at.
import { S } from "../store.js";
import { esc, pct, prob, am, mktName, bindTips, odds } from "../util.js";
import { lineChart, legend } from "./charts.js";
import { mean, meanCI, wilson, bootRatio, logLoss, brier, calibFit, relBins, ptDay, pTwo, fmtP, fmtT, clvOk, CLOSE_OK_MIN } from "../stats.js";
import { calendar, relChart, sortTable, bindSort, td, downloadCsv, chipBar } from "./deep.js";

const G = ["won", "lost", "push"];
const V = { dim: "tier", day: null };
const decOf = (a) => (a == null ? null : a > 0 ? 1 + a / 100 : 1 + 100 / -a);
const u = (x) => `${x >= 0 ? "+" : ""}${(x || 0).toFixed(2)}u`;
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const day = (p) => ptDay(p.commence) || p.date;
// flat 1-unit return at the graded price (every tier, "likely" and "lean" included): one scale for comparing groups
const flatOf = (p) => (p.flat != null ? p.flat : p.status === "won" ? (decOf(p.gradedAt) || 2) - 1 : p.status === "lost" ? -1 : 0);
const expU = (p) => { const d = decOf(p.gradedAt), f = p.fair; return d && f != null ? f * (p.units || 0) * (d - 1) - (1 - f) * (p.units || 0) : null; };
const varU = (p) => { const d = decOf(p.gradedAt), f = p.fair, x = p.units || 0; return d && f != null ? f * (1 - f) * (x * d) ** 2 : 0; };
const sup = (p) => Object.values(p.pillars || {}).filter((x) => x === "support").length;
const against = (p) => Object.values(p.pillars || {}).filter((x) => x === "against").length;
const kind = (p) => (p.player ? "Player prop" : /^h2h/.test(p.market || "") ? "Moneyline" : /spreads/.test(p.market || "") ? "Spread" : /team_totals/.test(p.market || "") ? "Team total" : /totals/.test(p.market || "") ? "Total" : "Other");
const DIMS = [["tier", "Tier"], ["league", "Sport"], ["kind", "Bet type"], ["market", "Market"], ["side", "Side"], ["checks", "Checks passed"], ["pillars", "Pillars"],
  ["why", "Market reason"], ["gap", "Model − market"], ["edge", "Price edge"], ["fairb", "Fair chance"], ["run", "Posted by"], ["venue", "Graded at"], ["src", "Price source"]];
const dimFn = {
  tier: (p) => ({ bet: "Bet tier", floor: "Floor (0.25u)", lean: "Lean (0.1u, tracked)", likely: "Likely (0u, tracked)" })[p.tier] || p.tier,
  league: (p) => p.league || "?", kind, market: (p) => mktName(p.market) || "?",
  side: (p) => (p.side === "Over" || p.side === "Under" ? p.side : decOf(p.gradedAt) >= 2 ? "Underdog side" : "Favorite side"),
  checks: (p) => (p.checks ? `${["logic", "model", "price"].filter((k) => p.checks[k]).length} of 3` : "Not recorded"),
  pillars: (p) => (p.pillars ? `${sup(p)} support${against(p) ? `, ${against(p)} against` : ""}` : "Not recorded"),
  gap: (p) => { const e = p.modelP != null && p.marketFair != null ? p.modelP - p.marketFair : null; return e == null ? "No model number" : e < 0 ? "Model below market" : e < 0.02 ? "0-2 pts" : e < 0.05 ? "2-5 pts" : e < 0.1 ? "5-10 pts" : "10+ pts"; },
  edge: (p) => { const d = decOf(p.gradedAt); const e = d && p.fair != null ? p.fair * d - 1 : null; return e == null ? "?" : e < 0 ? "Negative at graded price" : e < 0.03 ? "0-3%" : e < 0.06 ? "3-6%" : "6%+"; },
  fairb: (p) => (p.fair == null ? "?" : p.fair < 0.45 ? "Under 45%" : p.fair < 0.55 ? "45-55%" : p.fair < 0.7 ? "55-70%" : "70%+"),
  run: (p) => (p.early ? "Evening early look" : "Day runs"),
  why: (p) => WHY[p.why?.k]?.label || "Not tagged (posted before Nov 5)",
  venue: (p) => p.gradeVenue || "?", src: (p) => ({ "first seen": "First real price seen", posted: "First pull after posting", goodAt: "Good-at (no real price found)" })[p.gradeSrc] || "Before price checks",
};
const ORDER = { gap: ["Model below market", "0-2 pts", "2-5 pts", "5-10 pts", "10+ pts", "No model number"], fairb: ["Under 45%", "45-55%", "55-70%", "70%+"], edge: ["Negative at graded price", "0-3%", "3-6%", "6%+"] };

function agg(list) {
  const risk = list.reduce((s, p) => s + (p.units || 0), 0), pl = list.reduce((s, p) => s + (p.profit || 0), 0);
  const c = list.filter(clvOk), ex = list.map(expU).filter((x) => x != null), v = list.reduce((s, p) => s + varU(p), 0);
  const plE = list.filter((p) => expU(p) != null).reduce((s, p) => s + (p.profit || 0), 0), e = ex.reduce((a, b) => a + b, 0);
  const fl = list.map(flatOf);
  return { flat: fl.reduce((a, b) => a + b, 0), flatRoi: fl.length ? mean(fl) : null, n: list.length, w: list.filter((p) => p.status === "won").length, l: list.filter((p) => p.status === "lost").length, p: list.filter((p) => p.status === "push").length,
    risk, pl, roi: risk ? pl / risk : null, exp: e, luck: plE - e, z: v > 0 ? (plE - e) / Math.sqrt(v) : null, clv: c.length ? mean(c.map((p) => p.clv)) : null, beat: c.length ? c.filter((p) => p.clv > 0).length / c.length : null };
}
function table(rows, label) {
  return sortTable([label, "Picks", "W-L-P", "Flat 1u ROI", "Units risked", "Units", "ROI", "Expected", "Luck", "z", "Avg CLV", "Beat close"], rows.map(([k, r]) => `<tr><td>${esc(k)}</td>${td(r.n, r.n)}${td(`${r.w}-${r.l}${r.p ? "-" + r.p : ""}`, r.w / Math.max(1, r.w + r.l))}
    ${td(`<span class="${cls(r.flatRoi)}">${r.flatRoi != null ? pct(r.flatRoi) : "–"}</span>`, r.flatRoi)}
    ${td(r.risk.toFixed(2), r.risk)}${td(`<span class="${cls(r.pl)}">${u(r.pl)}</span>`, r.pl)}${td(r.roi != null ? pct(r.roi) : "–", r.roi)}${td(u(r.exp), r.exp)}${td(`<span class="${cls(r.luck)}">${u(r.luck)}</span>`, r.luck)}${td(fmtT(r.z), r.z)}
    ${td(`<span class="${cls(r.clv)}">${r.clv != null ? pct(r.clv) : "–"}</span>`, r.clv)}${td(r.beat != null ? Math.round(r.beat * 100) + "%" : "–", r.beat)}</tr>`));
}

// The fast read: results take hundreds of picks to say anything; CLV (did the closing sharp price move our way) carries
// the same signal with far less noise, so it is the early indicator. Verdicts per sport and tier need 15+ graded picks and
// |t| >= 1.5 on CLV; "picks to know" is how many more it takes for the current average to reach t = 2.
const DAY14 = () => new Date(Date.now() - 14 * 864e5).toISOString().slice(0, 10);
function verdict(c, n) {
  if (n < 15 || c.t == null) return `<span class="pill">too early</span>`;
  return c.t >= 1.5 ? `<span class="pill pos">working</span>` : c.t <= -1.5 ? `<span class="pill neg">not working</span>` : `<span class="pill">unclear</span>`;
}
function toKnow(c) {
  if (c.m == null || !c.sd || !c.m) return "–";
  const need = Math.max(15, Math.ceil((2 * c.sd / Math.abs(c.m)) ** 2));     // never fewer than the 15 the verdict needs
  return need <= c.n ? "known" : `~${need - c.n} more`;
}
function fastRead(g) {
  const recent = g.filter((p) => (day(p) || "") >= DAY14());
  const block = (list, lab) => {
    const raw = meanCI(list.filter(clvOk).map((p) => p.clv)), f = meanCI(list.map(flatOf));
    const cc = meanCI(list.filter((p) => clvOk(p) && p.clvCal != null).map((p) => p.clvCal)), c = cc.m != null ? cc : raw;
    const beat = list.filter(clvOk), bt = beat.length ? beat.filter((p) => p.clv > 0).length / beat.length : null;
    return `<div class="kpi"><div class="k">${lab}</div><div class="v ${cls(c.m)}">${c.m != null ? pct(c.m) : "–"} <span class="tiny muted">CLV</span></div>
      <div class="s">${list.length} picks${c === cc && raw.m != null ? ` · raw ${pct(raw.m)}` : ""} · beat the close ${bt != null ? Math.round(bt * 100) + "%" : "–"} · t ${fmtT(c.t)} · flat ROI ${f.m != null ? pct(f.m) : "–"} · ${toKnow(c)}</div></div>`;
  };
  const groups = (fn) => { const m = new Map(); g.forEach((p) => { const k = fn(p); if (!m.has(k)) m.set(k, []); m.get(k).push(p); }); return [...m].sort((a, b) => b[1].length - a[1].length); };
  const rows = (fn, lab) => sortTable([lab, "Picks", "CLV cal.", "t", "CLV raw", "Beat close", "Flat 1u ROI", "Verdict", "Picks to know"], groups(fn).map(([k, list]) => {
    const raw = meanCI(list.filter(clvOk).map((p) => p.clv)), f = meanCI(list.map(flatOf)), b = list.filter(clvOk);
    const cc = meanCI(list.filter((p) => clvOk(p) && p.clvCal != null).map((p) => p.clvCal)), c = cc.n >= Math.min(15, raw.n) && cc.m != null ? cc : raw;
    const bt = b.length ? b.filter((p) => p.clv > 0).length / b.length : null;
    return `<tr><td>${esc(k)}</td>${td(list.length, list.length)}${td(`<span class="${cls(c.m)}">${c.m != null ? pct(c.m) : "–"}${c === raw ? "*" : ""}</span>`, c.m)}${td(fmtT(c.t), c.t)}${td(raw.m != null ? pct(raw.m) : "–", raw.m)}${td(bt != null ? Math.round(bt * 100) + "%" : "–", bt)}
      ${td(`<span class="${cls(f.m)}">${f.m != null ? pct(f.m) : "–"}</span>`, f.m)}<td>${verdict(c, list.length)}</td><td>${toKnow(c)}</td></tr>`;
  }));
  return `<div class="sec-title">Fast read<span class="line"></span></div>
    <div class="kpis">${block(recent, "Last 14 days")}${block(g, "All graded picks")}</div>
    <div class="card"><b class="small">By sport</b>${rows((p) => p.league || "?", "Sport")}</div>
    <div class="card" style="margin-top:10px"><b class="small">By tier</b>${rows(dimFn.tier, "Tier")}</div>
    <p class="note">Every tier is graded, including leans (0.1u) and likely calls (0u), at the first real venue price. CLV settles fast: a few dozen picks show whether we beat the closing market, where profit needs hundreds. "Working" or "not working" means CLV with t of 1.5 or more either way on 15+ picks. "Picks to know" is how many more it takes for the current average to reach t = 2. The pick runs read this same record and cut back a sport or tier that isn't working.</p>`;
}

export function picksDeep() {
  const g = [...S.picks.values()].filter((p) => G.includes(p.status)).sort((a, b) => (a.commence || "").localeCompare(b.commence || ""));
  if (!g.length) return `<div class="card note">The deep view fills in once picks are graded.</div>`;
  const A = agg(g), boot = bootRatio(g, (p) => p.profit || 0, (p) => p.units || 0), C = meanCI(g.filter(clvOk).map((p) => p.clv));
  const real = g.filter((p) => p.gradeSrc === "first seen" || p.gradeSrc === "posted").length;
  // forecasts scored
  const sc = g.filter((p) => p.status !== "push" && p.fair != null), ys = sc.map((p) => (p.status === "won" ? 1 : 0));
  const three = sc.filter((p) => p.marketFair != null && p.modelP != null), y3 = three.map((p) => (p.status === "won" ? 1 : 0));
  const row3 = (lab, key) => { const ps = three.map((p) => p[key]); return `<tr><td>${lab}</td>${td(mean(ps) != null ? prob(mean(ps)) : "–", mean(ps))}${td(logLoss(ps, y3)?.toFixed(4) ?? "–", logLoss(ps, y3))}${td(brier(ps, y3)?.toFixed(4) ?? "–", brier(ps, y3))}</tr>`; };
  const cf = calibFit(sc.map((p) => p.fair), ys);
  const hit = wilson(ys.reduce((a, b) => a + b, 0), ys.length);
  // by day
  const dm = new Map();
  g.forEach((p) => { const d = day(p), x = dm.get(d) || { v: 0, n: 0, exp: 0, picks: [] }; x.v += p.profit || 0; x.n++; x.exp += expU(p) || 0; x.picks.push(p); dm.set(d, x); });
  dm.forEach((x, d) => { x.tip = `${d}: ${u(x.v)} on ${x.n} picks · expected ${u(x.exp)}`; });
  const dk = [...dm.keys()].sort();
  let cu = 0, ce = 0; const run = dk.map((d, i) => { cu += dm.get(d).v; return { i, y: cu, tip: `${d}: ${u(dm.get(d).v)} · total ${u(cu)}` }; }), er = dk.map((d, i) => { ce += dm.get(d).exp; return { i, y: ce }; });
  const sel = V.day && dm.get(V.day);
  const by = new Map(); g.forEach((p) => { const k = dimFn[V.dim](p); if (!by.has(k)) by.set(k, []); by.get(k).push(p); });
  let keys = [...by.keys()]; const ord = ORDER[V.dim]; if (ord) keys = ord.filter((k) => by.has(k)).concat(keys.filter((k) => !ord.includes(k)));
  const line = (p) => `<div class="an-mini"><span></span><span class="ellip">${esc(p.selection)}</span><span class="tiny muted">${odds(p.gradedAt)} · ${esc(p.tier)}</span><b class="num ${cls(p.profit)}">${u(p.profit)}</b></div>`;
  return `<div class="kpis" style="margin-top:4px">
      <div class="kpi"><div class="k">ROI on units risked</div><div class="v ${cls(A.roi)}">${pct(A.roi)}</div><div class="s">95% ${boot.lo != null ? `${pct(boot.lo, 0)} to ${pct(boot.hi, 0)}` : "–"} (bootstrap) · ${A.risk.toFixed(2)}u risked</div></div>
      <div class="kpi"><div class="k">Expected at our fair</div><div class="v ${cls(A.exp)}">${u(A.exp)}</div><div class="s">luck ${u(A.luck)} · z ${fmtT(A.z)} · p ${fmtP(pTwo(A.z))}</div></div>
      <div class="kpi"><div class="k">Hit rate</div><div class="v">${hit.p != null ? Math.round(hit.p * 100) + "%" : "–"}</div><div class="s">95% ${hit.lo != null ? `${Math.round(hit.lo * 100)}-${Math.round(hit.hi * 100)}%` : "–"} · our fair said ${sc.length ? prob(mean(sc.map((p) => p.fair))) : "–"}</div></div>
      <div class="kpi"><div class="k">CLV</div><div class="v ${cls(C.m)}">${C.m != null ? pct(C.m) : "–"}</div><div class="s">t ${fmtT(C.t)} · p ${fmtP(C.p)} · n ${C.n}</div></div>
      <div class="kpi"><div class="k">Graded at real prices</div><div class="v">${real} of ${g.length}</div><div class="s">the rest at good-at (before price checks)</div></div>
    </div>
    ${fastRead(g)}
    <div class="sec-title">Units by day<span class="line"></span></div>
    <div class="card">${calendar(new Map([...dm].map(([d, x]) => [d, { v: x.v, n: x.n, tip: x.tip }])), { fmt: u, short: (x) => `${x >= 0 ? "+" : "-"}${Math.abs(x).toFixed(1)}`, sel: V.day })}
      ${sel ? `<div class="card" style="margin-top:8px;background:var(--panel-2)"><b>${esc(V.day)}</b> · ${u(sel.v)} on ${sel.n} picks (expected ${u(sel.exp)})${sel.picks.map(line).join("")}</div>` : ""}
      ${lineChart([{ pts: run, color: "var(--accent)" }, { pts: er, color: "var(--muted)", dash: true }], { h: 170, fmt: (x) => `${x.toFixed(1)}u`, labels: dk.map((d, i) => (i % Math.ceil(dk.length / 7) === 0 ? d.slice(5) : "")) })}
      ${legend([["var(--accent)", "units, cumulative by game day"], ["var(--muted)", "expected at our fair and the graded price", true]])}</div>
    <div class="sec-title">Were the forecasts right?<span class="line"></span></div>
    <div class="an-grid2"><div class="card">${relChart([{ name: "Our fair", color: "var(--accent)", bins: relBins(sc.map((p) => p.fair), ys, 5) }, { name: "Market", color: "var(--muted)", bins: relBins(three.map((p) => p.marketFair), y3, 5) }])}
        ${legend([["var(--accent)", "our fair"], ["var(--muted)", "sharp market"]])}</div>
      <div class="card"><b class="small">Same ${three.length} picks, three forecasts</b>
        <div style="overflow-x:auto"><table class="l10t an-t"><thead><tr><th></th><th>Mean forecast</th><th>Log loss</th><th>Brier</th></tr></thead><tbody>${row3("Our fair (blend)", "fair")}${row3("Sharp market", "marketFair")}${row3("Our model", "modelP")}
          <tr><td>What happened</td>${td(three.length ? prob(mean(y3)) : "–", mean(y3))}<td></td><td></td></tr></tbody></table></div>
        <p class="note">Lower is better. A pick is chosen because our number is above the market's, so if the market scores better here, the gap we bet on was mostly our error (selection bias). Calibration slope of our fair: ${cf ? `b ${cf.b.toFixed(2)}${cf.seB != null ? ` ± ${(1.96 * cf.seB).toFixed(2)}` : ""}, a ${cf.a.toFixed(2)}` : "–"} (b = 1, a = 0 is honest).</p></div></div>
    <div class="sec-title">Breakdown explorer<span class="line"></span></div>
    <div class="card">${chipBar("pkdim", DIMS, V.dim)}${table(keys.map((k) => [k, agg(by.get(k))]), DIMS.find((d) => d[0] === V.dim)[1])}
      <p class="note">Expected is at our fair and the graded price; luck and z compare the result with it. Checks and pillars are as posted. Small groups swing wildly: read the z and the counts.</p></div>
    <div class="sec-title">Every graded pick<span class="line"></span></div>
    <div class="card">${sortTable(["Day", "Pick", "Tier", "Units", "Graded at", "Venue", "Fair", "Market", "Model", "Close", "CLV", "Result"], g.slice().reverse().map((p) => `<tr><td>${esc(day(p) || "")}</td><td class="ellip" style="max-width:260px;text-align:left" title="${esc(p.selection)}">${esc(p.selection)}</td><td>${esc(p.tier)}</td>${td(p.units, p.units)}
      ${td(`${odds(p.gradedAt)}${p.gradeSrc === "goodAt" || !p.gradeSrc ? "*" : ""}`, decOf(p.gradedAt))}<td>${esc(p.gradeVenue || "")}</td>${td(p.fair != null ? prob(p.fair) : "–", p.fair)}${td(p.marketFair != null ? prob(p.marketFair) : "–", p.marketFair)}${td(p.modelP != null ? prob(p.modelP) : "–", p.modelP)}
      ${td(p.closeFair != null ? prob(p.closeFair) : "–", p.closeFair)}${td(`<span class="${cls(p.clv)}">${p.clv != null ? pct(p.clv) : "–"}</span>`, p.clv)}${td(`<span class="${cls(p.profit)}">${u(p.profit)}</span>`, p.profit)}</tr>`))}
      <div class="row" style="justify-content:space-between;margin-top:6px"><span class="tiny muted">* graded at good-at: no real venue price was found for it</span><button class="btn sm ghost" id="pk-csv">Download CSV</button></div></div>`;
}

export function bindPicksDeep(el, rerender) {
  el.querySelectorAll("[data-pkdim]").forEach((b) => b.onclick = () => { V.dim = b.dataset.pkdim; rerender(); });
  el.querySelectorAll("[data-day]").forEach((b) => b.onclick = () => { V.day = V.day === b.dataset.day ? null : b.dataset.day; rerender(); });
  const c = el.querySelector("#pk-csv");
  if (c) c.onclick = () => {
    const g = [...S.picks.values()];
    downloadCsv(`slate-ledger-picks-${ptDay(new Date().toISOString())}.csv`, ["id", "game_day", "selection", "league", "market", "side", "point", "player", "tier", "units", "status", "graded_at", "grade_src", "grade_venue",
      "profit", "flat_1u", "fair", "market_fair", "model_p", "close_fair", "clv", "checks_logic", "checks_model", "checks_price", "pillars_support", "early"],
      g.map((p) => [p.id, day(p), p.selection, p.league, p.market, p.side, p.point, p.player || "", p.tier, p.units, p.status, p.gradedAt, p.gradeSrc || "", p.gradeVenue || "", p.profit, G.includes(p.status) ? flatOf(p) : "", p.fair, p.marketFair, p.modelP,
        p.closeFair, p.clv, p.checks?.logic, p.checks?.model, p.checks?.price, p.pillars ? sup(p) : "", p.early ? 1 : 0]));
  };
  bindSort(el);
  bindTips(el);
}
