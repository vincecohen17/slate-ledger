// Edges > Make orders: limit orders worth more than the sharp fair if they fill, with a real chance to fill before the
// start (engine/makers.py -> rep.makers). Suggestions only for now: Vince posts them by hand; filled ones get logged.
import { S, saveSettings } from "../store.js";
import { esc, bookLogo, prob, fmtTime, oddsP, money, toast, $$ } from "../util.js";

const MF = { kind: "All", lead: "All", league: "All", comp: true };
const mkMin = () => S.settings?.makerMin ?? 0.10;
const leadOf = (h) => (h < 6 ? "Within 6 h" : h < 24 ? "6-24 h" : "A day+");
const kindName = { main: "Main lines", alt: "Alts & periods", prop: "Props" };

const CENTS = new Set(["kalshi", "polymarket_us"]);
const sideLabel = (o) => o.label ? o.label : o.market === "totals" ? `${o.side} ${o.point}` : o.market === "spreads" ? `${o.side} ${o.point > 0 ? "+" : ""}${o.point}` : `${o.side} to win`;
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
    <div class="tiny" style="margin-top:6px">${o.competitive ? `<span class="pill pos" title="your order sells ${esc(o.behind.side)} at ${c(1 - o.bid)}; every sportsbook on the board charges at least that">No sportsbook beats it for the taker</span>` : o.elsewhere ? `<span class="pill" title="takers who shop will go there instead">${esc(o.elsewhere.name)} sells ${esc(o.behind.side)} at ${c(o.elsewhere.cost)}: only takers who don't shop fill this</span>` : ""}${o.spread != null ? ` <span class="tiny muted">make-take gap here ${c(o.spread)}</span>` : ""}</div>
    <div class="tiny" style="margin-top:6px">The best price there now on the other side, ${esc(o.behind.side)}, is ${c(o.behind.price)}${o.behind.avail != null ? ` with ${money(o.behind.avail)} waiting` : ""}. Your bid gives ${esc(o.behind.side)} takers a tick better, so you're first in line.${o.takeY != null ? ` Taking ${esc(o.side)} right now costs ${c(o.takeY)}.` : ""}</div>
    <div class="mk-plan small"><b>Plan:</b> ${esc(o.now || "post this side")}${o.pair ? ` <span class="tiny muted">(other side: ${c(o.pair.bid)} at ${esc(o.pair.venueName || o.pair.venue)}; both filled locks ${(o.pair.lock * 100).toFixed(1)}%)</span>` : ""}. ${esc(cap(o.ifFilled || ""))}.${o.other ? ` <span class="tiny muted">(${esc(o.other.side || o.behind.side)} costs ${c(o.other.cost)} at ${esc(o.other.venueName || o.other.venue)} now${o.other.ev != null ? `, ${o.other.ev >= 0 ? "+" : ""}${(o.other.ev * 100).toFixed(1)}% vs fair` : ""})</span>` : ""}</div>
    <div class="tiny neg" style="margin-top:4px">Cancel if the sharp fair for ${esc(o.side)} drops under ${prob(o.cancelBelow, 1)}, or if the line moves off ${o.point != null ? o.point : "this price"}.</div>
    <div class="row" style="justify-content:flex-end;margin-top:8px"><button class="btn sm ghost" data-log='${esc(JSON.stringify(spec))}' title="log it once it fills (the part that filled)">Log a fill</button></div>
  </div>`;
}

export function makersHtml() {
  const M = S.reports?.makers;
  if (!M) return `<div class="card note">Builds on the next engine run.</div>`;
  const live = (M.items || []).filter((o) => new Date(o.commence) > new Date());
  const items = live.filter((o) => o.ev >= mkMin() - 1e-9 && (!MF.comp || o.competitive) && (MF.kind === "All" || o.kind === MF.kind)
    && (MF.lead === "All" || leadOf(o.hrs) === MF.lead) && (MF.league === "All" || o.league === MF.league));
  const chip = (k, v, lab) => `<button class="chip ${MF[k] === v ? "on" : ""}" data-mf="${k}" data-v="${esc(String(v))}">${esc(lab ?? v)}</button>`;
  const leagues = ["All", ...new Set(live.map((o) => o.league).filter(Boolean))];
  const filters = `<div class="row" style="gap:6px;flex-wrap:wrap;align-items:center;margin:4px 0 6px"><span class="tiny muted">Edge if it fills</span>${[0.05, 0.10, 0.15, 0.20].map((m) => `<button class="chip ${Math.abs(mkMin() - m) < 1e-9 ? "on" : ""}" data-mkmin="${m}">${Math.round(m * 100)}%+</button>`).join("")}
      <button class="chip ${MF.comp ? "on" : ""}" data-mf="comp" data-v="${MF.comp ? "" : "1"}" title="your price for the taker is at least as good as every sportsbook's">Takers can't beat it elsewhere</button></div>
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:6px">${["All", "main", "alt", "prop"].map((k) => chip("kind", k, k === "All" ? "All markets" : kindName[k])).join("")}<span style="width:8px"></span>${["All", "Within 6 h", "6-24 h", "A day+"].map((k) => chip("lead", k, k === "All" ? "Any time" : k)).join("")}</div>
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-bottom:10px">${leagues.map((l) => chip("league", l, l === "All" ? "All sports" : l)).join("")}</div>`;
  const meth = (S.reports?.methods?.methods || []).find((m) => m.key === "maker");
  const P = M.paper || {}, L = meth?.live || {};
  const r = M.rules || {};
  return `<p class="note"><b>Where the big ones are:</b> the gap between the price you can make and the price takers pay is widest early and off the main lines: main lines run about 1c inside 6 hours but 2.5-3c a day or more out, alts and periods 2.5-5c, props 4.5-8c (Nov 8-17). The catch is the fill: a 10%+ bid only gets taken by someone paying 10%+ over the fair for the other side, so the ones worth posting are the ones where no sportsbook sells that side cheaper (on by default).</p>
    <p class="note">A <b>maker order</b> is a limit order you leave resting: you post a price and wait for a taker. The rule: an arb is always locked; otherwise only a side that's +EV on its own is ever bet. So each order says whether to post one side or both, and what to do the moment it fills: take the other side if that locks a profit (or is +EV itself), else hold it. These are every two-way market on your exchanges that the board prices off the market (main lines, alts, periods, team totals, props), up to ${Math.round((r.aheadH ?? 96) / 24)} days out. Each bid is one tick ahead of the best price there, so the next taker on the other side hits you first. Fill chances are a rough ceiling: how often the order behind a level like this disappeared on each venue (Nov 8-17), which on thin books includes makers moving their own orders. The paper record below is what will tell us the real rate.</p>
    <div class="card" style="margin-bottom:10px"><b>Paper record</b> <span class="tiny muted">every suggestion followed as if posted</span>
      <div class="row small" style="gap:14px;flex-wrap:wrap;margin-top:6px">
        <span>Resting now <b class="num">${P.resting ?? 0}</b></span><span>Filled <b class="num">${(P.filled ?? 0) + (P.locked ?? 0) + (P.filledOpen ?? 0)}</b>${(P.locked ?? 0) + (P.lockedOpen ?? 0) ? ` <span class="tiny muted">(${(P.locked ?? 0) + (P.lockedOpen ?? 0)} locked as arbs)</span>` : ""}</span>
        <span>Cancelled <b class="num">${P.cancelled ?? 0}</b></span><span>Expired unfilled <b class="num">${P.expired ?? 0}</b></span>
        <span>Fill rate <b class="num">${P.fillRate != null ? Math.round(P.fillRate * 100) + "%" : "–"}</b>${P.predicted != null ? ` <span class="tiny muted">(we estimated ${Math.round(P.predicted * 100)}%)</span>` : ""}</span>
        ${P.barComp ? `<span>At 10%+ that no sportsbook beats: fill rate <b class="num">${P.barComp.fillRate != null ? Math.round(P.barComp.fillRate * 100) + "%" : "–"}</b> <span class="tiny muted">(${(P.barComp.filled ?? 0) + (P.barComp.locked ?? 0)} of ${(P.barComp.filled ?? 0) + (P.barComp.locked ?? 0) + (P.barComp.cancelled ?? 0) + (P.barComp.expired ?? 0)} done)</span></span>` : ""}
        <span>Filled and graded <b class="num">${L.n ?? 0}</b>${L.n ? `, ROI <b class="num ${L.roi >= 0 ? "pos" : "neg"}">${(L.roi * 100).toFixed(1)}%</b>${L.clv != null ? `, CLV ${(L.clv * 100).toFixed(1)}%` : ""}` : ""}</span></div>
      <div class="tiny muted" style="margin-top:4px">Counted filled when the level behind ours gets eaten or disappears (the pessimistic read); cancelled when the fair moves our edge under ${((r.cancelEdge ?? 0.005) * 100).toFixed(1)}%. Also on Reports > Methods.</div></div>
    ${filters}
    <div class="tiny muted" style="margin-bottom:8px">${items.length} shown of ${live.length} found at ${Math.round((r.minEdge ?? 0.05) * 100)}%+ (${M.nBar ?? "?"} at 10%+, ${M.nBarComp ?? "?"} of them with no sportsbook beating them for the taker).</div>
    ${items.length ? `<div class="grid">${items.map(card).join("")}</div>` : `<div class="card note">None with these filters right now.</div>`}
    <p class="tiny muted" style="margin-top:8px">Maker fees assumed: Kalshi 0.0175 x p x (1-p) a contract; Polymarket US, Novig and ProphetX none. Check each venue's fee page before relying on it.</p>`;
}

export function bindMakers(el, rerender) {
  $$("[data-mf]", el).forEach((b) => b.onclick = () => { const k = b.dataset.mf; MF[k] = k === "comp" ? !!b.dataset.v : b.dataset.v; rerender(); });
  $$("[data-mkmin]", el).forEach((b) => b.onclick = () => saveSettings({ makerMin: +b.dataset.mkmin }).then(rerender).catch((e) => toast("Could not save: " + e.message)));
}
