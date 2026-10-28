// Projected profit on open bets (and open picks): each bet's chance right now (final score, live market or live model,
// else the latest sharp fair, else the fair when logged) turned into expected dollars, plus best/worst case and the
// chance of finishing up. Updates with every live pull.
import { S } from "./store.js";
import { esc, money, prob, am, fmtTime } from "./util.js";
import { settleProfit } from "./promo.js";
import { sitText } from "./views/live.js";

const N = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const phi = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
function erf(x) { const s = Math.sign(x); x = Math.abs(x); const t = 1 / (1 + 0.3275911 * x);
  return s * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x)); }

// the line in a game doc for this leg (spreads are stored as the home side's handicap)
function findLine(lines, home, leg) {
  const base = (leg.market || "").replace("alternate_", "");
  for (const l of lines || []) {
    if ((l.market || "").replace("alternate_", "") !== base) continue;
    if (!l.fair && !l.model) continue;
    if (leg.team && l.team && N(l.team) !== N(leg.team)) continue;
    const pt = l.point;
    if (base.startsWith("spreads")) {
      const want = N(leg.side) === N(home) ? leg.point : leg.point != null ? -leg.point : null;
      if (want == null || pt == null || Math.abs(pt - want) > 1e-6) continue;
    } else if (leg.point != null && pt != null && Math.abs(pt - leg.point) > 1e-6) continue;
    return l;
  }
  return null;
}
const sideKey = (obj, side) => obj ? Object.keys(obj).find((k) => N(k) === N(side)) : null;

function findProp(docs, leg) {
  for (const d of docs || []) {
    if (d.market !== leg.market) continue;
    for (const r of d.rows || []) {
      if (N(r.player) !== N(leg.player) || (leg.point != null && r.point != null && Math.abs(r.point - leg.point) > 1e-6)) continue;
      return r;
    }
  }
  return null;
}

// result from a final score (game lines only)
function fromFinal(f, leg) {
  if (!f || f.home?.score == null || leg.player) return null;
  const hs = f.home.score, as_ = f.away.score, base = (leg.market || "").replace("alternate_", "");
  const g = S.games.get(leg.gameId);
  const homeName = g?.home || f.home.name;
  const isHome = N(leg.side) === N(homeName) || N(leg.side) === N(f.home.name);
  if (base === "h2h") return hs === as_ ? 0.5 : (hs > as_) === isHome ? 1 : 0;
  if (base === "spreads" && leg.point != null) { const m = (isHome ? hs - as_ : as_ - hs) + leg.point; return m === 0 ? 0.5 : m > 0 ? 1 : 0; }
  if (base === "totals" && leg.point != null) { const t = hs + as_; return t === leg.point ? 0.5 : (t > leg.point) === (leg.side === "Over") ? 1 : 0; }
  return null;
}

// live normal approximation for basketball and football when the exact line isn't quoted live
const SD = { NBA: [12.5, 18.5], WNBA: [11, 15], NFL: [13.5, 13], NCAAF: [15.5, 15] };
function fromLiveModel(d, leg) {
  const m = d?.model, sd = SD[d?.league];
  if (!m || leg.point == null || (!sd && m.sdMargin == null)) return null;
  // the live model's own spread of outcomes for the time left, else the full-game sd scaled by time left
  const k = Math.sqrt(Math.max(0.02, m.left ?? 1)), base = (leg.market || "").replace("alternate_", "");
  const sM = m.sdMargin ?? (sd ? sd[0] * k : null), sT = m.sdTotal ?? (sd ? sd[1] * k : null);
  if (base === "spreads" && m.margin != null && sM) {
    const isHome = N(leg.side) === N(d.home), mar = isHome ? m.margin : -m.margin;
    return 1 - phi((-leg.point - mar) / sM);
  }
  if (base === "totals" && m.total != null && sT) { const po = 1 - phi((leg.point - m.total) / sT); return leg.side === "Over" ? po : 1 - po; }
  return null;
}

export function legNow(leg) {
  const fin = S.finals.find((x) => x.gameId === leg.gameId);
  const fr = fromFinal(fin, leg);
  if (fr != null) return { p: fr, src: "final" };
  const d = S.liveGames.get(leg.gameId);
  if (d) {
    if (leg.player) {
      const r = findProp(d.props, leg);
      const k = r && (sideKey(r.live, leg.side) || sideKey(r.fair, leg.side));
      if (r?.live && sideKey(r.live, leg.side)) return { p: r.live[sideKey(r.live, leg.side)], src: "live", cur: r.live.cur, proj: r.live.proj };
      if (k && r.fair?.[k] != null) return { p: r.fair[k], src: "live market" };
    } else {
      const l = findLine(d.lines, d.home, leg);
      const k = l && (sideKey(l.fair, leg.side) || sideKey(l.model, leg.side));
      if (l && k) return { p: l.fair?.[k] ?? l.model?.[k], src: "live" };
      const lm = fromLiveModel(d, leg);
      if (lm != null) return { p: lm, src: "live model" };
    }
  }
  const g = S.games.get(leg.gameId);
  if (g && !leg.player) {
    const l = findLine(g.lines, g.home, leg), k = l && sideKey(l.fair, leg.side);
    if (k) return { p: l.fair[k], src: new Date(g.commence) < new Date() ? "last pre-game" : "now" };
  }
  if (leg.player) {
    const docs = [...S.props.values()].filter((x) => x.id === leg.gameId);
    const r = findProp(docs, leg), k = r && sideKey(r.fair, leg.side);
    if (k) return { p: r.fair[k], src: "now" };
  }
  return { p: leg.fair ?? null, src: "when logged" };
}

// one open bet: chance now, what it pays, expected value
export function project(b, promo, units = false) {
  const legs = (b.legs && b.legs.length ? b.legs : [b]).filter((x) => x.gameId);
  let p = 1, srcs = [], known = true;
  for (const leg of legs) {
    const r = legNow({ ...leg, fair: leg.fair ?? (legs.length === 1 ? b.fairAtLog ?? b.fair : null) });
    if (r.p == null) { known = false; break; }
    p *= r.p === 0.5 && r.src === "final" ? 1 : r.p;          // a pushed leg drops out of a parlay
    srcs.push({ ...r, leg });
  }
  if (!legs.length || !known) { if (b.fairAtLog == null && b.fair == null) return null; p = b.fairAtLog ?? b.fair; srcs = [{ src: "when logged" }]; }
  const win = units ? (b.units || 0) * (b.dec - 1) : settleProfit(b, promo, "won");
  const lose = units ? -(b.units || 0) : settleProfit(b, promo, "lost");
  const push = legs.length === 1 && srcs[0]?.src === "final" && srcs[0]?.p === 0.5;
  if (push) return { b, p: null, ev: 0, win: 0, lose: 0, srcs, push: true };
  return { b, p, ev: p * win + (1 - p) * lose, win, lose, srcs };
}

export function summary(rows) {
  const ev = rows.reduce((s, r) => s + r.ev, 0);
  const best = rows.reduce((s, r) => s + Math.max(r.win, r.lose), 0), worst = rows.reduce((s, r) => s + Math.min(r.win, r.lose), 0);
  const v = rows.reduce((s, r) => s + (r.p == null ? 0 : r.p * (1 - r.p) * (r.win - r.lose) ** 2), 0);
  const up = v > 0 ? phi(ev / Math.sqrt(v)) : ev > 0 ? 1 : 0;
  return { ev, best, worst, up, live: rows.filter((r) => r.srcs.some((x) => /live|final/.test(x.src))).length };
}

// where a leg stands right now: score, what it still needs, where it's headed
const fmtN = (x) => Number.isInteger(+x) ? `${+x}` : (+x).toFixed(1);
export function progress(leg, r) {
  if (r.src === "final") return { cls: r.p === 1 ? "pos" : r.p === 0 ? "neg" : "", icon: r.p === 1 ? "✓" : r.p === 0 ? "✗" : "=", text: r.p === 1 ? "Won" : r.p === 0 ? "Lost" : "Push" };
  const d = S.liveGames.get(leg.gameId), st = d?.state || (S.live?.games || []).find((x) => x.id === leg.gameId)?.state;
  const g = S.games.get(leg.gameId);
  if (!st) return { cls: "muted", icon: "○", text: g ? `Starts ${fmtTime(g.commence)}` : "Not started" };
  const base = (leg.market || "").replace("alternate_", ""), pt = leg.point, m = d?.model || {};
  const over = leg.side === "Over" || leg.side === "Yes";
  const bar = (cur, target, proj) => {      // how far along: filled to now, tick at the line, dot at the projected finish
    const hi = Math.max(target, proj ?? 0, cur) * 1.12 || 1;
    return { cur: cur / hi, line: target / hi, proj: proj != null ? Math.min(1, proj / hi) : null };
  };
  if (leg.player) {
    if (r.cur == null) return { cls: "muted", icon: "●", text: "In play · live stat appears with the next live pull" };
    const cur = r.cur, proj = r.proj;
    if (over) {
      if (cur > pt) return { cls: "pos", icon: "✓", text: `${fmtN(cur)} · already over ${pt}`, bar: bar(cur, pt, proj) };
      const need = Math.floor(pt) + 1 - cur;
      return { cls: proj != null && proj > pt ? "pos" : "neg", icon: "●", text: `${fmtN(cur)} of ${pt} · needs ${fmtN(need)} more${proj != null ? ` · on pace for ${fmtN(proj)}` : ""}`, bar: bar(cur, pt, proj) };
    }
    if (cur > pt) return { cls: "neg", icon: "✗", text: `${fmtN(cur)} · already over ${pt}`, bar: bar(cur, pt, proj) };
    const room = Math.ceil(pt) - 1 - cur;
    return { cls: proj != null && proj < pt ? "pos" : "neg", icon: "●", text: `${fmtN(cur)} of ${pt} · room for ${fmtN(Math.max(0, room))} more${proj != null ? ` · on pace for ${fmtN(proj)}` : ""}`, bar: bar(cur, pt, proj) };
  }
  const hs = st.home?.score, as_ = st.away?.score;
  if (hs == null || as_ == null) return { cls: "muted", icon: "●", text: "In play" };
  const home = d?.home || g?.home, isHome = N(leg.side) === N(home);
  const ab = (h) => (h ? (d?.homeRef || g?.homeRef)?.abbr : (d?.awayRef || g?.awayRef)?.abbr) || (h ? "home" : "away");
  const me = isHome ? hs : as_, them = isHome ? as_ : hs, diff = me - them, who = ab(isHome);
  const lead = diff > 0 ? `${who} up ${fmtN(diff)}` : diff < 0 ? `${who} down ${fmtN(-diff)}` : "Tied";
  const projM = m.margin != null ? (isHome ? m.margin : -m.margin) : null;
  if (base === "h2h") return { cls: diff > 0 ? "pos" : diff < 0 ? "neg" : "", icon: "●", text: `${lead}${projM != null ? ` · model finish ${projM > 0 ? "+" : ""}${fmtN(Math.round(projM * 10) / 10)}` : ""}` };
  if (base.startsWith("spreads") && pt != null) {
    const cover = diff + pt;
    return { cls: cover > 0 ? "pos" : "neg", icon: "●", text: `${lead} · ${cover > 0 ? `covering ${pt > 0 ? "+" : ""}${pt} by ${fmtN(cover)}` : `${fmtN(-cover)} short of ${pt > 0 ? "+" : ""}${pt}`}${projM != null ? ` · model finish ${projM > 0 ? "+" : ""}${fmtN(Math.round(projM * 10) / 10)}` : ""}` };
  }
  if ((base.startsWith("totals") || base.startsWith("team_totals")) && pt != null) {
    const team = base.startsWith("team_totals");
    const cur = team ? (N(leg.team) === N(home) ? hs : as_) : hs + as_;
    const proj = team ? null : m.total ?? null;
    const lab = team ? `${leg.team?.split(" ").pop()} have ${fmtN(cur)}` : `Total ${fmtN(cur)}`;
    if (over) {
      if (cur > pt) return { cls: "pos", icon: "✓", text: `${lab} · already over ${pt}`, bar: bar(cur, pt, proj) };
      return { cls: proj != null && proj > pt ? "pos" : "neg", icon: "●", text: `${lab} · needs ${fmtN(Math.floor(pt) + 1 - cur)} more${proj != null ? ` · model finish ${fmtN(Math.round(proj * 10) / 10)}` : ""}`, bar: bar(cur, pt, proj) };
    }
    if (cur > pt) return { cls: "neg", icon: "✗", text: `${lab} · already over ${pt}`, bar: bar(cur, pt, proj) };
    return { cls: proj != null && proj < pt ? "pos" : "neg", icon: "●", text: `${lab} · room for ${fmtN(Math.max(0, Math.ceil(pt) - 1 - cur))} more${proj != null ? ` · model finish ${fmtN(Math.round(proj * 10) / 10)}` : ""}`, bar: bar(cur, pt, proj) };
  }
  return { cls: "muted", icon: "●", text: lead };
}
const progressHtml = (pg) => `<div class="pj-pg ${pg.cls}"><span class="pj-ic">${pg.icon}</span><span class="pj-tx">${esc(pg.text)}</span>
  ${pg.bar ? `<span class="pj-trk"><i style="width:${(Math.min(1, pg.bar.cur) * 100).toFixed(1)}%"></i><s style="left:${(pg.bar.line * 100).toFixed(1)}%"></s>${pg.bar.proj != null ? `<b style="left:${(pg.bar.proj * 100).toFixed(1)}%"></b>` : ""}</span>` : ""}</div>`;

const SRC = { final: "final", live: "live", "live model": "live model", "live market": "live market", now: "pre-game", "last pre-game": "pre-game", "when logged": "when logged" };

export function projectedCard({ items, title, fmt = money, units = false, settledToday = 0 }) {
  const rows = items.map((b) => project(b, b.promoId ? S.promos.get(b.promoId) : null, units)).filter(Boolean);
  if (!rows.length) return "";
  const s = summary(rows);
  const day = settledToday + s.ev;
  const state = (gid) => (S.live?.games || []).find((x) => x.id === gid)?.state;
  rows.sort((a, b) => (a.b.commence || "").localeCompare(b.b.commence || ""));
  return `<div class="card col pj" style="gap:10px">
    <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px"><b>${esc(title)}</b>
      <span class="tiny muted">${s.live ? `<span class="pill live">● ${s.live} in play</span> ` : ""}updates with every live pull</span></div>
    <div class="pr-ck">
      <div class="kpi"><div class="k">Projected</div><div class="v ${s.ev >= 0 ? "pos" : "neg"}">${fmt(s.ev)}</div><div class="s">on ${rows.length} open</div></div>
      ${!units ? `<div class="kpi"><div class="k">Today so far + projected</div><div class="v ${day >= 0 ? "pos" : "neg"}">${fmt(day)}</div><div class="s">${fmt(settledToday)} settled today</div></div>` : ""}
      <div class="kpi"><div class="k">Chance to finish up</div><div class="v">${Math.round(s.up * 100)}%</div><div class="s">on these bets</div></div>
      <div class="kpi"><div class="k">Range</div><div class="v small">${fmt(s.worst)} to ${fmt(s.best)}</div><div class="s">all lose · all win</div></div>
    </div>
    <div class="col" style="gap:4px">${rows.map((r) => {
      const b = r.b, st = state(b.gameId), was = b.fairAtLog ?? b.fair, d = r.p != null && was != null ? r.p - was : null;
      return `<div class="pj-row">
        <div class="col" style="gap:1px;min-width:0"><span class="ellip small">${esc(b.label || b.selection)}</span>
          <span class="tiny muted ellip">${st ? `<b>${st.away?.score ?? ""}–${st.home?.score ?? ""}</b> ${esc(sitText(st, b.league))} · ` : b.commence ? `${fmtTime(b.commence)} · ` : ""}${esc(SRC[r.srcs[0]?.src] || "")}${r.srcs[0]?.cur != null ? ` · ${r.srcs[0].cur} so far` : ""}${!units && b.am != null ? ` · ${am(b.am)} · ${money(b.stake)}` : units ? ` · ${b.units}u` : ""}</span></div>
        <div class="pj-bar" title="chance now${was != null ? ` (was ${prob(was)})` : ""}"><i style="width:${((r.p ?? 0.5) * 100).toFixed(0)}%"></i>${was != null ? `<s style="left:${(was * 100).toFixed(0)}%"></s>` : ""}</div>
        <span class="num small">${r.push ? "push" : prob(r.p)}${d != null && Math.abs(d) >= 0.03 ? ` <span class="${d > 0 ? "pos" : "neg"}">${d > 0 ? "▲" : "▼"}</span>` : ""}</span>
        <span class="num small ${r.ev >= 0 ? "pos" : "neg"}">${fmt(r.ev)}</span>
        <div class="pj-legs">${r.srcs.filter((x) => x.leg).map((x) => `${r.srcs.length > 1 ? `<div class="tiny muted ellip pj-ll">${esc(x.leg.label || "")}</div>` : ""}${progressHtml(progress(x.leg, x))}`).join("")}</div></div>`;
    }).join("")}</div>
    <div class="tiny muted">Chance now: final score, else the live market (our live model when the exact line isn't quoted), else the latest sharp fair, else your fair when logged. The tick on each bar is where it started. Parlays multiply their legs.</div>
  </div>`;
}
