// Shared pieces for the deep analysis pages (Ledger > Analysis, Ledger > Our picks, Reports): a day-by-day calendar,
// a reliability diagram with confidence whiskers, a histogram, sortable tables and CSV export.
import { esc } from "../util.js";

const MON = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

// days: Map("YYYY-MM-DD" -> {v, n, tip}); one month grid per month with data, newest first; sel = the tapped day
export function calendar(days, { fmt, short, sel = null, scale = null } = {}) {
  const keys = [...days.keys()].sort();
  if (!keys.length) return `<div class="note">No settled days yet.</div>`;
  const vals = keys.map((k) => days.get(k).v), sc = scale || Math.max(1e-9, ...vals.map(Math.abs));
  const months = [...new Set(keys.map((k) => k.slice(0, 7)))].sort().reverse();
  const sh = short || fmt;
  return `<div class="cal-wrap">${months.map((m) => {
    const [y, mo] = m.split("-").map(Number), first = new Date(Date.UTC(y, mo - 1, 1)).getUTCDay(), nd = new Date(Date.UTC(y, mo, 0)).getUTCDate();
    const inM = keys.filter((k) => k.startsWith(m)), tot = inM.reduce((a, k) => a + days.get(k).v, 0);
    const up = inM.filter((k) => days.get(k).v > 0.0001).length, dn = inM.filter((k) => days.get(k).v < -0.0001).length;
    const cells = [];
    for (let i = 0; i < first; i++) cells.push(`<div class="cal-d na"></div>`);
    for (let d = 1; d <= nd; d++) {
      const k = `${m}-${String(d).padStart(2, "0")}`, x = days.get(k);
      if (!x) { cells.push(`<div class="cal-d"><span class="cal-n">${d}</span></div>`); continue; }
      // x.proj: open bets' projected profit on that day (shown as ≈; a day with only open bets is outlined, not filled)
      const pOnly = !x.n && x.proj != null, a = Math.min(1, Math.abs(x.v) / sc) * 0.62 + 0.12, col = x.v >= 0 ? "--pos" : "--neg";
      const bg = pOnly ? "" : `background:color-mix(in srgb, var(${col}) ${Math.round(a * 100)}%, transparent)`;
      cells.push(`<button class="cal-d on ${pOnly ? "proj" : ""} ${sel === k ? "sel" : ""}" data-day="${k}" style="${bg}" data-tip="${esc(x.tip || `${k}: ${fmt(x.v)}`)}">
        <span class="cal-n">${d}</span>${pOnly ? "" : `<b class="num">${esc(sh(x.v))}</b>`}${x.proj != null ? `<span class="cal-p num ${x.proj >= 0 ? "pos" : "neg"}">≈${esc(sh(x.proj))}</span>` : ""}${x.n ? `<span class="cal-c">${x.n}</span>` : ""}</button>`);
    }
    return `<div class="cal-m"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><b>${MON[mo - 1]} ${y}</b>
      <span class="tiny muted">${up} up · ${dn} down · <b class="num ${tot >= 0 ? "pos" : "neg"}">${esc(fmt(tot))}</b></span></div>
      <div class="cal-g">${["S", "M", "T", "W", "T", "F", "S"].map((w) => `<div class="cal-w">${w}</div>`).join("")}${cells.join("")}</div></div>`;
  }).join("")}</div>`;
}

// reliability diagram: forecast (x) vs observed (y), one or more series of bins {p, o, lo, hi, n}
export function relChart(series, { h = 300 } = {}) {
  const W = 320, P = 34, S = (v) => P + v * (W - 2 * P), Y = (v) => h - P - v * (h - 2 * P);
  const maxN = Math.max(1, ...series.flatMap((s) => s.bins.map((b) => b.n)));
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return `<svg class="chart rel" viewBox="0 0 ${W} ${h}" role="img">
    ${ticks.map((t) => `<line class="grid-l" x1="${S(0)}" x2="${S(1)}" y1="${Y(t)}" y2="${Y(t)}"/><text x="${P - 5}" y="${Y(t) + 4}" text-anchor="end">${t * 100}%</text>
      <text x="${S(t)}" y="${h - P + 15}" text-anchor="middle">${t * 100}%</text>`).join("")}
    <line x1="${S(0)}" y1="${Y(0)}" x2="${S(1)}" y2="${Y(1)}" stroke="var(--faint)" stroke-dasharray="4 4"/>
    ${series.map((s, si) => s.bins.map((b) => { const dx = (si - (series.length - 1) / 2) * 4;
      return `${b.lo != null ? `<line x1="${S(b.p) + dx}" x2="${S(b.p) + dx}" y1="${Y(b.lo)}" y2="${Y(b.hi)}" stroke="${s.color}" stroke-width="1.5" opacity=".6"/>` : ""}
        <circle cx="${S(b.p) + dx}" cy="${Y(b.o)}" r="${(3 + 5 * Math.sqrt(b.n / maxN)).toFixed(1)}" fill="${s.color}" opacity=".85" data-tip="${esc(`${s.name}: forecast ${(b.p * 100).toFixed(1)}%, happened ${(b.o * 100).toFixed(1)}% (95% ${b.lo != null ? `${(b.lo * 100).toFixed(0)}-${(b.hi * 100).toFixed(0)}%` : "–"}) over ${b.n}`)}"/>`; }).join("")).join("")}
    <text x="${W / 2}" y="${h - 3}" text-anchor="middle">forecast</text>
  </svg>`;
}

// histogram of values with a zero line
export function histogram(vals, { nb = 16, fmt = (v) => v.toFixed(2), lo = null, hi = null, h = 160, color = "var(--accent)" } = {}) {
  if (vals.length < 3) return `<div class="note">Needs a few more values.</div>`;
  const a = lo ?? Math.min(...vals), b = hi ?? Math.max(...vals), w = (b - a) / nb || 1;
  const cnt = Array(nb).fill(0);
  vals.forEach((v) => { cnt[Math.max(0, Math.min(nb - 1, Math.floor((v - a) / w)))]++; });
  const W = 800, P = { l: 30, r: 10, t: 8, b: 24 }, bw = (W - P.l - P.r) / nb, mx = Math.max(...cnt);
  const X = (v) => P.l + (v - a) / (b - a || 1) * (W - P.l - P.r);
  return `<svg class="chart" viewBox="0 0 ${W} ${h}" role="img">
    ${cnt.map((c, i) => `<rect x="${(P.l + i * bw + 1).toFixed(1)}" y="${(P.t + (1 - c / mx) * (h - P.t - P.b)).toFixed(1)}" width="${(bw - 2).toFixed(1)}" height="${(c / mx * (h - P.t - P.b)).toFixed(1)}"
      fill="${a + (i + 0.5) * w >= 0 ? "var(--pos)" : "var(--neg)"}" opacity=".75" data-tip="${esc(`${fmt(a + i * w)} to ${fmt(a + (i + 1) * w)}: ${c}`)}"/>`).join("")}
    ${a < 0 && b > 0 ? `<line class="zero" x1="${X(0)}" x2="${X(0)}" y1="${P.t}" y2="${h - P.b}"/>` : ""}
    ${[a, (a + b) / 2, b].map((v) => `<text x="${X(v)}" y="${h - 6}" text-anchor="middle">${esc(fmt(v))}</text>`).join("")}
  </svg>`;
}

// a table whose headers sort it (numbers from data-v); call bindSort(el) after rendering
export function sortTable(head, rows, { id = "" } = {}) {
  return `<div style="overflow-x:auto"><table class="l10t an-t st" ${id ? `id="${id}"` : ""}><thead><tr>${head.map((h, i) => `<th data-si="${i}" title="sort">${h}</th>`).join("")}</tr></thead>
    <tbody>${rows.join("")}</tbody></table></div>`;
}
export function bindSort(el) {
  el.querySelectorAll("table.st").forEach((t) => t.querySelectorAll("th[data-si]").forEach((th) => th.onclick = () => {
    const i = +th.dataset.si, tb = t.tBodies[0], rs = [...tb.rows], dir = th.dataset.dir === "d" ? 1 : -1;
    t.querySelectorAll("th").forEach((x) => delete x.dataset.dir);
    th.dataset.dir = dir === 1 ? "a" : "d";
    const val = (r) => { const c = r.cells[i]; const v = c?.dataset.v; return v != null && v !== "" ? +v : (c?.textContent || "").trim().toLowerCase(); };
    rs.sort((x, y) => { const a = val(x), b = val(y); return (typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b))) * dir; });
    rs.forEach((r) => tb.appendChild(r));
  }));
}
// a cell with a sort value
export const td = (html, v, cls = "num") => `<td class="${cls}" ${v != null && isFinite(v) ? `data-v="${v}"` : ""}>${html}</td>`;

// download rows as a CSV file
export function downloadCsv(name, head, rows) {
  const q = (x) => { const s = x == null ? "" : String(x); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const blob = new Blob([[head, ...rows].map((r) => r.map(q).join(",")).join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
}

// chips to pick one of several views
export const chipBar = (attr, opts, cur) => `<div class="bar" style="margin:8px 0 4px">${opts.map(([k, l]) => `<button class="chip sm ${k === cur ? "on" : ""}" data-${attr}="${k}">${esc(l)}</button>`).join("")}</div>`;
