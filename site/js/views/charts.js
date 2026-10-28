// Small SVG charts shared by the Ledger's analysis and the Reports page. Every point carries a tooltip (data-tip).
import { esc } from "../util.js";

// lines over a shared x (index); a series may carry a band [{lo, hi}] drawn behind it
export function lineChart(series, { h = 220, fmt = (v) => v.toFixed(2), zero = true, labels = null } = {}) {
  const pts = series.flatMap((s) => s.pts.flatMap((p) => [p.y, ...(s.band ? [s.band[p.i]?.lo, s.band[p.i]?.hi] : [])])).filter((v) => v != null && isFinite(v));
  const nX = Math.max(...series.map((s) => s.pts.length));
  if (nX < 2 || !pts.length) return `<div class="note">Needs a few more settled bets.</div>`;
  const W = 800, P = { l: 56, r: 12, t: 12, b: labels ? 26 : 14 };
  let lo = Math.min(...pts, zero ? 0 : Infinity), hi = Math.max(...pts, zero ? 0 : -Infinity);
  if (hi === lo) { hi += 1; lo -= 1; }
  const X = (i) => P.l + i * (W - P.l - P.r) / (nX - 1), Y = (v) => P.t + (hi - v) * (h - P.t - P.b) / (hi - lo);
  const path = (s) => s.pts.map((p, j) => `${j ? "L" : "M"}${X(p.i).toFixed(1)},${Y(p.y).toFixed(1)}`).join("");
  const band = (s) => { const b = s.pts.map((p) => ({ i: p.i, ...s.band[p.i] })).filter((x) => x.lo != null);
    return b.length ? `<path d="${b.map((x, j) => `${j ? "L" : "M"}${X(x.i).toFixed(1)},${Y(x.hi).toFixed(1)}`).join("")}${[...b].reverse().map((x) => `L${X(x.i).toFixed(1)},${Y(x.lo).toFixed(1)}`).join("")}Z" fill="${s.color}" opacity=".12"/>` : ""; };
  const ticks = [lo, (lo + hi) / 2, hi];
  return `<svg class="chart" viewBox="0 0 ${W} ${h}" role="img">
    ${ticks.map((t) => `<line class="grid-l" x1="${P.l}" x2="${W - P.r}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${P.l - 6}" y="${Y(t) + 4}" text-anchor="end">${esc(fmt(t))}</text>`).join("")}
    ${zero && lo < 0 && hi > 0 ? `<line class="zero" x1="${P.l}" x2="${W - P.r}" y1="${Y(0)}" y2="${Y(0)}"/>` : ""}
    ${series.filter((s) => s.band).map(band).join("")}
    ${series.map((s) => `<path d="${path(s)}" fill="none" stroke="${s.color}" stroke-width="2" ${s.dash ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`).join("")}
    ${series[0].pts.map((p) => `<circle cx="${X(p.i)}" cy="${Y(p.y)}" r="8" fill="transparent" data-tip="${esc(p.tip || fmt(p.y))}"/>`).join("")}
    ${labels ? labels.map((l, i) => l ? `<text x="${X(i)}" y="${h - 6}" text-anchor="middle">${esc(l)}</text>` : "").join("") : ""}
  </svg>`;
}
export const legend = (items) => `<div class="row tiny muted" style="gap:14px;flex-wrap:wrap">${items.map(([c, t, dash]) => `<span><i class="lg-line${dash ? " dash" : ""}" style="${dash ? "" : `background:${c}`}"></i>${esc(t)}</span>`).join("")}</div>`;

// a diverging bar per row (teal right of zero, orange left), value text on the right
export function divRows(rows, { fmt, max } = {}) {
  const m = max || Math.max(0.005, ...rows.map((r) => Math.abs(r.v || 0)));
  return rows.map((r) => `<div class="an-row" ${r.tip ? `data-tip="${esc(r.tip)}"` : ""}><span class="ellip">${r.html || esc(r.k)}</span>
    <div class="eb" style="height:10px"><div class="l">${r.v < 0 ? `<i style="width:${Math.min(100, Math.abs(r.v) / m * 100)}%"></i>` : ""}</div><div class="r">${r.v > 0 ? `<i style="width:${Math.min(100, r.v / m * 100)}%"></i>` : ""}</div></div>
    <span class="num small ${r.v > 0 ? "pos" : r.v < 0 ? "neg" : ""}">${r.v == null ? "–" : fmt(r.v)}</span>${r.n != null ? `<span class="tiny muted num">${r.n}</span>` : ""}</div>`).join("");
}

// chance we gave vs how often it happened, per bucket
export function calibChart(rows) {
  const use = rows.filter((r) => r.n);
  if (!use.length) return `<div class="note">Needs settled bets with a fair price logged.</div>`;
  return `<div class="an-cal">${use.map((r) => `<div class="an-calc" data-tip="${esc(`${r.k}: expected ${Math.round(r.exp * 100)}%, won ${Math.round(r.act * 100)}% of ${r.n}`)}">
      <div class="an-calb"><i class="exp" style="height:${(r.exp * 100).toFixed(0)}%"></i><i class="act ${r.act >= r.exp ? "pos" : "neg"}" style="height:${(r.act * 100).toFixed(0)}%"></i></div>
      <span class="tiny">${esc(r.k)}</span><span class="tiny muted">${r.n}</span></div>`).join("")}</div>
    <div class="row tiny muted" style="gap:14px"><span><i class="lg-dot" style="background:var(--muted)"></i>chance at the price (fair when logged)</span><span><i class="lg-dot" style="background:var(--pos)"></i>how often it won</span></div>`;
}

// a grid of cells: rows x cols, each {v, n}; color by v
export function heat(rowsK, colsK, cell, { fmt, scale = 0.1 } = {}) {
  return `<div style="overflow-x:auto"><table class="an-heat"><thead><tr><th></th>${colsK.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>
    ${rowsK.map((r) => `<tr><th>${esc(r)}</th>${colsK.map((c) => { const x = cell(r, c);
      if (!x || !x.n) return `<td class="na">·</td>`;
      const a = Math.min(1, Math.abs(x.v) / scale) * 0.55 + 0.08;
      return `<td style="background:color-mix(in srgb, var(${x.v >= 0 ? "--pos" : "--neg"}) ${Math.round(a * 100)}%, transparent)" data-tip="${esc(`${r} · ${c}: ${fmt(x.v)} over ${x.n}`)}"><b>${fmt(x.v)}</b><span>${x.n}</span></td>`; }).join("")}</tr>`).join("")}
  </tbody></table></div>`;
}
