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
  return `<div class="card">
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
    ${sortTable(["", "Edges", "Won", "Expected", "Won − expected", "z", "Flat ROI"], ["all", "kept", "muted"].map((k) => { const s = P.muting[k] || {};
      return `<tr><td>${{ all: "All flagged", kept: "Kept (shown and alerted)", muted: "Muted" }[k]}</td>${td((s.n || 0).toLocaleString(), s.n)}${td(s.win != null ? pct(s.win).replace("+", "") : "–", s.win)}${td(s.exp != null ? pct(s.exp).replace("+", "") : "–", s.exp)}
        ${td(`<span class="${(s.gap || 0) >= 0 ? "pos" : "neg"}">${s.gap != null ? `${(s.gap * 100).toFixed(1)} pts` : "–"}</span>`, s.gap)}${td(fmtT(s.z), s.z)}${td(`<span class="${(s.roi || 0) >= 0 ? "pos" : "neg"}">${s.roi != null ? pct(s.roi) : "–"}</span>`, s.roi)}</tr>`; }))}
    ${P.blending?.all ? `<b style="display:block;margin-top:12px">Gated blend weights</b>
    ${sortTable(["", "Rows", "LL market", "LL old blend", "LL new blend", "New vs old", "New vs market"], [["All", P.blending.all], ...Object.entries(P.blending.bySport || {}).map(([k, v]) => [SPORT[k] || k, v])].map(([k, s]) =>
      `<tr><td>${esc(k)}</td>${td(s.n.toLocaleString(), s.n)}${td(s.llMarket.toFixed(4), s.llMarket)}${td(s.llOld.toFixed(4), s.llOld)}${td(s.llNew.toFixed(4), s.llNew)}
        ${td(`<span class="${s.newVsOld.gain >= 0 ? "pos" : "neg"}">${mn(s.newVsOld.gain)}</span> <span class="tiny muted">t ${fmtT(s.newVsOld.t)}</span>`, s.newVsOld.gain)}${td(`<span class="${s.newVsMarket.gain >= 0 ? "pos" : "neg"}">${mn(s.newVsMarket.gain)}</span> <span class="tiny muted">t ${fmtT(s.newVsMarket.t)}</span>`, s.newVsMarket.gain)}</tr>`))}` : ""}
    <p class="note">Both tests replay the live record day by day with only what was known before each day. Old blend = the weight fit on everything before (in-sample); new = that weight only where the walk-forward backtest (before the live period) found a significant gain. mnats per row; positive = better.</p></div>` : ""}`;
}

export function bindModels(el, rerender) {
  el.querySelectorAll("[data-msrc]").forEach((b) => b.onclick = () => { V.src = b.dataset.msrc; rerender(); });
  el.querySelectorAll("[data-mshow]").forEach((b) => b.onclick = () => { V.show = b.dataset.mshow; rerender(); });
  el.querySelectorAll("[data-mg]").forEach((r) => r.onclick = () => { V.focus = r.dataset.mg; rerender(); });
}
