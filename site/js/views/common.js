import { S, saveBet } from "../store.js";
import { esc, am, pct, prob, fmtTime, teamLogo, headshot, edgeBar, bookPill, sheet, toast, uid, decFromAm, amFromProb, todayPT, both, bothP, spark, hitRate, bookTag, bookLogo, kellyStake, $ } from "../util.js";
import { VENUES } from "../lab.js";
import { settleProfit, isBoost } from "../promo.js";
import { money } from "../util.js";

// image for a bet: player headshot for props, team logo for team sides, league logo otherwise
export function betImage(item, size = 42) {
  if (item.headshot || item.player) return headshot({ player: item.player || item.label, headshot: item.headshot }, size);
  const g = S.games.get(item.gameId);
  if (g && item.label) {
    if (item.label.startsWith(g.home)) return teamLogo(g.homeRef, g.home, size);
    if (item.label.startsWith(g.away)) return teamLogo(g.awayRef, g.away, size);
  }
  return g?.leagueLogo ? teamLogo({ logo: g.leagueLogo, abbr: item.league }, item.league, size) : teamLogo(null, item.league, size);
}

// how the market moved on this side since we first saw the line (fair, and the point if it changed)
function moveTag(e) {
  const d = e.fair - e.open, pt = e.openPt != null && e.point != null && Math.abs(e.openPt - e.point) > 1e-6 ? ` (opened ${e.openPt > 0 && /spreads/.test(e.market) ? "+" : ""}${e.openPt})` : "";
  if (pt) return `<span class="pill" title="first seen ${esc(e.openAt || "")}">line moved${pt}</span>`;
  if (Math.abs(d) < 0.01) return `<span class="pill" title="first seen ${esc(e.openAt || "")}">no move</span>`;
  return `<span class="pill ${d > 0 ? "pos" : "neg"}" title="first seen ${esc(e.openAt || "")}: ${prob(e.open)} then, ${prob(e.fair)} now. Moving toward your side means the market agrees.">${d > 0 ? "▲" : "▼"} ${Math.abs(d * 100).toFixed(1)} pts since open</span>`;
}

export function edgeCard(e) {
  const g = S.games.get(e.gameId);
  const href = `#/game/${e.gameId}/${/^(player|batter|pitcher)_/.test(e.market || "") ? "props" : "lines"}`;
  return `<div class="card">
    <div class="bet">
      ${betImage(e)}
      <div class="col"><a class="what" href="${e.live ? `#/game/${e.gameId}/live` : href}" style="text-decoration:none">${e.live ? `<span class="pill live">● LIVE</span> ` : ""}${esc(e.label)}</a>
        <span class="sub ellip">${esc(e.matchup)} · ${fmtTime(e.commence)}</span></div>
      <div class="price"><div class="big">${e.pickem ? "pick'em" : am(e.am)}</div><div class="tiny muted">${e.pickem ? (e.flex && e.minMult ? `needs ≥ ${e.minMult.toFixed(2)}x` : `needs ${prob(e.be ?? 0.5774, 1)}`) : prob(1 / decFromAm(e.am), 1)}</div><div class="tiny muted" style="margin-top:3px">${bookTag(e.book, e.bookName, 14)}</div></div>
    </div>
    <div class="bet-foot">
      <div class="col" style="gap:3px">${edgeBar(e.edge)}
        ${e.track || e.open != null ? `<span class="tiny row" style="gap:6px;flex-wrap:wrap">${e.track ? `<span class="pill ${e.track.clv > 0 ? "pos" : e.track.clv < 0 ? "neg" : ""}" title="how ${esc(e.track.cat)} edges have done at the close: ${Math.round(e.track.beat * 100)}% beat it">track ${pct(e.track.clv)} CLV · ${e.track.n}</span>` : ""}${e.open != null ? moveTag(e) : ""}</span>` : ""}
        ${(() => { const k0 = !e.pickem && e.am != null ? kellyStake(e.fair, decFromAm(e.am), S.settings) : null, k = k0 && (e.model || e.est) ? Math.round(k0 / 2) : k0; /* model or estimated fairs: half size */ return k ? `<span class="tiny"><span class="pill s" title="${Math.round((S.settings.kelly ?? 0.25) * 100)}% Kelly at the fair, capped at ${Math.round((S.settings.maxPct ?? 0.05) * 100)}% of the bankroll (set in Ledger > Analysis)">stake $${k}</span></span>` : ""; })()}
        <span class="tiny muted">fair ${bothP(e.fair)} · edge <span class="pos">${pct(e.edge)}</span>${e.ev != null ? ` · EV ${pct(e.ev)}` : ""}${e.model ? ` · <span class="pill" title="fair moved from the main line with the stat model">alt line</span>` : ""}${e.est ? ` · <span class="pill" title="nobody quotes the other side, so the fair is estimated (first-scorer markets de-vig across the whole field; others strip each book's measured margin)">est. fair</span>` : ""}</span>
        ${e.ctx?.length ? `<span class="tiny muted ellip" title="${esc(e.ctx.join(" · "))}">ⓘ ${esc(e.ctx[0])}${e.ctx.length > 1 ? ` +${e.ctx.length - 1}` : ""}</span>` : ""}
        ${e.last10?.length ? `<span class="l10">${spark(e.last10, e.point, { side: (e.label.match(/ (Over|Under) /) || [])[1] })}<span>last ${e.last10.length}: ${hitRate(e.last10, e.point, (e.label.match(/ (Over|Under) /) || [])[1])}</span></span>` : ""}
        ${e.logic ? `<span class="pill ${e.logic === "agrees" ? "logic-a" : e.logic === "disagrees" ? "logic-d" : ""}" title="Our sports model gives this ${prob(e.modelP, 1)}, a fair price of ${am(amFromProb(e.modelP))}${e.ctx?.length ? ". Context in the model: " + esc(e.ctx.join(" · ")) : ""}">${e.logic === "agrees" ? "▲ logic agrees" : e.logic === "disagrees" ? "▼ logic disagrees" : "• logic neutral"} · model ${bothP(e.modelP)}</span>` : ""}</div>
      <button class="btn sm ghost" data-log='${esc(JSON.stringify({ label: e.label, gameId: e.gameId, league: e.league, matchup: e.matchup, commence: e.commence, market: e.market, side: e.side, point: e.point, player: e.player || null, team: e.team || null, am: e.am, venue: e.book, venueName: e.bookName, fair: e.fair, source: e.live ? "live" : "edge", live: !!e.live }))}'>Log</button>
    </div>
  </div>`;
}

// price ladder for one side of one line: fair on top, then every book sorted by edge
export function ladder(line, side, limit = 8, showAll = false) {
  if (!line) return "";
  const rows = line.quotes.filter((q) => q.prices[side]).map((q) => ({ q, p: q.prices[side] }))
    .sort((a, b) => (b.p.edge ?? -9) - (a.p.edge ?? -9));
  const shown = showAll ? rows : rows.filter((r) => r.q.venue || r.q.kind === "sharp" || r.q.kind === "exchange").slice(0, limit);
  const max = Math.max(0.03, ...rows.map((r) => Math.abs(r.p.edge ?? 0)).slice(0, 6));
  let h = `<div class="ladder">`;
  if (line.fair) h += `<div class="lrow fair"><span>Sharp fair</span><span class="px">${am(line.fairAm[side])}</span><span class="tiny muted">${prob(line.fair[side], 1)} chance</span><span></span></div>`;
  for (const { q, p } of shown) {
    const e = p.edge;
    h += `<div class="lrow ${q.stale ? "stale" : ""}" data-tip="${esc(q.name)}: ${am(p.allInAm ?? p.am)}${p.allInAm ? " all-in (fee)" : ""} · edge ${pct(e)}${q.stale ? " · stale" : ""}">
      <span class="row ellip" style="gap:6px">${bookTag(q.book, q.name, 16)} ${bookPill(q)}</span>
      <span class="px">${am(p.allInAm ?? p.am)}</span>${edgeBar(e, max)}<span class="ed ${e > 0.005 ? "pos" : e < -0.04 ? "neg" : ""}">${pct(e)}</span></div>`;
  }
  return h + `</div>`;
}

// log-a-bet sheet (from edges, lab, picks or promos)
// log a new bet, or edit one already logged (pre.id): odds, stake, venue, promo, and whether the odds typed in already
// include the boost. Editing a settled bet re-figures its profit.
export function logSheet(pre = {}) {
  const venues = VENUES.filter((v) => v.key !== "any"), edit = !!pre.id;
  const settled = ["won", "lost", "push", "void"].includes(pre.status);
  // a bet on a game already in play is a live bet: keep the score and clock when it was placed
  const gid = pre.gameId || pre.legs?.[0]?.gameId, g = gid && S.games.get(gid), lv = gid && S.liveGames.get(gid);
  const st = lv?.state || (S.live?.games || []).find((x) => x.id === gid)?.state;
  const isLive = edit ? !!pre.live : !!(pre.live || lv || (g && new Date(g.commence) < new Date() && Date.now() - new Date(g.commence) < 5 * 3600000));
  const liveSt = edit ? (pre.liveState || null) : st && st.home?.score != null ? { away: st.away.score, home: st.home.score, detail: st.detail || "", text: `${st.away.score}–${st.home.score} ${st.detail || ""}`.trim() } : null;
  sheet(`<h3>${edit ? "Check this bet" : "Log a bet"}</h3>
    <div class="col" style="gap:10px">
      <label class="f">Bet<input id="lb-label" value="${esc(pre.label || "")}" placeholder="e.g. Knicks -3.5"></label>
      <div class="fgrid">
        <label class="f">Venue<select id="lb-venue">${venues.map((v) => `<option value="${v.key}" ${v.key === pre.venue ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
        <label class="f">Odds (American)<input id="lb-odds" inputmode="numeric" value="${pre.am ?? ""}" placeholder="-110"></label>
        <label class="f">Stake $<input id="lb-stake" inputmode="decimal" value="${pre.stake ?? (!edit && pre.fair != null && pre.am != null ? kellyStake(pre.fair, decFromAm(pre.am), S.settings) || "" : "")}" placeholder="10"></label>
        <label class="f">Promo<select id="lb-promo"><option value="">None</option>${[...S.promos.values()].map((p) => `<option value="${p.id}" ${p.id === pre.promoId ? "selected" : ""}>${esc(p.title || p.venueName || "Promo")}</option>`).join("")}</select></label>
        ${settled ? `<label class="f">Result<select id="lb-status">${["won", "lost", "push", "void"].map((s) => `<option ${s === pre.status ? "selected" : ""}>${s}</option>`).join("")}</select></label>` : ""}
      </div>
      <label class="row lb-boost" style="gap:8px;align-items:center"><input type="checkbox" id="lb-live" ${isLive ? "checked" : ""}>
        <span class="small">Live bet (placed during the game)${liveSt ? ` <span class="tiny muted">· ${esc(liveSt.text)}</span>` : ""}</span></label>
      <label class="row lb-boost lb-bst" style="gap:8px;align-items:flex-start" hidden><input type="checkbox" id="lb-boostin" ${pre.boostIn ? "checked" : ""} style="margin-top:3px">
        <span class="small">The odds above already include the boost (what the app showed after boosting). Leave unchecked if you typed the normal price.</span></label>
      <div class="lb-pay card" style="padding:10px 12px"></div>
      <p class="note">Our fair at logging: ${(pre.fairAtLog ?? pre.fair) != null ? prob(pre.fairAtLog ?? pre.fair, 1) : "none"}. Closing line value fills in automatically once grading runs.</p>
      <div class="row"><button class="btn" id="lb-save">${edit ? "Save changes" : "Save bet"}</button><button class="btn ghost" id="lb-cancel">Cancel</button></div>
    </div>`, (el, close) => {
    $("#lb-cancel", el).onclick = close;
    const read = () => {
      const odds = parseFloat($("#lb-odds", el).value), stake = parseFloat($("#lb-stake", el).value), promo = S.promos.get($("#lb-promo", el).value) || null;
      return { odds, stake, promo, boostIn: !!$("#lb-boostin", el).checked };
    };
    const preview = () => {          // what it pays, so a double-counted boost is easy to spot
      const { odds, stake, promo, boostIn } = read(), boost = isBoost(promo);
      $(".lb-bst", el).hidden = !boost;
      if (isNaN(odds) || isNaN(stake)) { $(".lb-pay", el).innerHTML = `<span class="tiny muted">Enter odds and stake to see the payout.</span>`; return; }
      const b = { stake, dec: decFromAm(odds), boostIn };
      const base = stake * (b.dec - 1), w = settleProfit(b, promo, "won"), l = settleProfit(b, promo, "lost");
      $(".lb-pay", el).innerHTML = `<div class="row" style="justify-content:space-between;gap:10px;flex-wrap:wrap">
        <span class="small">If it wins: <b class="pos num">${money(w)}</b>${boost && w !== base ? ` <span class="tiny muted">(${money(base)} at ${am(odds)} + ${money(w - base)} boost)</span>` : boost ? ` <span class="tiny muted">(boost already in the odds)</span>` : ""}</span>
        <span class="small">If it loses: <b class="neg num">${money(l)}</b></span></div>`;
    };
    ["lb-odds", "lb-stake"].forEach((id) => $("#" + id, el).oninput = preview);
    ["lb-promo", "lb-boostin"].forEach((id) => $("#" + id, el).onchange = preview);
    preview();
    $("#lb-save", el).onclick = async () => {
      const { odds, stake, boostIn } = read();
      if (!$("#lb-label", el).value.trim() || isNaN(odds) || isNaN(stake)) { toast("Bet, odds and stake are required"); return; }
      const v = venues.find((x) => x.key === $("#lb-venue", el).value);
      const promoId = $("#lb-promo", el).value || null;
      const common = { label: $("#lb-label", el).value.trim(), venue: v.key, venueName: v.name, am: odds, dec: decFromAm(odds), stake, promoId,
        boostIn: isBoost(S.promos.get(promoId)) ? boostIn : null, live: !!$("#lb-live", el).checked,
        liveState: $("#lb-live", el).checked ? liveSt : null };
      let bet;
      if (edit) {
        bet = { ...pre, ...common, checkedAt: new Date().toISOString() };
        const st = $("#lb-status", el)?.value || pre.status;
        if (settled) { bet.status = st; bet.profit = settleProfit(bet, promoId ? S.promos.get(promoId) : null, st); }
      } else {
        bet = { id: uid(), createdAt: new Date().toISOString(), date: todayPT(), ...common, status: "pending",
          gameId: pre.gameId || null, league: pre.league || null,
          matchup: pre.matchup || null, commence: pre.commence || null, market: pre.market || null,
          side: pre.side ?? null, point: pre.point ?? null, player: pre.player || null, team: pre.team || null,
          fairAtLog: pre.fair ?? null, legs: pre.legs || null, source: pre.source || "manual", pickId: pre.pickId || null };
      }
      try { await saveBet(bet); toast(edit ? "Bet updated" : "Bet logged"); close(); } catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

export function bindLog(root) {
  root.querySelectorAll("[data-log]").forEach((b) => b.onclick = (ev) => { ev.preventDefault(); logSheet(JSON.parse(b.dataset.log)); });
}
