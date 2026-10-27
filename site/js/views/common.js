import { S, saveBet } from "../store.js";
import { esc, am, pct, prob, fmtTime, teamLogo, headshot, edgeBar, bookPill, sheet, toast, uid, decFromAm, amFromProb, todayPT, both, bothP, spark, hitRate, bookTag, bookLogo, $ } from "../util.js";
import { VENUES } from "../lab.js";

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

export function edgeCard(e) {
  const g = S.games.get(e.gameId);
  const href = `#/game/${e.gameId}/${/^(player|batter|pitcher)_/.test(e.market || "") ? "props" : "lines"}`;
  return `<div class="card">
    <div class="bet">
      ${betImage(e)}
      <div class="col"><a class="what" href="${href}" style="text-decoration:none">${esc(e.label)}</a>
        <span class="sub ellip">${esc(e.matchup)} · ${fmtTime(e.commence)}</span></div>
      <div class="price"><div class="big">${e.pickem ? "pick'em" : am(e.am)}</div><div class="tiny muted">${e.pickem ? `needs ${prob(e.be ?? 0.5774, 1)}` : prob(1 / decFromAm(e.am), 1)}</div><div class="tiny muted" style="margin-top:3px">${bookTag(e.book, e.bookName, 14)}</div></div>
    </div>
    <div class="bet-foot">
      <div class="col" style="gap:3px">${edgeBar(e.edge)}
        <span class="tiny muted">fair ${bothP(e.fair)} · edge <span class="pos">${pct(e.edge)}</span>${e.ev != null ? ` · EV ${pct(e.ev)}` : ""}${e.model ? ` · <span class="pill" title="fair moved from the main line with the stat model">alt line</span>` : ""}${e.est ? ` · <span class="pill" title="nobody quotes the other side, so the fair is estimated (first-scorer markets de-vig across the whole field; others strip each book's measured margin)">est. fair</span>` : ""}</span>
        ${e.ctx?.length ? `<span class="tiny muted ellip" title="${esc(e.ctx.join(" · "))}">ⓘ ${esc(e.ctx[0])}${e.ctx.length > 1 ? ` +${e.ctx.length - 1}` : ""}</span>` : ""}
        ${e.last10?.length ? `<span class="l10">${spark(e.last10, e.point, { side: (e.label.match(/ (Over|Under) /) || [])[1] })}<span>last ${e.last10.length}: ${hitRate(e.last10, e.point, (e.label.match(/ (Over|Under) /) || [])[1])}</span></span>` : ""}
        ${e.logic ? `<span class="pill ${e.logic === "agrees" ? "logic-a" : e.logic === "disagrees" ? "logic-d" : ""}" title="Our sports model gives this ${prob(e.modelP, 1)}, a fair price of ${am(amFromProb(e.modelP))}${e.ctx?.length ? ". Context in the model: " + esc(e.ctx.join(" · ")) : ""}">${e.logic === "agrees" ? "▲ logic agrees" : e.logic === "disagrees" ? "▼ logic disagrees" : "• logic neutral"} · model ${bothP(e.modelP)}</span>` : ""}</div>
      <button class="btn sm ghost" data-log='${esc(JSON.stringify({ label: e.label, gameId: e.gameId, league: e.league, matchup: e.matchup, commence: e.commence, market: e.market, am: e.am, venue: e.book, venueName: e.bookName, fair: e.fair }))}'>Log</button>
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
export function logSheet(pre = {}) {
  const venues = VENUES.filter((v) => v.key !== "any");
  sheet(`<h3>Log a bet</h3>
    <div class="col" style="gap:10px">
      <label class="f">Bet<input id="lb-label" value="${esc(pre.label || "")}" placeholder="e.g. Knicks -3.5"></label>
      <div class="fgrid">
        <label class="f">Venue<select id="lb-venue">${venues.map((v) => `<option value="${v.key}" ${v.key === pre.venue ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>
        <label class="f">Odds (American)<input id="lb-odds" inputmode="numeric" value="${pre.am ?? ""}" placeholder="-110"></label>
        <label class="f">Stake $<input id="lb-stake" inputmode="decimal" value="${pre.stake ?? ""}" placeholder="10"></label>
        <label class="f">Promo<select id="lb-promo"><option value="">None</option>${[...S.promos.values()].map((p) => `<option value="${p.id}" ${p.id === pre.promoId ? "selected" : ""}>${esc(p.title || p.venueName || "Promo")}</option>`).join("")}</select></label>
      </div>
      <p class="note">Our fair at logging: ${pre.fair != null ? prob(pre.fair, 1) : "none"}. Closing line value fills in automatically once grading runs.</p>
      <div class="row"><button class="btn" id="lb-save">Save bet</button><button class="btn ghost" id="lb-cancel">Cancel</button></div>
    </div>`, (el, close) => {
    $("#lb-cancel", el).onclick = close;
    $("#lb-save", el).onclick = async () => {
      const odds = parseFloat($("#lb-odds", el).value), stake = parseFloat($("#lb-stake", el).value);
      if (!$("#lb-label", el).value.trim() || isNaN(odds) || isNaN(stake)) { toast("Bet, odds and stake are required"); return; }
      const v = venues.find((x) => x.key === $("#lb-venue", el).value);
      const bet = { id: uid(), createdAt: new Date().toISOString(), date: todayPT(), label: $("#lb-label", el).value.trim(),
        venue: v.key, venueName: v.name, am: odds, dec: decFromAm(odds), stake, status: "pending",
        promoId: $("#lb-promo", el).value || null, gameId: pre.gameId || null, league: pre.league || null,
        matchup: pre.matchup || null, commence: pre.commence || null, market: pre.market || null,
        fairAtLog: pre.fair ?? null, legs: pre.legs || null, source: pre.source || "manual" };
      try { await saveBet(bet); toast("Bet logged"); close(); } catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

export function bindLog(root) {
  root.querySelectorAll("[data-log]").forEach((b) => b.onclick = (ev) => { ev.preventDefault(); logSheet(JSON.parse(b.dataset.log)); });
}
