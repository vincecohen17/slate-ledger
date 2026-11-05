// Ledger > Analysis: everything the settled bets can say, with the statistics behind it. Filters apply to every
// section. Day by day (calendar, daily distribution, weekly table), skill vs luck with formal tests (return per $ risked
// with a bootstrap interval, luck z, CLV t-test, scoring rules and a calibration fit of the fair when logged), CLV
// distribution, reliability, every leg of every entry (hit rate against its closing chance by market, side, sport),
// a breakdown explorer over 14 dimensions, venue x type heatmaps, sizing against Kelly, promos, arbs and middles, the
// biggest swings, the full bet table (sortable, CSV) and what data each number rests on.
import { S, saveSettings, sportKeys } from "../store.js";
import { esc, am, pct, prob, money, fmtShortDay, bookLogo, mktName, bindTips } from "../util.js";
import { settleProfit, kindOf, normalize } from "../promo.js";
import { VENUES } from "../lab.js";
import { lineChart, legend, divRows, heat } from "./charts.js";
import { unify } from "../arbs.js";
import { walletAll } from "../wallet.js";
import { mean, meanCI, wilson, bootRatio, logLoss, brier, calibFit, relBins, ptDay, pTwo, fmtP, fmtT, sd, clvOk, CLOSE_OK_MIN } from "../stats.js";
import { calendar, relChart, histogram, sortTable, bindSort, td, downloadCsv, chipBar } from "./deep.js";
import { project } from "../projected.js";

const DFS = new Set(VENUES.filter((v) => v.dfs).map((v) => v.key));
const DONE = ["won", "lost", "push"];
const promoOf = (b) => (b.promoId ? S.promos.get(b.promoId) : null);
export function betType(b) {
  if (b.isGroup) return b.kind === "arb" ? "Arb" : "Middle";
  if ((b.legs || []).length > 1) return DFS.has(b.venue) ? "Pick'em entry" : "Parlay";
  if (DFS.has(b.venue)) return "Pick'em";
  const m = b.market || b.legs?.[0]?.market || "";
  if (b.player || b.legs?.[0]?.player || /^(player|batter|pitcher)_/.test(m)) return "Player prop";
  if (/^h2h/.test(m)) return "Moneyline";
  if (/spreads/.test(m)) return "Spread";
  if (/totals/.test(m)) return "Total";
  return "Other";
}
// what the bet loses if it loses (a bonus bet or free entry risks nothing)
const riskOf = (b) => (b.isGroup ? b.stake || 0 : Math.max(0, -settleProfit(b, promoOf(b), "lost")));
// what a bet was expected to make at the fair when logged (promo math included), and its variance
function expOf(b) {
  if (b.isGroup) return b.expPl != null ? { ev: b.expPl, v: b.expVar || 0 } : null;
  const p = b.fairAtLog;
  if (p == null || !b.dec) return null;
  const pr = promoOf(b), w = settleProfit(b, pr, "won"), l = settleProfit(b, pr, "lost");
  return { ev: p * w + (1 - p) * l, v: p * (1 - p) * (w - l) ** 2 };
}
const legsOf = (b) => (b.legs?.length ? b.legs : [b]);
// the game day (Pacific) a bet belongs to: its last leg's start, else when it settled
// the Pacific day a bet belongs to: its last game's day (the engine's lastGame, else each leg's start, from the leg or the
// board), never the day it happened to be graded or settled by hand
const startOf = (gid) => gid && (S.games.get(gid)?.commence || S.liveGames.get(gid)?.commence || (S.finals || []).find((x) => x.gameId === gid)?.commence);
export const gameDay = (b) => {
  if (b.lastGame) return ptDay(b.lastGame);
  if (b.legsB?.length) { const ds = b.legsB.map(gameDay).filter(Boolean).sort(); if (ds.length) return ds.at(-1); }
  const cs = legsOf(b).map((l) => l.commence || startOf(l.gameId) || b.commence || startOf(b.gameId)).filter(Boolean).sort();
  return ptDay(cs.at(-1) || b.settledAt || b.createdAt);
};
const promoKind = (b) => { if (!b.promoId) return "No promo"; const p = promoOf(b); return p ? kindOf(normalize(p).kind).name : "Promo (deleted)"; };
const hoursBefore = (b) => (b.commence && b.createdAt ? (new Date(b.commence) - new Date(b.createdAt)) / 3600000 : null);
const timing = (b) => { if (b.live) return "Live (in-game)"; const h = hoursBefore(b); return h == null ? "Unknown" : h < 1 ? "Under 1h before" : h < 3 ? "1-3h before" : h < 12 ? "3-12h before" : h < 36 ? "12-36h before" : "36h+ before"; };
const TIMING = ["36h+ before", "12-36h before", "3-12h before", "1-3h before", "Under 1h before", "Live (in-game)"];
const weekday = (b) => new Date(`${gameDay(b)}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", timeZone: "UTC" });
const oddsBand = (b) => { const d = b.dec || 0; return d < 1.5 ? "Heavy fav (< -200)" : d < 2 ? "Favorite (-200 to -100)" : d < 3 ? "Plus money (+100 to +200)" : d < 6 ? "Long (+200 to +500)" : "Longshot (+500 or more)"; };
const ODDS = ["Heavy fav (< -200)", "Favorite (-200 to -100)", "Plus money (+100 to +200)", "Long (+200 to +500)", "Longshot (+500 or more)"];
const edgeAt = (b) => { const x = expOf(b), r = riskOf(b); return x && r > 0 ? x.ev / r : null; };
const edgeBand = (b) => { const e = edgeAt(b); return e == null ? (riskOf(b) ? "No fair logged" : "Free (no risk)") : e < 0 ? "Negative EV" : e < 0.05 ? "0-5% EV" : e < 0.15 ? "5-15% EV" : "15%+ EV"; };
const EDGES = ["Negative EV", "0-5% EV", "5-15% EV", "15%+ EV", "Free (no risk)", "No fair logged"];
const nLegs = (b) => { if (b.isGroup) return "Arb or middle"; const n = (b.legs || []).length || 1; return n === 1 ? "Single" : n <= 3 ? `${n} legs` : n <= 5 ? "4-5 legs" : "6+ legs"; };
const sideOf = (b) => { const l = legsOf(b); if (l.length > 1 || b.isGroup) return "Multi-leg"; const s = l[0].side || b.side; return s === "Over" || s === "Under" || s === "Yes" || s === "No" ? s : b.dec >= 2 ? "Underdog side" : "Favorite side"; };
const marketOf = (b) => { const l = legsOf(b); if (b.isGroup) return "Arb or middle"; if (l.length > 1) { const ms = [...new Set(l.map((x) => mktName(x.market) || "?"))]; return ms.length === 1 ? `${ms[0]} (multi)` : "Mixed parlay"; } return mktName(l[0].market || b.market) || "Unknown"; };
const SOURCES = { arb: "Arbs & middles", edge: "Edges screen", pick: "Our picks", promo: "Promos", lab: "Bet Lab", manual: "Typed in", live: "Live tab" };

// one row of stats for a set of bets
function agg(list) {
  const g = list.filter((b) => DONE.includes(b.status));
  const risk = g.reduce((s, b) => s + riskOf(b), 0), pl = g.reduce((s, b) => s + (b.profit || 0), 0);
  // ROI is on money actually risked: bonus bets and free entries (nothing at risk) are profit but not in the ratio
  const plRisk = g.filter((b) => riskOf(b) > 0).reduce((s, b) => s + (b.profit || 0), 0);
  const c = g.filter(clvOk), ex = g.map(expOf).filter(Boolean);
  const rets = g.filter((b) => riskOf(b) > 0).map((b) => (b.profit || 0) / riskOf(b));
  const r = meanCI(rets), e = ex.reduce((s, x) => s + x.ev, 0), v = ex.reduce((s, x) => s + x.v, 0);
  const plE = g.filter(expOf).reduce((s, b) => s + (b.profit || 0), 0);
  return { n: g.length, w: g.filter((b) => b.status === "won").length, l: g.filter((b) => b.status === "lost").length, p: g.filter((b) => b.status === "push").length,
    risk, pl, plRisk, plFree: pl - plRisk, roi: risk ? plRisk / risk : null, ret: r, clv: c.length ? mean(c.map((b) => b.clv)) : null, nClv: c.length,
    beat: c.length ? c.filter((b) => b.clv > 0).length / c.length : null, exp: e, luck: plE - e, z: v > 0 ? (plE - e) / Math.sqrt(v) : null };
}
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
function groupRows(list, keyFn, order) {
  const by = new Map();
  list.forEach((b) => { for (const k of [].concat(keyFn(b))) { if (!by.has(k)) by.set(k, []); by.get(k).push(b); } });
  let ks = [...by.keys()];
  if (order) ks = order.filter((k) => by.has(k)).concat(ks.filter((k) => !order.includes(k)));
  return ks.map((k) => ({ k, ...agg(by.get(k)) })).filter((r) => r.n);
}
function statTable(rows, label = "") {
  return sortTable([label, "Bets", "W-L-P", "Risked", "P/L", "ROI", "Per-bet return ± 95%", "Expected", "Luck", "z", "Avg CLV", "Beat close"],
    rows.map((r) => `<tr><td>${r.html || esc(r.k)}</td>${td(r.n, r.n)}${td(`${r.w}-${r.l}${r.p ? "-" + r.p : ""}`, r.w / Math.max(1, r.w + r.l))}
      ${td(money(r.risk), r.risk)}${td(`<span class="${cls(r.pl)}">${money(r.pl)}</span>`, r.pl)}${td(`<span class="${cls(r.roi)}">${r.roi != null ? pct(r.roi) : "–"}</span>`, r.roi)}
      ${td(r.ret.m != null ? `${pct(r.ret.m)}${r.ret.se != null ? ` <span class="tiny muted">± ${(1.96 * r.ret.se * 100).toFixed(0)}</span>` : ""}` : "–", r.ret.m)}
      ${td(money(r.exp), r.exp)}${td(`<span class="${cls(r.luck)}">${money(r.luck)}</span>`, r.luck)}${td(fmtT(r.z), r.z)}
      ${td(`<span class="${cls(r.clv)}">${r.clv != null ? pct(r.clv) : "–"}</span>`, r.clv)}${td(r.beat != null ? Math.round(r.beat * 100) + "%" : "–", r.beat)}</tr>`));
}

// ---------- filters and view state
const F = { range: "all", sport: "All", venue: "All", type: "All", promo: "All", dim: "venue", legDim: "market", day: null };
const DIMS = [["venue", "Venue"], ["sport", "Sport"], ["type", "Bet type"], ["market", "Market"], ["promo", "Promo kind"], ["source", "Logged from"],
  ["odds", "Odds"], ["edge", "EV when logged"], ["timing", "Timing"], ["weekday", "Weekday"], ["legs", "Legs"], ["stake", "Stake size"], ["live", "Pre-game / live"], ["side", "Side"]];
function filtered(all) {
  const cut = F.range === "all" ? null : ptDay(new Date(Date.now() - (+F.range - 1) * 86400000).toISOString());
  return all.filter((b) => (!cut || (gameDay(b) || "") >= cut) && (F.sport === "All" || sportKeys(b).includes(F.sport))
    && (F.venue === "All" || (b.venueName || b.venue) === F.venue) && (F.type === "All" || betType(b) === F.type)
    && (F.promo === "All" || (F.promo === "with" ? !!b.promoId : !b.promoId)));
}

export function analysis() {
  const bankroll = S.settings?.bankroll || 0;
  const raw = [...S.bets.values()], allU = unify(raw);
  const doneAll = allU.filter((b) => DONE.includes(b.status));
  if (doneAll.filter((b) => !b.isGroup).length < 3) return `<div class="card note" style="margin-top:12px">The analysis fills in after a few settled bets.</div>`;
  const sel = (k, lab, opts) => `<select data-af="${k}" title="${lab}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${F[k] === v ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
  const sports = [...new Set(doneAll.flatMap(sportKeys))].sort(), venues = [...new Set(doneAll.map((b) => b.venueName || b.venue))].sort(), types = [...new Set(doneAll.map(betType))].sort();
  const bar = `<div class="an-filter">${sel("range", "dates", [["all", "All time"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["90", "Last 90 days"]])}
    ${sel("sport", "sport", [["All", "All sports"], ...sports.map((x) => [x, x])])}${sel("venue", "venue", [["All", "All venues"], ...venues.map((x) => [x, x])])}
    ${sel("type", "type", [["All", "All bet types"], ...types.map((x) => [x, x])])}${sel("promo", "promos", [["All", "With and without promos"], ["with", "Promo bets only"], ["without", "No-promo bets only"]])}
    <button class="btn sm ghost" id="an-csv">Download CSV</button></div>`;
  const all = filtered(allU);
  const done = all.filter((b) => DONE.includes(b.status)).sort((a, b) => (gameDay(a) + (a.settledAt || "")).localeCompare(gameDay(b) + (b.settledAt || "")));
  const st = done.filter((b) => !b.isGroup), stA = done.filter((b) => b.isGroup), arbPl = stA.reduce((x, b) => x + (b.profit || 0), 0);
  if (st.length < 1) return bar + `<div class="card note" style="margin-top:12px">No settled bets match these filters.</div>`;
  const A = agg(st), filt = F.range !== "all" || F.sport !== "All" || F.venue !== "All" || F.type !== "All" || F.promo !== "All";

  // ---- running P/L vs expected (skill vs luck)
  let c = 0, e = 0, v = 0;
  const run = [], exp = [], band = [];
  st.forEach((b, i) => { c += b.profit || 0; const x = expOf(b); if (x) { e += x.ev; v += x.v; }
    run.push({ i, y: c, tip: `${gameDay(b)} · ${b.label}<br>${money(b.profit)} · running ${money(c)} · expected ${money(e)}` });
    exp.push({ i, y: e }); band[i] = { lo: e - Math.sqrt(v), hi: e + Math.sqrt(v) }; });
  // streaks
  let k = 0, kt = null, longL = 0, longW = 0;
  st.forEach((b) => { if (b.status === "push") return; if (b.status === kt) k++; else { kt = b.status; k = 1; } if (kt === "lost") longL = Math.max(longL, k); else longW = Math.max(longW, k); });

  // ---- day by day
  const dayM = new Map();
  done.forEach((b) => { const d = gameDay(b); const x = dayM.get(d) || { v: 0, n: 0, risk: 0, exp: 0, bets: [] }; x.v += b.profit || 0; x.n++; x.risk += riskOf(b); x.exp += expOf(b)?.ev || 0; x.bets.push(b); dayM.set(d, x); });
  dayM.forEach((x, d) => { x.tip = `${d}: ${money(x.v)} on ${x.n} bet${x.n === 1 ? "" : "s"} · risked ${money(x.risk)} · expected ${money(x.exp)}`; });
  const dvals = [...dayM.values()].map((x) => x.v), dk = [...dayM.keys()].sort();
  const dMean = mean(dvals), dSd = sd(dvals), up = dvals.filter((x) => x > 0.005).length;
  let dl = 0, dlMax = 0; dk.forEach((d) => { if (dayM.get(d).v < -0.005) { dl++; dlMax = Math.max(dlMax, dl); } else dl = 0; });
  let cum = 0, cumE = 0; const dRun = dk.map((d, i) => { cum += dayM.get(d).v; return { i, y: cum, tip: `${d}: ${money(dayM.get(d).v)} · total ${money(cum)}` }; });
  const dExp = dk.map((d, i) => { cumE += dayM.get(d).exp; return { i, y: cumE }; });
  // ISO-ish weeks starting Monday
  const wk = (d) => { const t = new Date(`${d}T12:00:00Z`), dow = (t.getUTCDay() + 6) % 7; return new Date(t - dow * 86400000).toISOString().slice(0, 10); };
  const weeks = new Map(); dk.forEach((d) => { const w = wk(d), x = weeks.get(w) || { pl: 0, n: 0, risk: 0, days: 0 }, y = dayM.get(d); x.pl += y.v; x.n += y.n; x.risk += y.risk; x.days++; weeks.set(w, x); });
  let wc = 0; const wRows = [...weeks.keys()].sort().map((w) => { const x = weeks.get(w); wc += x.pl;
    return `<tr><td>Week of ${fmtShortDay(`${w}T12:00:00Z`)}</td>${td(x.days, x.days)}${td(x.n, x.n)}${td(money(x.risk), x.risk)}${td(`<span class="${cls(x.pl)}">${money(x.pl)}</span>`, x.pl)}${td(x.risk ? pct(x.pl / x.risk) : "–", x.risk ? x.pl / x.risk : null)}${td(money(wc), wc)}</tr>`; });
  const selDay = F.day && dayM.get(F.day);

  // ---- formal tests
  const rets = st.filter((b) => riskOf(b) > 0), R = meanCI(rets.map((b) => (b.profit || 0) / riskOf(b)));
  const boot = bootRatio(rets, (b) => b.profit || 0, riskOf);
  const free = st.filter((b) => riskOf(b) === 0), freePl = free.reduce((s, b) => s + (b.profit || 0), 0);
  const C = meanCI(st.filter(clvOk).map((b) => b.clv)), beatW = wilson(st.filter((b) => clvOk(b) && b.clv > 0).length, A.nClv);
  const scored = st.filter((b) => b.fairAtLog != null && b.status !== "push" && b.dec > 1);
  const ys = scored.map((b) => (b.status === "won" ? 1 : 0)), ps = scored.map((b) => b.fairAtLog);
  const plain = scored.filter((b) => !b.promoId), yP = plain.map((b) => (b.status === "won" ? 1 : 0));
  const llFair = logLoss(plain.map((b) => b.fairAtLog), yP), llImp = logLoss(plain.map((b) => 1 / b.dec), yP);
  const withClose = scored.filter((b) => b.closeFair != null), llClose = logLoss(withClose.map((b) => b.closeFair), withClose.map((b) => (b.status === "won" ? 1 : 0)));
  const llFairC = logLoss(withClose.map((b) => b.fairAtLog), withClose.map((b) => (b.status === "won" ? 1 : 0)));
  const cf = calibFit(ps, ys);
  const zL = A.z;
  const test = (name, est, ci, stat, p, read) => `<tr><td>${name}</td><td class="num">${est}</td><td class="num">${ci}</td><td class="num">${stat}</td><td class="num">${p}</td><td class="small">${read}</td></tr>`;

  // ---- legs (every leg of every entry, graded against its closing chance)
  const legRows = [];
  st.forEach((b) => (b.legResults || []).forEach((l) => { if (l.y == null || l.void) return;
    const g = S.games.get(l.gameId) || S.finals?.find?.((x) => x.gameId === l.gameId);
    legRows.push({ ...l, league: g?.league || (b.leagues?.length === 1 ? b.leagues[0] : b.league) || "?", venue: b.venueName || b.venue, size: (b.legs || []).length || 1, entryWon: b.status === "won", bet: b }); }));
  const LEGDIMS = [["market", "Market"], ["side", "Side"], ["league", "Sport"], ["close", "Closing chance"], ["size", "Entry size"], ["venue", "Venue"], ["kind", "Prop or game line"]];
  const legKey = (l) => ({ market: mktName(l.market) || "?", side: l.side === "Over" || l.side === "Under" || l.side === "Yes" || l.side === "No" ? l.side : "Team side", league: l.league,
    close: l.close == null ? "No close" : l.close < 0.35 ? "Under 35%" : l.close < 0.5 ? "35-50%" : l.close < 0.6 ? "50-60%" : l.close < 0.7 ? "60-70%" : "70%+",
    size: l.size === 1 ? "Single" : `${l.size}-leg entries`, venue: l.venue, kind: l.player ? "Player prop" : "Game line" })[F.legDim];
  const legAgg = new Map(); legRows.forEach((l) => { const kk = legKey(l), x = legAgg.get(kk) || { n: 0, y: 0, ce: 0, cv: 0, nc: 0 }; x.n++; x.y += l.y; if (l.close != null) { x.ce += l.close; x.cv += l.close * (1 - l.close); x.nc++; } legAgg.set(kk, x); });
  const legTable = sortTable(["", "Legs", "Hit", "Hit rate (95%)", "Closing chance", "Hit − close", "z"], [...legAgg.entries()].sort((a, b) => b[1].n - a[1].n).map(([kk, x]) => {
    const w = wilson(x.y, x.n), ec = x.nc ? x.ce / x.nc : null, yC = x.nc ? legRows.filter((l) => legKey(l) === kk && l.close != null).reduce((s, l) => s + l.y, 0) : null;
    const z = x.cv > 0 ? (yC - x.ce) / Math.sqrt(x.cv) : null, gap = ec != null ? yC / x.nc - ec : null;
    return `<tr><td>${esc(kk)}</td>${td(x.n, x.n)}${td(x.y, x.y)}${td(`${Math.round(w.p * 100)}% <span class="tiny muted">${Math.round(w.lo * 100)}-${Math.round(w.hi * 100)}</span>`, w.p)}${td(ec != null ? prob(ec) : "–", ec)}${td(`<span class="${cls(gap)}">${gap != null ? `${gap > 0 ? "+" : ""}${(gap * 100).toFixed(1)} pts` : "–"}</span>`, gap)}${td(fmtT(z), z)}</tr>`; }));
  const lostEntries = st.filter((b) => (b.legs || []).length > 1 && b.status === "lost" && b.legResults?.length);
  const killers = new Map(); lostEntries.forEach((b) => b.legResults.filter((l) => l.y === 0).forEach((l) => killers.set(mktName(l.market), (killers.get(mktName(l.market)) || 0) + 1)));
  const oneAway = lostEntries.filter((b) => b.legResults.filter((l) => l.y === 0).length === 1).length;

  // ---- breakdown explorer
  const sg = st, ss = sg.map((b) => b.stake).sort((a, b) => a - b), sc = [ss[Math.floor(ss.length / 3)], ss[Math.floor(ss.length * 2 / 3)]];
  const stakeBand = (b) => (b.stake <= sc[0] ? `Smallest third (≤ ${money(sc[0])})` : b.stake <= sc[1] ? "Middle third" : `Biggest third (> ${money(sc[1])})`);
  const dimFn = { venue: (b) => b.venueName || b.venue, sport: sportKeys, type: betType, market: marketOf, promo: promoKind, source: (b) => SOURCES[b.source] || (b.pickId ? "Our picks" : "Typed in"),
    odds: oddsBand, edge: edgeBand, timing, weekday, legs: nLegs, stake: stakeBand, live: (b) => (b.live ? "Live" : "Pre-game"), side: sideOf }[F.dim];
  const dimOrder = { odds: ODDS, edge: EDGES, timing: TIMING, weekday: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], legs: ["Single", "2 legs", "3 legs", "4-5 legs", "6+ legs"] }[F.dim];
  const dimRows = groupRows(st, dimFn, dimOrder).map((r) => F.dim === "venue" ? { ...r, html: `<span class="lg-name">${bookLogo(st.find((b) => (b.venueName || b.venue) === r.k)?.venue || "any", r.k, 16)}${esc(r.k)}</span>` } : r);

  // ---- heatmaps, sizing, promos (as before)
  const typesH = [...new Set(st.map(betType))], venuesH = [...new Set(st.map((b) => b.venueName || b.venue))];
  const cell = (metric) => (vn, ty) => { const s = st.filter((b) => (b.venueName || b.venue) === vn && betType(b) === ty); if (!s.length) return null; const a = agg(s); return { v: metric === "clv" ? a.clv ?? 0 : a.roi ?? 0, n: s.length }; };
  const kel = st.filter((b) => b.fairAtLog != null && b.dec > 1 && riskOf(b) > 0).map((b) => { const p = b.fairAtLog, f = (p * b.dec - 1) / (b.dec - 1); return { b, f, ratio: bankroll && f > 0 ? b.stake / (bankroll * f) : null }; });
  const negEV = kel.filter((x) => x.f <= 0), ratios = kel.map((x) => x.ratio).filter((x) => x != null).sort((a, b) => a - b), medR = ratios.length ? ratios[Math.floor(ratios.length / 2)] : null;
  const promoRows = new Map();
  st.filter((b) => b.promoId).forEach((b) => { const p = promoOf(b); const kind = promoKind(b), title = p?.title || kind;
    const base = settleProfit(b, null, b.status), extra = (b.profit || 0) - base, x = expOf(b);
    const r = promoRows.get(title) || { k: title, kind, n: 0, extra: 0, pl: 0, ev: 0, nev: 0 }; r.n++; r.extra += extra; r.pl += b.profit || 0; if (x) { r.ev += x.ev; r.nev++; } promoRows.set(title, r); });
  const promoTot = [...promoRows.values()].reduce((s, r) => s + r.extra, 0);
  const wal = walletAll().filter((w) => w.tracked), walP = wal.reduce((s, w) => s + (w.real || 0), 0), walAdj = wal.reduce((s, w) => s + (w.adj || 0), 0);
  const swing = [...st].sort((a, b) => (b.profit || 0) - (a.profit || 0)), clvS = st.filter(clvOk).sort((a, b) => b.clv - a.clv);
  const mini = (b, val, num) => `<div class="an-mini">${bookLogo(b.venue || "any", b.venueName || "", 18)}<span class="ellip">${esc(b.label)}</span><span class="tiny muted">${am(b.am)} · ${esc(gameDay(b) || "")}</span><b class="num ${cls(num)}">${val}</b></div>`;
  const dq = { n: st.length, fair: st.filter((b) => b.fairAtLog != null && b.fairAtLogSrc !== "close").length, fairC: st.filter((b) => b.fairAtLogSrc === "close").length,
    clv: st.filter((b) => b.clv != null).length, clvE: st.filter((b) => b.clv != null && b.closeEst).length, legs: st.filter((b) => b.legResults?.length).length };

  return bar + `
  <div class="an-tiles">
    <div class="kpi"><div class="k">Profit${filt ? " (filtered)" : ""}</div><div class="v ${cls(A.pl + arbPl)}">${money(A.pl + arbPl)}</div><div class="s">${A.n} bets · ${A.w}-${A.l}${A.p ? "-" + A.p : ""}${stA.length ? ` · arbs ${money(arbPl)}` : ""}</div></div>
    ${!filt && wal.length ? `<div class="kpi"><div class="k">Real profit (wallet)</div><div class="v ${cls(walP)}">${money(walP)}</div><div class="s">money out + in apps − money in · check-ins ${money(walAdj)}</div></div>` : ""}
    <div class="kpi"><div class="k">ROI on money risked</div><div class="v ${cls(A.roi)}">${pct(A.roi)}</div><div class="s">95% ${boot.lo != null ? `${pct(boot.lo, 0)} to ${pct(boot.hi, 0)}` : "–"} (bootstrap) · ${money(A.plRisk)} on ${money(A.risk)} risked</div></div>
    ${free.length ? `<div class="kpi"><div class="k">Free bets</div><div class="v ${cls(freePl)}">${money(freePl)}</div><div class="s">${free.length} bonus bets and free entries (nothing risked)</div></div>` : ""}
    <div class="kpi"><div class="k">Expected</div><div class="v ${cls(A.exp)}">${money(A.exp)}</div><div class="s">at the fair when logged (${A.n - st.filter((b) => !expOf(b)).length} of ${A.n} have one)</div></div>
    <div class="kpi"><div class="k">Luck</div><div class="v ${cls(A.luck)}">${money(A.luck)}</div><div class="s">z ${fmtT(zL)} · p ${fmtP(pTwo(zL))}</div></div>
    <div class="kpi"><div class="k">Avg CLV</div><div class="v ${cls(A.clv)}">${A.clv != null ? pct(A.clv) : "–"}</div><div class="s">t ${fmtT(C.t)} · beat close ${A.beat != null ? Math.round(A.beat * 100) + "%" : "–"} of ${A.nClv}${(() => { const out = st.filter((b) => b.clv != null && !clvOk(b)).length; return out ? ` · ${out} left out (stale or estimated close)` : ""; })()}</div></div>
    <div class="kpi"><div class="k">Per day</div><div class="v ${cls(dMean)}">${money(dMean)}</div><div class="s">SD ${money(dSd)} · ${up} of ${dvals.length} days up</div></div>
    <div class="kpi"><div class="k">Streaks</div><div class="v small">${kt === "won" ? "W" : "L"}${k} now</div><div class="s">longest ${longW}W · ${longL}L · ${dlMax} losing days in a row</div></div>
    <div class="kpi"><div class="k">Promo value</div><div class="v ${cls(promoTot)}">${money(promoTot)}</div><div class="s">over the same bets without the promo</div></div>
  </div>

  <div class="sec-title">Day by day<span class="line"></span></div>
  <div class="card">${calendar((() => {
      // settled profit on each game day, plus what the open bets on that day are projected to make (same day rule)
      const cm = new Map([...dayM].map(([d, x]) => [d, { v: x.v, n: x.n, tip: x.tip }]));
      all.filter((b) => b.status === "pending" && !b.isGroup).forEach((b) => {
        const r = project(b, b.promoId ? S.promos.get(b.promoId) : null); if (!r || r.p == null) return;
        const d = gameDay(b), x = cm.get(d) || { v: 0, n: 0, tip: "" }; x.proj = (x.proj || 0) + r.ev; x.nOpen = (x.nOpen || 0) + 1; cm.set(d, x);
      });
      cm.forEach((x, d) => { if (x.proj != null) x.tip = `${x.tip ? x.tip + " · " : `${d}: `}projected ${money(x.proj)} on ${x.nOpen} open (settled + projected ${money(x.v + x.proj)})`; });
      return cm; })(), { fmt: money, short: (x) => `${x < 0 ? "-" : "+"}${Math.abs(x) >= 100 ? Math.round(Math.abs(x)) : Math.abs(x).toFixed(0)}`, sel: F.day })}
    <p class="note">Each day is the Pacific date of the game (a parlay's last game), however late it was graded or settled; the number is that day's profit and the small count is bets. ≈ is what that day's open bets are projected to add. Tap a day for its bets.</p>
    ${selDay ? `<div class="card" style="margin-top:8px;background:var(--panel-2)"><b>${esc(F.day)}</b> · ${money(selDay.v)} on ${selDay.n} bets (expected ${money(selDay.exp)})${selDay.bets.map((b) => mini(b, money(b.profit), b.profit)).join("")}</div>` : ""}
    <div class="row small" style="gap:18px;flex-wrap:wrap;margin-top:10px"><span>Days: <b>${dvals.length}</b></span><span>Up: <b>${up}</b> (${dvals.length ? Math.round(up / dvals.length * 100) : 0}%)</span><span>Best: <b class="pos">${money(Math.max(...dvals))}</b></span><span>Worst: <b class="neg">${money(Math.min(...dvals))}</b></span>
      <span>Mean ± SD: <b>${money(dMean)} ± ${money(dSd)}</b></span><span title="mean daily P/L ÷ its SD; × √days is the t-stat that the daily mean is above zero">Daily Sharpe: <b>${dSd ? (dMean / dSd).toFixed(2) : "–"}</b> (t ${dSd ? fmtT(dMean / dSd * Math.sqrt(dvals.length)) : "–"})</span></div>
    ${lineChart([{ pts: dRun, color: "var(--accent)" }, { pts: dExp, color: "var(--muted)", dash: true }], { h: 180, fmt: (x) => money(x), labels: dk.map((d, i) => (i % Math.ceil(dk.length / 7) === 0 ? d.slice(5) : "")) })}
    ${legend([["var(--accent)", "profit by day, cumulative"], ["var(--muted)", "expected at your prices", true]])}
    <div style="margin-top:8px">${dvals.length < 10 ? "" : histogram(dvals, { nb: Math.min(14, Math.max(5, Math.round(Math.sqrt(dvals.length) * 2))), fmt: (x) => money(x), h: 130 })}</div>
    ${sortTable(["Week", "Days", "Bets", "Risked", "P/L", "ROI", "Running"], wRows)}</div>

  <div class="sec-title">Skill vs luck<span class="line"></span></div>
  <div class="card">${lineChart([{ pts: run, color: "var(--accent)" }, { pts: exp, color: "var(--muted)", dash: true, band }], { fmt: (x) => money(x) })}
    ${legend([["var(--accent)", "actual profit (bet by bet)"], ["var(--muted)", "expected from your prices (shaded: ±1σ)", true]])}
    <b style="display:block;margin-top:10px">Tests</b>
    <div style="overflow-x:auto"><table class="l10t an-t an-stats"><thead><tr><th>Question</th><th>Estimate</th><th>95% interval</th><th>Statistic</th><th>p</th><th>Reading</th></tr></thead><tbody>
      ${test("Return per $ risked (pooled ROI)", pct(A.roi), boot.lo != null ? `${pct(boot.lo)} to ${pct(boot.hi)}` : "–", `n ${rets.length}`, "–", "Percentile bootstrap over bets (2,000 resamples). Bonus bets and free entries risk nothing, so they're left out here and counted in profit.")}
      ${test("Mean return per bet", R.m != null ? pct(R.m) : "–", R.lo != null ? `${pct(R.lo)} to ${pct(R.hi)}` : "–", `t ${fmtT(R.t)}`, fmtP(R.p), "Each bet's profit ÷ its risk, equally weighted; t-test against 0. Long odds make this noisy.")}
      ${test("Luck: actual − expected", money(A.luck), `±${money(1.96 * Math.sqrt(st.map(expOf).filter(Boolean).reduce((s, x) => s + x.v, 0)))}`, `z ${fmtT(zL)}`, fmtP(pTwo(zL)), "Expected uses the fair when logged; σ is the sum of each bet's Bernoulli payoff variance (independence assumed).")}
      ${test("Closing line value", C.m != null ? pct(C.m) : "–", C.lo != null ? `${pct(C.lo)} to ${pct(C.hi)}` : "–", `t ${fmtT(C.t)}`, fmtP(C.p), `Price × closing fair − 1 per bet (live bets: the fair minutes later). ${dq.clvE ? `${dq.clvE} use an estimated close (pick'em numbers no book priced).` : ""}`)}
      ${test("Beat the close", A.beat != null ? `${Math.round(A.beat * 100)}%` : "–", beatW.lo != null ? `${Math.round(beatW.lo * 100)}-${Math.round(beatW.hi * 100)}%` : "–", `n ${A.nClv}`, "–", "Wilson interval for the share of bets priced better than the close.")}
      ${test("Our fair vs the price (log loss)", llFair != null ? llFair.toFixed(4) : "–", llImp != null ? `price-implied ${llImp.toFixed(4)}` : "–", `n ${plain.length}`, "–", "No-promo bets: log loss of our fair when logged vs 1/decimal odds (includes the book's margin). Lower is better.")}
      ${test("Our fair vs the close (log loss)", llFairC != null ? llFairC.toFixed(4) : "–", llClose != null ? `close ${llClose.toFixed(4)}` : "–", `n ${withClose.length}`, "–", "If the close scores better than our fair at logging, the market moved toward the truth after you bet (normal); if ours is better, we're ahead of it.")}
      ${test("Calibration of the fair (slope b)", cf ? cf.b.toFixed(2) : "–", cf?.seB != null ? `${(cf.b - 1.96 * cf.seB).toFixed(2)} to ${(cf.b + 1.96 * cf.seB).toFixed(2)}` : "–", cf ? `a ${cf.a.toFixed(2)}` : "–", "–", "Logistic fit of results on logit(fair). b = 1 and a = 0 is perfect; b < 1 means the fairs were too confident.")}
      ${test("Brier score of the fair", brier(ps, ys)?.toFixed(4) ?? "–", "–", `n ${ps.length}`, "–", "Mean squared error of the fair when logged; 0.25 is a coin flip at 50%.")}
    </tbody></table></div>
    <p class="note">Inside the shaded range the results are variance around what your prices earned. With a few dozen bets almost nothing is significant: read the intervals, not the point estimates. CLV converges far faster than profit, which is why it leads.</p></div>

  <div class="sec-title">Closing line value<span class="line"></span></div>
  <div class="an-grid2"><div class="card"><b>Distribution</b>${histogram(st.filter(clvOk).map((b) => b.clv), { nb: 14, fmt: (x) => pct(x, 0) })}
      <p class="note">Each bet's price valued at the closing fair. Mean ${C.m != null ? pct(C.m) : "–"}, median ${(() => { const s = st.filter(clvOk).map((b) => b.clv).sort((a, b) => a - b); return s.length ? pct(s[Math.floor(s.length / 2)]) : "–"; })()}, SD ${C.sd != null ? pct(C.sd) : "–"}.</p></div>
    <div class="card"><b>Rolling</b>${(() => { const wc2 = st.filter(clvOk), RR = Math.min(20, Math.max(5, Math.floor(wc2.length / 3)));
      const roll = wc2.map((b, i) => { const s = wc2.slice(Math.max(0, i - RR + 1), i + 1); return { i, y: mean(s.map((x) => x.clv)), tip: `${b.label}: ${pct(b.clv)} · last ${s.length} avg ${pct(mean(s.map((x) => x.clv)))}` }; });
      return lineChart([{ pts: roll, color: "var(--pos)" }], { h: 170, fmt: (x) => pct(x) }) + legend([["var(--pos)", `average over the last ${RR} bets`]]); })()}</div></div>

  <div class="sec-title">Were the fairs right?<span class="line"></span></div>
  <div class="an-grid2"><div class="card">${relChart([{ name: "Your bets", color: "var(--accent)", bins: relBins(ps, ys, 5) }])}
      <p class="note">Bets grouped by the fair chance when logged (x) against how often they won (y), with 95% Wilson whiskers; dot size is the count. On the diagonal is honest.</p></div>
    <div class="card">${relChart([{ name: "Legs", color: "var(--pos)", bins: relBins(legRows.filter((l) => l.close != null).map((l) => l.close), legRows.filter((l) => l.close != null).map((l) => l.y), 10) }])}
      <p class="note">Every leg of every entry at its closing chance (x) vs whether it hit (y): ${legRows.length} legs. This is where pick'em entries are won or lost.</p></div></div>

  <div class="sec-title">Every leg<span class="line"></span></div>
  <div class="card">${chipBar("legdim", LEGDIMS, F.legDim)}${legRows.length ? legTable : `<div class="note">Leg results fill in as bets grade (older bets are backfilled hourly).</div>`}
    <p class="note">Each leg's hit rate against its closing fair chance; z = (hits − Σ close) / √Σ close(1 − close). ${lostEntries.length ? `Of ${lostEntries.length} lost multi-leg entries, <b>${oneAway}</b> missed by a single leg. Legs that sank them: ${[...killers.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6).map(([m2, n]) => `${esc(m2)} ${n}`).join(", ")}.` : ""}</p></div>

  <div class="sec-title">Breakdown explorer<span class="line"></span></div>
  <div class="card">${chipBar("andim", DIMS, F.dim)}${statTable(dimRows, DIMS.find((d) => d[0] === F.dim)[1])}
    <p class="note">Tap a column to sort. Per-bet return is each bet's profit ÷ risk, averaged, with its 95% margin; luck and z compare actual with expected at the fair when logged. ${F.dim === "sport" ? "A parlay counts in each sport it has legs in." : ""}</p></div>

  <div class="sec-title">Where you win and lose<span class="line"></span></div>
  <div class="card"><b>CLV by venue and bet type</b>${heat(venuesH, typesH, cell("clv"), { fmt: (x) => pct(x, 0), scale: 0.06 })}
    <b style="display:block;margin-top:12px">ROI by venue and bet type</b>${heat(venuesH, typesH, cell("roi"), { fmt: (x) => pct(x, 0), scale: 0.4 })}</div>

  <div class="sec-title">Sizing<span class="line"></span></div>
  <div class="card"><div class="row" style="gap:10px;align-items:center;flex-wrap:wrap"><b>Bankroll</b><input id="an-bank" inputmode="decimal" placeholder="e.g. 1000" value="${bankroll || ""}" style="width:110px">
      <b>Kelly</b><select id="an-kelly" style="width:auto">${[[0.1, "tenth"], [0.25, "quarter"], [0.5, "half"], [1, "full"]].map(([v2, l]) => `<option value="${v2}" ${(S.settings?.kelly ?? 0.25) === v2 ? "selected" : ""}>${l}</option>`).join("")}</select>
      <b>Max per bet</b><select id="an-cap" style="width:auto">${[0.01, 0.02, 0.03, 0.05, 0.1].map((v2) => `<option value="${v2}" ${(S.settings?.maxPct ?? 0.05) === v2 ? "selected" : ""}>${v2 * 100}%</option>`).join("")}</select>
      <b>1 unit</b><input id="an-unit" inputmode="decimal" placeholder="$" value="${S.settings?.unit || ""}" style="width:80px">
      <span class="tiny muted">used for the suggested stake on every edge, pick and bet slip</span></div>
    ${bankroll ? `<p class="small" style="margin:8px 0 0">Your median stake is <b>${medR != null ? `${medR.toFixed(2)}×` : "–"}</b> full Kelly at the fair when logged. ${medR == null ? "" : medR > 0.5 ? "That's aggressive: most sharp bettors use a quarter to a half Kelly because the fair itself is uncertain." : medR < 0.1 ? "Very conservative." : "A sensible fraction."}</p>` : `<p class="note">Enter a bankroll to compare each stake with the Kelly stake its edge supports.</p>`}
    ${negEV.length ? `<p class="small neg" style="margin:6px 0 0">${negEV.length} settled bets had no edge at the fair when logged (Kelly says skip): ${money(negEV.reduce((s, x) => s + (x.b.profit || 0), 0))} on them.</p>` : ""}</div>

  ${promoRows.size ? `<div class="sec-title">Promos<span class="line"></span></div><div class="card">${divRows([...promoRows.values()].reduce((m, r) => { const x = m.find((y) => y.k === r.kind) || (m.push({ k: r.kind, v: 0, n: 0 }), m.at(-1)); x.v += r.extra; x.n += r.n; return m; }, []).map((r) => ({ ...r, tip: `${r.k}: ${money(r.v)} added over ${r.n} bets` })), { fmt: money })}
    ${sortTable(["Promo", "Kind", "Bets", "P/L", "Added by the promo", "Expected at log"], [...promoRows.values()].map((r) => `<tr><td>${esc(r.k)}</td><td>${esc(r.kind)}</td>${td(r.n, r.n)}${td(`<span class="${cls(r.pl)}">${money(r.pl)}</span>`, r.pl)}${td(`<span class="${cls(r.extra)}">${money(r.extra)}</span>`, r.extra)}${td(r.nev ? money(r.ev) : "–", r.ev)}</tr>`))}
    <p class="note">"Added" is the profit above what the same bet would have paid with no promo; "expected" is the promo-adjusted EV at the fair when logged.</p></div>` : ""}

  ${(() => { const gs = [...stA, ...all.filter((b) => b.isGroup && b.status === "pending")]; if (!gs.length) return "";
    const row = (kk) => { const x = gs.filter((b) => b.kind === kk); if (!x.length) return "";
      const xs = x.filter((b) => b.status !== "pending"), pl = xs.reduce((s2, b) => s2 + (b.profit || 0), 0), ex = xs.reduce((s2, b) => s2 + (b.expPl || 0), 0), stk = x.reduce((s2, b) => s2 + b.stake, 0);
      const hit = kk === "middle" ? xs.filter((b) => b.legsB.every((l) => l.status === "won")).length : null;
      return `<tr><td>${kk === "arb" ? "Arbs" : "Middles"}</td><td class="num">${xs.length}${x.length > xs.length ? ` <span class="tiny muted">+${x.length - xs.length} open</span>` : ""}</td><td class="num">${money(stk)}</td><td class="num ${cls(pl)}">${money(pl)}</td><td class="num ${cls(ex)}">${money(ex)}</td><td class="num">${hit == null ? "–" : `${hit} of ${xs.length}`}</td></tr>`; };
    return `<div class="sec-title">Arbs and middles<span class="line"></span></div><div class="card"><div style="overflow-x:auto"><table class="l10t an-t"><thead><tr><th></th><th>Pairs</th><th>Staked</th><th>Net P/L</th><th>Expected</th><th>Middles hit</th></tr></thead><tbody>${row("arb")}${row("middle")}</tbody></table></div>
      <p class="note">Arbs and middles are their own category: left out of every number above except total profit.</p></div>`; })()}

  <div class="sec-title">Biggest swings<span class="line"></span></div>
  <div class="an-two">
    <div class="card"><b>Best results</b>${swing.slice(0, 5).map((b) => mini(b, money(b.profit), b.profit)).join("")}<b style="display:block;margin-top:10px">Worst results</b>${swing.slice(-5).reverse().map((b) => mini(b, money(b.profit), b.profit)).join("")}</div>
    <div class="card"><b>Best prices (CLV)</b>${clvS.slice(0, 5).map((b) => mini(b, pct(b.clv), b.clv)).join("") || `<div class="note">No CLV yet.</div>`}<b style="display:block;margin-top:10px">Worst prices</b>${clvS.slice(-5).reverse().map((b) => mini(b, pct(b.clv), b.clv)).join("")}</div>
  </div>

  <div class="sec-title">Every settled bet<span class="line"></span></div>
  <div class="card">${sortTable(["Day", "Bet", "Venue", "Type", "Odds", "Stake", "P/L", "Fair at log", "EV at log", "Close", "CLV", "Promo"], st.slice().reverse().map((b) => {
    const ev = edgeAt(b); return `<tr><td data-v="${Date.parse(gameDay(b) || 0) || 0}">${esc(gameDay(b) || "")}</td><td class="ellip" style="max-width:280px;text-align:left" title="${esc(b.label)}">${esc(b.label)}</td><td>${esc(b.venueName || b.venue || "")}</td><td>${esc(betType(b))}</td>
      ${td(am(b.am), b.dec)}${td(money(b.stake), b.stake)}${td(`<span class="${cls(b.profit)}">${money(b.profit)}</span>`, b.profit)}${td(b.fairAtLog != null ? `${prob(b.fairAtLog)}${b.fairAtLogSrc === "close" ? "*" : ""}` : "–", b.fairAtLog)}
      ${td(ev != null ? pct(ev) : "–", ev)}${td(b.closeFair != null ? `${prob(b.closeFair)}${b.closeEst ? "~" : ""}` : "–", b.closeFair)}${td(`<span class="${cls(b.clv)}">${b.clv != null ? pct(b.clv) : "–"}</span>`, b.clv)}<td class="small">${esc(b.promoId ? promoKind(b) : "")}</td></tr>`; }))}
    <p class="note">* no fair was logged, so the close stands in. ~ the close is estimated (a pick'em number no book priced: the player's closing mean at that number).</p></div>

  <div class="sec-title">What these numbers rest on<span class="line"></span></div>
  <div class="card small">${dq.n} settled bets: <b>${dq.fair}</b> with a fair logged when placed${dq.fairC ? `, <b>${dq.fairC}</b> using the close instead` : ""}; <b>${dq.clv}</b> with CLV${dq.clvE ? ` (${dq.clvE} on an estimated close)` : ""}; <b>${dq.legs}</b> with each leg's result. Settled bets missing any of these are filled from the pre-game snapshots every hour where the game is still on file (21 days).</div>`;
}

export function bindAnalysis(el, rerender) {
  const i = el.querySelector("#an-bank");
  const save = (x) => saveSettings(x).then(rerender).catch(() => {});
  if (i) i.onchange = () => save({ bankroll: +i.value || 0 });
  const k = el.querySelector("#an-kelly"); if (k) k.onchange = () => save({ kelly: +k.value });
  const c = el.querySelector("#an-cap"); if (c) c.onchange = () => save({ maxPct: +c.value });
  const u = el.querySelector("#an-unit"); if (u) u.onchange = () => save({ unit: +u.value || 0 });
  el.querySelectorAll("[data-af]").forEach((s) => s.onchange = () => { F[s.dataset.af] = s.value; F.day = null; rerender(); });
  el.querySelectorAll("[data-andim]").forEach((b) => b.onclick = () => { F.dim = b.dataset.andim; rerender(); });
  el.querySelectorAll("[data-legdim]").forEach((b) => b.onclick = () => { F.legDim = b.dataset.legdim; rerender(); });
  el.querySelectorAll("[data-day]").forEach((b) => b.onclick = () => { F.day = F.day === b.dataset.day ? null : b.dataset.day; rerender(); });
  const cv = el.querySelector("#an-csv");
  if (cv) cv.onclick = () => {
    const rows = filtered(unify([...S.bets.values()])).filter((b) => DONE.includes(b.status) || b.status === "pending");
    downloadCsv(`slate-ledger-bets-${ptDay(new Date().toISOString())}.csv`,
      ["id", "game_day", "created", "settled", "status", "label", "venue", "type", "legs", "american", "decimal", "stake", "risk", "profit", "fair_at_log", "fair_src", "ev_per_risk", "close_fair", "close_est", "clv", "promo", "source", "sports", "live"],
      rows.map((b) => [b.id || b.groupId || "", gameDay(b), b.createdAt, b.settledAt, b.status, b.label, b.venueName || b.venue, betType(b), (b.legs || []).length || 1, b.am, b.dec, b.stake, riskOf(b), b.profit,
        b.fairAtLog, b.fairAtLogSrc || "", edgeAt(b), b.closeFair, b.closeEst ? 1 : 0, b.clv, b.promoId ? promoKind(b) : "", b.source || "", sportKeys(b).join("|"), b.live ? 1 : 0]));
  };
  bindSort(el);
  bindTips(el);
}
