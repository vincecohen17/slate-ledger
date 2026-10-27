// Live: games in play, our live win chance vs the live market, live edges; per-game live tab.
import { S } from "../store.js";
import { esc, am, prob, pct, both, bothP, teamLogo, headshot, ago, bookLogo, edgeBar, $$ } from "../util.js";
import { edgeCard, bindLog } from "./common.js";

const TENNIS = (lg) => lg === "ATP" || lg === "WTA";
const sitText = (st, league) => {
  if (!st) return "";
  if (TENNIS(league)) return st.detail || `set ${st.set || ""}`;
  if (st.minute != null) return `${st.clock || ""}${st.reds && (st.reds.home || st.reds.away) ? ` · 🟥 ${st.reds.away ? `${st.reds.away} away` : ""}${st.reds.home ? ` ${st.reds.home} home` : ""}` : ""}`;
  if (league === "MLB") return `${st.detail || ""}${st.outs != null && !st.between ? ` · ${st.outs} out` : ""}${st.on1 || st.on2 || st.on3 ? ` · on ${[st.on1 && "1st", st.on2 && "2nd", st.on3 && "3rd"].filter(Boolean).join(", ")}` : ""}`;
  if (league === "NFL" || league === "NCAAF") return `${st.detail || ""}${st.downText ? ` · ${st.downText}` : ""}`;
  return st.detail || `P${st.period} ${st.clock || ""}`;
};

// two stacked bars: our live win chance and the live market's
function wpPair(g, pModel, pMkt) {
  const row = (lab, p, cls) => p == null ? "" : `<div class="lwp"><span class="tiny muted">${lab}</span>
    <div class="lwp-bar ${cls}"><i style="width:${(1 - p) * 100}%"></i><i style="width:${p * 100}%"></i></div>
    <span class="num tiny">${Math.round((1 - p) * 100)}–${Math.round(p * 100)}</span></div>`;
  return row("model", pModel, "md") + row("market", pMkt, "mk");
}

function diamond(st) {
  if (!st || st.on1 == null) return "";
  const b = (on) => `fill="${on ? "var(--accent)" : "var(--panel-3)"}"`;
  return `<svg width="34" height="26" viewBox="0 0 34 26" aria-label="runners"><rect x="13" y="1" width="8" height="8" transform="rotate(45 17 5)" ${b(st.on2)}/>
    <rect x="23" y="11" width="8" height="8" transform="rotate(45 27 15)" ${b(st.on1)}/><rect x="3" y="11" width="8" height="8" transform="rotate(45 7 15)" ${b(st.on3)}/></svg>`;
}

// tennis score: set boxes per player, serving dot
function tennisScore(st, home, away) {
  const pl = st.players || [];
  const row = (name) => {
    const p = pl.find((q) => q.name && name && q.name.toLowerCase().split(" ").pop() === name.toLowerCase().split(" ").pop()) || {};
    return `<span class="row" style="gap:6px"><i class="srv ${p.serving ? "on" : ""}"></i><b>${esc(name)}</b></span>
      <span class="sets">${(p.sets || []).map((g) => `<b class="num">${g}</b>`).join("")}</span>`;
  };
  return `<div class="live-score tennis">${row(away)}${row(home)}</div>`;
}

export function liveCard(x) {
  const st = x.state || {};
  if (TENNIS(x.league)) {
    return `<a class="card live-card" href="#/game/${x.id}/live" style="text-decoration:none;color:inherit">
      <div class="row" style="justify-content:space-between"><span class="pill live">● LIVE</span><span class="tiny muted">${esc(x.league)} · ${esc(sitText(st, x.league))}</span></div>
      ${tennisScore(st, x.home, x.away)}${wpPair(x, x.pHome, x.mkt)}
      <div class="row tiny" style="justify-content:space-between"><span class="muted">${esc(x.home.split(" ").pop())} win chance</span>${x.edges ? `<span class="pill">${x.edges} live edge${x.edges > 1 ? "s" : ""}</span>` : ""}</div></a>`;
  }
  const swing = x.pHome != null && x.mkt != null ? x.pHome - x.mkt : null;
  return `<a class="card live-card" href="#/game/${x.id}/live" style="text-decoration:none;color:inherit">
    <div class="row" style="justify-content:space-between"><span class="pill live">● LIVE</span><span class="tiny muted">${esc(x.league)} · ${esc(sitText(st, x.league))}</span></div>
    <div class="live-score">
      <span class="row">${teamLogo(x.awayRef, x.away, 30)}<b>${esc(x.awayRef?.abbr || x.away)}</b></span><b class="num big">${st.away?.score ?? "–"}</b>
      <span class="row">${teamLogo(x.homeRef, x.home, 30)}<b>${esc(x.homeRef?.abbr || x.home)}</b></span><b class="num big">${st.home?.score ?? "–"}</b>
    </div>${diamond(st)}
    ${wpPair(x, x.pHome, x.mkt)}
    <div class="row tiny" style="justify-content:space-between;margin-top:4px"><span class="muted">home win chance</span>
      ${swing != null && Math.abs(swing) >= 0.04 ? `<span class="pill pos">model ${swing > 0 ? "higher" : "lower"} on ${esc(x.homeRef?.abbr || "home")} by ${Math.round(Math.abs(swing) * 100)}</span>` : ""}
      ${x.edges ? `<span class="pill">${x.edges} live edge${x.edges > 1 ? "s" : ""}</span>` : ""}</div></a>`;
}

export function renderLive(el) {
  const L = S.live;
  const games = L?.games || [];
  const edges = (L?.edges || []).filter((e) => e.edge >= 0.015);
  el.innerHTML = `<div class="row" style="justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px"><h2 style="margin:0">Live</h2>
      <span class="tiny muted">${L?.updated ? `live prices ${ago(L.updated)} · refreshes every minute while this screen is open · ${L.creditsToday ?? 0} live credits today` : "waiting for the first live pull"}</span></div>
    <p class="note">Live prices move in seconds and our feed is about a minute behind, so treat every live edge as a lead: open the app, check the price is still there, then decide.</p>
    ${games.length ? `<div class="grid">${games.map(liveCard).join("")}</div>` : `<div class="empty"><h3>No games in play</h3>Games show up here once they start.</div>`}
    <div class="sec-title">Live edges<span class="line"></span></div>
    ${edges.length ? `<div class="grid tight">${edges.slice(0, 60).map(edgeCard).join("")}</div>` : `<div class="card note">Nothing beats the live fair right now.</div>`}`;
  bindLog(el);
}

// game page Live tab
export function renderGameLive(el, g) {
  const d = S.liveGames.get(g.id);
  if (!d) { el.insertAdjacentHTML("beforeend", `<div class="card note">Waiting for the first live pull for this game (about a minute).</div>`); return; }
  const st = d.state || {}, m = d.model || {};
  const lines = (d.lines || []).filter((l) => ["h2h", "spreads", "totals", "team_totals"].includes(l.market) && l.fair);
  const row = (l, o) => {
    const best = l.bestVenue?.[o];
    const fair = l.fair?.[o], md = l.model?.[o];
    const lab = l.market === "h2h" ? `${o} ML` : l.market === "spreads" ? `${o} ${o === g.home ? (l.point > 0 ? "+" : "") + l.point : (-l.point > 0 ? "+" : "") + -l.point}`
      : l.market === "team_totals" ? `${l.team} ${o} ${l.point}` : `${o} ${l.point}`;
    return `<div class="lv-row"><span class="ellip">${esc(lab)}</span>
      <span class="num">${best ? `${bookLogo(best.book, best.name, 13)} ${am(best.am)}` : "–"}</span>
      <span class="num tiny">${fair != null ? bothP(fair) : "–"}</span>
      <span class="num tiny ${md != null && fair != null ? (md - fair >= 0.03 ? "pos" : md - fair <= -0.03 ? "neg" : "") : ""}">${md != null ? prob(md) : "–"}</span>
      ${edgeBar(best?.edge, 0.08)}</div>`;
  };
  // one line per market: the main one (most books, closest to a coin flip), team totals for both teams
  const pick = (mk) => { const ls = lines.filter((l) => l.market === mk); return ls.find((l) => l.isMain) || ls.sort((a, b) => (b.nBooks || 0) - (a.nBooks || 0))[0]; };
  const tts = [g.home, g.away].map((t) => lines.filter((l) => l.market === "team_totals" && l.team === t).sort((a, b) => (b.nBooks || 0) - (a.nBooks || 0))[0]);
  const mainLines = [pick("h2h"), pick("spreads"), pick("totals"), ...tts].filter(Boolean);
  const props = (d.props || []).flatMap((p) => p.rows.filter((r) => r.live && r.isMain).map((r) => ({ ...r, market: p.market })))
    .sort((a, b) => Math.abs((b.live.Over ?? 0.5) - (b.fair?.Over ?? 0.5)) - Math.abs((a.live.Over ?? 0.5) - (a.fair?.Over ?? 0.5))).slice(0, 24);
  el.insertAdjacentHTML("beforeend", `<div class="col" style="gap:12px">
    <div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap"><span class="pill live">● LIVE</span><span class="tiny muted">${esc(sitText(st, d.league))} · prices ${ago(d.updated)}</span></div>
      ${TENNIS(d.league) ? tennisScore(st, g.home, g.away) : `<div class="live-score big2">
        <span class="row">${teamLogo(g.awayRef, g.away, 40)}<b>${esc(g.away)}</b></span><b class="num">${st.away?.score ?? "–"}</b>
        <span class="row">${teamLogo(g.homeRef, g.home, 40)}<b>${esc(g.home)}</b></span><b class="num">${st.home?.score ?? "–"}</b></div>`}${diamond(st)}
      ${wpPair(g, m.pHome, (d.lines.find((l) => l.market === "h2h")?.fair || {})[g.home])}
      <div class="row tiny muted" style="gap:14px;flex-wrap:wrap;margin-top:6px">
        ${m.margin != null ? `<span>model final margin ${g.homeRef?.abbr || "home"} ${m.margin > 0 ? "+" : ""}${(+m.margin).toFixed(1)}</span>` : ""}
        ${m.total != null ? `<span>model final total ${(+m.total).toFixed(1)}</span>` : ""}
        ${m.left != null ? `<span>${Math.round(m.left * 100)}% of the game left</span>` : ""}
        ${m.pDraw != null ? `<span>draw ${prob(m.pDraw)}</span>` : ""}
        ${m.serve ? `<span>points won on serve (model): ${esc(g.home.split(" ").pop())} ${Math.round(m.serve[0] * 100)}%, ${esc(g.away.split(" ").pop())} ${Math.round(m.serve[1] * 100)}%</span>` : ""}</div></div>
    <div class="card"><b>Live lines</b> <span class="tiny muted">best price at your venues · live fair · our live model · edge</span>
      <div class="lv-head tiny muted"><span>Bet</span><span>Best</span><span>Fair</span><span>Model</span><span>Edge</span></div>
      ${mainLines.map((l) => l.outcomes.map((o) => row(l, o)).join("")).join("") || `<div class="note">No live lines yet.</div>`}</div>
    ${props.length ? `<div class="card"><b>Live props</b> <span class="tiny muted">now → our projected final · chance over the line (model vs market)</span>
      ${props.map((r) => `<div class="lv-prop">${headshot({ player: r.player, headshot: r.headshot }, 30)}
        <span class="col" style="gap:0;min-width:0"><span class="small ellip"><b>${esc(r.player)}</b> ${esc(r.market.replace(/^player_/, "").replace(/_/g, " "))} ${r.point}</span>
          <span class="tiny muted"><b class="num">${r.live.cur}</b> now → <b class="num">${r.live.proj}</b> projected</span></span>
        <div class="lv-prog"><i style="width:${Math.min(100, r.live.cur / Math.max(r.point, 0.5) * 100)}%"></i><i class="ln" style="left:${Math.min(100, r.point / Math.max(r.point, r.live.proj, 0.5) * 100)}%"></i></div>
        <span class="num tiny ${r.live.Over - (r.fair?.Over ?? 0.5) >= 0.05 ? "pos" : r.live.Over - (r.fair?.Over ?? 0.5) <= -0.05 ? "neg" : ""}">O ${prob(r.live.Over)}<br><span class="muted">mkt ${prob(r.fair?.Over)}</span></span></div>`).join("")}</div>` : ""}
    ${d.edges?.length ? `<div class="sec-title">Live edges in this game<span class="line"></span></div><div class="grid tight">${d.edges.map(edgeCard).join("")}</div>` : ""}</div>`);
  bindLog(el);
}
