// Projected profit on open bets (and open picks): each bet's chance right now (final score, live market or live model,
// else the latest sharp fair, else the fair when logged) turned into expected dollars, plus best/worst case and the
// chance of finishing up. Updates with every live pull.
// Bets on the same number are linked, not independent: both sides of a prop, a total, a team total, or the game margin
// (spreads and moneylines). An arb can't win both legs; a middle can. Each linked group gets one consistent set of
// outcomes, so the range, the chance to finish up and the hedge math are exact.
import { S } from "./store.js";
import { esc, money, prob, am, fmtTime, mktName, bookLogo, headshot, teamLogo } from "./util.js";
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

// bets logged before legs carried market/side/point: read them from the label the Bet Lab wrote
const PROPW = { walks: "batter_walks", hits: "batter_hits", "hits runs rbis": "batter_hits_runs_rbis", "total bases": "batter_total_bases",
  strikeouts: "pitcher_strikeouts", "earned runs": "pitcher_earned_runs", "total saves": "player_total_saves", rbis: "batter_rbis",
  "runs scored": "batter_runs_scored", "home runs": "batter_home_runs", outs: "pitcher_outs", "hits allowed": "pitcher_hits_allowed" };
export function resolveLeg(leg) {
  if (!leg || (leg.market && leg.side)) return leg;
  const text = String(leg.label || "").trim().replace(/\s*\([^)]*\)\s*$/, "");
  const g = S.games.get(leg.gameId) || S.liveGames.get(leg.gameId);
  const team = (x) => g && [g.home, g.away].find((t) => N(t) === N(x));
  let m;
  if ((m = text.match(/^(Over|Under) (\d+(?:\.\d+)?)$/))) return { ...leg, market: "totals", side: m[1], point: +m[2] };
  if ((m = text.match(/^(.+) ML$/)) && team(m[1])) return { ...leg, market: "h2h", side: team(m[1]), point: null };
  if ((m = text.match(/^(.+) ([+-]\d+(?:\.\d+)?)$/)) && team(m[1])) return { ...leg, market: "spreads", side: team(m[1]), point: +m[2] };
  if ((m = text.match(/^(.+) (Over|Under) (\d+(?:\.\d+)?)$/)) && team(m[1])) return { ...leg, market: "team_totals", side: m[2], point: +m[3], team: team(m[1]) };
  if ((m = text.match(/^(.+?) (Over|Under|More|Less) (\d+(?:\.\d+)?) ([a-z0-9 ]+)$/i))) {
    const words = m[4].toLowerCase().trim(), sd = m[2].toLowerCase();
    const doc = [...S.props.values()].find((d) => d.id === leg.gameId && mktName(d.market) === words);
    return { ...leg, market: doc?.market || PROPW[words] || `player_${words.replace(/ /g, "_")}`, side: sd === "more" || sd === "over" ? "Over" : "Under", point: +m[3], player: m[1] };
  }
  return leg;
}
export const betLegs = (b) => (b.legs && b.legs.length ? b.legs : [b]).map((x, i) => ({ ...resolveLeg(x), _k: `${b.id}#${i}` }));

// Last known numbers per leg and per game: a live pull that comes back without a game or its props must not wipe
// what we already knew. Running stats and scores never go backwards, a cleared line stays cleared, and the last live
// reading is kept (up to 3 hours) until a newer live number or the final arrives.
const memo = new Map(), stMemo = new Map();
const RANK = { final: 5, live: 4, "live market": 3, "live model": 3, "last pre-game": 1, now: 1, "when logged": 0 };
const legKey = (l) => [l.gameId, l.market, l.side, l.point, l.player, l.team].join("|");
export function gameState(gid) {
  const st = S.liveGames.get(gid)?.state || (S.live?.games || []).find((x) => x.id === gid)?.state;
  const old = stMemo.get(gid);
  if (st) {
    const keep = old && old.st && ["home", "away"].some((h) => (old.st[h]?.score ?? 0) > (st[h]?.score ?? 0));
    if (!keep) stMemo.set(gid, { st, at: Date.now() });
    return keep ? old.st : st;
  }
  return old && Date.now() - old.at < 3 * 3600000 ? old.st : null;
}

export function legNow(leg) {
  const raw = legRaw(leg), k = legKey(leg), old = memo.get(k);
  let r = raw;
  if (old && raw.src !== "final") {
    const fresh = Date.now() - old.at < 3 * 3600000;
    if (old.cashed) r = { ...old };                                           // already won or lost on the stat
    else if (fresh && (RANK[raw.src] ?? 0) < 3 && (RANK[old.src] ?? 0) >= 3) r = { ...old, held: true };
    else if (raw.cur != null && old.cur != null && raw.cur < old.cur) r = { ...raw, cur: old.cur, proj: Math.max(raw.proj ?? 0, old.proj ?? 0) || raw.proj };
  }
  // a running stat or total past the line settles the leg: over = won, under = lost
  if (r.cur != null && leg.point != null && r.cur > leg.point && ["Over", "Under", "Yes", "No"].includes(leg.side)) {
    r = { ...r, p: leg.side === "Over" || leg.side === "Yes" ? 1 : 0, cashed: true };
  }
  if (!r.held && (RANK[r.src] ?? 0) >= 3 || r.cashed) memo.set(k, { ...r, at: r.held ? old.at : Date.now() });
  return r;
}

function legRaw(leg) {
  const fin = S.finals.find((x) => x.gameId === leg.gameId);
  const fr = fromFinal(fin, leg);
  if (fr != null) return { p: fr, src: "final" };
  const d = S.liveGames.get(leg.gameId);
  if (d?.final && d.state?.home?.score != null) {     // just finished, not graded yet: the final score decides it
    const fr2 = fromFinal({ home: { score: d.state.home.score, name: d.home }, away: { score: d.state.away.score, name: d.away } }, leg);
    if (fr2 != null) return { p: fr2, src: "final" };
  }
  if (d) {
    const bl = leg._k && d.betLegs?.[leg._k];       // the engine's numbers for this exact leg: box-score stat every live run
    if (bl && bl.p != null) return { p: bl.p, src: d.final ? "final" : "live", cur: bl.cur, proj: bl.proj, dnp: bl.dnp };
    if (leg.player) {
      const r = findProp(d.props, leg);
      if (bl && bl.cur != null && !(r?.live)) {
        const k0 = r && sideKey(r.fair, leg.side);
        if (k0 && r.fair[k0] != null) return { p: r.fair[k0], src: "live market", cur: bl.cur, proj: bl.proj };
      }
      const k = r && (sideKey(r.live, leg.side) || sideKey(r.fair, leg.side));
      if (r?.live && sideKey(r.live, leg.side)) return { p: r.live[sideKey(r.live, leg.side)], src: "live", cur: r.live.cur, proj: r.live.proj };
      if (k && r.fair?.[k] != null) return { p: r.fair[k], src: "live market" };
    } else {
      const l = findLine(d.lines, d.home, leg);
      const k = l && (sideKey(l.fair, leg.side) || sideKey(l.model, leg.side));
      const st = d.state, base = (leg.market || "").replace("alternate_", "");
      const cur = st && st.home?.score != null && base.startsWith("totals") ? st.home.score + st.away.score
        : st && st.home?.score != null && base.startsWith("team_totals") ? (N(leg.team) === N(d.home) ? st.home.score : st.away.score) : null;
      if (l && k) return { p: l.fair?.[k] ?? l.model?.[k], src: "live", cur };
      const lm = fromLiveModel(d, leg);
      if (lm != null) return { p: lm, src: "live model", cur };
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
  const legs = betLegs(b).filter((x) => x.gameId);
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

// the number a single bet rides on, so bets on the same number can be linked: {key, t, up} means the bet wins when
// that number ends above t (up) or below t. Game margin = home score minus away score.
function specOf(r) {
  if (r.push || r.p == null) return null;
  // a parlay whose other legs already won (or pushed) rides on its last leg alone, so a hedge on that leg links to it
  const done = (x) => (x.src === "final" && (x.p === 1 || x.p === 0.5)) || (x.cashed && x.p === 1);
  const legs = r.srcs.filter((x) => x.leg), open = legs.length > 1 ? legs.filter((x) => !done(x)) : legs;
  if (open.length !== 1) return null;                 // other parlays stay on their own
  const leg = open[0].leg, gid = leg.gameId, side = leg.side;
  if (!gid || !leg.market) return null;
  const base = leg.market.replace("alternate_", "");
  if (leg.player) {
    const nm = `${leg.player} ${mktName(base)}`, key = `${gid}|${base}|${N(leg.player)}`;
    if (["Over", "Under"].includes(side) && leg.point != null) return { key, t: +leg.point, up: side === "Over", name: nm };
    if (["Yes", "No"].includes(side)) return { key, t: leg.point ?? 0.5, up: side === "Yes", name: nm };
    return null;
  }
  const g = S.games.get(gid) || S.liveGames.get(gid);
  if (/^(team_)?totals/.test(base) && ["Over", "Under"].includes(side) && leg.point != null)
    return { key: `${gid}|${base}|${N(leg.team)}`, t: +leg.point, up: side === "Over", name: base.startsWith("team") ? `${leg.team} points` : `${g ? `${g.away} @ ${g.home} ` : ""}total` };
  if (/^(spreads|h2h)(_[a-z0-9]+)?$/.test(base) && g) {
    const per = base.replace(/^(spreads|h2h)/, "");
    if (base.startsWith("h2h") && (g.lines || []).some((l) => (l.market || "").startsWith("h2h") && (l.outcomes || []).includes("Draw"))) return null;
    const home = N(side) === N(g.home), away = N(side) === N(g.away);
    const pt = base.startsWith("h2h") ? 0 : leg.point;
    if ((!home && !away) || pt == null) return null;
    const key = `${gid}|margin${per}`, name = `${g.home} margin${per ? ` (${per.slice(1)})` : ""}`;
    return home ? { key, t: -pt, up: true, name } : { key, t: +pt, up: false, name };   // home covers when margin > -pt; away when margin < pt
  }
  return null;
}

// one linked group: the chance the number ends above each line (from every bet's own chance, made consistent: it can
// only fall as the line rises), then what the group pays in each stretch between the lines
function linked(rs) {
  const T = [...new Set(rs.map((r) => r.spec.t))].sort((a, b) => a - b);
  let blocks = T.map((t) => { const v = rs.filter((r) => r.spec.t === t).map((r) => (r.spec.up ? r.p : 1 - r.p)); return { v: v.reduce((a, b) => a + b, 0) / v.length, w: v.length, n: 1 }; });
  for (let i = 0; i < blocks.length - 1;) {        // pool adjacent violators: survival must not rise
    if (blocks[i + 1].v > blocks[i].v + 1e-12) {
      const a = blocks[i], b = blocks[i + 1], w = a.w + b.w;
      blocks.splice(i, 2, { v: (a.v * a.w + b.v * b.w) / w, w, n: a.n + b.n });
      if (i > 0) i--;
    } else i++;
  }
  const sv = blocks.flatMap((b) => Array(b.n).fill(Math.max(0, Math.min(1, b.v))));
  const regions = [];
  for (let i = 0; i <= T.length; i++) {
    const p = (i === 0 ? 1 : sv[i - 1]) - (i === T.length ? 0 : sv[i]);
    const x = i === 0 ? T[0] - 0.25 : i === T.length ? T.at(-1) + 0.25 : (T[i - 1] + T[i]) / 2;
    const label = i === 0 ? `under ${T[0]}` : i === T.length ? `over ${T.at(-1)}` : `${T[i - 1]} to ${T[i]}`;
    const pay = rs.reduce((s2, r) => s2 + ((r.spec.up ? x > r.spec.t : x < r.spec.t) ? r.win : r.lose), 0);
    regions.push({ p: Math.max(0, p), x, label, pay });
  }
  for (const r of rs) {                            // each bet's chance, consistent with the group
    r.p = regions.reduce((s2, g) => s2 + ((r.spec.up ? g.x > r.spec.t : g.x < r.spec.t) ? g.p : 0), 0);
    r.ev = r.p * r.win + (1 - r.p) * r.lose;
  }
  return { name: rs[0].spec.name, rows: rs, regions };
}

const conv = (a, b) => {
  const m = new Map();
  for (const [x, p] of a) for (const [y, q] of b) { if (p * q < 1e-12) continue; const k = Math.round((x + y) * 100) / 100; m.set(k, (m.get(k) || 0) + p * q); }
  if (m.size <= 4000) return m;
  const ks = [...m.keys()], lo = Math.min(...ks), w = (Math.max(...ks) - lo) / 2000 || 1, out = new Map();     // coarser buckets, same mass
  for (const [x, p] of m) { const k = Math.round((lo + Math.round((x - lo) / w) * w) * 100) / 100; out.set(k, (out.get(k) || 0) + p); }
  return out;
};

// every open bet together: linked groups as one outcome each, everything else independent
export function summary(rows, settled = 0) {
  const groups = new Map(), comps = [];
  for (const r of rows) { r.spec = specOf(r); r.link = null; if (r.spec) { if (!groups.has(r.spec.key)) groups.set(r.spec.key, []); groups.get(r.spec.key).push(r); } }
  const links = [];
  for (const [, rs] of groups) {
    if (rs.length < 2) { rs.forEach((r) => (r.spec = null)); continue; }
    const L = linked(rs);
    rs.forEach((r) => (r.link = L));
    links.push(L);
    comps.push(L.regions.map((g) => [g.pay, g.p]));
  }
  for (const r of rows) if (!r.link) comps.push(r.push || r.p == null ? [[0, 1]] : [[r.win, r.p], [r.lose, 1 - r.p]]);
  let dist = new Map([[0, 1]]);
  for (const c of comps) dist = conv(dist, c);
  const live = (c) => c.filter(([, p]) => p > 1e-9).map(([x]) => x);
  const best = comps.reduce((s2, c) => s2 + Math.max(...live(c)), 0), worst = comps.reduce((s2, c) => s2 + Math.min(...live(c)), 0);
  const ev = comps.reduce((s2, c) => s2 + c.reduce((a, [x, p]) => a + x * p, 0), 0);
  let up = 0, upDay = 0;
  for (const [x, p] of dist) { if (x > 0.005) up += p; if (x + settled > 0.005) upDay += p; }
  return { ev, best, worst, up, upDay, links, live: rows.filter((r) => r.srcs.some((x) => /live|final/.test(x.src))).length };
}

// where a leg stands right now: score, what it still needs, where it's headed
const fmtN = (x) => Number.isInteger(+x) ? `${+x}` : (+x).toFixed(1);
export function progress(leg, r) {
  const pg = progress0(leg, r);
  return r.held ? { ...pg, held: true } : pg;
}
function progress0(leg, r) {
  if (r.src === "final") {
    const res = r.p === 1 ? "Won" : r.p === 0 ? "Lost" : "Push";
    const st0 = gameState(leg.gameId), fs = st0?.home?.score != null ? ` · final ${st0.away.score}–${st0.home.score}` : "";
    const stat = leg.player && r.cur != null ? ` · finished with ${fmtN(r.cur)} (${leg.side === "Under" || leg.side === "No" ? "under" : "over"} ${leg.point})${r.dnp ? " · didn't play: check if the app voids it" : ""}` : fs;
    return { cls: r.p === 1 ? "pos" : r.p === 0 ? "neg" : "", icon: r.p === 1 ? "✓" : r.p === 0 ? "✗" : "=", text: `${res}${stat} · grading soon`, bar: { done: r.p } };
  }
  const d = S.liveGames.get(leg.gameId), st = gameState(leg.gameId);
  const g = S.games.get(leg.gameId);
  if (!st) return { cls: "muted", icon: "○", text: g ? `Starts ${fmtTime(g.commence)}` : "Not started", bar: { cur: 0, line: null, proj: null } };
  const base = (leg.market || "").replace("alternate_", ""), pt = leg.point, m = d?.model || {};
  const over = leg.side === "Over" || leg.side === "Yes";
  const bar = (cur, target, proj) => {      // how far along: filled to now, tick at the line, dot at the projected finish
    const hi = Math.max(target, proj ?? 0, cur) * 1.12 || 1;
    return { cur: cur / hi, line: target / hi, proj: proj != null ? Math.min(1, proj / hi) : null };
  };
  if (leg.player) {
    if (r.cur == null) return { cls: "muted", icon: "●", text: "In play · live stat appears with the next live pull", bar: { cur: 0, line: 0.9, proj: null } };
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
  // margin bar: centre = the result needed (win, or cover), filled to where it stands, dot at the model's finish
  const SCALE = { NHL: 3, MLB: 4, NBA: 14, WNBA: 12, NFL: 14, NCAAF: 17 }[d?.league || g?.league] ?? 3;
  const mbar = (cushion, projC) => { const sc = Math.max(SCALE, Math.abs(cushion), Math.abs(projC ?? 0)) * 1.15; return { div: true, v: cushion / sc, proj: projC != null ? projC / sc : null }; };
  if (base === "h2h") return { cls: diff > 0 ? "pos" : diff < 0 ? "neg" : "", icon: "●", text: `${lead}${projM != null ? ` · model finish ${projM > 0 ? "+" : ""}${fmtN(Math.round(projM * 10) / 10)}` : ""}`, bar: mbar(diff, projM) };
  if (base.startsWith("spreads") && pt != null) {
    const cover = diff + pt;
    return { cls: cover > 0 ? "pos" : "neg", icon: "●", bar: mbar(cover, projM != null ? projM + pt : null), text: `${lead} · ${cover > 0 ? `covering ${pt > 0 ? "+" : ""}${pt} by ${fmtN(cover)}` : `${fmtN(-cover)} short of ${pt > 0 ? "+" : ""}${pt}`}${projM != null ? ` · model finish ${projM > 0 ? "+" : ""}${fmtN(Math.round(projM * 10) / 10)}` : ""}` };
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
function barHtml(b) {
  if (!b) return `<span></span>`;
  if (b.done != null) return `<span class="pj-trk"><i style="width:100%"></i></span>`;
  if (b.div) {        // diverging around the needed result
    const v = Math.max(-1, Math.min(1, b.v)), x = 50 + v * 50;
    return `<span class="pj-trk div"><s style="left:50%"></s><i style="left:${Math.min(50, x).toFixed(1)}%;width:${Math.abs(x - 50).toFixed(1)}%"></i>${b.proj != null ? `<b style="left:${(50 + Math.max(-1, Math.min(1, b.proj)) * 50).toFixed(1)}%"></b>` : ""}</span>`;
  }
  return `<span class="pj-trk"><i style="width:${(Math.min(1, b.cur) * 100).toFixed(1)}%"></i>${b.line != null ? `<s style="left:${(b.line * 100).toFixed(1)}%"></s>` : ""}${b.proj != null ? `<b style="left:${(b.proj * 100).toFixed(1)}%"></b>` : ""}</span>`;
}
const progressHtml = (pg) => `<div class="pj-pg ${pg.cls}"><span class="pj-ic">${pg.icon}</span><span class="pj-tx">${esc(pg.text)}${pg.held ? ` <span class="tiny muted">(last live reading)</span>` : ""}</span>${barHtml(pg.bar)}</div>`;

// the other side of a leg at the best price Vince can get, with its fair (live if the game is on)
function opposite(leg) {
  const d = S.liveGames.get(leg.gameId), g = S.games.get(leg.gameId);
  if (leg.player) {
    const docs = [...(d?.props || []), ...[...S.props.values()].filter((x) => x.id === leg.gameId)];
    const r = findProp(docs, leg); if (!r) return null;
    const opp = ["Over", "Under"].find((o) => o !== leg.side && (r.bestVenue?.[o] || r.fair?.[o] != null)); if (!opp) return null;
    const q = r.bestVenue?.[opp]; return q ? { label: `${leg.player} ${opp} ${r.point ?? ""}`, q, fair: r.fair?.[opp] } : null;
  }
  for (const src of [d, g]) {
    if (!src) continue;
    const l = findLine(src.lines, src.home, leg); if (!l) continue;
    const opp = (l.outcomes || []).find((o) => N(o) !== N(leg.side)); const q = opp && l.bestVenue?.[opp];
    if (q) { const pt = /spreads/.test(l.market) ? ` ${(opp === src.home ? l.point : -l.point) > 0 ? "+" : ""}${opp === src.home ? l.point : -l.point}` : /totals/.test(l.market) ? ` ${l.point}` : " ML";
      return { label: `${opp}${pt}`, q, fair: (l.fair || {})[opp] }; }
  }
  return null;
}
// a parlay down to its last leg: what it takes to lock a profit, and what that costs in expectation
function hedgeHtml(b, r, legs) {
  const open = legs.filter((x) => !(x.p >= 0.999));
  if (open.length !== 1 || legs.some((x) => x.p === 0)) return "";
  const o = opposite(open[0].leg); if (!o || !o.q?.cost) return "";
  const pay = settleProfit(b, b.promoId ? S.promos.get(b.promoId) : null, "won") + (b.stake || 0), dh = 1 / o.q.cost;
  const H = pay / dh, lock = pay - (b.stake || 0) - H, cost = o.fair != null ? H * (1 - o.fair * dh) : null;
  if (!(H > 0)) return "";
  const half = H / 2;
  return `<div class="pj-hedge"><b>Down to one leg.</b> To lock a result: bet <b>${money(H)}</b> on ${esc(o.label)} at ${bookLogo(o.q.book, o.q.name, 14)} ${esc(o.q.name)} <b class="num">${am(o.q.am)}</b>
    → <b class="${lock >= 0 ? "pos" : "neg"}">${money(lock)}</b> either way${cost != null ? ` · costs about ${money(cost)} in expectation (it's a bet at ${prob(o.q.cost)} on a ${prob(o.fair)} side)` : ""}.
    <span class="tiny muted">Half hedge (${money(half)}): ${money(pay - b.stake - half)} if the parlay hits, ${money(half * dh - half - b.stake)} if not. Letting it ride keeps the full ${money(pay - b.stake)} at ${prob(open[0].p)}.</span></div>`;
}

// the picture for a leg: player headshot, else the team's logo, else the league's
function legImg(leg, size = 26) {
  const g = S.games.get(leg.gameId) || S.liveGames.get(leg.gameId);
  if (leg.player) {
    const r = findProp([...S.props.values()].filter((x) => x.id === leg.gameId), { ...leg, point: null }) || findProp(S.liveGames.get(leg.gameId)?.props, { ...leg, point: null });
    return headshot({ player: leg.player, headshot: r?.headshot }, size);
  }
  const t = leg.team || leg.side;
  if (g && t && N(t) === N(g.home)) return teamLogo(g.homeRef, g.home, size);
  if (g && t && N(t) === N(g.away)) return teamLogo(g.awayRef, g.away, size);
  return teamLogo(g?.leagueLogo ? { logo: g.leagueLogo, abbr: leg.league } : null, leg.league || g?.league || "", size);
}
// a ring for the chance right now (the faint tick on the ring is where it started)
function ring(p, was, cls) {
  const R = 19, C = 2 * Math.PI * R, v = Math.max(0, Math.min(1, p ?? 0));
  const tick = was != null ? (() => { const a = was * 2 * Math.PI - Math.PI / 2; return `<line x1="${24 + Math.cos(a) * 15}" y1="${24 + Math.sin(a) * 15}" x2="${24 + Math.cos(a) * 23}" y2="${24 + Math.sin(a) * 23}" class="pj-rt"/>`; })() : "";
  return `<svg class="pj-ring ${cls}" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="${R}" class="pj-rb"/>
    <circle cx="24" cy="24" r="${R}" class="pj-rf" stroke-dasharray="${(v * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 24 24)"/>${tick}
    <text x="24" y="28" text-anchor="middle">${p == null ? "–" : Math.round(v * 100)}</text></svg>`;
}

const pctUp = (x) => (x > 0.995 && x < 0.99995 ? ">99%" : x < 0.005 && x > 0.00005 ? "<1%" : `${Math.round(x * 100)}%`);
export function projectedCard({ items, title, fmt = money, units = false, settledToday = 0 }) {
  const rows = items.map((b) => project(b, b.promoId ? S.promos.get(b.promoId) : null, units)).filter(Boolean);
  if (!rows.length) return "";
  const s = summary(rows, units ? 0 : settledToday);
  const day = settledToday + s.ev, locked = s.best - s.worst < 0.01;
  rows.sort((a, b) => (a.b.commence || "").localeCompare(b.b.commence || ""));
  // range bar: worst to best with the projection marked
  const span = (s.best - s.worst) || 1, at = (x) => Math.max(0, Math.min(100, (x - s.worst) / span * 100));
  return `<div class="card col pj" style="gap:12px">
    <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px"><b>${esc(title)}</b>
      <span class="tiny muted">${s.live ? `<span class="pill live">● ${s.live} in play</span> ` : ""}updates with every live pull</span></div>
    <div class="pj-top">
      <div class="pj-big"><div class="k">Projected</div><div class="v ${s.ev >= 0 ? "pos" : "neg"}">${fmt(s.ev)}</div><div class="s">on ${rows.length} open${!units ? ` · today ${fmt(day)} with ${fmt(settledToday)} settled` : ""}</div></div>
      <div class="pj-big"><div class="k">Chance to finish up</div><div class="v">${pctUp(s.up)}</div><div class="pj-meter"><i style="width:${(s.up * 100).toFixed(0)}%"></i></div>
        ${!units && Math.abs(settledToday) > 0.005 ? `<div class="s">the day: ${pctUp(s.upDay)} to end up with ${fmt(settledToday)} settled</div>` : ""}</div>
      <div class="pj-big wide"><div class="k">Range</div>${locked ? `<div class="v ${s.ev >= 0 ? "pos" : "neg"}" style="font-size:18px">${fmt(s.ev)} locked</div><div class="s">every open result pays the same</div>`
        : `<div class="pj-range"><span class="z" style="left:${at(0)}%"></span><b style="left:${at(s.ev)}%"></b></div>
        <div class="row tiny" style="justify-content:space-between"><span class="${s.worst >= 0 ? "pos" : "neg"}">${fmt(s.worst)} worst case</span><span class="pos">${fmt(s.best)} best case</span></div>`}</div>
    </div>
    ${s.links.length ? `<div class="col" style="gap:6px">${s.links.map((L) => `<div class="pj-link"><b>${esc(L.name)}</b> <span class="tiny muted">· ${L.rows.length} bets on the same number, counted together · expected <b class="num ${L.regions.reduce((a2, g) => a2 + g.p * g.pay, 0) >= 0 ? "pos" : "neg"}">${fmt(L.regions.reduce((a2, g) => a2 + g.p * g.pay, 0))}</b> for the group</span>
      <div class="pj-lr">${L.regions.filter((g) => g.p > 0.0005).map((g) => `<span class="pj-lc"><span class="tiny muted">${esc(g.label)} · ${prob(g.p)}</span><b class="num ${g.pay >= 0 ? "pos" : "neg"}">${fmt(g.pay)}</b></span>`).join("")}</div></div>`).join("")}</div>` : ""}
    <div class="col" style="gap:8px">${rows.map((r) => {
      const b = r.b, st = gameState(b.gameId), was = b.fairAtLog ?? b.fair, d = r.p != null && was != null ? r.p - was : null;
      const legs = r.srcs.filter((x) => x.leg), multi = legs.length > 1;
      const live = r.srcs.some((x) => /live/.test(x.src)), cls = r.p == null ? "" : r.p >= 0.6 ? "pos" : r.p <= 0.35 ? "neg" : "mid", tk = cls ? `tk-${cls}` : "";
      const pay = units ? `${b.units}u to win ${fmt(r.win)}` : `${money(b.stake)} to win ${money(r.win)}`;
      return `<div class="pj-tk ${tk}">
        <div class="pj-hd">
          <span class="pj-ven">${bookLogo(b.venue || "any", b.venueName || b.venue || "", 30)}</span>
          <div class="col" style="gap:2px;min-width:0"><b class="ellip">${multi ? `${legs.length}-leg parlay` : esc(b.label || b.selection)}</b>
            <span class="tiny muted ellip">${r.link ? `<span class="pill s" title="counted together with the other bets on ${esc(r.link.name)}">linked</span> ` : ""}${b.am != null ? `<b class="num">${am(b.am)}</b> · ` : ""}${pay}${b.venueName ? ` · ${esc(b.venueName)}` : ""}${live ? ` · <span class="pill live">● live</span>` : st ? "" : b.commence ? ` · ${fmtTime(b.commence)}` : ""}</span></div>
          <div class="pj-rc" title="chance now${was != null ? ` (was ${prob(was)})` : ""}">${r.push ? `<span class="pill">push</span>` : ring(r.p, was, cls)}
            ${d != null && Math.abs(d) >= 0.03 ? `<span class="tiny ${d > 0 ? "pos" : "neg"}">${d > 0 ? "▲" : "▼"} ${Math.round(Math.abs(d) * 100)}</span>` : ""}</div>
          <div class="pj-ev"><span class="tiny muted">exp.</span><b class="num ${r.ev >= 0 ? "pos" : "neg"}">${fmt(r.ev)}</b></div>
        </div>
        <div class="pj-legs">${legs.map((x) => {
          const lst = gameState(x.leg.gameId);
          return `<div class="pj-leg">${legImg(x.leg, 26)}<div class="col" style="gap:3px;min-width:0">
            ${multi ? `<span class="small ellip">${esc(x.leg.label || "")}${lst ? ` <span class="tiny muted">· ${lst.away?.score ?? ""}–${lst.home?.score ?? ""} ${esc(sitText(lst, x.leg.league || b.league))}</span>` : ""}</span>`
              : lst ? `<span class="tiny muted">${lst.away?.score ?? ""}–${lst.home?.score ?? ""} ${esc(sitText(lst, b.league))}</span>` : ""}
            ${progressHtml(progress(x.leg, x))}</div></div>`;
        }).join("")}</div>${!units && multi ? hedgeHtml(b, r, legs) : ""}
      </div>`;
    }).join("")}</div>
    <div class="tiny muted">The ring is the chance right now (its tick is where it started). Player stats come from the live box score every minute; the dot on each bar is the projected finish, the line is what the bet needs.</div>
  </div>`;
}
