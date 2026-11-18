// Edges > Make orders: limit orders worth more than the sharp fair if they fill, with a real chance to fill before the
// start (engine/makers.py -> rep.makers). Suggestions only for now: Vince posts them by hand; filled ones get logged.
import { S } from "../store.js";
import { esc, bookLogo, prob, fmtTime, oddsP, money } from "../util.js";

const CENTS = new Set(["kalshi", "polymarket_us"]);
const sideLabel = (o) => o.market === "totals" ? `${o.side} ${o.point}` : o.market === "spreads" ? `${o.side} ${o.point > 0 ? "+" : ""}${o.point}` : `${o.side} to win`;
const cap = (t) => t.charAt(0).toUpperCase() + t.slice(1);
const c = (p) => `${(p * 100).toFixed(1).replace(/\.0$/, "")}c`;

function card(o) {
  const cents = CENTS.has(o.venue);
  const spec = { label: `${sideLabel(o)} (maker order)`, gameId: o.gameId, league: o.league, matchup: o.matchup, commence: o.commence,
    market: o.market, side: o.side, point: o.point, team: o.team, venue: o.venue, am: o.am, fair: o.fair, source: "maker" };
  return `<div class="card mk-card">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><span class="row" style="gap:6px;align-items:center">${bookLogo(o.venue, o.venueName, 20)}<b>${esc(sideLabel(o))}</b></span>
      <span class="pill pos">+${(o.ev * 100).toFixed(1)}% if it fills</span></div>
    <div class="tiny muted">${esc(o.league || "")} · ${esc(o.matchup)} · ${fmtTime(o.commence)} (${o.hrs < 1 ? `${Math.round(o.hrs * 60)} min` : `${o.hrs.toFixed(1)} h`})</div>
    <div class="mk-grid">
      <div><div class="k">Post a bid at</div><div class="v">${cents ? `<b class="num">${c(o.bid)}</b> <span class="tiny muted">a share</span>` : `<b class="num">${c(o.bid)}</b> <span class="tiny muted">implied</span>`}</div>
        <div class="s">${oddsP(o.cost)}${o.fee ? ` <span class="tiny muted">with the maker fee</span>` : ""}</div></div>
      <div><div class="k">Sharp fair</div><div class="v">${oddsP(o.fair)}</div><div class="s">for ${esc(o.side)}</div></div>
      <div><div class="k">Fill chance</div><div class="v num">${Math.round(o.pFill * 100)}%</div><div class="s">est., before the start</div></div>
      <div><div class="k">Expected</div><div class="v num pos">+${money(o.exp100)}</div><div class="s">per $100 posted</div></div>
    </div>
    <div class="tiny" style="margin-top:6px">The best price there now on the other side, ${esc(o.behind.side)}, is ${c(o.behind.price)}${o.behind.avail != null ? ` with ${money(o.behind.avail)} waiting` : ""}. Your bid gives ${esc(o.behind.side)} takers a tick better, so you're first in line.${o.takeY != null ? ` Taking ${esc(o.side)} right now costs ${c(o.takeY)}.` : ""}</div>
    <div class="mk-plan small"><b>Plan:</b> ${esc(o.now || "post this side")}${o.pair ? ` <span class="tiny muted">(other side: ${c(o.pair.bid)} at ${esc(o.pair.venueName || o.pair.venue)}; both filled locks ${(o.pair.lock * 100).toFixed(1)}%)</span>` : ""}. ${esc(cap(o.ifFilled || ""))}.${o.other ? ` <span class="tiny muted">(${esc(o.other.side || o.behind.side)} costs ${c(o.other.cost)} at ${esc(o.other.venueName || o.other.venue)} now${o.other.ev != null ? `, ${o.other.ev >= 0 ? "+" : ""}${(o.other.ev * 100).toFixed(1)}% vs fair` : ""})</span>` : ""}</div>
    <div class="tiny neg" style="margin-top:4px">Cancel if the sharp fair for ${esc(o.side)} drops under ${prob(o.cancelBelow, 1)}, or if the line moves off ${o.point != null ? o.point : "this price"}.</div>
    <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn sm ghost" data-log='${esc(JSON.stringify(spec))}' title="log it once it fills (the part that filled)">Log a fill</button></div>
  </div>`;
}

export function makersHtml() {
  const M = S.reports?.makers;
  if (!M) return `<div class="card note">Builds on the next engine run.</div>`;
  const items = (M.items || []).filter((o) => new Date(o.commence) > new Date());
  const meth = (S.reports?.methods?.methods || []).find((m) => m.key === "maker");
  const P = M.paper || {}, L = meth?.live || {};
  const r = M.rules || {};
  return `<p class="note">A <b>maker order</b> is a limit order you leave resting: you post a price and wait for a taker. The rule: an arb is always locked; otherwise only a side that's +EV on its own is ever bet. So each order says whether to post one side or both, and what to do the moment it fills: take the other side if that locks a profit (or is +EV itself), else hold it. These are main lines on your exchanges, games within ${r.aheadH ?? 6} hours, at a price at least ${Math.round((r.minEdge ?? 0.02) * 100)}% better than the sharp fair for your side. Each bid is one tick ahead of the best price there, so the next taker on the other side hits you first. Fill chances come from how often resting orders like these were taken on each venue, Nov 12-17, by time to start; they are estimates until our own paper record says otherwise.</p>
    <div class="card" style="margin-bottom:10px"><b>Paper record</b> <span class="tiny muted">every suggestion followed as if posted</span>
      <div class="row small" style="gap:14px;flex-wrap:wrap;margin-top:6px">
        <span>Resting now <b class="num">${P.resting ?? 0}</b></span><span>Filled <b class="num">${(P.filled ?? 0) + (P.locked ?? 0) + (P.filledOpen ?? 0)}</b>${(P.locked ?? 0) + (P.lockedOpen ?? 0) ? ` <span class="tiny muted">(${(P.locked ?? 0) + (P.lockedOpen ?? 0)} locked as arbs)</span>` : ""}</span>
        <span>Cancelled <b class="num">${P.cancelled ?? 0}</b></span><span>Expired unfilled <b class="num">${P.expired ?? 0}</b></span>
        <span>Fill rate <b class="num">${P.fillRate != null ? Math.round(P.fillRate * 100) + "%" : "–"}</b>${P.predicted != null ? ` <span class="tiny muted">(we estimated ${Math.round(P.predicted * 100)}%)</span>` : ""}</span>
        <span>Filled and graded <b class="num">${L.n ?? 0}</b>${L.n ? `, ROI <b class="num ${L.roi >= 0 ? "pos" : "neg"}">${(L.roi * 100).toFixed(1)}%</b>${L.clv != null ? `, CLV ${(L.clv * 100).toFixed(1)}%` : ""}` : ""}</span></div>
      <div class="tiny muted" style="margin-top:4px">Counted filled when the level behind ours gets eaten or disappears (the pessimistic read); cancelled when the fair moves our edge under ${((r.cancelEdge ?? 0.005) * 100).toFixed(1)}%. Also on Reports > Methods.</div></div>
    ${items.length ? `<div class="grid">${items.map(card).join("")}</div>` : `<div class="card note">None right now: nothing within ${r.aheadH ?? 6} hours clears ${Math.round((r.minEdge ?? 0.02) * 100)}% on a near-even line.</div>`}
    <p class="tiny muted" style="margin-top:8px">Maker fees assumed: Kalshi 0.0175 x p x (1-p) a contract; Polymarket US, Novig and ProphetX none. Check each venue's fee page before relying on it.</p>`;
}
