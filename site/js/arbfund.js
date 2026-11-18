// The arb fund (Nov 17): money set aside in the arb venues (Novig, Bovada, ProphetX by default) that only takes arbs, with
// its winnings left in to compound. It is an earmark inside the apps you already track, not a separate account:
//   - fund entries (Firestore wallet/{id}, type "fund": {venue, amount, at, kind: seed | add | out | move, note}) put money
//     in, take it out, or move it between venues (a rebalance);
//   - arb pairs logged with "Count in the arb fund" carry group.fund: each leg's stake leaves its venue's fund balance when
//     placed, and whatever the leg returns lands back at that venue when it settles.
// So the fund always knows how much it holds at each venue, how much is riding, its profit, and its growth per day.
// Settings: settings/owner.arbFund {startedAt, alloc {venue: share}, rebalanceAt, target}.
import { S, saveWallet, saveSettings } from "./store.js";
import { esc, money, bookLogo, sheet, toast, uid, $, $$ } from "./util.js";
import { venueName } from "./lab.js";
import { settleProfit } from "./promo.js";

export const ALLOC = { novig: 0.45, bovada: 0.35, prophetx: 0.2 };
const DONE = ["won", "lost", "push", "void"];
const t = (x) => (x ? Date.parse(x) : 0);
const dayPT = (ms) => new Date(ms).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" });
const promoOf = (b) => (b.promoId ? S.promos.get(b.promoId) : null);
const ret = (b) => (b.status === "push" || b.status === "void" ? b.stake : b.status === "won" ? b.stake + (b.profit ?? settleProfit(b, promoOf(b), "won")) : 0);

export const fundCfg = () => (S.settings?.arbFund?.startedAt ? S.settings.arbFund : null);
export const isFundEntry = (w) => w.type === "fund";

export function fundState(now = Date.now()) {
  const cfg = fundCfg();
  if (!cfg) return null;
  const alloc = cfg.alloc || ALLOC;
  const ents = [...S.wallet.values()].filter(isFundEntry).sort((a, b) => t(a.at) - t(b.at));
  const bets = [...S.bets.values()].filter((b) => b.group?.fund && b.stake > 0);
  const venues = new Set([...Object.keys(alloc), ...ents.map((e) => e.venue), ...bets.map((b) => b.venue)]);
  const avail = Object.fromEntries([...venues].map((v) => [v, 0])), riding = Object.fromEntries([...venues].map((v) => [v, 0]));
  let contrib = 0, lastMove = null;
  const ev = [];
  for (const e of ents) ev.push({ at: t(e.at), e });
  for (const b of bets) {
    ev.push({ at: t(b.createdAt), place: b });
    if (DONE.includes(b.status)) ev.push({ at: t(b.settledAt || b.createdAt) + 1, settle: b });
  }
  ev.sort((a, b) => a.at - b.at);
  const series = new Map();          // Pacific day -> [contributed, value] at the end of that day
  for (const x of ev) {
    if (x.at > now) break;
    if (x.e) {
      const a = +x.e.amount || 0;
      avail[x.e.venue] = (avail[x.e.venue] || 0) + a;
      if (x.e.kind === "move") lastMove = x.at;
      else contrib += a;          // seed / add (+) and out (-)
    } else if (x.place) {
      avail[x.place.venue] -= x.place.stake;
      riding[x.place.venue] += x.place.stake;
    } else {
      riding[x.settle.venue] -= x.settle.stake;
      avail[x.settle.venue] += ret(x.settle);
    }
    const value = Object.values(avail).reduce((s, v) => s + v, 0) + Object.values(riding).reduce((s, v) => s + v, 0);
    series.set(dayPT(x.at), [contrib, value]);
  }
  const held = Object.fromEntries([...venues].map((v) => [v, (avail[v] || 0) + (riding[v] || 0)]));
  const value = Object.values(held).reduce((s, v) => s + v, 0);
  // arbs: pairs in the fund, settled and open
  const pairs = new Map();
  for (const b of bets) { const g = b.group.id; if (!pairs.has(g)) pairs.set(g, []); pairs.get(g).push(b); }
  const settled = [...pairs.values()].filter((L) => L.every((b) => DONE.includes(b.status)));
  const realized = settled.reduce((s, L) => s + L.reduce((x, b) => x + ret(b) - b.stake, 0), 0);
  const days = Math.max(1, (now - t(cfg.startedAt)) / 864e5);
  const daily = contrib > 0 && value > 0 ? Math.pow(value / contrib, 1 / days) - 1 : null;
  // rebalance: one venue holding too much of the fund, or a week since the last move with the split 10+ points off
  const share = Object.fromEntries([...venues].map((v) => [v, value > 0 ? held[v] / value : 0]));
  const off = Object.keys(alloc).map((v) => ({ v, d: (share[v] || 0) - alloc[v] })).sort((a, b) => b.d - a.d);
  const over = off[0], under = off.at(-1);
  const since = (now - (lastMove ?? t(cfg.startedAt))) / 864e5;
  const heavy = Object.entries(share).find(([v, s]) => s >= (cfg.rebalanceAt || 0.6));
  let rebalance = null;
  if (over && under && over.v !== under.v && (heavy || (since >= 7 && over.d >= 0.1))) {
    const amt = Math.floor(Math.min(over.d * value, avail[over.v] || 0, -under.d * value));
    if (amt >= 5) rebalance = { from: over.v, to: under.v, amount: amt, why: heavy ? `${venueName(heavy[0])} holds ${Math.round(heavy[1] * 100)}% of the fund` : `${Math.round(since)} days since the last rebalance` };
  }
  return { cfg, alloc, avail, riding, held, share, value, contrib, profit: value - contrib, realized, days, daily, series: [...series.entries()],
    nArbs: pairs.size, nSettled: settled.length, open: pairs.size - settled.length, rebalance, lastMove };
}

// what the fund can put on an opportunity: both legs' venues must hold fund money; at most 35% of the fund on one arb,
// no more than the prices fill
export function fundStake(o, F = fundState()) {
  if (!F || !o?.legs?.length || o.kind !== "arb") return null;
  const caps = o.legs.map((l, i) => (o.stakes[i] > 0 ? (F.avail[l.book] || 0) / o.stakes[i] : Infinity));
  if (o.legs.some((l) => !(l.book in F.alloc) && !(F.avail[l.book] > 0))) return { total: 0, why: `${o.legs.filter((l) => !(F.avail[l.book] > 0)).map((l) => l.name).join(" and ")} not in the fund` };
  const total = Math.floor(Math.min(...caps, 0.35 * F.value, o.maxStake ?? Infinity) * 100) / 100;
  const short = o.legs.find((l, i) => caps[i] === Math.min(...caps));
  return { total: Math.max(0, total), why: total < 5 ? `not enough fund money at ${short?.name || "a venue"} right now` : null };
}

// ---------- the Wallet card
function growthSvg(F) {
  const pts = F.series;
  if (pts.length < 2) return "";
  const start = t(F.cfg.startedAt), base = F.contrib || 1;
  const xs = pts.map(([d]) => (Date.parse(d + "T12:00:00") - start) / 864e5);
  const n = Math.max(7, Math.ceil(xs.at(-1)) + 1);
  const val = pts.map(([, [c, v]]) => (c > 0 ? v / c : 1));
  const path = (r) => Array.from({ length: n + 1 }, (_, i) => [i, Math.pow(1 + r, i)]);
  const lines = [[path(0.01), "tgt", "1%/day"], [path(0.005), "slow", "0.5%/day"]];
  const ymax = Math.max(...val, Math.pow(1.01, n)) * 1.02, ymin = Math.min(0.98, ...val);
  const W = 320, H = 120, X = (x) => (x / n) * (W - 30) + 28, Y = (y) => H - 16 - ((y - ymin) / (ymax - ymin)) * (H - 26);
  const pl = (arr) => arr.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ");
  return `<svg class="af-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="fund growth vs 1% a day">
    ${lines.map(([p, c]) => `<polyline class="${c}" points="${pl(p)}"/>`).join("")}
    <polyline class="act" points="${pl(xs.map((x, i) => [x, val[i]]))}"/>
    <text x="2" y="${Y(1) + 3}" class="lab">$${Math.round(base)}</text><text x="${W - 2}" y="${Y(Math.pow(1.01, n)) - 3}" text-anchor="end" class="lab">1%/day</text>
    <text x="28" y="${H - 2}" class="lab">day 0</text><text x="${W - 2}" y="${H - 2}" text-anchor="end" class="lab">day ${n}</text></svg>`;
}

export function fundCard() {
  const F = fundState();
  if (!F) return `<div class="card af"><b>Arb fund</b>
    <div class="small" style="margin-top:4px">Set money aside in your arb venues that only takes verified arbs, with the winnings left in to compound. Two weeks of our verified arbs say acting on alerts and rebalancing about weekly is worth roughly 0.5-1% a day early on; it slows past a few thousand dollars as Bovada limits and exchange depth bind.</div>
    <div class="row" style="margin-top:8px"><button class="btn sm" id="af-start">Start the arb fund</button></div></div>`;
  const pct = (x) => (x == null ? "–" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)}%`);
  const vrows = Object.keys({ ...F.alloc, ...F.held }).filter((v) => F.alloc[v] || F.held[v] > 0.005).map((v) => {
    const s = F.share[v] || 0, tg = F.alloc[v] || 0;
    return `<div class="af-v"><span class="lg-name">${bookLogo(v, venueName(v), 16)}${esc(venueName(v))}</span>
      <span class="af-bar" title="target ${Math.round(tg * 100)}%"><i style="width:${Math.min(100, s * 100).toFixed(1)}%"></i><b style="left:${(tg * 100).toFixed(1)}%"></b></span>
      <span class="num small">${money(F.avail[v] || 0)}${F.riding[v] > 0.005 ? ` <span class="tiny muted">+ ${money(F.riding[v])} riding</span>` : ""}</span>
      <span class="tiny muted num">${Math.round(s * 100)}% / ${Math.round(tg * 100)}%</span></div>`;
  }).join("");
  return `<div class="card af"><div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap"><b>Arb fund</b><span class="tiny muted">since ${new Date(F.cfg.startedAt).toLocaleDateString("en-US", { month: "short", day: "numeric" })} · day ${Math.floor(F.days)}</span></div>
    <div class="kpis" style="margin-top:8px">
      <div class="kpi"><div class="k">Fund now</div><div class="v">${money(F.value)}</div><div class="s">${money(F.contrib)} put in</div></div>
      <div class="kpi"><div class="k">Profit</div><div class="v ${F.profit >= 0 ? "pos" : "neg"}">${F.profit >= 0 ? "+" : ""}${money(F.profit)}</div><div class="s">${F.nSettled} arbs settled${F.open ? ` · ${F.open} open` : ""}</div></div>
      <div class="kpi"><div class="k">Per day</div><div class="v ${F.daily >= 0.01 ? "pos" : ""}">${pct(F.daily)}</div><div class="s">compounded · target ${pct(F.cfg.target ?? 0.01)}</div></div>
    </div>
    ${F.rebalance ? `<div class="af-reb"><b>Rebalance:</b> move <b>${money(F.rebalance.amount)}</b> from ${esc(venueName(F.rebalance.from))} to ${esc(venueName(F.rebalance.to))} <span class="tiny muted">(${esc(F.rebalance.why)})</span> <button class="btn sm" data-afmove="${F.rebalance.from}|${F.rebalance.to}|${F.rebalance.amount}">Log the move</button></div>` : ""}
    <div class="af-vs">${vrows}</div>
    ${growthSvg(F)}
    <div class="row" style="gap:6px;flex-wrap:wrap;margin-top:6px"><button class="btn sm ghost" data-afmove="">Move money</button><button class="btn sm ghost" id="af-add">Add money</button><button class="btn sm ghost" id="af-out">Take money out</button></div>
    <p class="note">The fund is an earmark inside these apps: arbs logged with "Count in the arb fund" take their stakes from it and their returns go back into it at the venue that paid. Arb cards size to what the fund holds at both venues (at most 35% of the fund on one). Your money lands at whichever venue won, so it drifts; rebalance when one app holds 60%+ or about weekly.</p></div>`;
}

function startSheet() {
  sheet(`<h3>Start the arb fund</h3><div class="col" style="gap:10px">
    <div class="fgrid"><label class="f">Amount $<input id="af-amt" inputmode="decimal" value="100"></label></div>
    ${Object.entries(ALLOC).map(([v, s]) => `<div class="fgrid"><label class="f">${esc(venueName(v))} %<input class="af-al" data-v="${v}" inputmode="decimal" value="${Math.round(s * 100)}"></label></div>`).join("")}
    <label class="row tiny" style="gap:6px;align-items:center;justify-content:flex-start"><input type="checkbox" id="af-dep" style="width:auto;flex:none;margin:0"> This is new money: also log it as deposits to these apps</label>
    <p class="note">Leave it unticked if the money is already sitting in these apps; the fund just earmarks it.</p>
    <div class="row"><button class="btn" id="af-go">Start</button><button class="btn ghost" id="af-x">Cancel</button></div></div>`, (el, close) => {
    $("#af-x", el).onclick = close;
    $("#af-go", el).onclick = async () => {
      const amt = parseFloat($("#af-amt", el).value), al = {};
      $$(".af-al", el).forEach((i) => { const x = parseFloat(i.value); if (x > 0) al[i.dataset.v] = x / 100; });
      const tot = Object.values(al).reduce((s, x) => s + x, 0);
      if (!(amt > 0) || !(tot > 0)) { toast("Type an amount and a split"); return; }
      Object.keys(al).forEach((v) => (al[v] = al[v] / tot));
      const at = new Date().toISOString(), dep = $("#af-dep", el).checked;
      try {
        for (const [v, s] of Object.entries(al)) {
          const a = Math.round(amt * s * 100) / 100;
          await saveWallet({ id: uid(), venue: v, type: "fund", kind: "seed", amount: a, at, note: "arb fund start" });
          if (dep) await saveWallet({ id: uid(), venue: v, type: "deposit", amount: a, at, note: "for the arb fund" });
        }
        await saveSettings({ arbFund: { startedAt: at, alloc: al, rebalanceAt: 0.6, target: 0.01 } });
        toast("Arb fund started"); close();
      } catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

function moneySheet(kind, pre = {}) {
  const F = fundState(), vs = Object.keys({ ...(F?.alloc || ALLOC), ...(F?.held || {}) });
  const opt = (sel) => vs.map((v) => `<option value="${v}" ${v === sel ? "selected" : ""}>${esc(venueName(v))}</option>`).join("");
  const title = kind === "move" ? "Move fund money between apps" : kind === "add" ? "Add money to the arb fund" : "Take money out of the arb fund";
  sheet(`<h3>${title}</h3><div class="col" style="gap:10px">
    <div class="fgrid">${kind === "move" ? `<label class="f">From<select id="af-from">${opt(pre.from)}</select></label><label class="f">To<select id="af-to">${opt(pre.to)}</select></label>` : `<label class="f">App<select id="af-v">${opt(pre.v)}</select></label>`}
      <label class="f">Amount $<input id="af-a" inputmode="decimal" value="${pre.amount ?? ""}"></label></div>
    <label class="row tiny" style="gap:6px;align-items:center;justify-content:flex-start"><input type="checkbox" id="af-w" checked style="width:auto;flex:none;margin:0"> Also log the ${kind === "move" ? "withdrawal and deposit" : kind === "add" ? "deposit" : "withdrawal"} in the wallet</label>
    <div class="row"><button class="btn" id="af-ok">Save</button><button class="btn ghost" id="af-x">Cancel</button></div></div>`, (el, close) => {
    $("#af-x", el).onclick = close;
    $("#af-ok", el).onclick = async () => {
      const a = parseFloat($("#af-a", el).value), at = new Date().toISOString(), w = $("#af-w", el).checked;
      if (!(a > 0)) { toast("Type the amount"); return; }
      try {
        if (kind === "move") {
          const f = $("#af-from", el).value, to = $("#af-to", el).value;
          if (f === to) { toast("Pick two different apps"); return; }
          await saveWallet({ id: uid(), venue: f, type: "fund", kind: "move", amount: -a, at, note: `to ${venueName(to)}` });
          await saveWallet({ id: uid(), venue: to, type: "fund", kind: "move", amount: a, at, note: `from ${venueName(f)}` });
          if (w) {
            await saveWallet({ id: uid(), venue: f, type: "withdrawal", amount: a, at, note: "arb fund rebalance" });
            await saveWallet({ id: uid(), venue: to, type: "deposit", amount: a, at, note: "arb fund rebalance" });
          }
        } else {
          const v = $("#af-v", el).value;
          await saveWallet({ id: uid(), venue: v, type: "fund", kind, amount: kind === "add" ? a : -a, at, note: kind === "add" ? "added to the arb fund" : "taken out of the arb fund" });
          if (w) await saveWallet({ id: uid(), venue: v, type: kind === "add" ? "deposit" : "withdrawal", amount: a, at, note: "arb fund" });
        }
        toast("Saved"); close();
      } catch (e) { toast("Could not save: " + e.message); }
    };
  });
}

export function bindFund(el) {
  const s = $("#af-start", el); if (s) s.onclick = startSheet;
  const a = $("#af-add", el); if (a) a.onclick = () => moneySheet("add");
  const o = $("#af-out", el); if (o) o.onclick = () => moneySheet("out");
  $$("[data-afmove]", el).forEach((b) => b.onclick = () => { const [from, to, amount] = (b.dataset.afmove || "").split("|"); moneySheet("move", { from, to, amount }); });
}
