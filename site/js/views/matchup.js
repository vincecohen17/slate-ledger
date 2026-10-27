// Matchup tab: Claude's read, model vs market, tale of the tape, key factors, injuries, goalies, players.
import { S, propsFor } from "../store.js";
import { esc, prob, teamLogo, headshot, mktName, signPt, bindTips, spark as spark2, hitRate } from "../util.js";

const fmtV = (r, v) => v == null ? "–" : r.pct ? `${(v * 100).toFixed(1)}%` : (+v).toFixed(r.d ?? 1);

function track(market, model, labL, labR) {
  const m = Math.max(1, Math.min(99, market * 100)), d = Math.max(1, Math.min(99, model * 100));
  return `<div class="mm-track" data-tip="Market (sharp fair) ${prob(market, 1)} · Model ${prob(model, 1)}">
    <span class="mm-l">${esc(labL)}</span><span class="mm-r">${esc(labR)}</span>
    <i class="mm-fill" style="left:${Math.min(m, d)}%;width:${Math.abs(d - m)}%"></i>
    <i class="mm-mk" style="left:${m}%"></i><i class="mm-md" style="left:${d}%"></i></div>`;
}

function modelVsMarket(mu, g) {
  const L = mu.model?.lines || {};
  const rows = [];
  const ab = (side) => (side === "home" ? g.homeRef?.abbr : g.awayRef?.abbr) || side;
  if (L.h2h?.market) {
    const mk = L.h2h.market[g.home], md = L.h2h.model[g.home];
    rows.push({ k: "Moneyline", mk, md, l: `${ab("away")} wins`, r: `${ab("home")} wins`, lean: md - mk, side: md > mk ? `${ab("home")} ML` : `${ab("away")} ML` });
  }
  if (L.spreads?.market) {
    const mk = L.spreads.market[g.home], md = L.spreads.model[g.home], pt = L.spreads.point;
    rows.push({ k: `Spread ${ab("home")} ${signPt(pt)}`, mk, md, l: `${ab("away")} ${signPt(-pt)}`, r: `${ab("home")} ${signPt(pt)}`, lean: md - mk,
                side: md > mk ? `${ab("home")} ${signPt(pt)}` : `${ab("away")} ${signPt(-pt)}` });
  }
  if (L.totals?.market) {
    const mk = L.totals.market.Over, md = L.totals.model.Over, pt = L.totals.point;
    rows.push({ k: `Total ${pt}`, mk, md, l: `Under ${pt}`, r: `Over ${pt}`, lean: md - mk, side: md > mk ? `Over ${pt}` : `Under ${pt}` });
  }
  if (!rows.length) return "";
  const T = mu.teams;
  const proj = T.away.pts != null ? `Model score ${esc(ab("away"))} ${T.away.pts} – ${esc(ab("home"))} ${T.home.pts}`
    : T.away.runs != null ? `Model runs ${esc(ab("away"))} ${T.away.runs} – ${esc(ab("home"))} ${T.home.runs}`
    : T.away.goals != null ? `Expected goals ${esc(ab("away"))} ${T.away.goals} – ${esc(ab("home"))} ${T.home.goals}` : "";
  return `<div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap"><b>Model vs market</b><span class="tiny muted">${proj}</span></div>
    <div class="col" style="gap:14px;margin-top:12px">${rows.map((r) => `<div>
      <div class="row" style="justify-content:space-between"><span class="small">${esc(r.k)}</span>
        ${Math.abs(r.lean) >= 0.04 ? `<span class="pill pos">model leans ${esc(r.side)} · ${Math.round(Math.abs(r.lean) * 100)} pts</span>` : `<span class="pill">close to market</span>`}</div>
      ${track(r.mk, r.md, r.l, r.r)}
      <div class="row tiny muted" style="justify-content:space-between"><span><i class="lg-dot mk"></i>market ${prob(r.mk)}</span><span><i class="lg-dot md"></i>model ${prob(r.md)}</span></div></div>`).join("")}</div>
    <p class="note" style="margin-bottom:0">Each bar runs from the left outcome (0%) to the right outcome (100%). Gray marks the sharp market, gold marks our model.</p></div>`;
}

function tape(mu, g) {
  const n = mu.nTeams || (mu.league === "NHL" ? 32 : 30);
  const w = (rank) => rank ? Math.max(6, (n + 1 - rank) / n * 100) : 0;
  const rows = mu.compare.map((r) => {
    const w = (rank) => rank ? Math.max(6, ((r.n || n) + 1 - rank) / (r.n || n) * 100) : 0;
    const better = r.hi == null || r.h == null || r.a == null ? null : (r.hi ? r.h > r.a : r.h < r.a) ? "h" : "a";
    return `<div class="tape-row">
      <span class="tv ${better === "a" ? "win" : ""}">${fmtV(r, r.a)}${r.ar ? `<sup>${r.ar}</sup>` : ""}</span>
      <div class="tb l"><i style="width:${w(r.ar)}%" class="${better === "a" ? "win" : ""}"></i></div>
      <span class="tk">${esc(r.k)}</span>
      <div class="tb r"><i style="width:${w(r.hr)}%" class="${better === "h" ? "win" : ""}"></i></div>
      <span class="tv ${better === "h" ? "win" : ""}">${fmtV(r, r.h)}${r.hr ? `<sup>${r.hr}</sup>` : ""}</span></div>`;
  }).join("");
  const rec = (t) => t.record ? `${t.record}` : t.gp != null ? `${t.gp} GP` : "";
  return `<div class="card"><div class="tape-head">
      <span class="row">${teamLogo(g.awayRef, g.away, 28)}<b>${esc(g.awayRef?.abbr || g.away)}</b><span class="tiny muted">${esc(rec(mu.teams.away))}</span></span>
      <b class="small muted">Tale of the tape</b>
      <span class="row" style="justify-content:flex-end"><span class="tiny muted">${esc(rec(mu.teams.home))}</span><b>${esc(g.homeRef?.abbr || g.home)}</b>${teamLogo(g.homeRef, g.home, 28)}</span></div>
    ${rows}<p class="note" style="margin-bottom:0">Bar length = league rank (longer is better); small number = rank. Current season, blended with last season early on.</p></div>`;
}

function factors(mu, g) {
  if (!mu.factors?.length) return "";
  const icon = { pos: `<span class="fi pos">▲</span>`, neg: `<span class="fi neg">▼</span>`, info: `<span class="fi">•</span>` };
  return `<div class="card"><b>Key factors</b><div class="col" style="gap:8px;margin-top:10px">${mu.factors.map((f) => `<div class="row" style="align-items:flex-start">
    ${f.side ? teamLogo(f.side === "home" ? g.homeRef : g.awayRef, f.side === "home" ? g.home : g.away, 22) : `<span style="width:22px"></span>`}
    ${icon[f.tone] || icon.info}<span class="small">${esc(f.text)}</span></div>`).join("")}</div></div>`;
}

function injuries(mu, g) {
  const col = (t, ref, name) => {
    const list = (t.injuries || []).slice(0, 8);
    return `<div><div class="side-h">${teamLogo(ref, name, 22)}<span>${esc(ref?.abbr || name)}</span></div>
      ${list.length ? list.map((x) => `<div class="inj" data-tip="${esc(x.note || x.detail || "")}"><span class="pill ${/out|reserve/i.test(x.status) ? "neg" : /question|doubt|day/i.test(x.status) ? "s" : ""}">${esc(x.status)}</span>
        <span class="small">${esc(x.name)}</span><span class="tiny muted ellip">${esc(x.pos || "")} ${esc(x.detail || "")}</span></div>`).join("") : `<div class="tiny muted">Nothing reported</div>`}</div>`;
  };
  return `<div class="card"><b>Injury report</b><div class="sides" style="margin-top:10px">${col(mu.teams.away, g.awayRef, g.away)}${col(mu.teams.home, g.homeRef, g.home)}</div></div>`;
}

// GSAx per game as bars above (saved more than expected) or below zero
function gsaxBars(vals, w = 150, h = 34) {
  const v = (vals || []).filter((x) => x != null);
  if (!v.length) return "";
  const m = Math.max(1, ...v.map((x) => Math.abs(x)));
  const bw = w / v.length;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="gbars" aria-label="goals saved above expected, last ${v.length}">
    <line x1="0" x2="${w}" y1="${h / 2}" y2="${h / 2}" class="sp-ref"/>
    ${v.map((x, i) => { const bh = Math.abs(x) / m * (h / 2 - 2); return `<rect x="${(i * bw + 1).toFixed(1)}" y="${(x >= 0 ? h / 2 - bh : h / 2).toFixed(1)}" width="${Math.max(2, bw - 2).toFixed(1)}" height="${Math.max(1, bh).toFixed(1)}" class="${x >= 0 ? "gb-pos" : "gb-neg"}"><title>${x > 0 ? "+" : ""}${x.toFixed(2)}</title></rect>`; }).join("")}</svg>`;
}

function goalies(mu, g) {
  if (mu.league !== "NHL") return "";
  const card = (t, ref, name) => {
    const gl = t.goalie || {};
    const r = gl.rank || {}, pool = gl.pool || "";
    const L = gl.last10 || {};
    const rankPill = (lab, v, d, rk) => `<span class="gpill" title="${lab}${rk ? `: rank ${rk} of ${pool} goalies` : ""}"><b class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}">${fmtS(v, d)}</b><span>${lab}</span>${rk ? `<i style="width:${Math.max(6, (pool + 1 - rk) / pool * 100)}%"></i>` : ""}</span>`;
    const gsaxSum = (L.gsax || []).reduce((a, b) => a + b, 0);
    const rows = (gl.starts || []).map((s) => `<tr class="${s.name === gl.name ? "" : "dim"}">
        <td>${esc(s.date.slice(5).replace("-", "/"))}</td><td>${esc(s.opp || "")}</td><td class="ellip" style="max-width:72px">${esc((s.name || "").split(" ").slice(-1)[0])}</td>
        <td class="num">${s.sv}/${s.sa}</td><td class="num ${s.svp >= 0.915 ? "pos" : s.svp != null && s.svp < 0.88 ? "neg" : ""}">${s.svp != null ? s.svp.toFixed(3).replace(/^0/, "") : "–"}</td>
        <td class="num ${s.gsax > 0.2 ? "pos" : s.gsax < -0.2 ? "neg" : ""}"><b>${s.gsax != null ? fmtS(s.gsax, 2) : "–"}</b></td><td class="num">${s.xga != null ? s.xga.toFixed(2) : "–"}</td></tr>`).join("");
    return `<div class="col" style="gap:8px">
      <div class="side-h">${headshot({ player: gl.name, headshot: gl.headshot }, 34)}<span>${esc(gl.name || "Unknown")}</span><span class="tiny muted">${esc(ref?.abbr || name)} · likely</span></div>
      <div class="gpills">${rankPill("GSAx/60 all", gl.gsax60, 2, r.gsax60)}${rankPill("GSAx/60 even", gl.ev60, 2, r.ev60)}${rankPill("GSAx per 2 PK min", gl.pk2, 3, r.pk2)}
        <span class="gpill"><b>${gl.proj?.saves ?? "–"}</b><span>proj saves</span></span></div>
      ${L.gsax?.length ? `<div class="gviz"><div><div class="tiny muted">GSAx per game, last ${L.gsax.length} <b class="${gsaxSum >= 0 ? "pos" : "neg"}">${fmtS(gsaxSum, 1)} total</b></div>${gsaxBars(L.gsax)}</div>
        <div><div class="tiny muted">Saves last ${L.sv.length} vs ${gl.proj?.saves ?? "–"} proj · ${hitRate(L.sv, gl.proj?.saves)}</div>${spark2(L.sv, gl.proj?.saves, { w: 150, h: 34 })}</div></div>`
        : L.sv?.length ? `<div class="l10">${spark2(L.sv, gl.proj?.saves)}<span>saves last ${L.sv.length}: ${hitRate(L.sv, gl.proj?.saves)} the projection</span></div>` : ""}
      ${rows ? `<div style="overflow-x:auto"><table class="l10t gtab"><thead><tr><th>Date</th><th>Opp</th><th>Goalie</th><th>SV/SA</th><th>SV%</th><th>GSAx</th><th>xGA</th></tr></thead><tbody>${rows}</tbody></table></div>
        <div class="tiny muted">Team's last ${gl.starts.length} games; faded rows were started by another goalie</div>` : ""}</div>`;
  };
  return `<div class="card"><b>Likely goalies</b> <span class="tiny muted">from recent starts · confirm before betting</span>
    <div class="sides" style="margin-top:10px">${card(mu.teams.away, g.awayRef, g.away)}${card(mu.teams.home, g.homeRef, g.home)}</div></div>`;
}

// ---------- MLB starting pitchers (Statcast)
function starters(mu, g) {
  if (mu.league !== "MLB" || !mu.starters) return "";
  const pc = (v, d = 1) => v == null ? "–" : `${(+v).toFixed(d)}%`;
  const card = (sp, ref, name) => {
    if (!sp) return `<div><div class="side-h">${teamLogo(ref, name, 22)}<span>Starter TBA</span></div><div class="tiny muted">No probable posted yet.</div></div>`;
    const r = sp.rank || {}, pool = sp.pool || "";
    const pill = (lab, v, rk) => `<span class="pill">${lab} ${v}${rk ? ` · #${rk}/${pool}` : ""}</span>`;
    const ars = (sp.arsenal || []).map((a) => `<div class="ars"><span class="ellip">${esc(a.name || a.type)}</span>
        <div class="ars-bar"><i style="width:${Math.min(100, a.usage || 0)}%"></i></div><span class="num tiny">${(a.usage || 0).toFixed(0)}%</span>
        <span class="num tiny muted">whiff ${a.whiff != null ? a.whiff.toFixed(0) + "%" : "–"}</span>
        <span class="num tiny ${a.rv100 != null && a.rv100 > 0.5 ? "pos" : a.rv100 != null && a.rv100 < -0.5 ? "neg" : "muted"}" title="run value per 100 pitches (pitcher's view)">${a.rv100 != null ? (a.rv100 > 0 ? "+" : "") + a.rv100.toFixed(1) : "–"}</span></div>`).join("");
    const st = (sp.starts || []).map((x) => `<div class="gstart"><span class="tiny muted">${esc((x.date || "").slice(5).replace("-", "/"))}</span>
        <span class="who"><span class="ellip tiny">vs ${esc(x.opp || "")}</span></span><span class="num">${esc(x.ip ?? "–")} IP</span>
        <span class="num">${x.k ?? "–"} K · ${x.er ?? "–"} ER</span></div>`).join("");
    const L = sp.last10 || {};
    return `<div class="col" style="gap:6px"><div class="side-h">${headshot({ player: sp.name, headshot: sp.headshot }, 34)}<span>${esc(sp.name)}</span><span class="tiny muted">${esc(sp.hand || "")}HP${sp.velo ? ` · ${sp.velo.toFixed(1)} mph` : ""}</span></div>
      <div class="row tiny" style="flex-wrap:wrap;gap:6px">${pill("xERA", sp.xera ?? "–", r.xera)}${pill("K", pc(sp.k), r.k_percent)}${pill("BB", pc(sp.bb), r.bb_percent)}${pill("whiff", pc(sp.whiff), r.whiff_percent)}${pill("barrel", pc(sp.barrel), r.barrel_batted_rate)}</div>
      ${sp.proj ? `<div class="row tiny" style="flex-wrap:wrap;gap:6px"><span class="pill s">proj ${sp.proj.k} K</span><span class="pill s">${(sp.proj.outs / 3).toFixed(1)} IP</span><span class="pill s">${sp.proj.h} H · ${sp.proj.bb} BB · ${sp.proj.er} ER</span></div>` : ""}
      ${L.k?.length ? `<div class="l10">${spark2(L.k, sp.proj?.k)}<span>strikeouts last ${L.k.length}: ${hitRate(L.k, sp.proj?.k)} the projection</span></div>` : ""}
      ${ars ? `<div class="tiny muted" style="margin-top:4px">Arsenal · usage · whiff · run value/100</div>${ars}` : ""}
      ${st ? `<div class="tiny muted" style="margin-top:4px">Recent starts</div>${st}` : ""}</div>`;
  };
  return `<div class="card"><b>Starting pitchers</b> <span class="tiny muted">Statcast · ranks among ${mu.starters.home?.pool || mu.starters.away?.pool || ""} pitchers with 50+ IP</span>
    <div class="sides" style="margin-top:10px">${card(mu.starters.away, g.awayRef, g.away)}${card(mu.starters.home, g.homeRef, g.home)}</div></div>`;
}

// ---------- lineups
function chip(p, sub) {
  return `<div class="lu-chip ${p.changed ? "chg" : ""}" title="${esc(p.name)}">${headshot({ player: p.name, headshot: p.headshot }, 38)}
    <span class="lu-n">${esc((p.name || "").split(" ").slice(-1)[0])}</span><span class="lu-s">${esc(sub || "")}</span></div>`;
}
function lineupCard(lu, ref, name) {
  if (!lu) return "";
  let body = "";
  if (lu.kind === "nba") {
    const slots = [[50, 16], [18, 34], [82, 34], [30, 72], [70, 72]];
    body = `<div class="court nba"><svg viewBox="0 0 100 100" preserveAspectRatio="none" class="court-lines"><rect x="1" y="1" width="98" height="98" rx="3"/>
        <rect x="34" y="62" width="32" height="37"/><path d="M34 62 A16 16 0 0 1 66 62"/><path d="M8 99 L8 70 A42 42 0 0 1 92 70 L92 99"/></svg>
      ${lu.starters.map((p, i) => `<div class="lu-slot" style="left:${slots[i][0]}%;top:${slots[i][1]}%">${chip(p, `${p.pos || ""} · ${Math.round(p.min || 0)}m`)}</div>`).join("")}</div>
      <div class="lu-bench"><span class="tiny muted">Bench</span>${lu.bench.map((p) => `<span class="pill">${esc(p.name)} ${Math.round(p.min || 0)}m</span>`).join("")}</div>`;
  } else if (lu.kind === "nhl") {
    body = `<div class="court nhl">${lu.lines.map((l, i) => `<div class="lu-row"><span class="lu-tag">L${i + 1}</span>${l.map((p) => chip(p, `${p.pos || ""} ${(p.toi || 0).toFixed(0)}m`)).join("")}</div>`).join("")}
      <div class="lu-sep"></div>${lu.pairs.map((l, i) => `<div class="lu-row"><span class="lu-tag">D${i + 1}</span>${l.map((p) => chip(p, `D ${(p.toi || 0).toFixed(0)}m`)).join("")}</div>`).join("")}
      <div class="lu-sep"></div><div class="lu-row"><span class="lu-tag">G</span>${chip(lu.goalie || {}, "likely")}</div></div>`;
  } else if (lu.kind === "mlb") {
    body = `<div class="bo">${(lu.order || []).map((p) => `<div class="bo-row">
        <span class="bo-n">${p.slot}</span>${headshot({ player: p.name, headshot: p.headshot }, 30)}
        <span class="col" style="gap:0;min-width:0"><span class="small ellip"><b>${esc(p.name)}</b> <span class="tiny muted">${esc(p.pos || "")} · bats ${esc(p.bats || "?")}</span></span>
          <span class="tiny muted num">xwOBA ${p.xwoba != null ? p.xwoba.toFixed(3).replace(/^0/, "") : "–"}${p.plat && Math.abs(p.plat - 1) >= 0.02 ? ` · platoon ${p.plat > 1 ? "+" : ""}${Math.round((p.plat - 1) * 100)}%` : ""}</span></span>
        <span class="col num tiny" style="align-items:flex-end;gap:0"><span>hit ${p.pHit != null ? Math.round(p.pHit * 100) + "%" : "–"} · HR ${p.pHr != null ? (p.pHr * 100).toFixed(0) + "%" : "–"}</span>
          <span class="muted">${p.proj ? `${p.proj.tb.toFixed(2)} TB · ${p.proj.k.toFixed(2)} K` : ""}</span></span>
        ${spark2(p.last10?.tb, 1.5, { w: 56, h: 20 })}</div>`).join("")}</div>
      <div class="tiny ${lu.confirmed ? "pos" : "muted"}" style="margin-top:6px">${lu.confirmed ? "✓ lineup posted" : "projected lineup"}${lu.vsHand ? ` · vs ${esc(lu.vsHand)}HP` : ""} · sparkline: total bases last 10 vs 1.5</div>`;
  } else if (lu.kind === "nfl") {
    const vol = (p) => `${p.vol} ${p.volKey === "att" ? "att" : p.volKey === "car" ? "car" : "tgt"}`;
    body = `<div class="court nfl">
      <div class="lu-row">${lu.wr.slice(0, 1).map((p) => chip(p, vol(p))).join("")}<span class="ol">${"<i></i>".repeat(5)}</span>${lu.te.map((p) => chip(p, vol(p))).join("")}${lu.wr.slice(1, 2).map((p) => chip(p, vol(p))).join("")}</div>
      <div class="lu-row">${lu.wr.slice(2, 3).map((p) => chip(p, vol(p))).join("")}${lu.qb.map((p) => chip(p, vol(p))).join("")}</div>
      <div class="lu-row">${lu.rb.map((p) => chip(p, vol(p))).join("")}</div></div>`;
  }
  return `<div><div class="side-h">${teamLogo(ref, name, 22)}<span>${esc(ref?.abbr || name)}</span></div>${body}
    ${lu.out?.length ? `<div class="tiny muted" style="margin-top:6px">Out: ${lu.out.map(esc).join(", ")}</div>` : ""}</div>`;
}
function lineups(mu, g) {
  if (!mu.lineups) return "";
  const how = mu.lineups.home?.how || "";
  return `<div class="card"><b>${mu.league === "MLB" ? "Batting orders" : "Projected lineups"}</b> <span class="tiny muted">${esc(how)}</span>
    <div class="sides" style="margin-top:10px">${lineupCard(mu.lineups.away, g.awayRef, g.away)}${lineupCard(mu.lineups.home, g.homeRef, g.home)}</div></div>`;
}

const fmtS = (v, d) => v == null ? "–" : `${v > 0 ? "+" : ""}${(+v).toFixed(d)}`;

const PSTATS = {
  NBA: [["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["fg3", "3PM"], ["stl", "STL"], ["blk", "BLK"], ["tov", "TOV"]],
  WNBA: [["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["fg3", "3PM"], ["stl", "STL"], ["blk", "BLK"], ["tov", "TOV"]],
  NHL: [["sog", "SOG"], ["pts", "PTS"], ["g", "G"], ["a", "A"], ["blk", "BLK"], ["hits", "HIT"]],
  NFL: [["pass_yds", "PYD"], ["rush_yds", "RYD"], ["car", "CAR"], ["rec", "REC"], ["rec_yds", "RECYD"], ["td", "TD"]],
  MLB: [["h", "H"], ["tb", "TB"], ["hr", "HR"], ["k", "K"], ["r", "R"], ["rbi", "RBI"], ["bb", "BB"], ["sb", "SB"]],
};
const MIN_KEY = { MLB: ["pa", "PA"], NBA: ["min", "MIN"], WNBA: ["min", "MIN"], NHL: ["toi", "TOI"], NFL: ["targets", "TGT"] };
// the stat and prop market that matter most for a player, for the sparkline and book line
function keyStat(league, p) {
  if (league === "NBA" || league === "WNBA") return ["pts", "player_points"];
  if (league === "NHL") return ["sog", "player_shots_on_goal"];
  if (league === "MLB") return ["tb", "batter_total_bases"];
  if (league === "NFL") return p.proj?.att ? ["pass_yds", "player_pass_yds"] : /RB|FB/.test(p.pos || "") ? ["rush_yds", "player_rush_yds"] : ["rec_yds", "player_reception_yds"];
  return [null, null];
}
const KEY_LABEL = { NBA: "points", WNBA: "points", NHL: "shots on goal", NFL: "yards", MLB: "total bases" };
const openP = new Set();

function players(mu, g) {
  const stats = PSTATS[mu.league] || [];
  if (!stats.length || !(mu.players?.home?.length || mu.players?.away?.length)) return "";
  const docs = propsFor(g.id);
  const lineFor = (name, mk) => docs.find((d) => d.market === mk)?.rows.find((r) => r.player === name && r.isMain);
  const [mk, ml] = MIN_KEY[mu.league] || ["min", "MIN"];
  const detail = (p) => {
    const L = p.last10 || {};
    const n = (L[stats[0][0]] || []).length;
    if (!n) return `<div class="tiny muted">No games yet this season.</div>`;
    const cols = [[mk, ml], ...stats];
    return `<table class="l10t"><thead><tr><th>Game</th>${cols.map(([, h]) => `<th>${h}</th>`).join("")}</tr></thead><tbody>
      ${Array.from({ length: n }, (_, i) => n - 1 - i).map((i) => `<tr><td class="muted">-${n - i}</td>${cols.map(([k]) => `<td class="num">${L[k]?.[i] ?? "–"}</td>`).join("")}</tr>`).join("")}
      <tr><td><b>avg</b></td>${cols.map(([k]) => { const a = (L[k] || []).filter((x) => x != null); return `<td class="num"><b>${a.length ? (a.reduce((x, y) => x + y, 0) / a.length).toFixed(1) : "–"}</b></td>`; }).join("")}</tr>
      <tr><td class="muted">proj</td><td class="num muted">${p.min != null ? (+p.min).toFixed(0) : "–"}</td>${stats.map(([k]) => `<td class="num muted">${p.proj?.[k] != null ? (+p.proj[k]).toFixed(1) : "–"}</td>`).join("")}</tr></tbody></table>`;
  };
  const col = (list, ref, name, side) => `<div><div class="side-h">${teamLogo(ref, name, 22)}<span>${esc(ref?.abbr || name)}</span>
      <span class="tiny muted" style="margin-left:auto">${ml} · ${stats.slice(0, 4).map(([, h]) => h).join(" · ")} · chart: last 10 ${KEY_LABEL[mu.league] || "games"}</span></div>
    ${list.map((p, i) => {
      const [ks, kmk] = keyStat(mu.league, p);
      const ln = lineFor(p.name, kmk);
      const key = `${g.id}|${side}|${p.name}`;
      return `<div class="pr ${p.out ? "out" : ""}" data-p="${esc(key)}">${headshot({ player: p.name, headshot: p.headshot }, 34)}
        <div class="col" style="gap:1px"><span class="small ellip"><b>${esc(p.name)}</b> <span class="tiny muted">${esc(p.pos || "")}</span>
          ${p.status && p.status !== "Active" ? `<span class="pill ${p.out ? "neg" : "s"}">${esc(p.status)}</span>` : ""}</span>
          <span class="tiny muted num">${p.out ? "out" : (p.min ?? 0).toFixed(0)} · ${stats.slice(0, 4).map(([k]) => (p.proj?.[k] ?? 0).toFixed(1)).join(" · ")}
          ${ln ? ` · line ${ln.point}${ln.model ? ` <span class="${ln.model.Over - (ln.fair?.Over ?? 0.5) > 0.04 ? "pos" : ln.model.Over - (ln.fair?.Over ?? 0.5) < -0.04 ? "neg" : ""}">model O ${prob(ln.model.Over)}</span>` : ""}` : ""}</span></div>
        ${spark2(p.last10?.[ks], ln?.point ?? p.proj?.[ks])}
        ${openP.has(key) ? `<div class="pr-detail">${detail(p)}</div>` : ""}</div>`;
    }).join("")}</div>`;
  return `<div class="card"><b>Players</b> <span class="tiny muted">our projections · tap a player for the last 10 games · dashed line = book line (or projection)</span>
    <div class="sides" style="margin-top:10px">${col(mu.players.away, g.awayRef, g.away, "a")}${col(mu.players.home, g.homeRef, g.home, "h")}</div></div>`;
}

function propGaps(mu) {
  const list = (mu.propEdges || []).filter((d) => Math.abs(d.diff) >= 0.05).slice(0, 8);
  if (!list.length) return "";
  return `<div class="sec-title">Where our model disagrees on props<span class="line"></span></div><div class="grid tight">${list.map((d) => {
    const side = d.diff > 0 ? "Over" : "Under";
    return `<div class="card col" style="gap:8px"><div class="row">${headshot({ player: d.player, headshot: d.headshot }, 40)}
      <div class="col"><b>${esc(d.player)}</b><span class="tiny muted">${esc(mktName(d.market))} ${d.point} · model projects ${d.mean}</span></div></div>
      ${track(d.fair, d.model, `Under ${d.point}`, `Over ${d.point}`)}
      <div class="row" style="justify-content:space-between"><span class="tiny muted">market O ${prob(d.fair)} · model O ${prob(d.model)}</span>
        <span class="pill ${Math.abs(d.diff) >= 0.08 ? "pos" : ""}">model: ${side} +${Math.round(Math.abs(d.diff) * 100)}</span></div></div>`;
  }).join("")}</div><p class="note">A gap is a lead to check (minutes, role, injury news), not a bet by itself. Picks need the logic and the price to agree.</p>`;
}

function preview(g) {
  const p = S.previews.get(g.id);
  if (!p) return `<div class="card note">Claude's written preview for this game posts with the daily run.</div>`;
  const paras = String(p.preview || "").split(/\n\s*\n/).filter(Boolean);
  return `<div class="card col" style="gap:8px"><div class="row" style="justify-content:space-between;flex-wrap:wrap"><b>${esc(p.headline || "Claude's read")}</b>
      ${p.lean ? `<span class="pill s">lean: ${esc(p.lean)}</span>` : ""}</div>
    ${paras.map((x) => `<p style="margin:0">${esc(x)}</p>`).join("")}
    ${(p.keys || []).length ? `<div class="pillars">${p.keys.map((k) => `<span class="pillar">${esc(k)}</span>`).join("")}</div>` : ""}
    ${p.updated ? `<span class="tiny muted">Updated ${esc(p.updated)}</span>` : ""}</div>`;
}

export function renderMatchup(el, g) {
  const mu = S.matchups.get(g.id);
  if (!mu) {
    el.insertAdjacentHTML("beforeend", `${preview(g)}<div class="empty"><h3>No model for this game yet</h3>Models cover NBA, WNBA, NHL, NFL, college football, MLB, soccer and tennis; this game is missing team data or a name match.</div>`);
    return;
  }
  el.insertAdjacentHTML("beforeend", `<div class="col" style="gap:12px">${preview(g)}
    <div class="mu-grid">${modelVsMarket(mu, g)}${factors(mu, g)}</div>
    ${tape(mu, g)}
    ${starters(mu, g)}
    ${goalies(mu, g)}
    ${injuries(mu, g)}
    ${lineups(mu, g)}
    ${players(mu, g)}
    ${propGaps(mu)}</div>`);
  el.querySelectorAll("[data-p]").forEach((r) => r.onclick = () => {
    const k = r.dataset.p; openP.has(k) ? openP.delete(k) : openP.add(k);
    window.dispatchEvent(new HashChangeEvent("hashchange"));   // re-render this screen in place
  });
  bindTips(el);
}
