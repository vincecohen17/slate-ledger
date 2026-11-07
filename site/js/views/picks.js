import { WHY, edgeFor } from "./market.js";
import { S } from "../store.js";
import { esc, am, pct, prob, fmtTime, fmtShortDay, units, todayPT, bookTag, $$, odds, oddsP } from "../util.js";
import { centsTag, kUsd } from "../util.js";
import { betImage, bindLog, labBtn } from "./common.js";
import { projectedCard, pickAsBet, betLegs, legNow, progress, progressHtml, gameState } from "../projected.js";
import { sitText } from "./live.js";

const PILLARS = [["model", "Model"], ["advanced", "Adv stats"], ["matchup", "Matchup"], ["role", "Role"], ["context", "Context"], ["market", "Market"]];
const ICON = { support: "▲", neutral: "•", against: "▼" };
let day = null;

// where the pick sits: sharp fair, our model, the blended fair we bet from, and the play-to price
function meter(p) {
  // the worst price still worth taking (goodAt) as a probability; the green band is the value between it and our fair
  const best = typeof p.goodAt === "number" ? 1 / decOf(p.goodAt) : null;
  const pts = [["Market", p.marketFair, "mk"], ["Model", p.modelP, "md"], ["Our fair", p.fair, "fr"], ["Play to", best, "bp"]].filter((x) => x[1] != null);
  if (pts.length < 2) return "";
  const lo = Math.max(0, Math.min(...pts.map((x) => x[1])) - 0.06), hi = Math.min(1, Math.max(...pts.map((x) => x[1])) + 0.06);
  const X = (v) => ((v - lo) / (hi - lo) * 100).toFixed(1);
  return `<div class="pmeter"><div class="pm-track">${best != null && p.fair != null ? `<i class="pm-edge" style="left:${X(Math.min(best, p.fair))}%;width:${Math.abs(X(p.fair) - X(best))}%"></i>` : ""}
    ${pts.map(([lab, v, c]) => `<span class="pm-dot ${c}" style="left:${X(v)}%" title="${lab} ${prob(v, 1)}"></span>`).join("")}</div>
    <div class="pm-leg">${pts.map(([lab, v, c]) => `<span><i class="pm-dot ${c}"></i>${lab} <b>${prob(v, 1)}</b></span>`).join("")}</div></div>`;
}
const decOf = (a) => a > 0 ? 1 + a / 100 : 1 + 100 / Math.abs(a);
const paras = (t) => String(t || "").split(/\n\s*\n/).filter(Boolean).map((x) => `<p>${esc(x)}</p>`).join("");

// in play: score and clock, the pick's own stat now -> projected finish (the engine's betLegs "<pickId>#0" every live run),
// its live chance to cash, and units projected; shown from the start until the pick grades
const isOpen = (p) => ["open", "pending", undefined, null].includes(p.status);
function pickLive(p) {
  if (!isOpen(p) || !p.gameId) return "";
  const st = gameState(p.gameId);
  if (!st) return "";
  const leg = betLegs({ ...p, label: p.selection })[0];
  const r = legNow({ ...leg, fair: p.fair ?? null });
  const pg = progress(leg, r);
  const b = pickAsBet(p), chance = r.p != null && r.src !== "final" ? r.p : null;
  const exp = b && chance != null ? chance * (p.units || 0) * (b.dec - 1) - (1 - chance) * (p.units || 0) : null;
  const final = S.liveGames.get(p.gameId)?.final || r.src === "final";
  return `<div class="pk-live">
    <div class="row" style="justify-content:space-between;gap:6px;flex-wrap:wrap">
      <span class="row" style="gap:6px">${final ? `<span class="pill">final · grading</span>` : `<span class="pill live">● LIVE</span>`}<span class="small num">${st.away?.score ?? ""}–${st.home?.score ?? ""}</span><span class="tiny muted">${esc(sitText(st, p.league))}</span></span>
      ${chance != null ? `<span class="tiny"><span class="muted">live chance</span> <b class="num">${prob(chance)}</b>${exp != null && p.units ? ` · <span class="muted">exp.</span> <b class="num ${exp >= 0 ? "pos" : "neg"}">${exp >= 0 ? "+" : ""}${exp.toFixed(2)}u</b>` : ""}</span>` : ""}</div>
    ${progressHtml(pg)}</div>`;
}

export function pickCard(p) {
  const st = p.status || "pending";
  const stPill = st === "won" ? `<span class="pill pos">won</span>` : st === "lost" ? `<span class="pill neg">lost</span>` : st === "void" ? `<span class="pill neg">withdrawn</span>` : `<span class="pill">${esc(st)}</span>`;
  const tier = (p.tier === "floor" ? `<span class="pill">floor</span>` : p.tier === "likely" ? `<span class="pill">likely · 0u</span>` : p.tier === "lean" ? `<span class="pill">lean · tracked</span>` : `<span class="pill s">bet</span>`)
    + (p.early ? ` <span class="pill v" title="posted the night before at the openers${p.earlyAt ? ` (${fmtTime(p.earlyAt)})` : ""}; the morning run re-checks it">early</span>` : "");
  const pillars = PILLARS.map(([k, t]) => {
    const v = p.pillars?.[k]; if (!v) return "";
    return `<span class="pillar ${v === "support" ? "sup" : v === "against" ? "ag" : ""}">${ICON[v] || ""} ${t}</span>`;
  }).join("");
  const liveRow = pickLive(p);
  // live check from the engine (every run): current price, still listed, still worth taking; else what the run posted
  const lv = S.picksLive?.[p.id];
  const vrows = lv ? lv.venues : (p.playTo || []).map((v) => ({ ...v, now: typeof v.price === "number" ? v.price : null, listed: true }));
  const venues = vrows.map((v) => `<div class="lrow" style="grid-template-columns:1fr auto auto">
      <span>${bookTag(v.venue, v.name || v.venue, 15)}${v.line != null ? ` <span class="tiny muted">${esc(v.line)}</span>` : ""}${v.note ? ` <span class="tiny muted">· ${esc(v.note)}</span>` : ""}</span>
      <span class="px">${v.listed === false ? `<span class="faint">not listed</span>` : (v.now != null ? odds(v.now) : "–")}${v.listed !== false && v.now != null && centsTag(v.now, v.venue) ? ` <span class="tiny muted">${centsTag(v.now, v.venue)}</span>` : ""}${v.avail != null && v.listed !== false ? ` <span class="tiny ${v.avail < 25 ? "neg" : "muted"}" title="on offer at this price right now">${kUsd(v.avail)}</span>` : ""}</span>
      <span class="${v.inRange ? "pos" : "faint"}">${v.inRange ? "✓ in range" : v.listed === false ? "–" : "wait"}</span></div>`).join("")
    + (lv ? `<div class="tiny muted" style="margin-top:4px">Checked ${fmtTime(lv.at)}${lv.fairNow != null ? ` · sharp fair now ${prob(lv.fairNow, 1)}` : ""}${lv.anyInRange ? "" : ` · <span class="neg">no venue in range right now</span>`}</div>` : "");
  const eNow = !st || st === "open" || st === "pending" ? edgeFor(p) : null, w = p.why || eNow?.why, wk = w ? WHY[w.k] : null;
  const whyHtml = w ? `<div class="why why-${esc(w.k || "")}"><div class="why-h"><b class="${wk?.tone || ""}" title="${esc(wk?.tip || "")}">${wk?.icon || "◆"} ${esc(wk?.label || "Why")}</b>${p.why ? "" : `<span class="tiny muted">from the board now</span>`}</div><div class="small">${esc(w.t || "")}</div>
    ${p.why && eNow?.why && eNow.why.k !== p.why.k ? `<div class="tiny muted">Now: ${esc(WHY[eNow.why.k]?.label || "")}: ${esc(eNow.why.t)}</div>` : ""}</div>` : "";
  return `<div class="card col ${st === "void" ? "pk-void" : ""}" style="gap:10px">
    ${st === "void" && p.voidReason ? `<div class="pk-vbar">Withdrawn: ${esc(p.voidReason)}</div>` : ""}
    <div class="bet">${betImage({ gameId: p.gameId, label: p.selection, player: p.player, headshot: p.headshot, league: p.league })}
      <div class="col"><span class="what">${esc(p.selection)}</span><span class="sub">${esc(p.matchup || "")}${p.commence ? ` · ${fmtTime(p.commence)}` : ""}</span></div>
      <div class="col" style="align-items:flex-end">${units(p.units)}<span class="tiny muted">${p.units ?? "–"}u${S.settings?.unit && p.units ? ` · $${Math.round(p.units * S.settings.unit)}` : ""}</span></div></div>
    ${liveRow}
    ${whyHtml}
    <div class="row" style="gap:6px;flex-wrap:wrap">${tier}${stPill}${p.clv != null ? `<span class="pill ${p.clv > 0 ? "pos" : "neg"}">CLV ${pct(p.clv)}</span>` : ""}
      ${(() => {      // the checks as posted, with the model check recomputed from the pick's own numbers
        const mOK = p.modelP != null && p.marketFair != null ? p.modelP > p.marketFair : null;
        const ch = p.checks ? { ...p.checks } : mOK != null ? { model: mOK } : null;
        if (!ch) return "";
        const wrong = ch.model === true && mOK === false;
        if (mOK != null) ch.model = mOK;
        return `<span class="pk-checks" title="a pick needs the sports case, our model and the price to agree">${[["logic", "Sports logic"], ["model", "Model"], ["price", "Price"]].filter(([k]) => ch[k] != null).map(([k, t]) => `<span class="pillar ${ch[k] ? "sup" : "ag"}">${ch[k] ? "✓" : "✗"} ${t}</span>`).join("")}</span>`
          + (wrong ? `<span class="pill neg" title="posted as model-agrees, but our model's ${prob(p.modelP, 1)} is below the market's ${prob(p.marketFair, 1)} on this side">model actually below market</span>` : "");
      })()}
      ${(p.fair || 0) >= 0.7 ? `<span class="pill" title="a 70%+ favorite pays little and loses to minutes, blowouts and foul trouble; it needs a real price edge">heavy favorite</span>` : ""}</div>
    ${p.logicCase ? `<div class="small" style="line-height:1.45"><b>The sports case:</b> ${esc(p.logicCase)}</div>` : ""}
    ${p.evidence && ((p.evidence.lean || []).length || p.evidence.support != null) ? `<div class="tiny row" style="gap:4px;flex-wrap:wrap"><span class="muted">On the record:</span>${(p.evidence.lean || []).map((k) => `<span class="pill pos">▲ ${esc(k)}</span>`).join("")}${p.evidence.support != null ? `<span class="pill ${p.evidence.support >= 0 && p.evidence.support < 1.5 ? "pos" : "neg"}" title="where our model sits from the price (0) to the fair (1)">model support ${(+p.evidence.support).toFixed(2)}</span>` : ""}</div>` : ""}
    <div class="playto">
      <div class="fc"><div class="k">Fair</div><div class="v">${p.fair != null ? oddsP(p.fair) : odds(p.fairAm)}</div><div class="s">no-vig fair</div></div>
      <div class="fc"><div class="k">Good at</div><div class="v">${odds(p.goodAt)}</div><div class="s">or better</div></div>
      <div class="fc"><div class="k">Great at</div><div class="v">${odds(p.greatAt)}</div><div class="s">bet bigger</div></div>
    </div>
    ${meter(p)}
    ${pillars ? `<div class="pillars">${pillars}</div>` : ""}
    ${(p.keyStats || []).length ? `<div class="kstats">${p.keyStats.map((k) => `<div class="kstat ${k.tone || ""}"><b>${esc(k.value)}</b><span>${esc(k.label)}</span>${k.note ? `<i>${esc(k.note)}</i>` : ""}</div>`).join("")}</div>` : ""}
    ${p.case ? `<div class="pcase"><div class="pc-h pos">▲ Why it's a good pick</div>${paras(p.case)}</div>` : ""}
    ${p.risk ? `<div class="pcase risk"><div class="pc-h neg">▼ What could go wrong</div>${paras(p.risk)}</div>` : ""}
    ${venues ? `<div><div class="tiny muted" style="margin-bottom:4px">Play-to by venue</div><div class="ladder">${venues}</div></div>` : ""}
    ${(p.confirm || []).length ? `<div class="note"><b>Confirm before start:</b> ${p.confirm.map(esc).join(" · ")}</div>` : ""}
    <div><button class="btn sm ghost" data-log='${esc(JSON.stringify({ label: p.selection, gameId: p.gameId, league: p.league, matchup: p.matchup, commence: p.commence, market: p.market, side: p.side, point: p.point, player: p.player, team: p.team, pickId: p.id, am: p.playTo?.find((v) => v.inRange)?.price ?? p.goodAt, venue: p.playTo?.find((v) => v.inRange)?.venue, fair: p.fair, source: "pick" }))}'>Log this bet</button>
      ${new Date(p.commence) > new Date() && p.status === "open" ? labBtn({ gameId: p.gameId, market: p.market, side: p.side, point: p.point, player: p.player, team: p.team }, { venue: p.playTo?.find((v) => v.inRange)?.venue }, "Open in Bet Lab") : ""}</div>
  </div>`;
}

export function renderPicks(el) {
  const all = [...S.picks.values()].sort((a, b) => (b.date || "").localeCompare(a.date || "") || (a.commence || "").localeCompare(b.commence || ""));
  const days = [...new Set(all.map((p) => p.date))].filter(Boolean);
  if (!day) day = days.includes(todayPT()) ? todayPT() : days[0];
  const shown = all.filter((p) => p.date === day).sort((a, b) => (a.status === "void") - (b.status === "void"));
  const graded = all.filter((p) => ["won", "lost", "push"].includes(p.status));
  const w = graded.filter((p) => p.status === "won").length, l = graded.filter((p) => p.status === "lost").length;
  const units_ = graded.reduce((s, p) => s + (p.profit ?? 0), 0);
  const clvs = all.filter((p) => p.clv != null);
  let h = `<div class="kpis" style="margin-top:18px">
    <div class="kpi"><div class="k">Record</div><div class="v">${w}-${l}</div><div class="s">${graded.length} graded</div></div>
    <div class="kpi"><div class="k">Units</div><div class="v ${units_ >= 0 ? "pos" : "neg"}">${units_ >= 0 ? "+" : ""}${units_.toFixed(2)}</div><div class="s">all picks</div></div>
    <div class="kpi"><div class="k">Avg CLV</div><div class="v">${clvs.length ? pct(clvs.reduce((s, p) => s + p.clv, 0) / clvs.length) : "–"}</div><div class="s">${clvs.length} with a close</div></div>
    <div class="kpi"><div class="k">Today</div><div class="v">${all.filter((p) => p.date === todayPT()).length}</div><div class="s">picks posted${all.some((p) => p.date === todayPT() && p.status === "void") ? ` · ${all.filter((p) => p.date === todayPT() && p.status === "void").length} withdrawn` : ""}</div></div></div>`;
  const inPlay = all.filter((p) => isOpen(p) && p.tier !== "likely" && p.gameId && gameState(p.gameId));
  if (inPlay.length) {
    const u = (x) => `${x >= 0 ? "+" : ""}${(x || 0).toFixed(2)}u`;
    h += `<div class="sec-title"><span class="neg">●</span>&nbsp;Our picks in play (${inPlay.length})<span class="line"></span></div>
      ${projectedCard({ items: inPlay.map(pickAsBet).filter(Boolean), title: "Live tracking: projected units on picks in play", fmt: u, units: true })}`;
  }
  h += `<div class="bar">${days.slice(0, 10).map((d) => `<button class="chip ${d === day ? "on" : ""}" data-day="${d}">${d === todayPT() ? "Today" : d > todayPT() ? `Tomorrow · early` : fmtShortDay(d + "T12:00:00")}</button>`).join("")}</div>`;
  // the day's mix: posted picks (bet / floor / lean) by league x kind; one cell over half the day is flagged, since the
  // pick runs cap a cell at half while anything else qualifies (60% for a cell the fast read says is working)
  const posted = shown.filter((p) => p.status !== "void" && ["bet", "floor", "lean"].includes(p.tier));
  if (posted.length >= 3) {
    const mix = new Map();
    posted.forEach((p) => { const k = `${p.league} ${p.player ? "props" : "lines"}`; mix.set(k, (mix.get(k) || 0) + 1); });
    const cells = [...mix.entries()].sort((a, b) => b[1] - a[1]), top = cells[0][1] / posted.length;
    h += `<div class="row" style="gap:6px;flex-wrap:wrap;margin:-4px 0 10px"><span class="tiny muted">Mix</span>${cells.map(([k, n]) => `<span class="pill ${n / posted.length > 0.5 ? "warn" : ""}">${esc(k)} ${n}</span>`).join("")}
      ${top > 0.5 ? `<span class="tiny muted">${Math.round(top * 100)}% in one place: the runs cap that at half unless nothing else qualifies</span>` : ""}</div>`;
  }
  h += shown.length ? `<div class="grid">${shown.map(pickCard).join("")}</div>`
    : `<div class="empty"><h3>No picks posted yet</h3>Claude's daily run posts picks here with the full case, the six pillars and play-to prices at each venue.</div>`;
  el.innerHTML = h;
  $$("[data-day]", el).forEach((b) => b.onclick = () => { day = b.dataset.day; renderPicks(el); });
  bindLog(el);
}
