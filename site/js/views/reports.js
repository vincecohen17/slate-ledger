import { S } from "../store.js";
import { esc, pct, ago, bindTips, bookLogo, $$ } from "../util.js";
import { lineChart, legend } from "./charts.js";

let span = "total", focus = null, ecDim = "venue";

// the edges' report card: every edge we flagged, graded at the close and after the result
const EC_DIMS = [["venue", "Venue"], ["kind", "Line type"], ["market", "Market"], ["edge", "Edge size"], ["logic", "Model"], ["timing", "Time before start"],
  ["league", "Sport"], ["venueKind", "Venue x type"], ["venueLeague", "Venue x sport"]];
const venueName = (k) => S.edges.find((e) => e.book === k)?.bookName || k;
function edgeCard(C) {
  if (!C || !C.n) return `<div class="card note">Collecting. Every edge the board flags is logged with its price, followed to the close and graded after the game; the first grades land after today's games, and categories show from 3 edges (trust them from a few hundred).</div>`;
  const rows = C[ecDim] || [];
  // results first: wins against what the fair expected. CLV on lines priced from our own model is measured against that
  // same model, so it can look great while the bets lose; it stays as a column, second.
  const gap = (r) => r.gap ?? (r.win != null && r.exp != null ? r.win - r.exp : null);
  const max = Math.max(0.01, ...rows.map((r) => Math.abs(gap(r) || 0)));
  const conf = (r) => r.zRes == null ? "" : Math.abs(r.zRes) >= 2.5 ? `<span class="pill ${r.zRes > 0 ? "pos" : "neg"}">${r.zRes > 0 ? "real edge" : "mirage"}</span>` : Math.abs(r.zRes) >= 1.5 ? `<span class="pill">${r.zRes > 0 ? "promising" : "doubtful"}</span>` : `<span class="pill" style="opacity:.6">too early</span>`;
  const name = (k) => ecDim === "venue" ? `<span class="lg-name">${bookLogo(k, venueName(k), 16)}${esc(venueName(k))}</span>` : ecDim.startsWith("venue") ? (() => { const [v, rest] = k.split(" · "); return `<span class="lg-name">${bookLogo(v, venueName(v), 16)}${esc(venueName(v))} · ${esc(rest)}</span>`; })() : esc(k);
  const d = C.daily || [];
  const A = C.all, ga = gap(A);
  const pp = (x) => `${x > 0 ? "+" : ""}${(x * 100).toFixed(1)} pts`;
  return `<div class="card">
    <div class="kpis"><div class="kpi"><div class="k">Edges graded</div><div class="v">${C.n.toLocaleString()}</div><div class="s">since ${esc(C.since || "")}</div></div>
      <div class="kpi"><div class="k">Won vs expected</div><div class="v ${ga > 0 ? "pos" : ga < 0 ? "neg" : ""}">${ga != null ? pp(ga) : "–"}</div><div class="s">won ${A.win != null ? Math.round(A.win * 100) + "%" : "–"} vs ${A.exp != null ? Math.round(A.exp * 100) + "%" : "–"} the fair said${A.zRes != null ? ` · ${A.zRes > 0 ? "+" : ""}${A.zRes}σ` : ""}</div></div>
      <div class="kpi"><div class="k">Flat 1u ROI</div><div class="v ${A.roi > 0 ? "pos" : "neg"}">${A.roi != null ? pct(A.roi) : "–"}</div><div class="s">every flagged edge at its flagged price</div></div>
      <div class="kpi"><div class="k">CLV at the flag</div><div class="v">${pct(A.clv)}</div><div class="s">vs our closing fair (self-graded on model-priced lines)</div></div></div>
    <div class="bar" style="margin-top:10px">${EC_DIMS.map(([k, l]) => `<button class="chip sm ${k === ecDim ? "on" : ""}" data-ec="${k}">${l}</button>`).join("")}</div>
    <div style="overflow-x:auto"><table class="l10t an-t" style="width:100%"><thead><tr><th></th><th>Graded</th><th style="min-width:130px">Won vs expected</th><th>Won / exp</th><th>ROI</th><th>CLV</th><th>Waited</th><th></th></tr></thead><tbody>
      ${rows.map((r) => { const g = gap(r); return `<tr><td>${name(r.k)}</td><td class="num">${r.graded ?? r.n}</td>
        <td><div class="lg-roi"><span class="lg-mid"></span><i class="${g >= 0 ? "pos" : "neg"}" style="${g >= 0 ? "left:50%" : "right:50%"};width:${(Math.abs(g || 0) / max * 50).toFixed(0)}%"></i></div><span class="num tiny ${g >= 0 ? "pos" : "neg"}">${g != null ? pp(g) : "–"}</span></td>
        <td class="num">${r.win != null ? `${Math.round(r.win * 100)} / ${Math.round(r.exp * 100)}` : "–"}</td>
        <td class="num ${r.roi > 0 ? "pos" : r.roi < 0 ? "neg" : ""}">${r.roi != null ? pct(r.roi) : "–"}</td><td class="num">${pct(r.clv)}</td><td class="num">${pct(r.clvLast)}</td><td>${conf(r)}</td></tr>`; }).join("")}
    </tbody></table></div>
    ${d.length >= 3 ? `<div style="margin-top:10px">${lineChart([{ pts: d.map((x, i) => ({ i, y: gap(x) ?? 0, tip: `${x.d}: won ${Math.round((x.win || 0) * 100)}% vs ${Math.round((x.exp || 0) * 100)}% expected over ${x.graded ?? x.n} edges` })), color: "var(--accent)" }], { h: 160, fmt: (x) => pp(x), labels: d.map((x, i) => (i % Math.ceil(d.length / 6) === 0 ? x.d.slice(5) : "")) })}${legend([["var(--accent)", "each day: flagged edges' win rate minus what their fair expected"]])}</div>` : ""}
    <p class="note"><b>Won vs expected</b> is the honest test of an edge: how often that kind of flagged bet won, minus the chance our fair gave it. Above zero over a few hundred edges, the edge is real; below, the fair was wrong (a "mirage"). The tag reads it at 2.5 standard errors.
      <b>CLV</b> here is the flagged price at our own closing fair, which on lines priced from our model grades the model against itself, so trust it only on main lines. <b>Waited</b> is the venue's last price before the start. These results also nudge each new edge's rank: a kind that wins less than its fair says gets pushed down.</p></div>`;
}

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

// how our model is doing vs the closing sharp market, per market, and how much it is trusted after tuning
function modelCard(R) {
  const rows = R?.models || [], pend = R?.modelsPending || [];
  const name = (g) => { const [sp, kind, mk] = g.split("|"); return `${(sp.split("_").pop() || "").toUpperCase()} ${kind === "prop" ? mk.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ") : mk === "*" ? "all" : mk.replace(/_/g, " ")}${mk === "*" ? (kind === "prop" ? " props" : " lines") : ""}`; };
  const head = `<p class="note">Every game, our model's chance and the closing sharp fair are saved, then graded after the final. Lower log loss is better.
    <b>b</b> under 1 means the model was too confident and gets pulled toward 50%. <b>Trust</b> is the weight the model earns next to the market when the two are blended. Both refit on every run.</p>`;
  if (!rows.length) {
    const n = pend.reduce((a, r) => a + r.n, 0);
    return `<div class="card">${head}<div class="note">Collecting: ${n} graded so far. A market starts tuning at 150 graded results (400 for a league-wide fallback).</div>
      ${pend.length ? `<div class="tiny muted">${pend.slice(0, 12).map((r) => `${esc(name(r.group))} ${r.n}`).join(" · ")}</div>` : ""}</div>`;
  }
  return `<div class="card">${head}<div style="overflow-x:auto"><table class="l10t" style="width:100%"><thead><tr><th style="text-align:left">Market</th><th>n</th><th>Model</th><th>Tuned</th><th>Market</th><th>Blend</th><th>b</th><th>Trust</th></tr></thead><tbody>
    ${rows.map((r) => `<tr><td style="text-align:left">${esc(name(r.group))}</td><td class="num">${r.n}</td><td class="num">${r.llModel.toFixed(3)}</td>
      <td class="num">${r.llCal.toFixed(3)}</td><td class="num">${r.llMarket.toFixed(3)}</td><td class="num ${r.llBlend < r.llMarket ? "pos" : ""}">${r.llBlend.toFixed(3)}</td>
      <td class="num">${r.b.toFixed(2)}</td><td class="num">${Math.round(r.w * 100)}%</td></tr>`).join("")}</tbody></table></div></div>`;
}

// walk-forward backtests: the model rebuilt each morning from data before that day, scored against the closing sharp fair
const SPORTN = { basketball_nba: "NBA", icehockey_nhl: "NHL", americanfootball_nfl: "NFL", americanfootball_ncaaf: "NCAAF", baseball_mlb: "MLB" };
function backtestCard(B) {
  const h = B?.history;
  const prog = h ? `<div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px"><b>Historical odds collected</b>
      <span class="tiny muted">${h.complete ? "done" : "still collecting"}${h.at ? ` · ${ago(h.at)}` : ""}</span></div>
      <div class="kpis" style="margin-top:8px"><div class="kpi"><div class="k">Games</div><div class="v">${(h.games || 0).toLocaleString()}</div><div class="s">closing lines + main props</div></div>
      <div class="kpi"><div class="k">Credits used</div><div class="v">${Math.round((h.spent || 0) / 1000)}k</div><div class="s">of ${Math.round(h.budget / 1000)}k set aside</div></div></div>
      <div class="bt-prog"><i style="width:${Math.min(100, (h.spent || 0) / h.budget * 100).toFixed(1)}%"></i></div>
      <div class="tiny muted" style="margin-top:6px">${Object.entries(h.days || {}).map(([k, n]) => { const [sp, se] = k.split("|"); return `${SPORTN[sp] || sp} ${esc(se)}: ${n} days`; }).join(" · ")}</div></div>`
    : `<div class="card note">Historical odds collection hasn't started yet.</div>`;
  const ms = (B?.markets || []).filter((m) => m.n >= 30);
  if (!ms.length) return prog + `<div class="card note" style="margin-top:10px">Results show here after the first backtest run over the collected games.</div>`;
  const name = (g) => { const [sp, kind, mk] = g.split("|"); return `${SPORTN[sp] || sp} ${kind === "prop" ? mk.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ") : mk.replace(/_/g, " ")}`; };
  const maxR = Math.max(0.05, ...ms.map((m) => Math.abs(m.roiAtClose || 0)));
  return prog + `<div class="card" style="margin-top:10px"><b>Model vs the closing line</b>
    <p class="note">Each row replays every collected game: the model is rebuilt from data before that morning, then scored against the closing sharp fair and the result.
      <b>Skill</b> is how much better (green) or worse than the closing market the model's log loss was. <b>Bets</b> takes the side the model likes by 4+ points at the closing fair; beating the close there is very hard, so a green bar is a strong sign.</p>
    <div style="overflow-x:auto"><table class="l10t bt-t" style="width:100%"><thead><tr><th style="text-align:left">Market</th><th>Games</th><th>Skill</th><th>Bets</th><th>Won / expected</th><th style="min-width:140px">Return at close</th></tr></thead><tbody>
    ${ms.map((m) => { const sk = (m.llMarket - m.llModel) / m.llMarket; const r = m.roiAtClose;
      return `<tr><td style="text-align:left">${esc(name(m.group))}</td><td class="num">${m.n}</td><td class="num ${sk > 0 ? "pos" : "neg"}">${sk > 0 ? "+" : ""}${(sk * 100).toFixed(1)}%</td>
        <td class="num">${m.bets}</td><td class="num">${m.won} / ${m.expected}</td>
        <td>${r == null ? "–" : `<div class="bt-roi"><span class="bt-mid"></span><i class="${r > 0 ? "pos" : "neg"}" style="${r > 0 ? "left:50%" : `right:50%`};width:${(Math.abs(r) / maxR * 50).toFixed(1)}%"></i></div><span class="num tiny ${r > 0 ? "pos" : "neg"}">${r > 0 ? "+" : ""}${(r * 100).toFixed(1)}%</span>`}</td></tr>`; }).join("")}
    </tbody></table></div><div class="tiny muted" style="margin-top:6px">${B.rows?.toLocaleString() || 0} graded results · run ${B.at ? ago(B.at) : ""}. These results also feed the model report card above.</div></div>`;
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
    <div class="sec-title">Edge report card: are our edges real?<span class="line"></span></div>
    ${edgeCard(R?.edgecard)}
    <div class="sec-title">Where the books are off<span class="line"></span></div>
    <div class="card">${rows.length ? divBars(rows.map((r) => ({ ...r })), (r) => -r.overpay, (v) => pct(v, 2),
        (r) => `${r.name}: average price ${pct(-r.overpay, 2)} vs sharp fair over ${r.n} prices · beats fair ${Math.round(r.plusRate * 100)}% of the time · stale ${Math.round(r.staleRate * 100)}%`)
      : `<div class="note">Builds up with every pull.</div>`}
      <p class="note">Average gap between each book's price and the sharp fair, per side offered. Closer to zero (or right of it) means cheaper for you. Tap a book for its sports and markets.</p></div>
    ${f ? `<div class="card" style="margin-top:10px"><b>${esc(f.name)}</b> by sport and market<div style="margin-top:8px">${divBars(f.scopes.map((s) => ({ ...s, name: s.scope, book: f.book })), (s) => -s.overpay, (v) => pct(v, 2), (s) => `${s.scope}: ${s.n} prices, beats fair ${Math.round(s.plusRate * 100)}%`)}</div></div>` : ""}
    <div class="sec-title">Best price share<span class="line"></span></div>
    <div class="card">${rows.length ? seqBars([...rows].sort((a, b) => b.bestRate - a.bestRate).slice(0, 15), (r) => r.bestRate, (v) => Math.round(v * 100) + "%") : ""}
      <p class="note">How often each book has the best price on the board for a side. A book you can bet with a high share is where to look first.</p></div>
    <div class="sec-title">Model report card<span class="line"></span></div>
    ${modelCard(R)}
    <div class="sec-title">Backtests<span class="line"></span></div>
    ${backtestCard(R?.backtest)}`;
  $$("[data-span]", el).forEach((b) => b.onclick = () => { span = b.dataset.span; renderReports(el); });
  $$("[data-ec]", el).forEach((b) => b.onclick = () => { ecDim = b.dataset.ec; renderReports(el); });
  $$("[data-book]", el).forEach((b) => b.onclick = () => { focus = focus === b.dataset.book ? null : b.dataset.book; renderReports(el); });
  bindTips(el);
}
