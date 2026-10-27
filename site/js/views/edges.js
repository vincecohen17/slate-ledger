import { S } from "../store.js";
import { esc, bookLogo, $$ } from "../util.js";
import { edgeCard, bindLog } from "./common.js";

const F = { league: "All", type: "All", min: 0.015, venue: "All", logic: "All" };
const isLine = (e) => !/^(player|batter|pitcher)_/.test(e.market || "");

export function renderEdges(el) {
  const leagues = ["All", ...new Set(S.edges.map((e) => e.league))];
  const venues = ["All", ...new Set(S.edges.map((e) => e.bookName))];
  const items = S.edges.filter((e) =>
    (F.logic === "All" || (F.logic === "Logic agrees" && e.logic === "agrees")) && (F.league === "All" || e.league === F.league) && (F.venue === "All" || e.bookName === F.venue) && e.edge >= F.min &&
    (F.type === "All" || (F.type === "Lines" && isLine(e)) || (F.type === "Props" && !isLine(e) && !e.pickem) || (F.type === "Pick'em" && e.pickem)) &&
    new Date(e.commence) > new Date(Date.now() - 5 * 60000));
  const bookOf = Object.fromEntries(S.edges.map((e) => [e.bookName, e.book]));
  const chips = (key, vals, fmt = (v) => v) => vals.map((v) => `<button class="chip ${F[key] === v ? "on" : ""}" data-f="${key}" data-v="${esc(v)}">${key === "venue" && bookOf[v] ? bookLogo(bookOf[v], v, 14) : ""}${esc(fmt(v))}</button>`).join("");
  el.innerHTML = `<div class="bar">${chips("league", leagues)}<span class="spacer"></span>${chips("type", ["All", "Lines", "Props", "Pick'em"])}</div>
    <div class="bar" style="margin-top:0">${chips("logic", ["All", "Logic agrees"])}<span class="spacer"></span>${chips("venue", venues)}<span class="spacer"></span>
      ${[0.015, 0.03, 0.05].map((m) => `<button class="chip ${F.min === m ? "on" : ""}" data-min="${m}">${(m * 100).toFixed(1)}%+</button>`).join("")}</div>
    <p class="note">Prices at your venues that beat the sharp no-vig fair. Ranked by edge, boosted when our sports model agrees and cut when it disagrees; alt and pick'em lines count half until backtests prove them. "Logic agrees" is the shortlist.</p>
    ${items.length ? `<div class="grid tight">${items.slice(0, 150).map(edgeCard).join("")}</div>` : `<div class="empty"><h3>Nothing at this threshold</h3>Lower the minimum edge or check back after the next pull.</div>`}`;
  $$("[data-f]", el).forEach((b) => b.onclick = () => { F[b.dataset.f] = b.dataset.v; renderEdges(el); });
  $$("[data-min]", el).forEach((b) => b.onclick = () => { F.min = +b.dataset.min; renderEdges(el); });
  bindLog(el);
}
