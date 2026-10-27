import { S } from "../store.js";
import { esc, pct, ago, bindTips, bookLogo, $$ } from "../util.js";

let span = "total", focus = null;

function divBars(rows, val, fmt, tip) {
  const max = Math.max(0.005, ...rows.map((r) => Math.abs(val(r))));
  return rows.map((r) => { const v = val(r);
    return `<div class="hbar" data-book="${esc(r.book)}" style="cursor:pointer" data-tip="${esc(tip(r))}"><span class="ellip row" style="gap:6px">${bookLogo(r.book, r.name, 15)}${esc(r.name)} ${r.venue ? `<span class="pill v">yours</span>` : ""}</span>
      <div class="eb" style="height:12px"><div class="l">${v < 0 ? `<i style="width:${Math.abs(v) / max * 100}%"></i>` : ""}</div><div class="r">${v > 0 ? `<i style="width:${v / max * 100}%"></i>` : ""}</div></div>
      <span class="num small ${v > 0 ? "pos" : v < -0.02 ? "neg" : ""}" style="text-align:right">${fmt(v)}</span></div>`; }).join("");
}

function seqBars(rows, val, fmt) {
  const max = Math.max(0.01, ...rows.map(val));
  return rows.map((r) => `<div class="hbar"><span class="ellip">${esc(r.name)}</span>
    <div class="tr"><i style="display:block;height:100%;width:${val(r) / max * 100}%;background:var(--accent);border-radius:0 3px 3px 0"></i></div>
    <span class="num small" style="text-align:right">${fmt(val(r))}</span></div>`).join("");
}

export function renderReports(el) {
  const R = S.reports, st = S.status;
  const rows = R ? (span === "now" ? R.now : R.total) || [] : [];
  const f = focus && rows.find((r) => r.book === focus);
  const games = st?.games ? Object.entries(st.games).filter(([, n]) => n) : [];
  el.innerHTML = `
    <div class="kpis" style="margin-top:18px">
      <div class="kpi"><div class="k">Last pull</div><div class="v" style="font-size:18px">${st ? ago(st.updated) : "–"}</div><div class="s">${esc(st?.mode || "")}</div></div>
      <div class="kpi"><div class="k">Credits left</div><div class="v">${st?.creditsRemaining ?? "–"}</div><div class="s">${st?.creditsThisRun ?? 0} used last run</div></div>
      <div class="kpi"><div class="k">Games on board</div><div class="v">${S.games.size}</div><div class="s">${games.map(([k, n]) => `${k} ${n}`).join(" · ")}</div></div>
      <div class="kpi"><div class="k">Edges now</div><div class="v">${S.edges.length}</div><div class="s">${st?.propGames ?? 0} games with props</div></div>
    </div>
    ${st?.errors?.length ? `<div class="card note" style="margin-top:10px">${st.errors.map(esc).join("<br>")}</div>` : ""}
    <div class="bar"><button class="chip ${span === "total" ? "on" : ""}" data-span="total">Since ${R?.since ? esc(R.since.slice(0, 10)) : "start"} · ${R?.runs ?? 0} pulls</button>
      <button class="chip ${span === "now" ? "on" : ""}" data-span="now">Latest pull</button></div>
    <div class="sec-title">Where the books are off<span class="line"></span></div>
    <div class="card">${rows.length ? divBars(rows.map((r) => ({ ...r })), (r) => -r.overpay, (v) => pct(v, 2),
        (r) => `${r.name}: average price ${pct(-r.overpay, 2)} vs sharp fair over ${r.n} prices · beats fair ${Math.round(r.plusRate * 100)}% of the time · stale ${Math.round(r.staleRate * 100)}%`)
      : `<div class="note">Builds up with every pull.</div>`}
      <p class="note">Average gap between each book's price and the sharp fair, per side offered. Closer to zero (or right of it) means cheaper for you. Tap a book for its sports and markets.</p></div>
    ${f ? `<div class="card" style="margin-top:10px"><b>${esc(f.name)}</b> by sport and market<div style="margin-top:8px">${divBars(f.scopes.map((s) => ({ ...s, name: s.scope, book: f.book })), (s) => -s.overpay, (v) => pct(v, 2), (s) => `${s.scope}: ${s.n} prices, beats fair ${Math.round(s.plusRate * 100)}%`)}</div></div>` : ""}
    <div class="sec-title">Best price share<span class="line"></span></div>
    <div class="card">${rows.length ? seqBars([...rows].sort((a, b) => b.bestRate - a.bestRate).slice(0, 15), (r) => r.bestRate, (v) => Math.round(v * 100) + "%") : ""}
      <p class="note">How often each book has the best price on the board for a side. A book you can bet with a high share is where to look first.</p></div>
    <div class="sec-title">Backtests<span class="line"></span></div>
    <div class="grid">
      ${[["Sharp-gap bets", "Bet any venue price 2, 3 or 5 points off Pinnacle's no-vig fair. Did it beat the close, and did it win?"],
         ["Pick'em lines vs books", "Take pick'em sides where the sportsbook line has moved past the app's line. Hit rate vs the 57.7% break-even."],
         ["Prop projections", "Rebuild projections with only pre-game data. Calibration and closing line value by stat."],
         ["Promo strategies", "Which bets get the most value from each boost type, using past prices."]]
        .map(([t, d]) => `<div class="card"><b>${t}</b><p class="note">${d}</p><span class="pill">unlocks with historical odds (paid plan)</span></div>`).join("")}
    </div>`;
  $$("[data-span]", el).forEach((b) => b.onclick = () => { span = b.dataset.span; renderReports(el); });
  $$("[data-book]", el).forEach((b) => b.onclick = () => { focus = focus === b.dataset.book ? null : b.dataset.book; renderReports(el); });
  bindTips(el);
}
