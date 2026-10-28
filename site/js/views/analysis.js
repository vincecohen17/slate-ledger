// Ledger > Analysis: everything the settled bets can say. Skill vs luck, closing line value over time, how well the
// prices were judged, where (venue x bet type) the money is made or lost, timing, sizing against Kelly, promo value,
// parlays, and the biggest swings. Each section says in one line what to do with it.
import { S, saveSettings } from "../store.js";
import { esc, am, pct, prob, money, fmtShortDay, bookLogo } from "../util.js";
import { settleProfit, kindOf, normalize } from "../promo.js";
import { VENUES } from "../lab.js";
import { lineChart, legend, divRows, calibChart, heat } from "./charts.js";

const DFS = new Set(VENUES.filter((v) => v.dfs).map((v) => v.key));
const DONE = ["won", "lost", "push"];
const promoOf = (b) => (b.promoId ? S.promos.get(b.promoId) : null);
export function betType(b) {
  if ((b.legs || []).length > 1) return "Parlay";
  if (DFS.has(b.venue)) return "Pick'em";
  const m = b.market || b.legs?.[0]?.market || "";
  if (b.player || b.legs?.[0]?.player || /^(player|batter|pitcher)_/.test(m)) return "Player prop";
  if (/^h2h/.test(m)) return "Moneyline";
  if (/spreads/.test(m)) return "Spread";
  if (/totals/.test(m)) return "Total";
  return "Other";
}
// what a bet was expected to make at the fair when logged (promo math included)
function expOf(b) {
  const p = b.fairAtLog;
  if (p == null || !b.dec) return null;
  const pr = promoOf(b), w = settleProfit(b, pr, "won"), l = settleProfit(b, pr, "lost");
  return { ev: p * w + (1 - p) * l, v: p * (1 - p) * (w - l) ** 2 };
}
function agg(list) {
  const g = list.filter((b) => DONE.includes(b.status));
  const st = g.reduce((s, b) => s + (b.stake || 0), 0), pl = g.reduce((s, b) => s + (b.profit || 0), 0);
  const c = g.filter((b) => b.clv != null), ex = g.map(expOf).filter(Boolean);
  return { n: g.length, w: g.filter((b) => b.status === "won").length, l: g.filter((b) => b.status === "lost").length, staked: st, pl,
    roi: st ? pl / st : null, clv: c.length ? c.reduce((s, b) => s + b.clv, 0) / c.length : null, nClv: c.length,
    beat: c.length ? c.filter((b) => b.clv > 0).length / c.length : null, exp: ex.reduce((s, x) => s + x.ev, 0) };
}
const hoursBefore = (b) => (b.commence && b.createdAt ? (new Date(b.commence) - new Date(b.createdAt)) / 3600000 : null);
const timing = (b) => { if (b.live) return "Live (in-game)"; const h = hoursBefore(b); return h == null ? "Unknown" : h < 1 ? "Under 1h before" : h < 3 ? "1-3h before" : h < 12 ? "3-12h before" : h < 36 ? "12-36h before" : "36h+ before"; };
const TIMING = ["36h+ before", "12-36h before", "3-12h before", "1-3h before", "Under 1h before", "Live (in-game)"];
const day = (b) => new Date(b.commence || b.createdAt).toLocaleDateString("en-US", { weekday: "short", timeZone: "America/Los_Angeles" });
const stakeBand = (b, cuts) => (b.stake <= cuts[0] ? `Smallest third (≤ ${money(cuts[0])})` : b.stake <= cuts[1] ? "Middle third" : `Biggest third (> ${money(cuts[1])})`);



function table(rows, head) {
  return `<div style="overflow-x:auto"><table class="l10t an-t"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
function groupRows(list, keyFn, order) {
  const by = new Map();
  list.forEach((b) => { const k = keyFn(b); if (!by.has(k)) by.set(k, []); by.get(k).push(b); });
  let ks = [...by.keys()];
  if (order) ks = order.filter((k) => by.has(k)).concat(ks.filter((k) => !order.includes(k)));
  return ks.map((k) => ({ k, ...agg(by.get(k)) })).filter((r) => r.n);
}
function statTable(rows, label = "") {
  return table(rows.map((r) => `<tr><td>${r.html || esc(r.k)}</td><td class="num">${r.n}</td><td class="num">${r.w}-${r.l}</td>
    <td class="num ${cls(r.pl)}">${money(r.pl)}</td><td class="num ${cls(r.roi)}">${r.roi != null ? pct(r.roi) : "–"}</td>
    <td class="num ${cls(r.pl - r.exp)}" title="actual minus expected at your prices">${money(r.pl - r.exp)}</td>
    <td class="num ${cls(r.clv)}">${r.clv != null ? pct(r.clv) : "–"}</td><td class="num">${r.beat != null ? Math.round(r.beat * 100) + "%" : "–"}</td></tr>`),
    [label, "Bets", "W-L", "P/L", "ROI", "Luck", "Avg CLV", "Beat close"]);
}

export function analysis() {
  const bankroll = S.settings?.bankroll || 0;
  const all = [...S.bets.values()];
  const st = all.filter((b) => DONE.includes(b.status)).sort((a, b) => (a.settledAt || "").localeCompare(b.settledAt || ""));
  if (st.length < 3) return `<div class="card note" style="margin-top:12px">The analysis fills in after a few settled bets.</div>`;
  const A = agg(st);

  // skill vs luck: running P/L against what the prices said, with a 1-sigma band
  let c = 0, e = 0, v = 0, peak = 0, dd = 0, ddAt = null;
  const run = [], exp = [], band = [];
  st.forEach((b, i) => {
    c += b.profit || 0; const x = expOf(b); if (x) { e += x.ev; v += x.v; }
    peak = Math.max(peak, c); if (peak - c > dd) { dd = peak - c; ddAt = b.settledAt; }
    run.push({ i, y: c, tip: `${fmtShortDay(b.settledAt || b.createdAt)} · ${b.label}<br>${money(b.profit)} · running ${money(c)} · expected ${money(e)}` });
    exp.push({ i, y: e }); band[i] = { lo: e - Math.sqrt(v), hi: e + Math.sqrt(v) };
  });
  const z = v > 0 ? (c - e) / Math.sqrt(v) : 0;
  // streaks
  let cur = 0, curT = null, longL = 0, longW = 0, k = 0, kt = null;
  st.forEach((b) => { if (b.status === "push") return; if (b.status === kt) k++; else { kt = b.status; k = 1; } if (kt === "lost") longL = Math.max(longL, k); else longW = Math.max(longW, k); });
  cur = k; curT = kt;
  const avgDec = st.reduce((s, b) => s + (b.dec || 0), 0) / st.length;
  const days = Math.max(1, (new Date(st.at(-1).settledAt || Date.now()) - new Date(st[0].createdAt || st[0].settledAt)) / 86400000);

  // rolling CLV
  const wc = st.filter((b) => b.clv != null), R = Math.min(20, Math.max(5, Math.floor(wc.length / 3)));
  const roll = wc.map((b, i) => { const s = wc.slice(Math.max(0, i - R + 1), i + 1); return { i, y: s.reduce((a, x) => a + x.clv, 0) / s.length, tip: `${b.label}: ${pct(b.clv)} · last ${s.length} avg ${pct(s.reduce((a, x) => a + x.clv, 0) / s.length)}` }; });

  // calibration of the prices: fair at log vs results (singles)
  const singles = st.filter((b) => b.fairAtLog != null && b.status !== "push" && (b.legs || []).length <= 1);
  const cuts = [[0, 0.3, "<30%"], [0.3, 0.45, "30-45%"], [0.45, 0.55, "45-55%"], [0.55, 0.7, "55-70%"], [0.7, 1.01, "70%+"]];
  const cal = cuts.map(([lo, hi, k]) => { const s = singles.filter((b) => b.fairAtLog >= lo && b.fairAtLog < hi);
    return { k, n: s.length, exp: s.length ? s.reduce((a, b) => a + b.fairAtLog, 0) / s.length : 0, act: s.length ? s.filter((b) => b.status === "won").length / s.length : 0 }; });

  // venue x type
  const types = [...new Set(st.map(betType))], venues = [...new Set(st.map((b) => b.venueName || b.venue))];
  const cell = (metric) => (vn, ty) => { const s = st.filter((b) => (b.venueName || b.venue) === vn && betType(b) === ty); if (!s.length) return null; const a = agg(s); return { v: metric === "clv" ? a.clv ?? 0 : a.roi ?? 0, n: s.length }; };

  // sizing vs Kelly at the fair when logged
  const kel = st.filter((b) => b.fairAtLog != null && b.dec > 1).map((b) => { const p = b.fairAtLog, f = (p * b.dec - 1) / (b.dec - 1); return { b, f, ratio: bankroll && f > 0 ? b.stake / (bankroll * f) : null }; });
  const negEV = kel.filter((x) => x.f <= 0), ratios = kel.map((x) => x.ratio).filter((x) => x != null).sort((a, b) => a - b);
  const medR = ratios.length ? ratios[Math.floor(ratios.length / 2)] : null;

  // promo value: what each promo added over the same bet without it
  const promoRows = new Map();
  st.filter((b) => b.promoId).forEach((b) => { const p = promoOf(b); const kind = p ? kindOf(normalize(p).kind).name : "Promo (deleted)";
    const base = settleProfit(b, null, b.status), extra = (b.profit || 0) - base; const r = promoRows.get(kind) || { k: kind, n: 0, extra: 0, pl: 0 }; r.n++; r.extra += extra; r.pl += b.profit || 0; promoRows.set(kind, r); });
  const promoTot = [...promoRows.values()].reduce((s, r) => s + r.extra, 0);

  // parlays by legs
  const legsN = (b) => { const n = (b.legs || []).length || 1; return n === 1 ? "Single" : n <= 3 ? `${n} legs` : n <= 5 ? "4-5 legs" : "6+ legs"; };

  // stake thirds
  const ss = st.map((b) => b.stake).sort((a, b) => a - b), sc = [ss[Math.floor(ss.length / 3)], ss[Math.floor(ss.length * 2 / 3)]];

  const swing = [...st].sort((a, b) => (b.profit || 0) - (a.profit || 0));
  const clvS = st.filter((b) => b.clv != null).sort((a, b) => b.clv - a.clv);
  const mini = (b, val, num) => `<div class="an-mini">${bookLogo(b.venue || "any", b.venueName || "", 18)}<span class="ellip">${esc(b.label)}</span><span class="tiny muted">${am(b.am)} · ${fmtShortDay(b.settledAt || b.createdAt)}</span><b class="num ${cls(num)}">${val}</b></div>`;

  return `
  <div class="an-tiles">
    <div class="kpi"><div class="k">Profit</div><div class="v ${cls(A.pl)}">${money(A.pl)}</div><div class="s">${A.n} settled · ROI ${pct(A.roi)}</div></div>
    <div class="kpi"><div class="k">Expected</div><div class="v ${cls(A.exp)}">${money(A.exp)}</div><div class="s">from the prices you got</div></div>
    <div class="kpi"><div class="k">Luck</div><div class="v ${cls(A.pl - A.exp)}">${money(A.pl - A.exp)}</div><div class="s">${Math.abs(z) < 1 ? "normal variance" : Math.abs(z) < 2 ? "a real swing" : "a big swing"} (${z >= 0 ? "+" : ""}${z.toFixed(1)}σ)</div></div>
    <div class="kpi"><div class="k">Avg CLV</div><div class="v ${cls(A.clv)}">${A.clv != null ? pct(A.clv) : "–"}</div><div class="s">beat close ${A.beat != null ? Math.round(A.beat * 100) + "%" : "–"} of ${A.nClv}</div></div>
    <div class="kpi"><div class="k">Max drawdown</div><div class="v neg">${money(-dd)}</div><div class="s">${ddAt ? `deepest ${fmtShortDay(ddAt)}` : "none yet"}</div></div>
    <div class="kpi"><div class="k">Streaks</div><div class="v small">${curT === "won" ? "W" : "L"}${cur} now</div><div class="s">longest: ${longW}W · ${longL}L</div></div>
    <div class="kpi"><div class="k">Typical bet</div><div class="v small">${am(Math.round(avgDec >= 2 ? (avgDec - 1) * 100 : -100 / (avgDec - 1)))}</div><div class="s">${money(A.staked / A.n)} avg stake · ${(A.n / days * 7).toFixed(1)} bets/week</div></div>
    <div class="kpi"><div class="k">Promo value</div><div class="v ${cls(promoTot)}">${money(promoTot)}</div><div class="s">extra from promos on settled bets</div></div>
  </div>

  <div class="sec-title">Skill vs luck<span class="line"></span></div>
  <div class="card">${lineChart([{ pts: run, color: "var(--accent)" }, { pts: exp, color: "var(--muted)", dash: true, band }], { fmt: (x) => money(x) })}
    ${legend([["var(--accent)", "actual profit"], ["var(--muted)", "expected from your prices (shaded: normal luck range)", true]])}
    <p class="note">Inside the shaded range the results are just variance around what your prices earned. Above it you've run hot, below it cold. The dashed line climbing is the skill part: it only rises if you keep getting prices better than fair.</p></div>

  <div class="sec-title">Closing line value over time<span class="line"></span></div>
  <div class="card">${lineChart([{ pts: roll, color: "var(--pos)" }], { fmt: (x) => pct(x) })}
    ${legend([["var(--pos)", `average CLV over the last ${R} bets`]])}
    <p class="note">The single best predictor of long-run profit. Holding above zero means you consistently beat where the market closed (for live bets, where it was a few minutes later).</p></div>

  <div class="sec-title">Were the prices right?<span class="line"></span></div>
  <div class="card">${calibChart(cal)}
    <p class="note">Single bets grouped by the fair chance when you logged them. If the green bars track the grey ones, our fair prices are honest; small samples swing a lot, so read this after 100+ bets.</p></div>

  <div class="sec-title">Where you win and lose<span class="line"></span></div>
  <div class="card"><b>CLV by venue and bet type</b>${heat(venues, types, cell("clv"), { fmt: (x) => pct(x, 0), scale: 0.06 })}
    <b style="display:block;margin-top:12px">ROI by venue and bet type</b>${heat(venues, types, cell("roi"), { fmt: (x) => pct(x, 0), scale: 0.4 })}
    <p class="note">Green cells with a decent count are where to keep firing; orange CLV cells are where the venue's prices are beating you.</p></div>
  <div class="card" style="margin-top:10px"><b>By venue</b>${statTable(groupRows(st, (b) => b.venueName || b.venue).map((r) => ({ ...r, html: `<span class="lg-name">${bookLogo(st.find((b) => (b.venueName || b.venue) === r.k)?.venue || "any", r.k, 16)}${esc(r.k)}</span>` })), "Venue")}</div>
  <div class="card" style="margin-top:10px"><b>By sport</b>${statTable(groupRows(st, (b) => b.league || "Unknown"), "Sport")}</div>
  <div class="card" style="margin-top:10px"><b>By bet type</b>${statTable(groupRows(st, betType), "Type")}</div>

  <div class="sec-title">Timing<span class="line"></span></div>
  <div class="card">${statTable(groupRows(st, timing, TIMING), "When you bet")}
    <p class="note">Earlier bets usually earn more CLV (soft openers) and later bets carry more information (news, lineups). Your own split says which works for you.</p></div>
  <div class="card" style="margin-top:10px"><b>By day</b>${statTable(groupRows(st, day, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]), "Day")}</div>

  <div class="sec-title">Sizing<span class="line"></span></div>
  <div class="card"><div class="row" style="gap:10px;align-items:center;flex-wrap:wrap"><b>Bankroll</b><input id="an-bank" inputmode="decimal" placeholder="e.g. 1000" value="${bankroll || ""}" style="width:110px">
      <b>Kelly</b><select id="an-kelly">${[[0.1, "tenth"], [0.25, "quarter"], [0.5, "half"], [1, "full"]].map(([v, l]) => `<option value="${v}" ${(S.settings?.kelly ?? 0.25) === v ? "selected" : ""}>${l}</option>`).join("")}</select>
      <b>Max per bet</b><select id="an-cap">${[0.01, 0.02, 0.03, 0.05, 0.1].map((v) => `<option value="${v}" ${(S.settings?.maxPct ?? 0.05) === v ? "selected" : ""}>${v * 100}%</option>`).join("")}</select>
      <b>1 unit</b><input id="an-unit" inputmode="decimal" placeholder="$" value="${S.settings?.unit || ""}" style="width:80px">
      <span class="tiny muted">used for the suggested stake on every edge, pick and bet slip</span></div>
    ${bankroll ? `<p class="small" style="margin:8px 0 0">Your median stake is <b>${medR != null ? `${medR.toFixed(2)}×` : "–"}</b> full Kelly at the fair when logged. ${medR == null ? "" : medR > 0.5 ? "That's aggressive: most sharp bettors use a quarter to a half Kelly because the fair itself is uncertain." : medR < 0.1 ? "Very conservative: fine while you build a record, but you're leaving growth on the table on your best edges." : "A sensible fraction."}</p>` : `<p class="note">Enter a bankroll to compare each stake with the Kelly stake its edge supports.</p>`}
    ${negEV.length ? `<p class="small neg" style="margin:6px 0 0">${negEV.length} settled bets had no edge at the fair when logged (Kelly says skip): ${money(negEV.reduce((s, x) => s + (x.b.profit || 0), 0))} on them.</p>` : ""}
    ${statTable(groupRows(st, (b) => stakeBand(b, sc), [`Smallest third (≤ ${money(sc[0])})`, "Middle third", `Biggest third (> ${money(sc[1])})`]), "Stake size")}</div>

  <div class="sec-title">Parlays and promos<span class="line"></span></div>
  <div class="card">${statTable(groupRows(st, legsN, ["Single", "2 legs", "3 legs", "4-5 legs", "6+ legs"]), "Legs")}
    <p class="note">Each extra leg compounds the hold; parlays only make sense with correlation (same-game legs that move together) or a boost that covers the extra margin.</p></div>
  ${promoRows.size ? `<div class="card" style="margin-top:10px"><b>What each promo type added</b>${divRows([...promoRows.values()].map((r) => ({ k: r.k, v: r.extra, n: r.n, tip: `${r.k}: ${money(r.extra)} added over ${r.n} bets (total P/L ${money(r.pl)})` })), { fmt: money })}
    <p class="note">Profit above what the same bet would have paid without the promo.</p></div>` : ""}

  <div class="sec-title">Biggest swings<span class="line"></span></div>
  <div class="an-two">
    <div class="card"><b>Best results</b>${swing.slice(0, 5).map((b) => mini(b, money(b.profit), b.profit)).join("")}<b style="display:block;margin-top:10px">Worst results</b>${swing.slice(-5).reverse().map((b) => mini(b, money(b.profit), b.profit)).join("")}</div>
    <div class="card"><b>Best prices (CLV)</b>${clvS.slice(0, 5).map((b) => mini(b, pct(b.clv), b.clv)).join("") || `<div class="note">No CLV yet.</div>`}<b style="display:block;margin-top:10px">Worst prices</b>${clvS.slice(-5).reverse().map((b) => mini(b, pct(b.clv), b.clv)).join("")}</div>
  </div>`;
}

export function bindAnalysis(el, rerender) {
  const i = el.querySelector("#an-bank");
  const save = (x) => saveSettings(x).then(rerender).catch(() => {});
  if (i) i.onchange = () => save({ bankroll: +i.value || 0 });
  const k = el.querySelector("#an-kelly"); if (k) k.onchange = () => save({ kelly: +k.value });
  const c = el.querySelector("#an-cap"); if (c) c.onchange = () => save({ maxPct: +c.value });
  const u = el.querySelector("#an-unit"); if (u) u.onchange = () => save({ unit: +u.value || 0 });
}
