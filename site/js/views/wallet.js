// Ledger > Wallet: what's in each app (cash and bonus), deposits and withdrawals, and the real profit per app.
// The math lives in ../wallet.js.
import { S, saveWallet, deleteWallet, saveSettings } from "../store.js";
import { esc, money, bookLogo, sheet, toast, uid, $, $$ } from "../util.js";
import { VENUES, venueName } from "../lab.js";
import { venueState, walletAll, accountName, accountOf, syncWalletPromos } from "../wallet.js";
import { divRows } from "./charts.js";
import { sortTable, td, bindSort } from "./deep.js";

let openHist = new Set();
const when = (x) => (x ? new Date(x).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Los_Angeles" }) : "");
const ago = (x) => { if (!x) return ""; const m = (Date.now() - Date.parse(x)) / 60000; return m < 60 ? `${Math.max(1, Math.round(m))} min ago` : m < 48 * 60 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} days ago`; };
const cls = (v) => (v > 0.005 ? "pos" : v < -0.005 ? "neg" : "");
const sgn = (v) => `${v > 0.005 ? "+" : ""}${money(v)}`;

function entryLine(e) {
  const lab = e.type === "balance" ? `Check-in: ${money(+e.cash || 0)} cash${+e.bonus ? `, ${money(+e.bonus)} bonus` : ""}${e.first ? " (tracking starts)" : ""}`
    : e.type === "deposit" ? `Deposit ${money(+e.amount)}` : e.type === "withdrawal" ? `Withdrawal ${money(+e.amount)}` : `Bonus received ${money(+e.amount)}`;
  const off = e.type === "balance" && !e.first && (Math.abs(e.diffCash) > 0.009 || Math.abs(e.diffBonus) > 0.009)
    ? ` <span class="tiny ${cls(e.diffCash)}">expected ${money(e.expCash)}${Math.abs(e.diffBonus) > 0.009 ? ` / ${money(e.expBonus)} bonus` : ""}: off ${sgn(e.diffCash)}${Math.abs(e.diffBonus) > 0.009 ? ` cash, ${sgn(e.diffBonus)} bonus` : ""}</span>`
    : e.type === "balance" && !e.first ? ` <span class="tiny pos">matches the ledger</span>` : "";
  return `<div class="row wl-h"><span class="tiny muted" style="min-width:96px">${esc(when(e.at))}</span><span class="grow small">${lab}${off}${e.note ? ` <span class="tiny muted">· ${esc(e.note)}</span>` : ""}</span><button class="x" data-wdel="${esc(e.id)}" title="delete">×</button></div>`;
}

function appCard(w) {
  const name = accountName(w.venue);
  const part = (lab, v, tip) => Math.abs(v || 0) > 0.005 ? `<span class="wl-part" title="${esc(tip)}">${esc(lab)} <b class="num ${cls(v)}">${sgn(v)}</b></span>` : "";
  return `<div class="card col wl-card" style="gap:8px">
    <div class="row" style="justify-content:space-between;gap:8px"><b class="row" style="gap:8px">${bookLogo(w.venue, name, 24)}${esc(name)}</b>
      <span class="tiny muted">${w.checkedAt ? `checked in ${ago(w.checkedAt)}${w.nBets ? ` · ${w.nBets} bet${w.nBets === 1 ? "" : "s"} since tracking` : ""}` : "no check-in yet"}</span></div>
    <div class="wl-nums">
      <div><div class="k">Cash</div><div class="v num">${money(w.cash)}</div><div class="s">${w.tracked ? "what the app should show" : "check in to start"}</div></div>
      <div><div class="k">Bonus</div><div class="v num">${money(w.bonus)}</div><div class="s">${w.cashLike ? "counts as money here" : "counts once it's cash"}${w.openBonus ? ` · ${money(w.openBonus)} in open bets` : ""}${w.bonus >= 0.5 ? ` · <a href="#/promos">use it</a>` : ""}</div></div>
      <div><div class="k">In open bets</div><div class="v num">${money(w.openCash)}</div><div class="s">cash</div></div>
      <div><div class="k">Profit</div><div class="v num ${cls(w.real)}">${w.real != null ? sgn(w.real) : "–"}</div><div class="s">${w.tracked ? "money out + now − money in" : "after a check-in"}</div></div>
    </div>
    ${w.tracked ? `<div class="tiny muted">Money in ${money(w.moneyIn)} (started with ${money(w.opening)}${w.dep ? ` + deposited ${money(w.dep)}` : ""}) · money out ${money(w.wd)} · in the app now ${money(w.now)}</div>
      <div class="row tiny" style="gap:10px;flex-wrap:wrap">${part("bets settled", w.ledger, "profit of the bets you logged here that settled since tracking started")}${part("check-in adjustments", w.adj, "check-ins that differed from what your logged bets predicted: counted in the profit")}${part("bonus credited − bonus staked", w.bonusNet, "bonus funds count as money at this app")}${w.other != null && Math.abs(w.other) > 0.5 ? part("other", w.other, "bets placed before tracking started that settled after, and rounding") : ""}</div>` : ""}
    ${Math.abs(w.unacc) > 0.5 || Math.abs(w.unaccBonus) > 0.5 ? `<div class="tiny ${cls(w.unacc)}">Check-ins differed from the ledger by ${sgn(w.unacc)} cash${Math.abs(w.unaccBonus) > 0.5 ? `, ${sgn(w.unaccBonus)} bonus` : ""}${w.cashLike || Math.abs(w.unaccBonus) < 0.5 ? "" : " (bonus differences don't count here until they turn into cash)"}. It's in the profit; if it was an unlogged bet or a typo, logging or fixing it moves it into "bets settled".</div>` : ""}
    <label class="row tiny" style="gap:6px;align-items:center;justify-content:flex-start"><input type="checkbox" style="width:auto;flex:none;margin:0" data-bcash="${w.venue}" ${w.cashLike ? "checked" : ""}> Bonus funds here work like cash (count them as money; turning bonus into cash isn't profit)</label>
    <div class="row" style="gap:6px;flex-wrap:wrap">
      <button class="btn sm" data-wact="balance|${w.venue}">Check in</button><button class="btn sm ghost" data-wact="deposit|${w.venue}">Deposit</button>
      <button class="btn sm ghost" data-wact="withdrawal|${w.venue}">Withdraw</button><button class="btn sm ghost" data-wact="bonus|${w.venue}">Bonus received</button>
      <button class="btn sm ghost" data-whist="${w.venue}">${openHist.has(w.venue) ? "Hide" : "History"}</button></div>
    ${openHist.has(w.venue) ? `<div class="col" style="gap:4px">${w.log.map(entryLine).join("") || `<div class="tiny muted">Nothing entered yet.</div>`}</div>` : ""}
  </div>`;
}

export function walletHtml() {
  const all = walletAll().sort((a, b) => (b.cash + b.openCash) - (a.cash + a.openCash));
  const tr = all.filter((w) => w.tracked);
  // an app with nothing in it and nothing ever moved gets one line at the bottom instead of a card and table rows
  const idle = (w) => Math.abs(w.moneyIn || 0) < 0.005 && !w.wd && Math.abs(w.now || 0) < 0.005 && !w.bonus && !w.nBets;
  const act = tr.filter((w) => !idle(w));
  const sum = (k, L = all) => L.reduce((x, w) => x + (w[k] || 0), 0);
  const realT = sum("real", tr), ledT = sum("ledger", tr), adjT = sum("adj", tr), bnT = sum("bonusNet", tr), othT = sum("other", tr);
  syncWalletPromos();
  const others = VENUES.filter((v) => v.key !== "any" && accountOf(v.key) === v.key && !all.some((w) => w.venue === v.key));
  const inT = sum("moneyIn", tr), outT = sum("wd", tr), nowT = sum("now", tr);
  return `${all.length ? `<div class="kpis" style="margin-top:12px">
      <div class="kpi"><div class="k">Money in</div><div class="v">${money(inT)}</div><div class="s">started with ${money(sum("opening", tr))} + deposited ${money(sum("dep", tr))}</div></div>
      <div class="kpi"><div class="k">Money out</div><div class="v">${money(outT)}</div><div class="s">withdrawn since tracking</div></div>
      <div class="kpi"><div class="k">In the apps now</div><div class="v">${money(nowT)}</div><div class="s">cash ${money(sum("cash", tr))} + open bets ${money(sum("openCash", tr))}${tr.some((w) => w.cashLike && w.bonus) ? " + bonus that counts" : ""}</div></div>
      <div class="kpi"><div class="k">Profit</div><div class="v ${cls(realT)}">${tr.length ? sgn(realT) : "–"}</div><div class="s">out + now − in${tr.length && inT ? ` · ${(realT / inT * 100).toFixed(1)}% on money in` : ""}</div></div>
      <div class="kpi"><div class="k">Bonus funds</div><div class="v">${money(sum("bonus"))}</div><div class="s">${tr.some((w) => w.cashLike) ? "counted as money where it works like cash" : "not money until it turns into cash"}</div></div>
    </div>
    ${tr.length ? `<div class="card" style="margin-top:10px"><b>Where the profit came from</b>
      <div class="row small" style="gap:16px;flex-wrap:wrap;margin-top:6px"><span>Bets settled <b class="num ${cls(ledT)}">${sgn(ledT)}</b></span><span>Check-in adjustments <b class="num ${cls(adjT)}">${sgn(adjT)}</b></span>${Math.abs(bnT) > 0.005 ? `<span>Bonus credited − staked (cash-like apps) <b class="num ${cls(bnT)}">${sgn(bnT)}</b></span>` : ""}${Math.abs(othT) > 0.5 ? `<span>Other <b class="num ${cls(othT)}">${sgn(othT)}</b></span>` : ""}<span>= <b class="num ${cls(realT)}">${sgn(realT)}</b></span></div>
      ${sortTable(["App", "Money in", "Out", "Now", "Profit", "Bets", "Check-ins", "Bonus as cash"], act.map((w) => `<tr><td><span class="lg-name">${bookLogo(w.venue, accountName(w.venue), 16)}${esc(accountName(w.venue))}</span></td>
        ${td(money(w.moneyIn), w.moneyIn)}${td(money(w.wd), w.wd)}${td(money(w.now), w.now)}${td(`<b class="${cls(w.real)}">${sgn(w.real)}</b>`, w.real)}${td(sgn(w.ledger), w.ledger)}${td(sgn(w.adj), w.adj)}${td(w.cashLike ? "yes" : "no", w.cashLike ? 1 : 0)}</tr>`))}
      <p class="note">Money in is everything that was in each app when you first checked it in, plus deposits. Profit is what you've taken out plus what's in the apps now, minus money in, so every check-in counts. A check-in that differs from what your logged bets predicted shows as an adjustment: an unlogged bet, a typo, a fee, or bonus that turned into cash. At apps where bonus funds work like cash (tick it on the app's card) they count as money, so turning them into cash isn't profit; elsewhere bonus counts once it's cash.</p></div>` : ""}` : `<div class="card note" style="margin-top:12px">Track what's really in each app. Pick an app below and check in: type the cash and bonus balance the app shows right now. That's your starting point (money in). From then on every bet you log, deposit and withdrawal updates it, and each later check-in shows whether the ledger missed anything.</div>`}
    <div class="row" style="gap:8px;margin-top:12px;flex-wrap:wrap;align-items:center"><select id="wl-new" style="max-width:220px">${others.map((v) => `<option value="${v.key}">${esc(accountName(v.key))}</option>`).join("")}</select>
      <button class="btn sm" id="wl-add">Check in an app</button><span class="tiny muted">start tracking another app with its balance right now</span></div>
    ${tr.length > 1 ? `<div class="sec-title">Where you're making it<span class="line"></span></div><div class="card">${divRows(act.map((w) => ({ k: accountName(w.venue), html: `<span class="lg-name">${bookLogo(w.venue, accountName(w.venue), 16)}${esc(accountName(w.venue))}</span>`, v: w.real, tip: `${accountName(w.venue)}: real ${sgn(w.real)}, ledger ${sgn(w.ledger)}` })).sort((a, b) => b.v - a.v), { fmt: sgn })}
      <p class="note">Profit per app since you started tracking it: withdrawals + what's in it now − (what was there at the first check-in + deposits).</p></div>` : ""}
    ${all.length ? `<div class="sec-title">Your apps<span class="line"></span></div><div class="grid wl-grid">${all.filter((w) => !idle(w)).map(appCard).join("")}</div>
      ${all.some(idle) ? `<p class="tiny muted" style="margin-top:8px">Empty, nothing logged: ${all.filter(idle).map((w) => `<button class="lnk" data-wact="balance|${w.venue}">${esc(accountName(w.venue))}</button>`).join(" · ")} (tap one to check in)</p>` : ""}` : ""}`;
}

function walletSheet(type, venue) {
  const w = venueState(venue) || { cash: 0, bonus: 0, tracked: false };
  const name = accountName(accountOf(venue));
  const title = { balance: `Check in: ${name}`, deposit: `Deposit to ${name}`, withdrawal: `Withdraw from ${name}`, bonus: `Bonus received at ${name}` }[type];
  const now = new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  sheet(`<h3>${esc(title)}</h3><div class="col" style="gap:10px">
    ${type === "balance" ? `<p class="small" style="margin:0">Type exactly what ${esc(name)} shows right now.${w.tracked ? ` Your logged bets say it should be <b>${money(w.cash)}</b> cash${w.bonus ? ` and <b>${money(w.bonus)}</b> bonus` : ""}.` : " This starts tracking: it's your starting point for real profit here."}</p>
      <div class="fgrid"><label class="f">Cash $<input id="wl-cash" inputmode="decimal" value="${w.tracked ? w.cash.toFixed(2) : ""}"></label><label class="f">Bonus funds $<input id="wl-bonus" inputmode="decimal" value="${w.tracked && w.bonus ? w.bonus.toFixed(2) : ""}" placeholder="0"></label></div>
      <div class="tiny muted" id="wl-diff"></div>`
    : `<div class="fgrid"><label class="f">Amount $<input id="wl-amt" inputmode="decimal"></label></div>`}
    <div class="fgrid"><label class="f">When<input id="wl-at" type="datetime-local" value="${now}"></label><label class="f">Note (optional)<input id="wl-note" placeholder="${type === "bonus" ? "e.g. no-sweat refund" : ""}"></label></div>
    <div class="row"><button class="btn" id="wl-save">Save</button><button class="btn ghost" id="wl-cancel">Cancel</button></div></div>`, (el, close) => {
    $("#wl-cancel", el).onclick = close;
    const diff = () => {
      const d = $("#wl-diff", el); if (!d || !w.tracked) return;
      const c = parseFloat($("#wl-cash", el).value), b = parseFloat($("#wl-bonus", el).value) || 0;
      if (isNaN(c)) { d.textContent = ""; return; }
      const dc = c - w.cash, dbn = b - (w.bonus || 0);
      d.innerHTML = Math.abs(dc) < 0.01 && Math.abs(dbn) < 0.01 ? `<span class="pos">Matches your ledger exactly.</span>` : `Off by <b class="${cls(dc)}">${sgn(dc)}</b> cash${Math.abs(dbn) >= 0.01 ? ` and <b class="${cls(dbn)}">${sgn(dbn)}</b> bonus` : ""} from what the ledger expects: it'll show as unaccounted. Look for a bet that isn't logged or a stake or odds typo.`;
    };
    ["wl-cash", "wl-bonus"].forEach((id) => { const i = $("#" + id, el); if (i) i.oninput = diff; });
    diff();
    $("#wl-save", el).onclick = async () => {
      const at = new Date($("#wl-at", el).value || Date.now()).toISOString(), note = $("#wl-note", el).value.trim() || null;
      const e = { id: uid(), venue, type, at, note };
      if (type === "balance") {
        e.cash = parseFloat($("#wl-cash", el).value); e.bonus = parseFloat($("#wl-bonus", el).value) || 0;
        if (isNaN(e.cash)) { toast("Type the cash balance"); return; }
      } else {
        e.amount = parseFloat($("#wl-amt", el).value);
        if (!(e.amount > 0)) { toast("Type the amount"); return; }
      }
      try { await saveWallet(e); toast("Saved"); close(); } catch (err) { toast("Could not save: " + err.message); }
    };
  });
}

export function bindWallet(el, rerender) {
  $$("[data-wact]", el).forEach((b) => b.onclick = () => { const [type, v] = b.dataset.wact.split("|"); walletSheet(type, v); });
  $$("[data-whist]", el).forEach((b) => b.onclick = () => { const v = b.dataset.whist; openHist.has(v) ? openHist.delete(v) : openHist.add(v); rerender(); });
  $$("[data-wdel]", el).forEach((b) => b.onclick = () => { if (b.dataset.c) deleteWallet(b.dataset.wdel); else { b.dataset.c = 1; b.textContent = "?"; b.title = "tap again to delete"; } });
  const add = $("#wl-add", el); if (add) add.onclick = () => walletSheet("balance", $("#wl-new", el).value);
  $$("[data-bcash]", el).forEach((c) => c.onchange = () => {
    const bc = { ...(S.settings?.bonusCash || {}), [c.dataset.bcash]: c.checked };
    saveSettings({ bonusCash: bc }).then(() => { toast(c.checked ? "Bonus counts as money at this app" : "Bonus counts once it's cash"); rerender(); }).catch((e) => toast("Could not save: " + e.message));
  });
  bindSort(el);
}
