// Reports > Where the money is (engine/oppreport.py -> rep.oppreport): every arb, middle and maker order on its own
// record. When they show up, which venues, sports, markets and lead times carry them, and on paper which maker orders fill
// and pay.
import { esc, $$ } from "../util.js";
import { sortTable, td, chipBar } from "./deep.js";

let arbDim = "byPair", mkDim = "byWhere", cnDim = "byWhere";
const pc = (v, d = 1) => (v == null ? "–" : `${(v * 100).toFixed(d)}%`);
const sg = (v, d = 1) => (v == null ? "–" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`);
const mins = (m) => (m == null ? "–" : m < 90 ? `${Math.round(m)} min` : `${(m / 60).toFixed(1)} h`);
const nd = (v) => (v == null ? "–" : v >= 10 ? Math.round(v) : v);
const dol = (x) => (x == null ? "–" : `$${Math.round(x).toLocaleString()}`);
const hr = (h) => (h === -1 ? "?" : `${(h % 12) || 12} ${h < 12 ? "AM" : "PM"}`);
const VN = { kalshi: "Kalshi", polymarket_us: "Polymarket US", novig: "Novig", prophetx: "ProphetX", bovada: "Bovada", draftkings: "DK Predictions",
  fanduel: "FanDuel Predicts", fliff: "Fliff", courtside: "Courtside" };
const vname = (k) => String(k).split(" · ").map((p) => p.split(" + ").map((x) => VN[x] || x).join(" + ")).join(" · ");
const KIND = { main: "Main lines", alt: "Alts & periods", prop: "Props" };

function hourBars(rows, key, lab) {
  const mx = Math.max(0.01, ...rows.map((r) => r[key] || 0)), w = 100 / 24;
  return `<svg viewBox="0 0 240 64" class="or-hours" role="img" aria-label="${esc(lab)} by hour (Pacific)">
    ${rows.map((r, i) => { const v = r[key] || 0, h = (v / mx) * 48; return `<rect x="${(i * 10 + 1).toFixed(1)}" y="${(50 - h).toFixed(1)}" width="8" height="${h.toFixed(1)}" rx="1.5" class="or-bar" data-tip="${esc(`${hr(r.h ?? r.k)}: ${v} ${lab} a day`)}"/>`; }).join("")}
    ${[0, 6, 12, 18].map((h) => `<text x="${h * 10 + 1}" y="61" text-anchor="start">${hr(h)}</text>`).join("")}
  </svg>`;
}

function arbSection(A, bar) {
  if (!A) return `<div class="card note">Builds as arbs come and go on the board.</div>`;
  const a = A.all || {}, m = A.middles || {};
  const dims = [["byPair", "Venue pair"], ["byVenue", "Venue"], ["byLeague", "Sport"], ["byKind", "Lines or props"], ["byLead", "Time to start"]];
  const rows = (A[arbDim] || []).map((c) => `<tr><td>${esc(arbDim === "byPair" || arbDim === "byVenue" ? vname(c.k) : c.k)}</td>${td(nd(c.c3PerDay), c.c3PerDay)}${td(nd(c.perDay), c.perDay)}
    ${td(pc(c.med), c.med)}${td(pc(c.best), c.best)}${td(mins(c.life3 ?? c.life), c.life3 ?? c.life)}${td(dol(c.stake3 || null), c.stake3 || null)}</tr>`);
  const days = [...(A.byDay || [])].reverse().slice(0, 14).map((d) => `<tr><td>${esc(d.d.slice(5))}</td>${td(d.conf, d.conf)}${td(d.c3, d.c3)}${td(pc(d.best), d.best)}${td(dol(d.money3), d.money3)}${td(d.mid ?? "–", d.mid)}${td(d.free ?? "–", d.free)}</tr>`);
  return `<div class="card">
    <div class="kpis or-k">
      <div class="kpi"><div class="k">Verified arbs a day</div><div class="v num">${nd(a.perDay)}</div><div class="s">median ${pc(a.med)} · last ${A.days} days</div></div>
      <div class="kpi"><div class="k">${Math.round(bar * 100)}%+ a day</div><div class="v num pos">${nd(a.c3PerDay)}</div><div class="s">best ${pc(a.best)}</div></div>
      <div class="kpi"><div class="k">How long a ${Math.round(bar * 100)}%+ one lasts</div><div class="v num">${mins(a.life3)}</div><div class="s">median, first to last seen</div></div>
      <div class="kpi"><div class="k">Stake it takes</div><div class="v num">${dol(a.stake3)}</div><div class="s">median fill, both legs</div></div>
      <div class="kpi"><div class="k">Middles a day</div><div class="v num">${nd(m.perDay)}</div><div class="s">${m.free ?? 0} no-lose · median EV ${pc(m.medEv)}</div></div>
    </div>
    <div class="small" style="margin:10px 0 2px"><b>When to look:</b> verified ${Math.round(bar * 100)}%+ arbs first seen each hour (Pacific), a day</div>
    ${hourBars(A.byHour || [], "c3PerDay", `${Math.round(bar * 100)}%+ arbs`)}
    ${chipBar("ord", dims, arbDim)}
    ${sortTable([`Where`, `${Math.round(bar * 100)}%+ a day`, "Verified a day", "Median", "Best", "Lasts", "Stake"], rows)}
    <details style="margin-top:8px"><summary class="small">Day by day</summary>
      ${sortTable(["Day", "Verified", `${Math.round(bar * 100)}%+`, "Best", `${Math.round(bar * 100)}%+ profit on offer`, "Middles", "No-lose"], days)}
      <div class="tiny muted">Profit on offer adds up what each ${Math.round(bar * 100)}%+ arb could pay at the size both venues showed; arbs on the same game overlap, so it's a ceiling.</div></details>
    <p class="tiny muted" style="margin-top:6px">Verified = seen on 2+ pulls with fresh book prices, no borrowed venue price and nothing too good to be true. Lasts is a floor: pulls are minutes apart. Since ${esc(A.since || "")}.</p></div>`;
}

function makerSection(Mk, Cn) {
  const dims = [["byWhere", "Early / fringe / board"], ["byVenue", "Venue"], ["byKind", "Market type"], ["byLead", "Time to start"], ["byLeague", "Sport"],
    ["byEv", "Edge if it fills"], ["byComp", "Can takers do better?"], ["byHour", "Hour posted"]];
  const lab = (dim, k) => (dim === "byVenue" ? vname(k) : dim === "byKind" ? KIND[k] || k : dim === "byHour" ? hr(+k) : dim === "byVenueKind" ? vname(k).replace(/· (main|alt|prop)$/, (x, y) => `· ${KIND[y]}`) : k);
  let paper = `<div class="note">The paper record starts with the first orders that finish (fill, cancel or expire).</div>`;
  if (Mk?.all) {
    const a = Mk.all;
    const rows = (Mk[mkDim] || []).map((c) => `<tr><td>${esc(lab(mkDim, c.k))}</td>${td(c.decided ?? c.done, c.decided ?? c.done)}${td(c.resting, c.resting)}${td(c.filled, c.filled)}
      ${td(`${pc(c.fillRate, 0)}${c.predicted != null ? ` <span class="tiny muted">(est ${pc(c.predicted, 0)})</span>` : ""}`, c.fillRate)}
      ${td(pc(c.evPosted), c.evPosted)}${td(c.graded, c.graded)}${td(`<span class="${c.roi > 0 ? "pos" : c.roi < 0 ? "neg" : ""}">${sg(c.roi)}</span>${c.roiSE ? ` <span class="tiny muted">±${(c.roiSE * 100).toFixed(1)}</span>` : ""}`, c.roi)}
      ${td(sg(c.clv), c.clv)}${td(sg(c.perOrder), c.perOrder)}</tr>`);
    paper = `<div class="kpis or-k">
        <div class="kpi"><div class="k">Orders decided</div><div class="v num">${a.decided ?? a.done}</div><div class="s">filled, cancelled or expired · ${a.resting} resting</div></div>
        <div class="kpi"><div class="k">Possible fill rate</div><div class="v num">${pc(a.fillRate, 0)}</div><div class="s">we estimated ${pc(a.predicted, 0)} · ${a.locked} locked as arbs</div></div>
        <div class="kpi"><div class="k">Filled and graded</div><div class="v num">${a.graded}</div><div class="s" style="margin-bottom:-2px">${a.pending} waiting on their games</div><div class="s">ROI <b class="${a.roi > 0 ? "pos" : a.roi < 0 ? "neg" : ""}">${sg(a.roi)}</b>${a.roiT != null ? ` · t ${a.roiT}` : ""}</div></div>
        <div class="kpi"><div class="k">Per order posted</div><div class="v num ${a.perOrder > 0 ? "pos" : a.perOrder < 0 ? "neg" : ""}">${sg(a.perOrder)}</div><div class="s">what posting one returned, fills or not</div></div>
      </div>
      ${chipBar("omk", dims, mkDim)}
      ${sortTable(["Where", "Decided", "Resting", "Filled", "Fill rate", "Edge posted", "Graded", "ROI", "CLV", "Per order"], rows)}`;
  }
  let census = "";
  if (Cn?.all) {
    const a = Cn.all;
    const cdims = [["byWhere", "Early / fringe / board"], ["byVenue", "Venue"], ["byVenueKind", "Venue x type"], ["byKind", "Market type"], ["byLead", "Time to start"], ["byLeague", "Sport"]];
    const rows = (Cn[cnDim] || []).map((c) => `<tr><td>${esc(lab(cnDim, c.k))}</td>${td(c.n10c, c.n10c)}${td(c.n10, c.n10)}${td(c.games10c, c.games10c)}${td(pc(c.medEv), c.medEv)}${td(pc(c.best), c.best)}${td(mins(c.life), c.life)}</tr>`);
    const days = [...(Cn.byDay || [])].reverse().slice(0, 14).map((d) => `<tr><td>${esc(d.d.slice(5))}</td>${td(d.n10c, d.n10c)}${td(d.n10, d.n10)}${td(d.games10c ?? "–", d.games10c)}${td(pc(d.best), d.best)}</tr>`);
    census = `<div class="small" style="margin:14px 0 4px"><b>Where the 10%+ orders are</b> <span class="tiny muted">every suggestion counted once a day, last ${Cn.days} days</span></div>
      <div class="kpis or-k">
        <div class="kpi"><div class="k">10%+ no sportsbook beats</div><div class="v num pos">${a.n10c}</div><div class="s">a day · ${a.games10c} games</div></div>
        <div class="kpi"><div class="k">10%+ in all</div><div class="v num">${a.n10}</div><div class="s">a day</div></div>
        <div class="kpi"><div class="k">How long one sits there</div><div class="v num">${mins(a.life)}</div><div class="s">median, first to last seen</div></div>
      </div>
      <div class="small" style="margin:8px 0 2px">10%+ orders no sportsbook beats, first seen each hour (Pacific), a day</div>
      ${hourBars(Cn.byHour || [], "n10c", "orders")}
      ${chipBar("ocn", cdims, cnDim)}
      ${sortTable(["Where", "10%+ unbeaten a day", "10%+ a day", "Games", "Median edge", "Best", "Sits"], rows)}
      <details style="margin-top:8px"><summary class="small">Day by day</summary>${sortTable(["Day", "10%+ unbeaten", "10%+", "Games", "Best"], days)}</details>`;
  }
  return `<div class="card"><div class="small"><b>On paper:</b> every suggestion followed as if posted. A possible fill when the order behind ours gets taken or pulled, graded at our price. Real fills will be fewer.</div>
    ${paper}${census}
    <p class="tiny muted" style="margin-top:6px">Early = 36+ hours out (the scan pulls alternates that far out every 4 hours). Fringe = sports off the board that the scan picks up hourly. ROI is clustered by game; Per order = profit over every order that finished, filled or not. The fill read is noisy until real orders run: a level that vanishes from the feed counts as taken, and on thin books makers pull orders too.</p></div>`;
}

export function oppReport(R) {
  if (!R) return `<div class="card note">Builds on the next engine run.</div>`;
  return `<div class="small muted" style="margin:2px 0 8px">Every arb, middle and maker order the board finds goes on the record: when it showed up, where, how long it lasted, and for maker orders whether it filled and paid. Use it to know when to look and where to put money.</div>
    <div class="small" style="margin:6px 0"><b>Arbs and middles</b></div>${arbSection(R.arbs, R.arbBar ?? 0.03)}
    <div class="small" style="margin:14px 0 6px"><b>Maker orders</b></div>${makerSection(R.makers, R.census)}`;
}

export function bindOppReport(el, rerender) {
  $$("[data-ord]", el).forEach((b) => b.onclick = () => { arbDim = b.dataset.ord; rerender(); });
  $$("[data-omk]", el).forEach((b) => b.onclick = () => { mkDim = b.dataset.omk; rerender(); });
  $$("[data-ocn]", el).forEach((b) => b.onclick = () => { cnDim = b.dataset.ocn; rerender(); });
}
