import { S } from "../store.js";
import { esc, am, pct, prob, fmtTime, fmtShortDay, units, todayPT, bookTag, $$ } from "../util.js";
import { betImage, bindLog } from "./common.js";

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

export function pickCard(p) {
  const st = p.status || "pending";
  const stPill = st === "won" ? `<span class="pill pos">won</span>` : st === "lost" ? `<span class="pill neg">lost</span>` : st === "void" ? `<span class="pill neg">withdrawn</span>` : `<span class="pill">${esc(st)}</span>`;
  const tier = (p.tier === "floor" ? `<span class="pill">floor</span>` : p.tier === "likely" ? `<span class="pill">likely</span>` : `<span class="pill s">bet</span>`)
    + (p.early ? ` <span class="pill v" title="posted the night before at the openers${p.earlyAt ? ` (${fmtTime(p.earlyAt)})` : ""}; the morning run re-checks it">early</span>` : "");
  const pillars = PILLARS.map(([k, t]) => {
    const v = p.pillars?.[k]; if (!v) return "";
    return `<span class="pillar ${v === "support" ? "sup" : v === "against" ? "ag" : ""}">${ICON[v] || ""} ${t}</span>`;
  }).join("");
  const venues = (p.playTo || []).map((v) => `<div class="lrow" style="grid-template-columns:1fr auto auto">
      <span>${bookTag(v.venue, v.name || v.venue, 15)}${v.line ? ` <span class="tiny muted">${esc(v.line)}</span>` : ""}</span>
      <span class="px">${esc(typeof v.price === "number" ? am(v.price) : v.price || "–")}</span>
      <span class="${v.inRange ? "pos" : "faint"}">${v.inRange ? "✓ in range" : "wait"}</span></div>`).join("");
  return `<div class="card col ${st === "void" ? "pk-void" : ""}" style="gap:10px">
    ${st === "void" && p.voidReason ? `<div class="pk-vbar">Withdrawn: ${esc(p.voidReason)}</div>` : ""}
    <div class="bet">${betImage({ gameId: p.gameId, label: p.selection, player: p.player, headshot: p.headshot, league: p.league })}
      <div class="col"><span class="what">${esc(p.selection)}</span><span class="sub">${esc(p.matchup || "")}${p.commence ? ` · ${fmtTime(p.commence)}` : ""}</span></div>
      <div class="col" style="align-items:flex-end">${units(p.units)}<span class="tiny muted">${p.units ?? "–"}u${S.settings?.unit && p.units ? ` · $${Math.round(p.units * S.settings.unit)}` : ""}</span></div></div>
    <div class="row" style="gap:6px">${tier}${stPill}${p.clv != null ? `<span class="pill ${p.clv > 0 ? "pos" : "neg"}">CLV ${pct(p.clv)}</span>` : ""}</div>
    <div class="playto">
      <div class="fc"><div class="k">Fair</div><div class="v">${am(p.fairAm)}</div><div class="s">${prob(p.fair, 1)}</div></div>
      <div class="fc"><div class="k">Good at</div><div class="v">${am(p.goodAt)}</div><div class="s">or better</div></div>
      <div class="fc"><div class="k">Great at</div><div class="v">${am(p.greatAt)}</div><div class="s">bet bigger</div></div>
    </div>
    ${meter(p)}
    ${pillars ? `<div class="pillars">${pillars}</div>` : ""}
    ${(p.keyStats || []).length ? `<div class="kstats">${p.keyStats.map((k) => `<div class="kstat ${k.tone || ""}"><b>${esc(k.value)}</b><span>${esc(k.label)}</span>${k.note ? `<i>${esc(k.note)}</i>` : ""}</div>`).join("")}</div>` : ""}
    ${p.case ? `<div class="pcase"><div class="pc-h pos">▲ Why it's a good pick</div>${paras(p.case)}</div>` : ""}
    ${p.risk ? `<div class="pcase risk"><div class="pc-h neg">▼ What could go wrong</div>${paras(p.risk)}</div>` : ""}
    ${venues ? `<div><div class="tiny muted" style="margin-bottom:4px">Play-to by venue</div><div class="ladder">${venues}</div></div>` : ""}
    ${(p.confirm || []).length ? `<div class="note"><b>Confirm before start:</b> ${p.confirm.map(esc).join(" · ")}</div>` : ""}
    <div><button class="btn sm ghost" data-log='${esc(JSON.stringify({ label: p.selection, gameId: p.gameId, league: p.league, matchup: p.matchup, commence: p.commence, market: p.market, side: p.side, point: p.point, player: p.player, team: p.team, pickId: p.id, am: p.playTo?.find((v) => v.inRange)?.price ?? p.goodAt, venue: p.playTo?.find((v) => v.inRange)?.venue, fair: p.fair, source: "pick" }))}'>Log this bet</button></div>
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
  h += `<div class="bar">${days.slice(0, 10).map((d) => `<button class="chip ${d === day ? "on" : ""}" data-day="${d}">${d === todayPT() ? "Today" : d > todayPT() ? `Tomorrow · early` : fmtShortDay(d + "T12:00:00")}</button>`).join("")}</div>`;
  h += shown.length ? `<div class="grid">${shown.map(pickCard).join("")}</div>`
    : `<div class="empty"><h3>No picks posted yet</h3>Claude's daily run posts picks here with the full case, the six pillars and play-to prices at each venue.</div>`;
  el.innerHTML = h;
  $$("[data-day]", el).forEach((b) => b.onclick = () => { day = b.dataset.day; renderPicks(el); });
  bindLog(el);
}
