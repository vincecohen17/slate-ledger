// Finished games: compact cards (Board, Live) and the full final page (score, line score, our pre-game numbers vs the
// result, team stats, scoring plays, box scores).
import { S, loadFinal } from "../store.js";
import { esc, prob, fmtTime, fmtDay, teamLogo, signPt, $$ } from "../util.js";

const ref = (t) => ({ abbr: t.abbr, logo: t.logo, color: t.color ? (String(t.color).startsWith("#") ? t.color : "#" + t.color) : null });
// team color for bars, unless it is too dark or too light to see on either theme
function barColor(t, fallback) {
  const c = ref(t).color;
  if (!c || !/^#[0-9a-f]{6}$/i.test(c)) return fallback;
  const n = parseInt(c.slice(1), 16), l = (0.2126 * (n >> 16) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
  return l < 0.14 || l > 0.9 ? fallback : c;
}
const sc = (v) => v == null ? "–" : Number.isInteger(+v) ? +v : v;
let boxTeam = {};

// how our pre-game numbers did: one chip per market (our side, its chance, and whether it hit)
function verdicts(x, short = false) {
  const pre = x.pre, res = x.result;
  if (!pre || !res) return "";
  const h = x.home.abbr || "home", a = x.away.abbr || "away", chips = [];
  const one = (ourLab, otherLab, pModel, pMkt, hit) => {
    if (hit == null || pModel == null) return;
    const ours = pModel >= 0.5;                       // the side our model favored
    const won = ours ? hit === 1 : hit === 0;
    chips.push(`<span class="fv ${won ? "ok" : "bad"}" title="our model ${prob(ours ? pModel : 1 - pModel)} · market ${prob(ours ? pMkt : 1 - pMkt)}">${won ? "✓" : "✗"} ${esc(ours ? ourLab : otherLab)}${short ? "" : ` <span class="tiny">${prob(ours ? pModel : 1 - pModel)}</span>`}</span>`);
  };
  if (pre.h2h) one(h, a, pre.h2h.p, pre.h2h.f, res.h2h);
  if (pre.spreads) one(`${h} ${signPt(pre.spreads.pt)}`, `${a} ${signPt(-pre.spreads.pt)}`, pre.spreads.p, pre.spreads.f, res.spreads);
  if (pre.totals) one(`O ${pre.totals.pt}`, `U ${pre.totals.pt}`, pre.totals.p, pre.totals.f, res.totals);
  return chips.length ? `<div class="fvs">${short ? `<span class="tiny muted">Model</span>` : ""}${chips.join("")}</div>` : "";
}

export function finalCard(x) {
  const a = x.away, h = x.home;
  const href = x.gameId ? `#/game/${x.gameId}` : `#/final/${encodeURIComponent(x.key)}`;
  const row = (t) => `<div class="fc-row ${t.winner ? "win" : ""}">${x.tennis ? "" : teamLogo(ref(t), t.name, 26)}<span class="ellip">${esc(x.tennis ? t.name : t.name || t.abbr)}</span><b class="num">${sc(t.score)}</b></div>`;
  return `<a class="card fcard" href="${href}">
    <div class="card-head"><span class="row" style="gap:6px"><b style="color:var(--accent)">${esc(x.league)}</b> · <span class="pill">${esc(x.status || "Final")}</span></span><span class="tiny faint">${fmtTime(x.commence)}</span></div>
    ${row(a)}${row(h)}
    ${verdicts(x, true)}
  </a>`;
}

// ---------- the final page
function lineScore(x) {
  const n = Math.max(x.home.line?.length || 0, x.away.line?.length || 0);
  if (!n) return "";
  const REG = { NHL: 3, MLB: 99, NCAAB: 2, EPL: 2, MLS: 2, UCL: 2, UEL: 2, "La Liga": 2, Bundesliga: 2, "Serie A": 2, "Ligue 1": 2, "Liga MX": 2 };
  const reg = x.tennis ? 99 : REG[x.league] ?? 4;
  const heads = Array.from({ length: n }, (_, i) => x.tennis ? `S${i + 1}` : i < reg ? i + 1 : i === reg ? (["NHL"].includes(x.league) && i === reg + 1 ? "SO" : "OT") : `${i - reg + 1}OT`);
  const tr = (t) => `<tr class="${t.winner ? "win" : ""}"><td>${esc(t.abbr || t.name)}</td>${Array.from({ length: n }, (_, i) => `<td>${esc(t.line?.[i] ?? "")}</td>`).join("")}<td><b>${sc(t.score)}</b></td></tr>`;
  return `<div style="overflow-x:auto"><table class="l10t fls"><thead><tr><th></th>${heads.map((hh) => `<th>${hh}</th>`).join("")}<th>${x.tennis ? "Sets" : "T"}</th></tr></thead>
    <tbody>${tr(x.away)}${tr(x.home)}</tbody></table></div>`;
}

function preCard(x) {
  const pre = x.pre;
  if (!pre) return "";
  const h = x.home.abbr || "home";
  const tile = (title, line, p, f, hit, sideHome) => {
    if (p == null) return "";
    const ours = p >= 0.5, won = hit == null ? null : ours ? hit === 1 : hit === 0;
    return `<div class="kpi"><div class="k">${esc(title)}</div><div class="v small">${esc(line)}</div>
      <div class="s">model ${prob(p)} · market ${prob(f)}</div>
      ${won == null ? `<span class="pill">push</span>` : `<span class="pill ${won ? "pos" : "neg"}">${won ? "model side won" : "model side lost"}</span>`}</div>`;
  };
  return `<div class="card col" style="gap:8px"><b>What we said before the game</b>
    <div class="pr-ck">
      ${pre.h2h ? tile("Moneyline", `${h} to win`, pre.h2h.p, pre.h2h.f, x.result?.h2h) : ""}
      ${pre.spreads ? tile("Spread", `${h} ${signPt(pre.spreads.pt)}`, pre.spreads.p, pre.spreads.f, x.result?.spreads) : ""}
      ${pre.totals ? tile("Total", `Over ${pre.totals.pt}`, pre.totals.p, pre.totals.f, x.result?.totals) : ""}
    </div><div class="tiny muted">Chances are for the side shown; the pill says whether the side our model favored came in. Final total: ${sc((x.home.score || 0) + (x.away.score || 0))}, margin ${x.home.abbr} ${signPt((x.home.score || 0) - (x.away.score || 0))}.</div></div>`;
}

function teamStats(x) {
  const rows = (x.teamStats || []).map((s) => {
    const nh = parseFloat(String(s.h).replace(/[^\d.-]/g, "")), na = parseFloat(String(s.a).replace(/[^\d.-]/g, ""));
    const ok = isFinite(nh) && isFinite(na) && nh + na > 0;
    const wa = ok ? na / (na + nh) * 100 : 50;
    return `<div class="fts"><span class="num ${ok && na > nh ? "b" : ""}">${esc(s.a)}</span>
      <div class="fts-bar">${ok ? `<i class="a" style="width:${wa.toFixed(0)}%;background:${esc(barColor(x.away, "var(--faint)"))}"></i><i class="h" style="width:${(100 - wa).toFixed(0)}%;background:${esc(barColor(x.home, "var(--muted)"))}"></i>` : ""}</div>
      <span class="num ${ok && nh > na ? "b" : ""}">${esc(s.h)}</span><span class="fts-k tiny muted">${esc(s.k)}</span></div>`;
  });
  return rows.length ? `<div class="card col" style="gap:6px"><div class="row" style="justify-content:space-between"><b>${esc(x.away.abbr)}</b><b>Team stats</b><b>${esc(x.home.abbr)}</b></div>${rows.join("")}</div>` : "";
}

function plays(x) {
  if (!x.plays?.length) return "";
  return `<div class="card col" style="gap:4px"><b>Scoring</b>${x.plays.map((p) => `<div class="fpl"><span class="tiny muted">${p.per ? (x.league === "MLB" ? `Inn ${p.per}` : `P${p.per}`) : ""} ${esc(p.clk || "")}</span>
    <span class="pill">${esc(p.team || "")}</span><span class="small grow">${esc(p.t || "")}</span>${p.hs != null ? `<span class="num tiny">${p.as}-${p.hs}</span>` : ""}</div>`).join("")}</div>`;
}

function boxScore(x) {
  const teams = Object.keys(x.box || {});
  if (!teams.length) return "";
  const cur = boxTeam[x.key] && teams.includes(boxTeam[x.key]) ? boxTeam[x.key] : teams[0];
  const groups = x.box[cur] || [];
  return `<div class="card col" style="gap:8px"><div class="row" style="gap:6px;flex-wrap:wrap"><b>Box score</b>
      ${teams.map((t) => `<button class="chip sm ${t === cur ? "on" : ""}" data-boxt="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    ${groups.map((g) => `<div style="overflow-x:auto">${g.name ? `<div class="tiny muted" style="margin:4px 0">${esc(g.name)}</div>` : ""}
      <table class="l10t fbox"><thead><tr><th>Player</th>${g.labels.map((l) => `<th>${esc(l)}</th>`).join("")}</tr></thead><tbody>
      ${g.rows.map((r) => `<tr><td class="${r.st ? "b" : ""}">${esc(r.n)}${r.pos ? ` <span class="tiny faint">${esc(r.pos)}</span>` : ""}</td>${r.v.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>`).join("")}
      ${g.tot ? `<tr class="tot"><td>Team</td>${g.tot.map((v) => `<td>${esc(v)}</td>`).join("")}</tr>` : ""}</tbody></table></div>`).join("")}
    <div class="tiny faint">Starters in bold.</div></div>`;
}

// a final belongs to a game only if it started at the same time (series play the same teams on back-to-back days)
const sameStart = (x, g) => !g || Math.abs(new Date(x.commence) - new Date(g.commence)) < 4 * 3600000;
export function findFinal(gameId) { return S.finals.find((x) => x.gameId === gameId && sameStart(x, S.games.get(gameId))) || null; }

export function renderFinal(el, key) {
  loadFinal(key);
  const row = S.finals.find((x) => x.key === key);
  const x = S.finalDocs.get(key) || row;
  if (!x) { el.innerHTML = `<div class="empty"><h3>Game not found</h3>Finished games stay here for three days.</div>`; return; }
  const a = x.away, h = x.home;
  const pick = [...S.picks.values()].filter((p) => x.gameId && p.gameId === x.gameId);
  el.innerHTML = `<div class="col" style="gap:12px;margin-top:14px">
    <a href="#/" class="tiny muted">← Board</a>
    <div class="card col" style="gap:10px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px"><span class="row" style="gap:6px"><b style="color:var(--accent)">${esc(x.league)}</b><span class="pill">${esc(x.status || "Final")}</span></span>
        <span class="tiny muted">${fmtDay(x.commence)} · ${fmtTime(x.commence)}${x.venue ? ` · ${esc(x.venue)}` : ""}${x.event ? ` · ${esc(x.event)}` : ""}</span></div>
      <div class="fscore">
        <div class="ft ${a.winner ? "win" : ""}">${x.tennis ? "" : teamLogo(ref(a), a.name, 52)}<span>${esc(a.name)}</span>${a.record ? `<span class="tiny muted">${esc(a.record)}</span>` : ""}</div>
        <div class="fnum"><b class="${a.winner ? "" : "muted"}">${sc(a.score)}</b><span class="faint">–</span><b class="${h.winner ? "" : "muted"}">${sc(h.score)}</b></div>
        <div class="ft ${h.winner ? "win" : ""}">${x.tennis ? "" : teamLogo(ref(h), h.name, 52)}<span>${esc(h.name)}</span>${h.record ? `<span class="tiny muted">${esc(h.record)}</span>` : ""}</div>
      </div>
      ${lineScore(x)}
    </div>
    ${pick.length ? `<div class="card col" style="gap:6px"><b>Our pick${pick.length > 1 ? "s" : ""}</b>${pick.map((p) => `<div class="row" style="gap:8px"><span class="grow">${esc(p.selection)}</span><span class="pill ${p.status === "won" ? "pos" : p.status === "lost" ? "neg" : ""}">${esc(p.status === "void" ? "withdrawn" : p.status || "pending")}</span></div>`).join("")}</div>` : ""}
    ${preCard(x)}
    ${S.finalDocs.has(key) ? `${teamStats(x)}${plays(x)}${boxScore(x)}` : `<div class="card note">Loading the box score…</div>`}
  </div>`;
  $$("[data-boxt]", el).forEach((b) => b.onclick = () => { boxTeam[key] = b.dataset.boxt; renderFinal(el, key); });
}
