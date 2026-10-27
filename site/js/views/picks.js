import { S } from "../store.js";
import { esc, am, pct, prob, fmtTime, fmtShortDay, units, todayPT, bookTag, $$ } from "../util.js";
import { betImage, bindLog } from "./common.js";

const PILLARS = [["model", "Model"], ["advanced", "Adv stats"], ["matchup", "Matchup"], ["role", "Role"], ["context", "Context"], ["market", "Market"]];
const ICON = { support: "▲", neutral: "•", against: "▼" };
let day = null;

export function pickCard(p) {
  const st = p.status || "pending";
  const stPill = st === "won" ? `<span class="pill pos">won</span>` : st === "lost" ? `<span class="pill neg">lost</span>` : `<span class="pill">${esc(st)}</span>`;
  const tier = p.tier === "floor" ? `<span class="pill">floor</span>` : p.tier === "likely" ? `<span class="pill">likely</span>` : `<span class="pill s">bet</span>`;
  const pillars = PILLARS.map(([k, t]) => {
    const v = p.pillars?.[k]; if (!v) return "";
    return `<span class="pillar ${v === "support" ? "sup" : v === "against" ? "ag" : ""}">${ICON[v] || ""} ${t}</span>`;
  }).join("");
  const venues = (p.playTo || []).map((v) => `<div class="lrow" style="grid-template-columns:1fr auto auto">
      <span>${bookTag(v.venue, v.name || v.venue, 15)}${v.line ? ` <span class="tiny muted">${esc(v.line)}</span>` : ""}</span>
      <span class="px">${esc(typeof v.price === "number" ? am(v.price) : v.price || "–")}</span>
      <span class="${v.inRange ? "pos" : "faint"}">${v.inRange ? "✓ in range" : "wait"}</span></div>`).join("");
  return `<div class="card col" style="gap:10px">
    <div class="bet">${betImage({ gameId: p.gameId, label: p.selection, player: p.player, headshot: p.headshot, league: p.league })}
      <div class="col"><span class="what">${esc(p.selection)}</span><span class="sub">${esc(p.matchup || "")}${p.commence ? ` · ${fmtTime(p.commence)}` : ""}</span></div>
      <div class="col" style="align-items:flex-end">${units(p.units)}<span class="tiny muted">${p.units ?? "–"}u</span></div></div>
    <div class="row" style="gap:6px">${tier}${stPill}${p.clv != null ? `<span class="pill ${p.clv > 0 ? "pos" : "neg"}">CLV ${pct(p.clv)}</span>` : ""}</div>
    <div class="playto">
      <div class="fc"><div class="k">Fair</div><div class="v">${am(p.fairAm)}</div><div class="s">${prob(p.fair, 1)}</div></div>
      <div class="fc"><div class="k">Good at</div><div class="v">${am(p.goodAt)}</div><div class="s">or better</div></div>
      <div class="fc"><div class="k">Great at</div><div class="v">${am(p.greatAt)}</div><div class="s">bet bigger</div></div>
    </div>
    ${p.case ? `<div>${esc(p.case)}</div>` : ""}
    ${pillars ? `<div class="pillars">${pillars}</div>` : ""}
    ${venues ? `<div><div class="tiny muted" style="margin-bottom:4px">Play-to by venue</div><div class="ladder">${venues}</div></div>` : ""}
    ${p.risk ? `<div class="note"><b>Risk:</b> ${esc(p.risk)}</div>` : ""}
    ${(p.confirm || []).length ? `<div class="note"><b>Confirm before start:</b> ${p.confirm.map(esc).join(" · ")}</div>` : ""}
    <div><button class="btn sm ghost" data-log='${esc(JSON.stringify({ label: p.selection, gameId: p.gameId, league: p.league, matchup: p.matchup, commence: p.commence, am: p.playTo?.find((v) => v.inRange)?.price ?? p.goodAt, venue: p.playTo?.find((v) => v.inRange)?.venue, fair: p.fair, source: "pick" }))}'>Log this bet</button></div>
  </div>`;
}

export function renderPicks(el) {
  const all = [...S.picks.values()].sort((a, b) => (b.date || "").localeCompare(a.date || "") || (a.commence || "").localeCompare(b.commence || ""));
  const days = [...new Set(all.map((p) => p.date))].filter(Boolean);
  if (!day) day = days.includes(todayPT()) ? todayPT() : days[0];
  const shown = all.filter((p) => p.date === day);
  const graded = all.filter((p) => ["won", "lost", "push"].includes(p.status));
  const w = graded.filter((p) => p.status === "won").length, l = graded.filter((p) => p.status === "lost").length;
  const units_ = graded.reduce((s, p) => s + (p.profit ?? 0), 0);
  const clvs = all.filter((p) => p.clv != null);
  let h = `<div class="kpis" style="margin-top:18px">
    <div class="kpi"><div class="k">Record</div><div class="v">${w}-${l}</div><div class="s">${graded.length} graded</div></div>
    <div class="kpi"><div class="k">Units</div><div class="v ${units_ >= 0 ? "pos" : "neg"}">${units_ >= 0 ? "+" : ""}${units_.toFixed(2)}</div><div class="s">all picks</div></div>
    <div class="kpi"><div class="k">Avg CLV</div><div class="v">${clvs.length ? pct(clvs.reduce((s, p) => s + p.clv, 0) / clvs.length) : "–"}</div><div class="s">${clvs.length} with a close</div></div>
    <div class="kpi"><div class="k">Today</div><div class="v">${all.filter((p) => p.date === todayPT()).length}</div><div class="s">picks posted</div></div></div>`;
  h += `<div class="bar">${days.slice(0, 10).map((d) => `<button class="chip ${d === day ? "on" : ""}" data-day="${d}">${d === todayPT() ? "Today" : fmtShortDay(d + "T12:00:00")}</button>`).join("")}</div>`;
  h += shown.length ? `<div class="grid">${shown.map(pickCard).join("")}</div>`
    : `<div class="empty"><h3>No picks posted yet</h3>Claude's daily run posts picks here with the full case, the six pillars and play-to prices at each venue.</div>`;
  el.innerHTML = h;
  $$("[data-day]", el).forEach((b) => b.onclick = () => { day = b.dataset.day; renderPicks(el); });
  bindLog(el);
}
