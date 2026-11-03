// Reports > Model logic: does our model's view of a flagged edge say anything about whether it wins? From every graded
// edge (engine/policytest.py logic_study + the walk-forward rule tests). Two measures of the model's view:
//   logic   — the flag at the time: agrees (model >= price + 1 pt), neutral, disagrees (model < price - 2 pts)
//   support — where the model sits between the venue's price (0) and our fair (1); rows before Nov 3 use the closing model
// Each result splits into how far the fair moved by the close and how the result beat the close.
import { S, loadMstats } from "../store.js";
import { esc, pct } from "../util.js";
import { lineChart, legend, divRows } from "./charts.js";
import { sortTable, td, chipBar } from "./deep.js";
import { fmtT } from "../stats.js";

const V = { size: "5%+", curve: "5%+", where: "leagueLogic" };
const pts = (x) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}`);
const LOGIC = { agrees: "Model agrees", neutral: "Model neutral", disagrees: "Model disagrees", none: "No model number" };

function combine(rows) {
  const n = rows.reduce((s, r) => s + r.n, 0);
  if (!n) return null;
  const w = (k) => rows.reduce((s, r) => s + (r[k] ?? 0) * r.n, 0) / n;
  return { n, gap: w("gap"), roi: w("roi"), move: w("move"), beatClose: w("beatClose") };
}

function statRow(label, s) {
  return `<tr><td>${label}</td>${td(s.n.toLocaleString(), s.n)}
    ${td(`<span class="${s.gap >= 0 ? "pos" : "neg"}">${pts(s.gap)}</span> <span class="tiny muted">z ${fmtT(s.z)}</span>`, s.gap)}
    ${td(pts(s.move), s.move)}${td(`<span class="${s.beatClose >= 0 ? "pos" : "neg"}">${pts(s.beatClose)}</span>`, s.beatClose)}
    ${td(pct(s.clv), s.clv)}${td(`<span class="${s.roi >= 0 ? "pos" : "neg"}">${pct(s.roi)}</span>${s.roiSE != null ? ` <span class="tiny muted">± ${(s.roiSE * 196).toFixed(0)}</span>` : ""}`, s.roi)}</tr>`;
}
const HEAD = ["", "Edges", "Won − fair (pts)", "Fair moved by close", "Beat the close", "CLV", "Flat ROI ± 95%"];

export function logicDeep() {
  loadMstats();
  const M = S.mstats;
  const L = M?.policy?.logic, P = M?.policy;
  if (!M) return `<div class="card note">Loading…</div>`;
  if (!L) return `<div class="card note">The logic study is computed with the next engine run.</div>`;
  const curve = (L.supCurve || []).find((c) => c.size === "5%+")?.rows || [];
  const between = combine(curve.filter((r) => r.k === "0 – 0.5" || r.k === "0.5 – 1"));
  const past = combine(curve.filter((r) => r.k === "1.5 – 2.5" || r.k === "past 2.5x"));
  const under = curve.find((r) => r.k.startsWith("under"));
  const kpi = (k, v, s, cls) => `<div class="kpi"><div class="k">${k}</div><div class="v ${cls}">${v}</div><div class="s">${s}</div></div>`;
  const rule = (x) => x?.kept ? `${pct(x.kept.roi)} on ${x.kept.n.toLocaleString()}` : "–";
  const sizeRows = (L.logicSize || []).find((t) => t.size === V.size)?.rows || [];
  const curveRows = (L.supCurve || []).find((c) => c.size === V.curve)?.rows || [];
  const whereRows = (L[V.where] || []).filter((r) => r.n >= 20);
  const daily = (L.daily || []).filter((d) => d.agrees != null || d.disagrees != null || d.support != null);
  const series = (k, color, dash) => ({ pts: daily.map((d, i) => ({ i, y: d[k] == null ? null : d[k] * 100, tip: `${d.d}: ${k} ${pts(d[k])} pts over ${d["n" + { agrees: "A", disagrees: "D", support: "S", against: "O" }[k]]}` })).filter((p) => p.y != null), color, dash });
  return `<div class="card">
    <div class="kpis">
      ${L.matched ? kpi("Agrees minus disagrees, same league, line type and size", `${pts(L.matched.diff)} pts`, `z ${fmtT(L.matched.z)} over ${L.matched.strata} matched groups: the mix of edges can't explain it`, L.matched.diff >= 0 ? "pos" : "neg") : ""}
      ${between ? kpi("5%+ edges, model between price and fair", `${pts(between.gap)} pts`, `won vs fair · flat ROI ${pct(between.roi)} · ${between.n} edges`, between.gap >= 0 ? "pos" : "neg") : ""}
      ${past ? kpi("5%+ edges, model 1.5x+ past the fair", `${pts(past.gap)} pts`, `flat ROI ${pct(past.roi)} · ${past.n} edges: a shared error, not support`, past.gap >= 0 ? "pos" : "neg") : ""}
      ${under ? kpi("5%+ edges, model under the price", `${pts(under.gap)} pts`, `flat ROI ${pct(under.roi)} · ${under.n} edges`, under.gap >= 0 ? "pos" : "neg") : ""}
    </div>
    <p class="note"><b>How the model votes.</b> For each flagged price we compare our model's chance with the venue's price (what you pay) and our sharp fair.
      <b>Logic</b> is the flag on the edge: agrees when the model is at least 1 pt above the price, disagrees when it's 2+ pts under.
      <b>Support</b> is sharper: where the model sits on the line from the price (0) to the fair (1).</p>
    <div class="lg-scale"><span class="neg">under the price<br><b>&lt; 0</b></span><span class="pos">between price and fair<br><b>0 – 1</b></span><span>near the fair<br><b>1 – 1.5</b></span><span class="neg">past the fair<br><b>1.5+</b></span></div>
    <p class="note">Between 0 and 1, the model and the fair both say the price is cheap and the model is the cautious one: that is where 5%+ edges win.
      Past 1.5 the model is even more bullish than a fair that already shows a big edge. That mostly happens on alternate lines whose fair is built from the same model shape,
      so the two share an error. Those edges lose like edges the model fights.</p>
  </div>
  <div class="card" style="margin-top:10px"><b>Logic by edge size</b>
    ${chipBar("lgsize", [["all", "All edges"], ["under 5%", "Under 5%"], ["5%+", "5%+"]], V.size)}
    ${sortTable(HEAD, sizeRows.map((r) => statRow(esc(LOGIC[r.k] || r.k), r)))}
    <p class="note">Won − fair is wins minus what our fair at the flag expected, in points per edge. It splits exactly into how far the fair moved by the close (the market coming to us)
      plus how the result beat the closing fair. Most of the logic signal is in the second part: the model spots fairs that stay wrong through the close (bad alt-line shapes, thin markets), more than it predicts line moves.</p></div>
  <div class="card" style="margin-top:10px"><b>Support curve</b> <span class="tiny muted">(${(L.supRows || 0).toLocaleString()} graded edges with a model number; ${(L.supFlag || 0).toLocaleString()} measured at the flag, the rest from the closing model)</span>
    ${chipBar("lgcurve", [["5%+", "5%+ edges"], ["under 5%", "Under 5%"]], V.curve)}
    ${divRows(curveRows.map((r) => ({ k: r.k, v: r.gap, n: r.n, tip: `${r.k}: won ${pts(r.gap)} pts vs fair, ROI ${pct(r.roi)}, ${r.n} edges` })), { fmt: (v) => `${pts(v)} pts` })}
    ${sortTable(HEAD, curveRows.map((r) => statRow(esc(r.k), r)))}</div>
  <div class="card" style="margin-top:10px"><b>Where it holds</b> <span class="tiny muted">(5%+ edges)</span>
    ${chipBar("lgwhere", [["leagueLogic", "Sport x logic"], ["leagueBand", "Sport x support"], ["kindBand", "Line type x support"], ["srcBand", "Flag vs closing model"]], V.where)}
    <div class="tiny muted">Groups with 20+ graded edges.</div>
    ${sortTable(HEAD, whereRows.map((r) => statRow(esc(r.k), r)))}
    <p class="note">NBA is where the model's vote is worth the most. In the NFL the model's game lines were weak and its agreement didn't help, so the override never brings NFL edges back on logic alone.
      "Flag vs closing model" separates support measured when the edge was flagged (from Nov 3) from the backfill that used the model at the close; trust the first as it grows.</p></div>
  ${daily.length >= 3 ? `<div class="card" style="margin-top:10px"><b>Day by day</b> <span class="tiny muted">(5%+ edges, won − fair in pts; days with 5+ edges in a group)</span>
    ${lineChart([series("agrees", "var(--pos)"), series("disagrees", "var(--neg)"), series("support", "var(--accent)", true), series("against", "var(--muted)", true)], { h: 180, fmt: (x) => x.toFixed(0), labels: daily.map((d) => d.d.slice(5)) })}
    ${legend([["var(--pos)", "model agrees"], ["var(--neg)", "model disagrees"], ["var(--accent)", "support 0 – 1.5", true], ["var(--muted)", "support under 0 or past 1.5", true]])}</div>` : ""}
  ${P?.mutingLogic ? `<div class="card" style="margin-top:10px"><b>What the rules do</b> <span class="tiny muted">(walk-forward: each day's rule only knows earlier days)</span>
    ${sortTable(["Rule", "Edges kept", "Kept: won − fair", "Kept: flat ROI", "Muted: flat ROI"], [["Mute losing edge types", P.muting], ["+ logic override", P.mutingLogic], ["+ support override", P.mutingSupport]].filter(([, x]) => x).map(([k, x]) =>
      `<tr><td>${k}</td>${td(x.kept.n.toLocaleString(), x.kept.n)}${td(`${pts(x.kept.gap)} pts`, x.kept.gap)}${td(`<span class="${x.kept.roi >= 0 ? "pos" : "neg"}">${pct(x.kept.roi)}</span>`, x.kept.roi)}${td(`<span class="${(x.muted.roi || 0) >= 0 ? "pos" : "neg"}">${pct(x.muted.roi)}</span>`, x.muted.roi)}</tr>`))}
    <p class="note">The logic override un-mutes a 5%+ edge the model agrees with once its sport's record for those edges holds its fair over 100+ graded, and mutes one it disagrees with once that record has lost by 2+ SE.
      The support override does the same by support band (between / near come back, under / past are muted), and wins over the logic flag when the edge has a model number.
      Kept ${rule(P.mutingLogic)} with logic, ${rule(P.mutingSupport)} with support so far. The support cells need 100 graded edges each before they act, so they start mostly on the logic rule and take over as flag-time numbers accumulate.</p></div>` : ""}`;
}

export function bindLogic(el, rerender) {
  el.querySelectorAll("[data-lgsize]").forEach((b) => b.onclick = () => { V.size = b.dataset.lgsize; rerender(); });
  el.querySelectorAll("[data-lgcurve]").forEach((b) => b.onclick = () => { V.curve = b.dataset.lgcurve; rerender(); });
  el.querySelectorAll("[data-lgwhere]").forEach((b) => b.onclick = () => { V.where = b.dataset.lgwhere; rerender(); });
}
