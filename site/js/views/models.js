// Reports > Models in depth: our model's probabilities against the closing sharp market, per market, from the graded
// tracking rows (engine/modelstats.py): log-loss skill with clustered standard errors, the walk-forward (out-of-sample)
// gain from blending the calibrated model into the market, reliability, Murphy decomposition, disagreement buckets and
// a daily series; plus the walk-forward test of the decision rules (edge muting, gated blend weights).
import { S, loadMstats } from "../store.js";
import { esc, pct, ago } from "../util.js";
import { lineChart, legend } from "./charts.js";
import { relChart, sortTable, td, chipBar } from "./deep.js";
import { fmtT } from "../stats.js";

const SPORT = { basketball_nba: "NBA", basketball_wnba: "WNBA", icehockey_nhl: "NHL", americanfootball_nfl: "NFL", americanfootball_ncaaf: "NCAAF", baseball_mlb: "MLB",
  soccer_epl: "EPL", soccer_usa_mls: "MLS", soccer_italy_serie_a: "Serie A", soccer_spain_la_liga: "La Liga", soccer_germany_bundesliga: "Bundesliga",
  soccer_france_ligue_one: "Ligue 1", soccer_mexico_ligamx: "Liga MX", "*": "All sports" };
// every model, decision-rule, pick-rule and measurement change, with what the record did in the two weeks either side
const KIND = { model: ["Model", "s"], logic: ["Edge logic", ""], picks: ["Pick rules", "pos"], measure: ["Measurement", ""] };
function changesAfter(M) {
  const C = M.changes || [], T = M.timeline || [];
  if (!C.length) return "";
  const pp = (x) => (x == null ? "–" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)}`);
  const cell = (e, unit = "") => {
    if (!e || (!e.before && !e.after)) return `<td class="num">–</td>`;
    const b = e.before?.m, a = e.after?.m, d = e.diff;
    return `<td class="num" data-v="${d?.d ?? ""}"><span class="tiny muted">${pp(b)}${unit} → </span><b class="${d?.d > 0 ? "pos" : d?.d < 0 ? "neg" : ""}">${pp(a)}${unit}</b>${d?.t != null ? `<div class="tiny ${Math.abs(d.t) >= 2 ? "" : "muted"}">t ${d.t > 0 ? "+" : ""}${d.t}${e.after?.n != null ? ` · n ${e.after.n}` : ""}</div>` : e.after?.n != null ? `<div class="tiny muted">n ${e.after.n}</div>` : ""}</td>`;
  };
  // timeline: each game day's edges (calibrated CLV and won-vs-expected) and our picks, with the changes marked
  const idx = Object.fromEntries(T.map((x, i) => [x.d, i]));
  const byDay = new Map(); C.forEach((c) => { const i = idx[c.date.slice(0, 10)]; if (i == null) return; if (!byDay.has(i)) byDay.set(i, []); byDay.get(i).push(c); });
  const marks = [...byDay].map(([i, cs]) => ({ i, label: cs.length > 1 ? `${cs.length} changes` : cs[0].area, tip: `${C.length ? T[i].d : ""}: ${cs.map((c) => c.area).join(" · ")}` }));
  const chart = T.length >= 3 ? `${lineChart([
      { pts: T.map((x, i) => ({ i, y: x.gap ?? 0, tip: `${x.d}: edges won ${pp(x.gap)} pts vs their fair (${x.ng})` })), color: "var(--accent)" },
      { pts: T.map((x, i) => ({ i, y: x.clvCal ?? 0 })), color: "var(--muted)", dash: true },
      { pts: T.filter((x) => x.pclv != null).map((x) => ({ i: idx[x.d], y: x.pclv })), color: "var(--pos)" }],
    { h: 190, fmt: (x) => `${(x * 100).toFixed(0)}`, marks, labels: T.map((x, i) => (i % Math.ceil(T.length / 7) === 0 ? x.d.slice(5) : "")) })}
    ${legend([["var(--accent)", "edges: won vs expected (pts)"], ["var(--muted)", "edges: calibrated CLV (%)", true], ["var(--pos)", "our picks: CLV (%)"]])}` : "";
  return `<div class="card" style="margin-bottom:10px"><b>What changed, and what happened after</b>
    <span class="tiny muted"> · ${C.length} changes · each compared with the ${C[0].window || 14} days before (only the sports and markets it touched)</span>
    ${chart}
    ${sortTable(["Date", "Change", "Edges: won vs fair", "Edges: CLV cal.", "Edges: ROI", "Picks: CLV", "Picks: flat ROI", "Model vs close"], C.slice().reverse().map((c) => {
      const e = c.effects || {}, k = KIND[c.kind] || [c.kind, ""];
      return `<tr><td class="nowrap" data-v="${esc(c.date)}">${esc(c.date.slice(5, 10))}</td><td style="text-align:left;min-width:220px"><span class="pill ${k[1]}">${esc(k[0])}</span> <b>${esc(c.area)}</b>
        <div class="tiny muted" style="max-width:380px;white-space:normal">${esc(c.change)}${c.through ? ` · after = through ${esc(c.through.slice(5))}` : ""}</div></td>
        ${c.kind === "picks" ? `<td class="num">–</td><td class="num">–</td><td class="num">–</td>` : `${cell(e.edges?.gap, "")}${cell(e.edges?.clvCal, "%")}${cell(e.edges?.roi, "%")}`}${cell(e.picks?.clv, "%")}${cell(e.picks?.flat, "%")}${c.kind === "model" ? cell(e.model?.skill, "") : `<td class="num">–</td>`}</tr>`;
    }))}
    <p class="note">Before → after for each change: edges' win rate minus their fair (points), calibrated CLV and flat ROI; our picks' CLV and flat return; and the model's log-loss edge over the closing line (×100, above 0 = the model beat the close). t over about 2 is a real difference. Changes made the same day share their windows, so read them together, and the "after" side fills in over two weeks. Edges on the same game aren't independent, so a t near 2 on edges is weaker than it looks.</p></div>`;
}

export function groupName(g) {
  const [sp, kind, mk] = g.split("|");
  const s = SPORT[sp] || sp;
  if (kind === "*") return `${s} · everything`;
  if (mk === "*") return `${s} · all ${kind === "prop" ? "props" : "game lines"}`;
  return `${s} · ${mk.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ")}`;
}
const mn = (x) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${(x * 1000).toFixed(2)}`);      // millinats per row
const verdict = (t) => (t == null ? `<span class="ver">too few days</span>` : t >= 2 ? `<span class="ver pos">model adds value</span>` : t <= -2 ? `<span class="ver neg">model hurts</span>` : `<span class="ver">no clear effect</span>`);

const V = { src: "live", focus: "basketball_nba|prop|player_points", show: "groups" };

export function modelsDeep() {
  loadMstats();
  const M = S.mstats;
  if (!M) return `<div class="card note">Loading the model statistics…</div>`;
  const rows = (M.sources?.[V.src] || []);
  if (!rows.length) return `<div class="card note">No graded rows for this source yet.</div>`;
  const pooled = rows.filter((r) => r.group.includes("*")), single = rows.filter((r) => !r.group.includes("*"));
  const top = ["*|*|*", "*|prop|*", "*|line|*"].map((g) => rows.find((r) => r.group === g)).filter(Boolean);
  const f = rows.find((r) => r.group === V.focus) || single[0] || rows[0];
  const table = (list) => sortTable(["Market", "Rows", "Days", "Log loss: model", "market", "Skill ± SE", "t", "Blend gain (OOS) ± SE", "t", "Slope b", "w", "Verdict"],
    list.map((r) => `<tr data-mg="${esc(r.group)}" style="cursor:pointer" class="${r.group === f.group ? "sel" : ""}"><td>${esc(groupName(r.group))}</td>${td(r.n.toLocaleString(), r.n)}${td(r.days, r.days)}
      ${td(r.llModel.toFixed(4), r.llModel)}${td(r.llMarket.toFixed(4), r.llMarket)}
      ${td(`<span class="${r.skill > 0 ? "pos" : "neg"}">${mn(r.skill)}</span>${r.skillSE != null ? ` <span class="tiny muted">± ${(r.skillSE * 1000).toFixed(2)}</span>` : ""}`, r.skill)}${td(fmtT(r.skillT), r.skillT)}
      ${td(r.blendGain != null ? `<span class="${r.blendGain > 0 ? "pos" : "neg"}">${mn(r.blendGain)}</span>${r.blendGainSE != null ? ` <span class="tiny muted">± ${(r.blendGainSE * 1000).toFixed(2)}</span>` : ""}` : "–", r.blendGain)}${td(fmtT(r.blendGainT), r.blendGainT)}
      ${td(r.fit ? r.fit.b.toFixed(2) : "–", r.fit?.b)}${td(r.fit ? `${Math.round(r.fit.w * 100)}%` : "–", r.fit?.w)}<td>${verdict(r.blendGainT)}</td></tr>`));
  const mur = (m) => m ? `REL ${m.rel.toFixed(4)} · RES ${m.res.toFixed(4)} · UNC ${m.unc.toFixed(4)}` : "–";
  const P = M.policy;
  const btBy = Object.fromEntries((M.sources?.bt || []).map((r) => [r.group, r]));
  const effects = changesAfter(M);
  const mc = (M.changes || []).filter((c) => (c.groups || []).length);
  const changes = effects + (mc.length ? `<div class="card" style="margin-bottom:10px"><b>Model fixes, backtested</b> <span class="tiny muted">(backtest log loss on the closing lines, lower is better; a coin flip is 0.693)</span>
    ${sortTable(["Date", "What changed", "Market", "Before", "After", "Now", "Market (close)"], mc.flatMap((c) => (c.groups.length ? c.groups : [null]).map((g, i) => { const now = g ? btBy[g.g] : null;
      return `<tr><td class="nowrap">${i ? "" : esc(c.date.slice(5))}</td><td style="text-align:left">${i ? "" : `<b>${esc(c.area)}</b><div class="tiny muted" style="max-width:420px;white-space:normal">${esc(c.change)}</div>`}</td>
        <td style="text-align:left">${g ? esc(groupName(g.g)) : "–"}</td>${td(g ? g.before.toFixed(4) : "–", g?.before)}${td(g ? `<span class="${g.after < g.before ? "pos" : "neg"}">${g.after.toFixed(4)}</span>` : "–", g?.after)}
        ${td(now ? now.llModel.toFixed(4) : "–", now?.llModel)}${td(now ? now.llMarket.toFixed(4) : "–", now?.llMarket)}</tr>`; })))}
    <p class="note">Before and after come from rerunning the full walk-forward backtest with the fix; "Now" is the current backtest. These models still lose to the close, so their weight in fairs stays 0 until the out-of-sample blend gain clears 2 SE. The fixes matter for the model's vote on edges (Model logic, above).</p></div>` : "");
  return `${changes}<div class="card">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><b>Our model against the closing market</b><span class="tiny muted">computed ${M.at ? ago(M.at) : ""}</span></div>
    ${chipBar("msrc", [["live", "Live record"], ["bt", "Backtests (walk-forward replays)"]], V.src)}
    <div class="kpis">${top.map((r) => `<div class="kpi"><div class="k">${esc(groupName(r.group))}</div><div class="v ${r.skill > 0 ? "pos" : "neg"}">${mn(r.skill)}</div>
      <div class="s">skill, mnats/row (t ${fmtT(r.skillT)}) · blend gain ${mn(r.blendGain)} (t ${fmtT(r.blendGainT)}) · ${r.n.toLocaleString()} rows, ${r.days} days</div></div>`).join("")}</div>
    <p class="note"><b>Skill</b> is the mean per-row log-loss difference, market minus model, in millinats (×1000): positive means our raw model beat the closing no-vig market.
      <b>Blend gain</b> is the same against the market for the calibrated model blended in at weight w, scored <b>out of sample</b>: every test week uses a recalibration (ridge logistic a, b on logit p) and a w fit only on earlier days.
      Standard errors are cluster-robust: by game where rows carry it, else by day (alternate lines of one player-game are strongly correlated, so naive SEs would be far too small).
      The pick runs only blend the model in where that out-of-sample gain is 2+ SE above zero; elsewhere fair = market.</p>
    ${chipBar("mshow", [["groups", "Each market"], ["pooled", "Pooled by sport and kind"]], V.show)}
    ${table(V.show === "groups" ? single : pooled)}
  </div>
  ${f ? `<div class="card" style="margin-top:10px"><b>${esc(groupName(f.group))}</b> <span class="tiny muted">· ${f.n.toLocaleString()} rows · ${f.days} days (${esc(f.first)} to ${esc(f.last)}) · hit rate ${pct(f.base).replace("+", "")} · SE clustered by ${esc(f.cluster)}</span>
    <div class="an-grid2" style="margin-top:8px"><div>${relChart([{ name: "Model", color: "var(--accent)", bins: (f.relModel || []).filter(Boolean) }, { name: "Market", color: "var(--muted)", bins: (f.relMarket || []).filter(Boolean) }])}
        ${legend([["var(--accent)", "model (raw)"], ["var(--muted)", "closing market"]])}
        <p class="note">Forecast vs what happened, with 95% Wilson whiskers. Brier decomposition (REL lower is better, RES higher is better):<br>model ${mur(f.murphyModel)}<br>market ${mur(f.murphyMarket)}</p></div>
      <div><b class="small">When the model and the market disagree</b>
        ${sortTable(["Model − market", "Rows", "Model", "Market", "Happened", "LL model", "LL market", "Model's side at the close"], (f.buckets || []).filter((b) => b.n).map((b) => `<tr><td>${esc(b.k)}</td>${td(b.n.toLocaleString(), b.n)}${td(pct(b.p).replace("+", ""), b.p)}${td(pct(b.f).replace("+", ""), b.f)}
          ${td(`<b>${pct(b.o).replace("+", "")}</b>`, b.o)}${td(b.llModel.toFixed(3), b.llModel)}${td(b.llMarket.toFixed(3), b.llMarket)}${td(`<span class="${b.roi > 0 ? "pos" : "neg"}">${pct(b.roi)}</span>${b.roiSE != null ? ` <span class="tiny muted">± ${(b.roiSE * 196).toFixed(1)}</span>` : ""}`, b.roi)}</tr>`))}
        <p class="note">The last column is the flat return of betting whichever side the model preferred at the closing no-vig price (± 95%). If the model knew something the close didn't, the big-disagreement rows would be green.</p>
        ${f.oosN ? `<p class="small">Out of sample (${f.oosN.toLocaleString()} rows): calibrated model ${f.llCalOOS.toFixed(4)}, blend ${f.llBlendOOS.toFixed(4)}, market ${f.llMarketOOS.toFixed(4)}. Latest fit: a ${f.fit?.a}, b ${f.fit?.b}, w ${f.fit?.w}. Model vs market correlation ${f.corr ?? "–"} (logits), spread of the gap ${f.sdGap != null ? pct(f.sdGap).replace("+", "") : "–"}.</p>` : ""}</div></div>
    ${(f.daily || []).length >= 3 ? `${lineChart([{ pts: f.daily.map((d, i) => ({ i, y: d.skill * 1000, tip: `${d.d}: skill ${mn(d.skill)} over ${d.n} rows${d.blendGain != null ? ` · blend gain ${mn(d.blendGain)}` : ""}` })), color: "var(--accent)" },
        { pts: f.daily.map((d, i) => ({ i, y: (d.blendGain ?? 0) * 1000 })), color: "var(--pos)" }], { h: 160, fmt: (x) => x.toFixed(1), labels: f.daily.map((d, i) => (i % Math.ceil(f.daily.length / 7) === 0 ? d.d.slice(5) : "")) })}
      ${legend([["var(--accent)", "daily skill (mnats/row)"], ["var(--pos)", "daily out-of-sample blend gain"]])}` : ""}</div>` : ""}
  ${P ? `<div class="sec-title">Did the decision changes help?<span class="line"></span></div><div class="card">
    <b>Muting losing edge kinds</b> <span class="tiny muted">(walk-forward: each day's rule uses only earlier days)</span>
    ${sortTable(["", "Edges", "Won", "Expected", "Won − expected", "z", "Flat ROI"], [["all", P.muting], ["kept", P.muting], ["muted", P.muting], ["kept", P.mutingLogic, true], ["muted", P.mutingLogic, true]].filter(([, src]) => src).map(([k, src, lg]) => { const s = src[k] || {};
      return `<tr><td>${{ all: "All flagged", kept: "Kept (shown and alerted)", muted: "Muted" }[k]}${lg ? " · with the logic override" : ""}</td>${td((s.n || 0).toLocaleString(), s.n)}${td(s.win != null ? pct(s.win).replace("+", "") : "–", s.win)}${td(s.exp != null ? pct(s.exp).replace("+", "") : "–", s.exp)}
        ${td(`<span class="${(s.gap || 0) >= 0 ? "pos" : "neg"}">${s.gap != null ? `${(s.gap * 100).toFixed(1)} pts` : "–"}</span>`, s.gap)}${td(fmtT(s.z), s.z)}${td(`<span class="${(s.roi || 0) >= 0 ? "pos" : "neg"}">${s.roi != null ? pct(s.roi) : "–"}</span>`, s.roi)}</tr>`; }))}
    ${P.blending?.all ? `<b style="display:block;margin-top:12px">Gated blend weights</b>
    ${sortTable(["", "Rows", "LL market", "LL old blend", "LL new blend", "New vs old", "New vs market"], [["All", P.blending.all], ...Object.entries(P.blending.bySport || {}).map(([k, v]) => [SPORT[k] || k, v])].map(([k, s]) =>
      `<tr><td>${esc(k)}</td>${td(s.n.toLocaleString(), s.n)}${td(s.llMarket.toFixed(4), s.llMarket)}${td(s.llOld.toFixed(4), s.llOld)}${td(s.llNew.toFixed(4), s.llNew)}
        ${td(`<span class="${s.newVsOld.gain >= 0 ? "pos" : "neg"}">${mn(s.newVsOld.gain)}</span> <span class="tiny muted">t ${fmtT(s.newVsOld.t)}</span>`, s.newVsOld.gain)}${td(`<span class="${s.newVsMarket.gain >= 0 ? "pos" : "neg"}">${mn(s.newVsMarket.gain)}</span> <span class="tiny muted">t ${fmtT(s.newVsMarket.t)}</span>`, s.newVsMarket.gain)}</tr>`))}` : ""}
    <p class="note">Both tests replay the live record day by day with only what was known before each day. Old blend = the weight fit on everything before (in-sample); new = that weight only where the walk-forward backtest (before the live period) found a significant gain. mnats per row; positive = better.</p>
    ${P.residual ? `<b style="display:block;margin-top:12px">Market-anchored residual model</b> <span class="tiny muted">(${(P.residual.rows || 0).toLocaleString()} graded rows with features)</span>
    ${Object.keys(P.residual.groups || {}).length ? sortTable(["Group", "Rows", "Gain vs close", "t", "Model gap", "Move since open", "Book spread", "Depth", "Mean gap"], Object.entries(P.residual.groups).map(([k, s]) => { const [sp, kind] = k.split("|"); const c = s.coef || {};
      return `<tr><td>${esc(SPORT[sp] || sp)} ${esc(kind === "prop" ? "props" : "lines")}</td>${td((s.n || 0).toLocaleString(), s.n)}${s.gain != null ? `${td(`<span class="${s.gain >= 0 ? "pos" : "neg"}">${mn(s.gain)}</span>`, s.gain)}${td(fmtT(s.t), s.t)}${["gap", "move", "disp", "depth", "meangap"].map((f) => td(c[f] != null ? c[f].toFixed(2) : "–", c[f])).join("")}` : `<td colspan="7" class="muted small">${esc(s.status || "")}</td>`}</tr>`; })) : `<p class="small muted">Collecting: rows start carrying features with games graded from today.</p>`}
    <p class="note">The close plus a fitted nudge from what we knew at the close: the model's gap to it, how far the price moved since open, how much books disagreed, how deep the market was, and the model's mean vs the market's. Fit on earlier days, scored on each new day. It goes into fairs only if it beats the close with t ≥ 2.</p>` : ""}</div>` : ""}`;
}

export function bindModels(el, rerender) {
  el.querySelectorAll("[data-msrc]").forEach((b) => b.onclick = () => { V.src = b.dataset.msrc; rerender(); });
  el.querySelectorAll("[data-mshow]").forEach((b) => b.onclick = () => { V.show = b.dataset.mshow; rerender(); });
  el.querySelectorAll("[data-mg]").forEach((r) => r.onclick = () => { V.focus = r.dataset.mg; rerender(); });
}
