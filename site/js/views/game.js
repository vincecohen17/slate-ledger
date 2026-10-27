import { S, loadProps, propsFor } from "../store.js";
import { esc, am, pct, prob, fmtTime, fmtDay, teamLogo, headshot, leagueLogo, wpBar, signPt, mktName, edgeCls, bindTips, spark, hitRate, both, marketInfo, bookLogo, $$ } from "../util.js";
import { edgeCard, ladder, bindLog } from "./common.js";
import { pickCard } from "./picks.js";
import { renderMatchup } from "./matchup.js";

const open = new Set();
let propMarket = null;

function hero(g) {
  const ml = g.lines.find((l) => l.market === "h2h");
  const sp = g.lines.find((l) => l.market === "spreads" && l.isMain);
  const tot = g.lines.find((l) => l.market === "totals" && l.isMain);
  const cA = g.awayRef?.color || "transparent", cH = g.homeRef?.color || "transparent";
  const nEdges = S.edges.filter((e) => e.gameId === g.id).length;
  return `<div class="hero"><div class="glow" style="background:linear-gradient(90deg, ${esc(cA)} 0%, transparent 45%, transparent 55%, ${esc(cH)} 100%)"></div>
    <div class="hero-meta">${leagueLogo(g.leagueLogo, g.league, 16)}<b>${esc(g.league)}</b>·<span>${fmtDay(g.commence)} · ${fmtTime(g.commence)} PT</span></div>
    <div class="teams">
      <div class="team">${teamLogo(g.awayRef, g.away, 76)}<span class="nm">${esc(g.away)}</span></div>
      <div class="vs">@</div>
      <div class="team">${teamLogo(g.homeRef, g.home, 76)}<span class="nm">${esc(g.home)}</span></div>
    </div>
    ${ml?.fair ? wpBar(g.away, g.home, ml.fair[g.away], ml.fair[g.home], g.awayRef, g.homeRef) : ""}
    <div class="fair-chips">
      ${ml?.fair ? `<div class="fc"><div class="k">Fair moneyline</div><div class="v">${am(ml.fairAm[g.away])} / ${am(ml.fairAm[g.home])}</div><div class="s">${esc(g.awayRef?.abbr || "away")} / ${esc(g.homeRef?.abbr || "home")}</div></div>` : ""}
      ${sp?.fair ? `<div class="fc"><div class="k">Spread</div><div class="v">${esc(g.homeRef?.abbr || "")} ${signPt(sp.point)}</div><div class="s">fair ${am(sp.fairAm[g.home])} / ${am(sp.fairAm[g.away])}</div></div>` : ""}
      ${tot?.fair ? `<div class="fc"><div class="k">Total</div><div class="v">${tot.point}</div><div class="s">over ${prob(tot.fair.Over)} · under ${prob(tot.fair.Under)}</div></div>` : ""}
      <div class="fc"><div class="k">Edges</div><div class="v ${nEdges ? "pos" : ""}">${nEdges}</div><div class="s">at your venues</div></div>
    </div></div>`;
}

function sidesBlock(g, l, limit, all) {
  const k = marketInfo(l.market).kind;
  const lab = (o) => k === "spreads" ? `${o} ${signPt(o === g.home ? l.point : -l.point)}` : k === "team_totals" ? `${l.team} ${o} ${l.point}` : k === "totals" ? `${o} ${l.point}` : o;
  const ref = (o) => o === g.home ? g.homeRef : o === g.away ? g.awayRef : null;
  return `<div class="sides">${l.outcomes.map((o) => `<div><div class="side-h">${ref(o) || o === g.home || o === g.away ? teamLogo(ref(o), o, 22) : ""}<span>${esc(lab(o))}</span></div>${ladder(l, o, limit, all)}</div>`).join("")}</div>`;
}

function overview(g) {
  const edges = S.edges.filter((e) => e.gameId === g.id).slice(0, 6);
  let h = `<div class="sec-title">Best bets at your venues<span class="line"></span></div>`;
  h += edges.length ? `<div class="grid tight">${edges.map(edgeCard).join("")}</div>`
    : `<div class="card muted">No price at your venues beats the sharp fair right now. Check the props tab or come back closer to start.</div>`;
  for (const [m, t] of [["h2h", "Moneyline"], ["spreads", "Spread"], ["totals", "Total"]]) {
    const l = g.lines.find((x) => x.market === m && x.isMain);
    if (!l) continue;
    h += `<div class="sec-title">${t}${m !== "h2h" ? ` · main line` : ""}<span class="line"></span><span class="tiny">${l.nBooks} books</span></div>${sidesBlock(g, l, 5, false)}`;
  }
  return h;
}

function lines(g) {
  // group: main markets first, then each period, alternates collapsed behind a button
  const groups = new Map();
  for (const l of g.lines) {
    const info = marketInfo(l.market);
    const key = info.period + "|" + info.kind + (l.team ? "|" + l.team : "");
    if (!groups.has(key)) groups.set(key, { info, team: l.team, lines: [] });
    groups.get(key).lines.push(l);
  }
  const rank = (x) => (x.info.period ? 10 : 0) + ({ h2h: 0, spreads: 1, totals: 2, team_totals: 3, other: 4 }[x.info.kind] ?? 5);
  const out = [];
  for (const grp of [...groups.values()].sort((a, b) => rank(a) - rank(b))) {
    const ls = grp.lines.sort((a, b) => (b.isMain ? 1 : 0) - (a.isMain ? 1 : 0) || (a.point ?? 0) - (b.point ?? 0));
    const key = g.id + "|" + grp.info.period + grp.info.kind + (grp.team || "");
    const showAll = open.has(key);
    const main = ls.filter((l) => l.isMain || (!grp.info.alt && ls.length === 1));
    const shown = showAll ? ls : (main.length ? main : ls.slice(0, 1));
    for (const l of shown) {
      const info = marketInfo(l.market);
      const t = `${info.label}${l.team ? " · " + esc(l.team) : ""}${info.kind === "spreads" ? ` · ${esc(g.homeRef?.abbr || g.home)} ${signPt(l.point)}` : info.kind === "totals" || info.kind === "team_totals" ? ` ${l.point}` : ""}`;
      const bk = g.id + l.id, all = open.has(bk);
      out.push(`<div class="panel"><h3><span>${t} ${l.isMain && info.kind !== "h2h" ? `<span class="pill s">main</span>` : ""}</span>
        <span class="row"><span class="tiny muted">fair from ${esc(l.fairSource || "–")}</span><button class="btn sm ghost" data-all="${esc(bk)}">${all ? "Key books" : `All ${l.nBooks} books`}</button></span></h3>
        <div class="pad">${sidesBlock(g, l, 7, all)}</div></div>`);
    }
    if (ls.length > shown.length || showAll && ls.length > 1) {
      out.push(`<button class="btn sm ghost" style="margin:-4px 0 10px" data-all="${esc(key)}">${showAll ? "Hide" : "Show"} ${ls.length - (showAll ? 1 : shown.length)} more ${esc(grp.info.label.toLowerCase())} lines</button>`);
    }
  }
  return out.join("");
}

function propCard(player, rows, market) {
  rows.sort((a, b) => (b.isMain ? 1 : 0) - (a.isMain ? 1 : 0) || a.point - b.point);
  const main = rows[0];
  const info = [main.team, main.pos].filter(Boolean).join(" · ");
  // main line, any pick'em line, and the single best-priced alt line inside a sensible range
  const inRange = (r) => r.fair?.Over == null || (r.fair.Over > 0.15 && r.fair.Over < 0.85);
  const bestAlt = rows.filter((r) => !r.isMain && !(r.dfs || []).length && inRange(r))
    .map((r) => ({ r, e: Math.max(r.bestVenue?.Over?.edge ?? -1, r.bestVenue?.Under?.edge ?? -1) }))
    .filter((x) => x.e > 0.005).sort((a, b) => b.e - a.e)[0]?.r;
  const shown = rows.filter((r) => r.isMain || (r.dfs || []).length || r === bestAlt).slice(0, 4);
  const lineHtml = (r) => {
    const f = r.fair || {};
    const po = f.Over ?? f.Yes, pu = f.Under ?? f.No;
    const bo = r.bestVenue?.Over || r.bestVenue?.Yes, bu = r.bestVenue?.Under || r.bestVenue?.No;
    const pk = (r.dfs || []).filter((d) => d.side).map((d) => `<span class="pill ${d.edge >= 0.02 ? "pos" : ""}" title="${esc(d.name)} line ${d.point}: our ${d.side} chance ${prob(d.p)} vs 57.7% to break even">${bookLogo(d.book, d.name, 13)} ${esc(d.name)} ${d.side[0]} ${prob(d.p)}</span>`).join(" ");
    return `<div class="pl">
      <div class="pl-h"><b class="num">${r.point ?? ""}</b><span class="tiny muted">${r.isMain ? "main line" : r.modelFair ? "alt · market model" : "alt"}</span></div>
      ${r.model?.Over != null ? `<div class="row tiny" style="gap:6px;margin-top:4px"><span class="pill ${r.model.Over - (po ?? 0.5) >= 0.04 ? "pos" : r.model.Over - (po ?? 0.5) <= -0.04 ? "neg" : ""}">our model ${r.model.mean} · O ${prob(r.model.Over)}</span></div>` : ""}
      ${po != null && pu != null ? `<div class="ou"><i style="width:${po * 100}%"></i><i style="width:${pu * 100}%"></i></div>
      <div class="pl-b tiny muted"><span>Over ${prob(po)}</span><span>Under ${prob(pu)}</span></div>` : po != null ? `<div class="tiny muted">Yes ${prob(po)}</div>` : ""}
      <div class="pl-b" style="margin-top:4px">
        <span class="mini ${edgeCls(bo?.edge)}">O ${bo ? `${both(bo.am)} <span class="tiny">${bookLogo(bo.book, bo.name, 13)} ${esc(bo.name)} ${pct(bo.edge)}</span>` : "–"}</span>
        <span class="mini ${edgeCls(bu?.edge)}">U ${bu ? `${both(bu.am)} <span class="tiny">${bookLogo(bu.book, bu.name, 13)} ${esc(bu.name)} ${pct(bu.edge)}</span>` : "–"}</span>
      </div>
      ${r.last10?.length ? `<div class="l10" style="margin-top:6px">${spark(r.last10, r.point)}<span>last ${r.last10.length}: ${hitRate(r.last10, r.point)} · avg ${(r.last10.reduce((a, b) => a + b, 0) / r.last10.length).toFixed(1)}</span></div>` : ""}${pk ? `<div class="row" style="flex-wrap:wrap;gap:4px;margin-top:6px">${pk}</div>` : ""}</div>`;
  };
  return `<div class="card pc"><div class="pc-top">${headshot(main, 54)}<div class="col"><b>${esc(player)}</b><span class="tiny muted">${esc(info || mktName(market))}</span></div></div>
    <div class="pc-lines">${shown.map(lineHtml).join("")}</div></div>`;
}

function props(g) {
  const docs = propsFor(g.id);
  if (!docs.length) return `<div class="empty"><h3>No props for this game yet</h3>Props are pulled for games starting within about a day.</div>`;
  const markets = docs.map((d) => d.market).sort();
  if (!propMarket || !markets.includes(propMarket)) propMarket = markets.includes("player_points") ? "player_points" : markets[0];
  const d = docs.find((x) => x.market === propMarket);
  const byP = new Map();
  d.rows.forEach((r) => { if (!byP.has(r.player)) byP.set(r.player, []); byP.get(r.player).push(r); });
  const score = (rows) => Math.max(...rows.flatMap((r) => [r.bestVenue?.Over?.edge ?? -1, r.bestVenue?.Under?.edge ?? -1, ...(r.dfs || []).map((x) => x.edge ?? -1)]));
  const players = [...byP.entries()].sort((a, b) => score(b[1]) - score(a[1]));
  return `<div class="bar">${markets.map((m) => `<button class="chip ${m === propMarket ? "on" : ""}" data-pm="${m}">${esc(mktName(m))}</button>`).join("")}
    <span class="spacer"></span><span class="tiny muted">Gold = chance of the over at the sharp fair</span></div>
    <div class="grid tight">${players.map(([p, rows]) => propCard(p, rows, propMarket)).join("")}</div>`;
}

export function renderGame(el, id, tab = "matchup") {
  const g = S.games.get(id);
  if (!g) { el.innerHTML = `<div class="empty"><h3>Game not on the board</h3><a href="#/">Back to the board</a></div>`; return; }
  loadProps(id);
  const picks = [...S.picks.values()].filter((p) => p.gameId === id);
  const tabs = [["matchup", "Matchup"], ["overview", "Best bets"], ["lines", "Lines"], ["props", "Props"], ...(picks.length ? [["pick", `Pick${picks.length > 1 ? "s" : ""}`]] : [])];
  let h = `<a class="back" href="#/">← Board</a>${hero(g)}
    <div class="bar">${tabs.map(([k, t]) => `<a class="chip ${k === tab ? "on" : ""}" href="#/game/${id}/${k}">${t}</a>`).join("")}
    <span class="spacer"></span><span class="tiny muted">Bars: edge vs sharp fair · <span class="pos">teal</span> = value, <span class="neg">orange</span> = overpriced</span></div>`;
  if (tab === "lines") h += lines(g);
  else if (tab === "props") h += props(g);
  else if (tab === "pick") h += `<div class="grid">${picks.map(pickCard).join("")}</div>`;
  else if (tab === "overview") h += overview(g);
  el.innerHTML = h;
  if (tab === "matchup") renderMatchup(el, g);
  $$("[data-all]", el).forEach((b) => b.onclick = () => { open.has(b.dataset.all) ? open.delete(b.dataset.all) : open.add(b.dataset.all); renderGame(el, id, tab); });
  $$("[data-pm]", el).forEach((b) => b.onclick = () => { propMarket = b.dataset.pm; renderGame(el, id, tab); });
  bindLog(el); bindTips(el);
}
