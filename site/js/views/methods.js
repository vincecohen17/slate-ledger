// Reports > Methods: every betting method on its own record (engine/methods.py -> rep.methods). Adopted methods can carry
// a bet or floor pick; testing ones only tracked leans; dropped ones none.
import { esc, pct } from "../util.js";

const ST = {
  adopted: ["Adopted", "pos", "Proven on a playable record: picks can be built on it."],
  promising: ["Promising", "s", "Ahead so far but not proven: leans only."],
  testing: ["Testing", "", "Not enough to say yet: leans only, still graded."],
  dropped: ["Dropped", "neg", "Lost with significance: not used for picks."],
};
const sgn = (v) => (v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
const tcls = (t) => (t == null ? "" : t >= 2 ? "pos" : t <= -2 ? "neg" : "");
function spark(xs) {
  if (!xs || xs.length < 3) return "";
  let c = 0;
  const ys = xs.map((x) => (c += x)), lo = Math.min(0, ...ys), hi = Math.max(0, ...ys), w = 80, h = 22, sx = w / (ys.length - 1), sy = (v) => h - 2 - ((v - lo) / (hi - lo || 1)) * (h - 4);
  return `<svg class="mt-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true"><line x1="0" x2="${w}" y1="${sy(0)}" y2="${sy(0)}" class="z"/><polyline points="${ys.map((v, i) => `${(i * sx).toFixed(1)},${sy(v).toFixed(1)}`).join(" ")}" class="${c >= 0 ? "up" : "dn"}"/></svg>`;
}
function rec(s, label) {
  if (!s || !s.n) return `<span class="tiny muted">${label ? `${label}: ` : ""}none yet</span>`;
  return `<span class="num">${s.games ?? "–"} games · ${s.n} sides</span> · ROI <b class="num ${s.roi > 0 ? "pos" : "neg"}">${sgn(s.roi)}</b>${s.roiSE ? `<span class="tiny muted"> ±${(s.roiSE * 100).toFixed(1)}</span>` : ""} · t <b class="num ${tcls(s.roiT)}">${s.roiT ?? "–"}</b>${s.clv != null ? ` · CLV <span class="num">${sgn(s.clv)}</span>${s.clvT != null ? ` (t ${s.clvT})` : ""}` : ""}`;
}
export function methodsReport(M) {
  if (!M?.methods?.length) return `<div class="card note">Builds on the next engine run: every method's record from the edge log and the market card, plus paper bets on the market methods near each start.</div>`;
  const r = M.rules || {};
  const card = (m) => {
    const [lab, cls, tip] = ST[m.status] || [m.status, "", ""];
    const L = m.live || {}, Cc = m.close;
    return `<div class="mt-row">
      <div class="mt-head"><span class="pill ${cls}" title="${esc(tip)}">${esc(lab)}</span><b>${esc(m.name)}</b>${spark(L.spark || Cc?.spark)}</div>
      <div class="tiny muted">${esc(m.blurb)}</div>
      <div class="small">${m.src === "edges" ? `At the flagged price (best venue per side): ${rec(L)}` : m.src === "paper" ? `Possible fills on paper (one-sided), at our price: ${rec(L)}${m.locks && m.locks.n ? ` · locked pairs apart: ${rec(m.locks)}` : ""}${m.paperOnly ? " · paper only, never adopted until real orders run" : ""}` : `Paper bets at the best venue price: ${rec(L)}`}</div>
      ${m.fill ? `<div class="tiny muted">Posted on paper: ${m.fill.filled + (m.fill.filledOpen || 0)} possibly filled, ${m.fill.cancelled} cancelled, ${m.fill.expired} expired, ${m.fill.resting} resting${m.fill.fillRate != null ? ` · fill rate ${Math.round(m.fill.fillRate * 100)}% (estimated ${Math.round((m.fill.predicted || 0) * 100)}%)` : ""}</div>` : ""}
      ${m.src === "market" ? `<div class="small">Earlier, at the closing fair price (no vig): ${rec(Cc)}${m.closeRead ? ` <span class="tiny muted">· ${esc(m.closeRead)}</span>` : ""}</div>` : ""}
      ${L.recent ? `<div class="tiny muted">Last 14 days: ${L.recent.games} games, ROI ${sgn(L.recent.roi)}${(L.leagues || []).length ? ` · ${L.leagues.slice(0, 4).map((x) => `${esc(x.k)} ${sgn(x.roi)} (${x.games})`).join(" · ")}` : ""}</div>` : ""}
    </div>`;
  };
  const groups = ["adopted", "promising", "testing", "dropped"].map((s) => [s, M.methods.filter((m) => m.status === s)]).filter(([, v]) => v.length);
  return `<div class="card"><b>Methods: which ways of betting actually work</b>
    <div class="tiny muted">Each method is a plain rule graded on its own record, one side per game line or prop (the best venue), significance clustered by game.
      Adopted: ${r.adoptGames ?? 100}+ games on a playable record and ROI t ≥ ${r.adoptT ?? 2} (or market CLV t ≥ ${r.adoptT ?? 2} without losing); dropped at t ≤ ${r.dropT ?? -2}.
      Only adopted methods can carry a bet or floor pick. Market methods paper-bet 1u at the best price on your venues in the last ${r.flagMin ?? 90} minutes before the start.
      Edge methods are judged on results only (their CLV is against our own fair).</div>
    ${groups.map(([s, v]) => `<div class="mt-group"><div class="small" style="margin-top:10px"><b>${esc(ST[s][0])}</b> <span class="tiny muted">${esc(ST[s][2])}</span></div>${v.map(card).join("")}</div>`).join("")}
    <div class="tiny muted" style="margin-top:8px">Updated ${M.updated ? esc(new Date(M.updated).toLocaleString()) : ""}.</div>
  </div>`;
}
