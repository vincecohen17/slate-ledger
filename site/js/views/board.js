import { S } from "../store.js";
import { esc, am, prob, fmtTime, fmtDay, teamLogo, leagueLogo, signPt, $$ } from "../util.js";
import { sitText } from "./live.js";
import { finalCard } from "./final.js";

let league = "All", sort = "time";
const mainLine = (g, m) => g.lines.find((l) => l.market === m && l.isMain);
const TEAMS_N = { NBA: 30, NHL: 32, NFL: 32, MLB: 30, WNBA: 13, NCAAF: 136 };
// how far model and market must differ before the card calls it out (points, goals or runs)
const GAP = { NBA: 1.5, WNBA: 1.5, NFL: 1.5, NCAAF: 2.5, NCAAB: 2, NHL: 0.25, MLB: 0.4 };
const SOCCER_GAP = 0.25;
const isSoccer = (g) => g.draw || /soccer/.test(g.sport || "") || ["EPL", "MLS", "UCL", "UEL", "La Liga", "Bundesliga", "Serie A", "Ligue 1", "Liga MX"].includes(g.league);

function q(label, line, side, txt) {
  const b = line?.bestVenue?.[side];
  if (!b) return `<div class="q"><div class="k">${label}</div><div class="v faint">–</div></div>`;
  const good = b.edge != null && b.edge > 0.005;
  return `<div class="q" title="${esc(b.name)} · fair ${am(line.fairAm?.[side])}"><div class="k">${label}</div>
    <div class="v ${good ? "good" : ""}"><span>${txt}</span><span>${am(b.am)}</span></div></div>`;
}

// win chance: the sharp market on top, our model underneath, same scale; soccer gets the draw in the middle
function winBars(g, mu) {
  const ml = mainLine(g, "h2h");
  const mk = ml?.fair, md = mu?.model?.lines?.h2h?.model;
  if (!mk && !md) return "";
  const cA = g.awayRef?.color || "var(--faint)", cH = g.homeRef?.color || "var(--muted)";
  const row = (lab, p) => {
    if (!p || p[g.away] == null) return "";
    const a = p[g.away], h = p[g.home], d = p.Draw;
    return `<div class="wb-row"><span class="wb-k">${lab}</span><div class="wb-bar">
      <i style="width:${(a * 100).toFixed(1)}%;background:${esc(cA)}"></i>${d != null ? `<i class="wb-d" style="width:${(d * 100).toFixed(1)}%"></i>` : ""}<i style="width:${(h * 100).toFixed(1)}%;background:${esc(cH)}"></i></div>
      <span class="wb-n">${prob(a)}${d != null ? ` · <span class="faint">${prob(d)}</span>` : ""} · ${prob(h)}</span></div>`;
  };
  const gap = mk && md && md[g.home] != null && mk[g.home] != null ? md[g.home] - mk[g.home] : null;
  const big = gap != null && Math.abs(gap) >= 0.04;
  return `<div class="wb">${row("Market", mk)}${row("Model", md)}
    ${big ? `<div class="wb-note">Model is <b class="${"pos"}">${Math.round(Math.abs(gap) * 100)} pts</b> higher on ${esc((gap > 0 ? g.homeRef?.abbr || g.home : g.awayRef?.abbr || g.away))}</div>` : ""}</div>`;
}

// spread and total: market number vs model number on one small line each
function numbers(g, mu) {
  const m = mu?.model || {};
  if (m.lines && !Object.keys(m.lines).length) return "";        // model held back (e.g. MLB starter not posted yet)
  const sp = mainLine(g, "spreads"), tot = mainLine(g, "totals");
  const soccer = isSoccer(g);
  const out = [];
  const lim = soccer ? SOCCER_GAP : GAP[g.league] ?? 1.5;
  const hA = g.homeRef?.abbr || "Home", aA = g.awayRef?.abbr || "Away";
  if (m.margin != null && sp && !soccer) {
    const mkHome = sp.point, mdHome = -m.margin;                 // home handicap: negative = home favored
    const fav = (x) => x <= 0 ? `${hA} ${signPt(x)}` : `${aA} ${signPt(-x)}`;
    const d = mkHome - mdHome;                                    // > 0: model likes home more than the market
    out.push(cmp("Spread", fav(mkHome), fav(Math.round(mdHome * 10) / 10), Math.abs(d) >= lim ? `model ${Math.abs(d).toFixed(1)} more on ${d > 0 ? hA : aA}` : "", d));
  }
  if (m.total != null && tot && tot.point != null) {
    const d = m.total - tot.point;
    out.push(cmp(soccer ? "Goals" : "Total", tot.point, m.total.toFixed(soccer || g.league === "NHL" || g.league === "MLB" ? 2 : 1), Math.abs(d) >= lim ? `model ${d > 0 ? "over" : "under"} by ${Math.abs(d).toFixed(soccer || g.league === "NHL" || g.league === "MLB" ? 2 : 1)}` : "", d));
  }
  return out.length ? `<div class="nums">${out.join("")}</div>` : "";
}
const cmp = (k, mk, md, note) => `<div class="num-row"><span class="k">${k}</span><span>Mkt <b>${esc(mk)}</b></span><span>Model <b>${esc(md)}</b></span>${note ? `<span class="pill pos">${esc(note)}</span>` : `<span class="tiny faint">close</span>`}</div>`;

// head-to-head ranks: a mirrored bar per stat, longer = better rank
function ranks(g, mu) {
  const rows = (mu?.compare || []).filter((c) => c.hr != null && c.ar != null).slice(0, 4);
  if (!rows.length) return "";
  const n = mu.nTeams || rows[0].n || TEAMS_N[g.league] || 30;
  const w = (r) => Math.max(6, (n - r + 1) / n * 100).toFixed(0);
  const better = (c) => c.hr === c.ar ? 0 : c.hr < c.ar ? 1 : -1;
  return `<div class="rk"><div class="rk-h"><span>${esc(g.awayRef?.abbr || "Away")}</span><span class="tiny faint">league rank</span><span>${esc(g.homeRef?.abbr || "Home")}</span></div>
    ${rows.map((c) => { const b = better(c); return `<div class="rk-row"><span class="rk-n ${b < 0 ? "win" : ""}">${ord(c.ar)}</span>
      <div class="rk-l"><i class="${b < 0 ? "win" : ""}" style="width:${w(c.ar)}%"></i></div><span class="rk-k">${esc(short(c.k))}</span>
      <div class="rk-r"><i class="${b > 0 ? "win" : ""}" style="width:${w(c.hr)}%"></i></div><span class="rk-n ${b > 0 ? "win" : ""}">${ord(c.hr)}</span></div>`; }).join("")}</div>`;
}
const ord = (n) => n + (["th", "st", "nd", "rd"][(n % 100 > 10 && n % 100 < 14) ? 0 : n % 10] || "th");
const short = (k) => k.replace(/\s*\(.*?\)\s*/g, " ").replace("allowed", "allowed").replace("Even-strength", "5v5").replace("xGoals", "xG").trim();

// context chips from the model's factors: injuries, goalies, starters, rest, weather, park
function chips(g, mu) {
  const fs = (mu?.factors || []).filter((f) => !/^(Model|Opponent-adjusted model|Elo model)/.test(f.text));
  if (!fs.length) return "";
  const kind = (t) => /without|out\b|injur|questionable|doubtful/i.test(t) ? "INJ" : /in goal|goalie/i.test(t) ? "G" : /RHP|LHP|starter|probable/i.test(t) ? "SP"
    : /back-to-back|rest|travel/i.test(t) ? "REST" : /weather|wind|°F|rain|snow/i.test(t) ? "WX" : /park|Field|Stadium/i.test(t) ? "PARK" : /pace/i.test(t) ? "PACE" : "NOTE";
  const tidy = (t) => t.replace(/\s*—\s*confirm$/, "").replace(/goals saved above expected per 60/g, "GSAx/60").replace(/ \(0 min to redistribute\)/, "");
  const shown = fs.slice(0, 4);
  return `<div class="ctx">${shown.map((f) => { const k = kind(f.text); return `<span class="cx ${f.tone || "info"}" title="${esc(f.text)}"><b>${k}</b>${esc(tidy(f.text))}</span>`; }).join("")}
    ${fs.length > shown.length ? `<span class="tiny faint">+${fs.length - shown.length} more</span>` : ""}</div>`;
}

function liveStrip(g) {
  const x = (S.live?.games || []).find((y) => y.id === g.id);
  if (!x) return "";
  const st = x.state || {};
  const scored = st.home?.score != null;
  return `<div class="lstrip"><span class="pill live">● LIVE</span>${scored ? `<b class="num">${esc(g.awayRef?.abbr || "")} ${st.away?.score ?? ""} – ${st.home?.score ?? ""} ${esc(g.homeRef?.abbr || "")}</b>` : ""}
    <span class="tiny muted ellip">${esc(sitText(st, g.league))}</span>${x.pHome != null ? `<span class="tiny">${esc(g.homeRef?.abbr || "home")} <b>${prob(x.pHome)}</b></span>` : ""}</div>`;
}

export function card(g, edgeN, pick) {
  const mu = S.matchups.get(g.id), pv = S.previews.get(g.id);
  const started = new Date(g.commence) < new Date();
  const ml = mainLine(g, "h2h"), sp = mainLine(g, "spreads"), tot = mainLine(g, "totals");
  return `<a class="card bcard" href="#/game/${g.id}">
    <div class="card-head"><span class="row" style="gap:6px">${leagueLogo(g.leagueLogo, g.league)}<b style="color:var(--accent)">${esc(g.league)}</b> · ${started ? "started" : fmtTime(g.commence)}</span>
      <span class="row" style="gap:4px">${pick ? `<span class="pill tier-${esc(pick.tier)}">${esc(pick.tier === "bet" ? "PICK" : pick.tier.toUpperCase())}</span>` : ""}
      ${edgeN ? `<span class="badge">${edgeN} edge${edgeN > 1 ? "s" : ""}</span>` : `<span class="tiny faint">${ml?.nBooks ?? 0} books</span>`}</span></div>
    <div class="teams">
      <div class="team">${teamLogo(g.awayRef, g.away, 40)}<span class="nm">${esc(g.awayRef?.short || g.away)}</span>${rec(mu, "away")}</div>
      <div class="vs">@</div>
      <div class="team">${teamLogo(g.homeRef, g.home, 40)}<span class="nm">${esc(g.homeRef?.short || g.home)}</span>${rec(mu, "home")}</div>
    </div>
    ${started ? liveStrip(g) : ""}
    ${winBars(g, started ? null : mu)}
    ${started ? "" : numbers(g, mu)}
    ${ranks(g, mu)}
    ${chips(g, mu)}
    ${pv?.headline ? `<div class="pv"><b>${esc(pv.headline)}</b>${pv.lean ? `<span class="tiny muted">Lean: ${esc(pv.lean)}</span>` : ""}</div>` : ""}
    ${pick ? `<div class="pkline"><span class="tiny muted">Pick</span> <b>${esc(pick.selection)}</b> <span class="tiny muted">fair ${am(pick.fairAm)}</span></div>` : ""}
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
const rec = (mu, side) => { const r = mu?.teams?.[side]?.record; return r ? `<span class="tiny faint">${esc(r)}</span>` : ""; };

// how strongly the model and the market disagree on this game (for sorting)
function gapScore(g) {
  const mu = S.matchups.get(g.id), ml = mainLine(g, "h2h");
  const md = mu?.model?.lines?.h2h?.model, mk = ml?.fair;
  return md && mk && md[g.home] != null && mk[g.home] != null ? Math.abs(md[g.home] - mk[g.home]) : 0;
}

export function renderBoard(el) {
  const games = [...S.games.values()].sort((a, b) => a.commence.localeCompare(b.commence));
  if (!games.length && !(S.finals || []).length) { el.innerHTML = `<div class="empty"><h3>No games yet</h3>The next odds pull fills the board.</div>`; return; }
  const leagues = [...new Map(games.map((g) => [g.league, g.leagueLogo])).entries()];
  let shown = games.filter((g) => league === "All" || g.league === league);
  const edgeN = {}; S.edges.forEach((e) => { edgeN[e.gameId] = (edgeN[e.gameId] || 0) + 1; });
  const picks = {}; S.picks.forEach((p) => { if (p.status !== "void" && (!picks[p.gameId] || p.tier === "bet")) picks[p.gameId] = p; });
  if (sort === "gap") shown = [...shown].sort((a, b) => gapScore(b) - gapScore(a));
  if (sort === "edges") shown = [...shown].sort((a, b) => (edgeN[b.id] || 0) - (edgeN[a.id] || 0));
  const lgStats = (lg) => { const gs = games.filter((g) => g.league === lg); return { n: gs.length, e: gs.reduce((s, g) => s + (edgeN[g.id] || 0), 0), p: gs.filter((g) => picks[g.id]).length }; };
  let h = `<div class="bar"><button class="chip ${league === "All" ? "on" : ""}" data-lg="All">All · ${games.length}</button>
    ${leagues.map(([lg, logo]) => { const s = lgStats(lg); return `<button class="chip ${lg === league ? "on" : ""}" data-lg="${esc(lg)}">${logo ? `<img src="${esc(logo)}" alt="" onerror="this.remove()">` : ""}${esc(lg)} · ${s.n}${s.p ? ` <span class="pos">●</span>` : ""}</button>`; }).join("")}</div>
    <div class="bar" style="margin-top:6px"><span class="tiny muted">Sort</span>
      ${[["time", "Start time"], ["gap", "Model vs market"], ["edges", "Most edges"]].map(([k, l]) => `<button class="chip sm ${sort === k ? "on" : ""}" data-sort="${k}">${l}</button>`).join("")}
      <span class="spacer"></span><span class="tiny muted">Bars: market vs model win chance · rank bars: longer is better · <span class="pos">teal</span> price beats the sharp fair</span></div>`;
  // finished games: the final card replaces the game's card; earlier finals get their own section per day
  const finals = (S.finals || []).filter((x) => league === "All" || x.league === league);
  const finByGame = new Map(finals.filter((x) => x.gameId).map((x) => [x.gameId, x]));
  let day = "";
  if (sort !== "time") h += `<div class="grid">`;
  for (const g of shown) {
    if (finByGame.has(g.id)) {
      if (sort === "time") { const d = fmtDay(g.commence); if (d !== day) { h += `${day ? "</div>" : ""}<div class="sec-title">${d}<span class="line"></span></div><div class="grid">`; day = d; } }
      h += finalCard(finByGame.get(g.id)); continue;
    }
    if (sort === "time") {
      const d = fmtDay(g.commence);
      if (d !== day) { h += `${day ? "</div>" : ""}<div class="sec-title">${d}<span class="line"></span></div><div class="grid">`; day = d; }
    }
    h += card(g, edgeN[g.id], picks[g.id]);
  }
  h += sort !== "time" || day ? "</div>" : "";
  const onBoard = new Set(shown.map((g) => g.id));
  const rest = finals.filter((x) => !x.gameId || !onBoard.has(x.gameId));
  const byDay = new Map();
  rest.forEach((x) => { const d = fmtDay(x.commence); if (!byDay.has(d)) byDay.set(d, []); byDay.get(d).push(x); });
  for (const [d, xs] of byDay) h += `<div class="sec-title">Final · ${d}<span class="line"></span></div><div class="grid tight">${xs.map(finalCard).join("")}</div>`;
  el.innerHTML = h;
  $$("[data-lg]", el).forEach((b) => b.onclick = () => { league = b.dataset.lg; renderBoard(el); });
  $$("[data-sort]", el).forEach((b) => b.onclick = () => { sort = b.dataset.sort; renderBoard(el); });
}
