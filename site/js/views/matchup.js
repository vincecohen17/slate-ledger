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
  const proj = mu.league === "NBA" ? `Model score ${esc(ab("away"))} ${mu.teams.away.pts} – ${esc(ab("home"))} ${mu.teams.home.pts}`
    : `Expected goals ${esc(ab("away"))} ${mu.teams.away.goals} – ${esc(ab("home"))} ${mu.teams.home.goals}`;
  return `<div class="card"><div class="row" style="justify-content:space-between;flex-wrap:wrap"><b>Model vs market</b><span class="tiny muted">${proj}</span></div>
    <div class="col" style="gap:14px;margin-top:12px">${rows.map((r) => `<div>
      <div class="row" style="justify-content:space-between"><span class="small">${esc(r.k)}</span>
        ${Math.abs(r.lean) >= 0.04 ? `<span class="pill pos">model leans ${esc(r.side)} · ${Math.round(Math.abs(r.lean) * 100)} pts</span>` : `<span class="pill">close to market</span>`}</div>
      ${track(r.mk, r.md, r.l, r.r)}
      <div class="row tiny muted" style="justify-content:space-between"><span><i class="lg-dot mk"></i>market ${prob(r.mk)}</span><span><i class="lg-dot md"></i>model ${prob(r.md)}</span></div></div>`).join("")}</div>
    <p class="note" style="margin-bottom:0">Each bar runs from the left outcome (0%) to the right outcome (100%). Gray marks the sharp market, gold marks our model.</p></div>`;
}

function tape(mu, g) {
  const n = mu.league === "NHL" ? 32 : 30;
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

function goalies(mu, g) {
  if (mu.league !== "NHL") return "";
  const card = (t, ref, name) => {
    const gl = t.goalie || {};
    const r = gl.rank || {}, pool = gl.pool || "";
    const starts = (gl.starts || []).map((s) => `<div class="gstart"><span class="tiny muted">${esc(s.date.slice(5).replace("-", "/"))}</span>
        <span class="who"><i class="${s.name === gl.name ? "on" : ""}"></i><span class="ellip">${esc(s.name)}</span><span class="tiny muted">vs ${esc(s.opp)}</span></span>
        <span class="num">${s.sv}/${s.sa}</span><span class="num ${s.svp >= 0.915 ? "pos" : s.svp < 0.88 ? "neg" : ""}">${s.svp != null ? s.svp.toFixed(3).replace(/^0/, "") : "–"}</span></div>`).join("");
    const L = gl.last10 || {};
    return `<div class="col" style="gap:6px"><div class="side-h">${teamLogo(ref, name, 22)}<span>${esc(gl.name || "Unknown")}</span></div>
      <div class="row tiny" style="flex-wrap:wrap;gap:6px">
        <span class="pill">all ${fmtS(gl.gsax60, 2)}/60${r.gsax60 ? ` · #${r.gsax60}/${pool}` : ""}</span>
        <span class="pill">EV ${fmtS(gl.ev60, 2)}/60${r.ev60 ? ` · #${r.ev60}` : ""}</span>
        <span class="pill">PK ${fmtS(gl.pk2, 3)}/2m${r.pk2 ? ` · #${r.pk2}` : ""}</span>
        <span class="pill s">proj saves ${gl.proj?.saves ?? "–"}</span></div>
      ${L.sv?.length ? `<div class="l10">${spark2(L.sv, gl.proj?.saves)}<span>saves last ${L.sv.length}: ${hitRate(L.sv, gl.proj?.saves)} the projection · avg sv% ${(L.sv.reduce((a, b) => a + b, 0) / Math.max(1, L.sa.reduce((a, b) => a + b, 0))).toFixed(3).replace(/^0/, "")}</span></div>` : ""}
      <div class="tiny muted" style="margin-top:4px">Team's last ${gl.starts?.length || 0} starts (gold = likely starter)</div>${starts}</div>`;
  };
  return `<div class="card"><b>Likely goalies</b> <span class="tiny muted">from recent starts · confirm before betting</span>
    <div class="sides" style="margin-top:10px">${card(mu.teams.away, g.awayRef, g.away)}${card(mu.teams.home, g.homeRef, g.home)}</div></div>`;
}

const fmtS = (v, d) => v == null ? "–" : `${v > 0 ? "+" : ""}${(+v).toFixed(d)}`;

const PSTATS = {
  NBA: [["pts", "PTS"], ["reb", "REB"], ["ast", "AST"], ["fg3", "3PM"], ["stl", "STL"], ["blk", "BLK"], ["tov", "TOV"]],
  NHL: [["sog", "SOG"], ["pts", "PTS"], ["g", "G"], ["a", "A"], ["blk", "BLK"], ["hits", "HIT"]],
  NFL: [["pass_yds", "PYD"], ["rush_yds", "RYD"], ["car", "CAR"], ["rec", "REC"], ["rec_yds", "RECYD"], ["td", "TD"]],
};
const MIN_KEY = { NBA: ["min", "MIN"], NHL: ["toi", "TOI"], NFL: ["targets", "TGT"] };
// the stat and prop market that matter most for a player, for the sparkline and book line
function keyStat(league, p) {
  if (league === "NBA") return ["pts", "player_points"];
  if (league === "NHL") return ["sog", "player_shots_on_goal"];
  if (league === "NFL") return p.proj?.att ? ["pass_yds", "player_pass_yds"] : /RB|FB/.test(p.pos || "") ? ["rush_yds", "player_rush_yds"] : ["rec_yds", "player_reception_yds"];
  return [null, null];
}
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
      <span class="tiny muted" style="margin-left:auto">${ml} · ${stats.slice(0, 4).map(([, h]) => h).join(" · ")} · last 10 key stat</span></div>
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
    el.insertAdjacentHTML("beforeend", `${preview(g)}<div class="empty"><h3>No model for this game yet</h3>Models cover NBA, NHL, NFL, college football, MLB, soccer and tennis; this game is missing team data or a name match.</div>`);
    return;
  }
  el.insertAdjacentHTML("beforeend", `<div class="col" style="gap:12px">${preview(g)}
    <div class="mu-grid">${modelVsMarket(mu, g)}${factors(mu, g)}</div>
    ${tape(mu, g)}
    <div class="mu-grid">${injuries(mu, g)}${goalies(mu, g) || ""}</div>
    ${players(mu, g)}
    ${propGaps(mu)}</div>`);
  el.querySelectorAll("[data-p]").forEach((r) => r.onclick = () => {
    const k = r.dataset.p; openP.has(k) ? openP.delete(k) : openP.add(k);
    window.dispatchEvent(new HashChangeEvent("hashchange"));   // re-render this screen in place
  });
  bindTips(el);
}
