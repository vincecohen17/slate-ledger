import { S } from "../store.js";
import { esc, am, prob, fmtTime, fmtDay, teamLogo, leagueLogo, wpBar, signPt, $$ } from "../util.js";

let league = "All";
const mainLine = (g, m) => g.lines.find((l) => l.market === m && l.isMain);

function q(label, line, side, txt) {
  const b = line?.bestVenue?.[side];
  if (!b) return `<div class="q"><div class="k">${label}</div><div class="v faint">–</div></div>`;
  const good = b.edge != null && b.edge > 0.005;
  return `<div class="q" title="${esc(b.name)} · fair ${am(line.fairAm?.[side])}"><div class="k">${label}</div>
    <div class="v ${good ? "good" : ""}"><span>${txt}</span><span>${am(b.am)}</span></div></div>`;
}

export function card(g, edgeN) {
  const ml = mainLine(g, "h2h"), sp = mainLine(g, "spreads"), tot = mainLine(g, "totals");
  const live = new Date(g.commence) < new Date();
  const pA = ml?.fair?.[g.away], pH = ml?.fair?.[g.home];
  return `<a class="card" href="#/game/${g.id}">
    <div class="card-head"><span class="row" style="gap:6px">${leagueLogo(g.leagueLogo, g.league)}<b style="color:var(--accent)">${esc(g.league)}</b> · ${live ? "started" : fmtTime(g.commence)}</span>
      ${edgeN ? `<span class="badge">${edgeN} edge${edgeN > 1 ? "s" : ""}</span>` : `<span class="tiny faint">${ml?.nBooks ?? 0} books</span>`}</div>
    <div class="teams">
      <div class="team">${teamLogo(g.awayRef, g.away, 44)}<span class="nm">${esc(g.awayRef?.short || g.away)}</span></div>
      <div class="vs">@</div>
      <div class="team">${teamLogo(g.homeRef, g.home, 44)}<span class="nm">${esc(g.homeRef?.short || g.home)}</span></div>
    </div>
    ${pA != null ? wpBar(g.away, g.home, pA, pH, g.awayRef, g.homeRef) : ""}
    <div class="quick">
      ${sp ? q(esc(g.awayRef?.abbr || "Away"), sp, g.away, signPt(-sp.point)) : q("Spread", null)}
      ${tot ? q("Over", tot, "Over", tot.point) : q("Total", null)}
      ${q("ML " + esc(g.awayRef?.abbr || ""), ml, g.away, "")}
      ${sp ? q(esc(g.homeRef?.abbr || "Home"), sp, g.home, signPt(sp.point)) : ""}
      ${tot ? q("Under", tot, "Under", tot.point) : ""}
      ${q("ML " + esc(g.homeRef?.abbr || ""), ml, g.home, "")}
    </div>
  </a>`;
}

export function renderBoard(el) {
  const games = [...S.games.values()].sort((a, b) => a.commence.localeCompare(b.commence));
  if (!games.length) { el.innerHTML = `<div class="empty"><h3>No games yet</h3>The next odds pull fills the board.</div>`; return; }
  const leagues = [...new Map(games.map((g) => [g.league, g.leagueLogo])).entries()];
  const shown = games.filter((g) => league === "All" || g.league === league);
  const edgeN = {}; S.edges.forEach((e) => { edgeN[e.gameId] = (edgeN[e.gameId] || 0) + 1; });
  let h = `<div class="bar"><button class="chip ${league === "All" ? "on" : ""}" data-lg="All">All · ${games.length}</button>
    ${leagues.map(([lg, logo]) => `<button class="chip ${lg === league ? "on" : ""}" data-lg="${esc(lg)}">${logo ? `<img src="${esc(logo)}" alt="" onerror="this.remove()">` : ""}${esc(lg)} · ${games.filter((g) => g.league === lg).length}</button>`).join("")}
    <span class="spacer"></span><span class="tiny muted">Best price at your venues · <span class="pos">teal</span> beats the sharp fair</span></div>`;
  let day = "";
  for (const g of shown) {
    const d = fmtDay(g.commence);
    if (d !== day) { h += `${day ? "</div>" : ""}<div class="sec-title">${d}<span class="line"></span></div><div class="grid">`; day = d; }
    h += card(g, edgeN[g.id]);
  }
  el.innerHTML = h + (day ? "</div>" : "");
  $$("[data-lg]", el).forEach((b) => b.onclick = () => { league = b.dataset.lg; renderBoard(el); });
}
