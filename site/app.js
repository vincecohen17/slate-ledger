import { initializeApp } from "https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js";
import { getFirestore, doc, collection, onSnapshot, query, where }
  from "https://www.gstatic.com/firebasejs/13.1.0/firebase-firestore.js";

// Public project identifiers (not secrets). Access is enforced by Firestore rules.
const app = initializeApp({
  apiKey: "AIzaSyBKakn2Rex1ZpKKehuvxJjD7cUnxsJTeEU",
  authDomain: "slate-ledger.firebaseapp.com",
  projectId: "slate-ledger",
  storageBucket: "slate-ledger.firebasestorage.app",
  messagingSenderId: "144087163957",
  appId: "1:144087163957:web:b618e215de05808ef03d7c",
});
const auth = getAuth(app);
const db = getFirestore(app);

const $ = (s) => document.querySelector(s);
const appEl = $("#app");
const S = { games: new Map(), status: null, edges: [], props: new Map(), league: "All", unsubs: [],
            propSub: null, propGame: null, propMarket: null, open: new Set(), edgeLeague: "All", pickem: true };

// ---------- formatting
const TZ = "America/Los_Angeles";
const fmtTime = (iso) => new Date(iso).toLocaleTimeString("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
const fmtDay = (iso) => new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, weekday: "long", month: "short", day: "numeric" });
const am = (x) => x == null ? "–" : (x > 0 ? `+${x}` : `${x}`);
const pct = (x, d = 1) => x == null ? "" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
const fairPct = (x) => x == null ? "–" : `${(x * 100).toFixed(1)}%`;
const ecls = (e) => e == null ? "" : e >= 0.03 ? "e2" : e >= 0.015 ? "e1" : e < -0.04 ? "neg" : "";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ago = (iso) => { const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 1 ? "just now" : m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`; };
const mktName = (m) => m.replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ");
const signPt = (p) => p > 0 ? `+${p}` : `${p}`;

// ---------- auth
async function doSignIn() {
  const p = new GoogleAuthProvider();
  try { await signInWithPopup(auth, p); }
  catch (e) {
    if (String(e.code).includes("popup")) await signInWithRedirect(auth, p);
    else { const m = $("#signin-msg"); if (m) m.textContent = e.message; }
  }
}
$("#signin-btn").onclick = doSignIn;

onAuthStateChanged(auth, (user) => {
  S.unsubs.forEach((u) => u()); S.unsubs = [];
  if (!user) { $("#nav").hidden = true; $("#status").innerHTML = ""; return showSignin(); }
  $("#nav").hidden = false;
  subscribe();
  route();
});

function showSignin(msg) {
  appEl.innerHTML = `<section class="signin"><h1>Slate Ledger</h1>
    <p>Every line, every venue, priced against the sharp market.</p>
    <button class="btn" id="si">Sign in with Google</button>
    <p class="muted small" id="signin-msg">${esc(msg || "")}</p></section>`;
  $("#si").onclick = doSignIn;
}

function denied() {
  appEl.innerHTML = `<section class="signin"><h1>Not this account</h1>
    <p class="muted">This board only opens for its owner.</p>
    <button class="btn ghost" id="so">Sign out</button></section>`;
  $("#so").onclick = () => signOut(auth);
}

function subscribe() {
  const onErr = (e) => { if (e.code === "permission-denied") denied(); else console.error(e); };
  S.unsubs.push(onSnapshot(doc(db, "board", "status"), (d) => { S.status = d.data() || null; renderStatus(); }, onErr));
  S.unsubs.push(onSnapshot(doc(db, "board", "edges"), (d) => {
    S.edges = d.exists() ? JSON.parse(d.data().payload).items : []; rerender();
  }, onErr));
  S.unsubs.push(onSnapshot(collection(db, "games"), (qs) => {
    qs.docChanges().forEach((ch) => {
      if (ch.type === "removed") S.games.delete(ch.doc.id);
      else S.games.set(ch.doc.id, JSON.parse(ch.doc.data().payload));
    });
    rerender();
  }, onErr));
}

function renderStatus() {
  const s = S.status;
  if (!s) { $("#status").innerHTML = `<span class="muted">waiting for first pull</span>`; return; }
  const old = (Date.now() - new Date(s.updated)) > 30 * 60000;
  $("#status").innerHTML = `<span><i class="dot ${old ? "old" : ""}"></i>${ago(s.updated)}</span>
    <span class="hide-sm">${s.mode === "mock" ? "mock · " : ""}${s.creditsRemaining ?? "?"} credits left</span>
    ${s.errors?.length ? `<span title="${esc(s.errors.join("\n"))}">${s.errors.length} note${s.errors.length > 1 ? "s" : ""}</span>` : ""}`;
}

// ---------- routing
window.addEventListener("hashchange", route);
let rerenderTimer = null;
function rerender() { clearTimeout(rerenderTimer); rerenderTimer = setTimeout(route, 120); }

function route() {
  if (!auth.currentUser) return;
  const h = location.hash || "#/";
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("on",
    (a.dataset.route === "edges" && h.startsWith("#/edges")) || (a.dataset.route === "board" && (h === "#/" || h === "#"))));
  if (h.startsWith("#/game/")) return renderGame(h.split("/")[2], h.split("/")[3] || "lines");
  if (h.startsWith("#/edges")) return renderEdges();
  renderBoard();
}

// ---------- board
function mainLine(g, m) { return g.lines.find((l) => l.market === m && l.isMain); }

function cell(line, side, label) {
  if (!line || !line.bestVenue?.[side]) return `<div class="c muted">–</div>`;
  const b = line.bestVenue[side];
  return `<div class="c ${ecls(b.edge)}" title="${esc(b.name)} ${am(b.am)} · fair ${am(line.fairAm?.[side])}">${label ? `<span class="muted">${label}</span> ` : ""}${am(b.am)}</div>`;
}

function renderBoard() {
  const games = [...S.games.values()].sort((a, b) => a.commence.localeCompare(b.commence));
  if (!games.length) { appEl.innerHTML = `<div class="empty">No games on the board yet. The first pull fills this in.</div>`; return; }
  const leagues = ["All", ...new Set(games.map((g) => g.league))];
  const shown = games.filter((g) => S.league === "All" || g.league === S.league);
  const edgeCount = {};
  S.edges.forEach((e) => { edgeCount[e.gameId] = (edgeCount[e.gameId] || 0) + 1; });
  let html = `<div class="bar">${leagues.map((l) => `<button class="chip ${l === S.league ? "on" : ""}" data-lg="${esc(l)}">${esc(l)}</button>`).join("")}
    <span class="spacer"></span><span class="muted small">Best price at your venues · green = beats the sharp fair</span></div>`;
  let day = "";
  for (const g of shown) {
    const d = fmtDay(g.commence);
    if (d !== day) { if (day) html += `</div>`; html += `<h2 class="day">${d}</h2><div class="grid">`; day = d; }
    const ml = mainLine(g, "h2h"), sp = mainLine(g, "spreads"), tot = mainLine(g, "totals");
    const live = new Date(g.commence) < new Date();
    html += `<a class="card" href="#/game/${g.id}">
      <div class="card-head"><span><span class="lg">${esc(g.league)}</span> · ${live ? "started" : fmtTime(g.commence)}</span>
        ${edgeCount[g.id] ? `<span class="badge">${edgeCount[g.id]} edge${edgeCount[g.id] > 1 ? "s" : ""}</span>` : ""}</div>
      <div class="mkt">
        <div></div><div class="h">Spread</div><div class="h">Total</div><div class="h">ML</div>
        <div class="team">${esc(g.away)}</div>
        ${cell(sp, g.away, sp ? signPt(-sp.point) : "")}${cell(tot, "Over", tot ? "o" + tot.point : "")}${cell(ml, g.away)}
        <div class="team">${esc(g.home)}</div>
        ${cell(sp, g.home, sp ? signPt(sp.point) : "")}${cell(tot, "Under", tot ? "u" + tot.point : "")}${cell(ml, g.home)}
      </div>
      <div class="foot"><span>Fair ML ${am(ml?.fairAm?.[g.away])} / ${am(ml?.fairAm?.[g.home])}</span><span>${ml?.nBooks ?? 0} books</span></div>
    </a>`;
  }
  appEl.innerHTML = html + (day ? `</div>` : "");
  appEl.querySelectorAll("[data-lg]").forEach((b) => b.onclick = () => { S.league = b.dataset.lg; renderBoard(); });
}

// ---------- game
function priceCell(q, o) {
  const p = q.prices[o];
  if (!p) return `<td class="muted">–</td>`;
  const shown = p.allInAm ?? p.am;
  return `<td class="${ecls(p.edge)}"><span class="px">${am(shown)}</span>${p.edge != null ? `<span class="ed">${pct(p.edge)}</span>` : ""}</td>`;
}

function bookTag(q) {
  if (q.venue) return `<span class="tag v">yours</span>`;
  if (q.kind === "sharp") return `<span class="tag s">sharp</span>`;
  if (q.kind === "exchange") return `<span class="tag">exch</span>`;
  return "";
}

function lineTable(line, labels, key) {
  const os = line.outcomes;
  const showAll = S.open.has("all:" + key);
  const hidden = line.quotes.filter((q) => q.kind === "other").length;
  let h = `<div class="scroll"><table><thead><tr><th>Book</th>${os.map((o) => `<th>${esc(labels[o] ?? o)}</th>`).join("")}</tr></thead><tbody>`;
  if (line.fair) h += `<tr class="fair"><td>Sharp fair <span class="muted small">(${line.fairSource}, w ${line.fairWeight})</span></td>${os.map((o) => `<td><span class="px">${am(line.fairAm[o])}</span><span class="ed">${fairPct(line.fair[o])}</span></td>`).join("")}</tr>`;
  for (const q of line.quotes) {
    if (q.kind === "other" && !showAll) continue;
    h += `<tr class="${q.stale ? "stale" : ""}"><td>${esc(q.name)}${bookTag(q)}${q.stale ? ` <span class="tag">stale</span>` : ""}</td>${os.map((o) => priceCell(q, o)).join("")}</tr>`;
  }
  h += `</tbody></table></div>`;
  if (hidden) h += `<button class="more-btn" data-more="${esc(key)}">${showAll ? "Hide" : "Show"} ${hidden} international books</button>`;
  return h;
}
function bindMore(redraw) {
  appEl.querySelectorAll("[data-more]").forEach((b) => b.onclick = () => {
    const k = "all:" + b.dataset.more; S.open.has(k) ? S.open.delete(k) : S.open.add(k); redraw();
  });
}

function renderGame(id, tab) {
  const g = S.games.get(id);
  if (!g) { appEl.innerHTML = `<div class="empty">Game not on the board. <a href="#/">Back</a></div>`; return; }
  let html = `<div class="game-head"><a class="back" href="#/">← Board</a>
    <h1>${esc(g.away)} @ ${esc(g.home)}</h1>
    <div class="muted">${esc(g.league)} · ${fmtDay(g.commence)} · ${fmtTime(g.commence)} PT</div></div>
    <div class="bar"><a class="chip ${tab === "lines" ? "on" : ""}" href="#/game/${id}/lines">Game lines</a>
    <a class="chip ${tab === "props" ? "on" : ""}" href="#/game/${id}/props">Player props</a>
    <span class="spacer"></span><span class="muted small">Price · edge vs sharp fair (fees included)</span></div>`;
  if (tab === "props") { appEl.innerHTML = html + `<div id="props"></div>`; return loadProps(g); }

  const order = { h2h: 0, spreads: 1, totals: 2 };
  const lines = [...g.lines].sort((a, b) => (order[a.market] - order[b.market]) || ((b.isMain ? 1 : 0) - (a.isMain ? 1 : 0)) || ((a.point ?? 0) - (b.point ?? 0)));
  for (const l of lines) {
    let title, labels = {};
    if (l.market === "h2h") title = "Moneyline";
    else if (l.market === "spreads") { title = `Spread ${g.home} ${signPt(l.point)}`; labels = { [g.away]: `${g.away} ${signPt(-l.point)}`, [g.home]: `${g.home} ${signPt(l.point)}` }; }
    else { title = `Total ${l.point}`; labels = { Over: `Over ${l.point}`, Under: `Under ${l.point}` }; }
    html += `<div class="panel"><h3><span>${esc(title)}${l.isMain && l.market !== "h2h" ? ` <span class="tag s">main</span>` : ""}</span><span class="muted">${l.nBooks} books</span></h3>${lineTable(l, labels, g.id + l.id)}</div>`;
  }
  appEl.innerHTML = html;
  bindMore(() => renderGame(id, tab));
}

function loadProps(g) {
  if (S.propGame !== g.id) {
    S.propSub?.(); S.props = new Map(); S.propGame = g.id; S.propMarket = null;
    S.propSub = onSnapshot(query(collection(db, "props"), where("gameId", "==", g.id)), (qs) => {
      qs.docChanges().forEach((ch) => {
        if (ch.type === "removed") S.props.delete(ch.doc.id); else S.props.set(ch.doc.id, JSON.parse(ch.doc.data().payload));
      });
      if (location.hash.startsWith(`#/game/${g.id}/props`)) renderProps(g);
    });
  }
  renderProps(g);
}

function renderProps(g) {
  const el = $("#props"); if (!el) return;
  const docs = [...S.props.values()];
  if (!docs.length) { el.innerHTML = `<div class="empty">No props pulled for this game yet.</div>`; return; }
  const markets = docs.map((d) => d.market).sort();
  if (!S.propMarket || !markets.includes(S.propMarket)) S.propMarket = markets[0];
  const d = docs.find((x) => x.market === S.propMarket);
  const byPlayer = new Map();
  d.rows.forEach((r) => { if (!byPlayer.has(r.player)) byPlayer.set(r.player, []); byPlayer.get(r.player).push(r); });
  let h = `<div class="bar">${markets.map((m) => `<button class="chip ${m === S.propMarket ? "on" : ""}" data-pm="${m}">${esc(mktName(m))}</button>`).join("")}</div>
    <div class="panel"><div class="scroll"><table><thead><tr><th>Player</th><th>Line</th><th>Fair O</th><th>Fair U</th><th>Proj</th><th>Best yours O</th><th>Best yours U</th><th>Pick'em</th></tr></thead><tbody>`;
  for (const [player, rows] of [...byPlayer.entries()].sort()) {
    rows.sort((a, b) => (b.isMain ? 1 : 0) - (a.isMain ? 1 : 0) || a.point - b.point);
    for (const r of rows) {
      const f = r.fair || r.modelFair;
      const bo = r.bestVenue?.Over || r.bestVenue?.Yes, bu = r.bestVenue?.Under || r.bestVenue?.No;
      const pk = (r.dfs || []).map((x) => x.side ? `<span class="${ecls(x.edge)}">${esc(x.name)} ${x.side[0]} ${(x.p * 100).toFixed(0)}%</span>` : esc(x.name)).join(" · ");
      const key = `${player}|${r.point}`;
      h += `<tr data-row="${esc(key)}" style="cursor:pointer"><td>${esc(player)}${r.isMain ? "" : ` <span class="tag">alt</span>`}</td>
        <td class="num">${r.point ?? "–"}</td>
        <td class="num">${f?.Over != null ? fairPct(f.Over) : f?.Yes != null ? fairPct(f.Yes) : "–"}${r.fair ? "" : f ? ` <span class="tag">model</span>` : ""}</td>
        <td class="num">${f?.Under != null ? fairPct(f.Under) : "–"}</td>
        <td class="num">${r.mean ?? "–"}</td>
        <td class="${ecls(bo?.edge)}">${bo ? `<span class="px">${am(bo.am)}</span><span class="ed">${esc(bo.name)} ${pct(bo.edge)}</span>` : "–"}</td>
        <td class="${ecls(bu?.edge)}">${bu ? `<span class="px">${am(bu.am)}</span><span class="ed">${esc(bu.name)} ${pct(bu.edge)}</span>` : "–"}</td>
        <td>${pk || "–"}</td></tr>`;
      if (S.open.has(key) && r.quotes?.length) {
        const os = r.outcomes || [];
        h += `<tr><td colspan="8" style="padding:0"><div class="qlist">${r.quotes.map((q) =>
          `<div>${esc(q.name)} ${os.map((o) => q.prices[o] ? `<span class="${ecls(q.prices[o].edge)}">${o[0]} ${am(q.prices[o].allInAm ?? q.prices[o].am)}${q.prices[o].edge != null ? ` (${pct(q.prices[o].edge)})` : ""}</span>` : "").join(" ")}</div>`).join("")}</div></td></tr>`;
      }
    }
  }
  el.innerHTML = h + `</tbody></table></div><div class="note">Tap a row for every book. "model" = fair moved from the main line with a stat-spread model; pick'em % is our chance of that side vs ${(57.74).toFixed(1)}% to break even on a 2-pick.</div></div>`;
  el.querySelectorAll("[data-pm]").forEach((b) => b.onclick = () => { S.propMarket = b.dataset.pm; renderProps(g); });
  el.querySelectorAll("[data-row]").forEach((tr) => tr.onclick = () => {
    const k = tr.dataset.row; S.open.has(k) ? S.open.delete(k) : S.open.add(k); renderProps(g);
  });
}

// ---------- edges
function renderEdges() {
  const items = S.edges.filter((e) => (S.edgeLeague === "All" || e.league === S.edgeLeague) && (S.pickem || !e.pickem));
  const leagues = ["All", ...new Set(S.edges.map((e) => e.league))];
  let h = `<div class="bar">${leagues.map((l) => `<button class="chip ${l === S.edgeLeague ? "on" : ""}" data-el="${esc(l)}">${esc(l)}</button>`).join("")}
    <button class="chip ${S.pickem ? "on" : ""}" id="pk">Pick'em</button><span class="spacer"></span>
    <span class="muted small">Prices at your venues that beat the sharp no-vig fair. Market signal only, not picks.</span></div>`;
  if (!items.length) { appEl.innerHTML = h + `<div class="empty">No edges right now.</div>`; return bindEdges(); }
  h += `<div class="panel"><div class="scroll"><table><thead><tr><th>Bet</th><th>Game</th><th>Start</th><th>Venue</th><th>Price</th><th>Fair</th><th>Edge</th><th>EV</th></tr></thead><tbody>`;
  for (const e of items) {
    h += `<tr><td><a href="#/game/${e.gameId}/${e.market && !["h2h", "spreads", "totals"].includes(e.market) ? "props" : "lines"}">${esc(e.label)}</a></td>
      <td class="muted">${esc(e.matchup)}</td><td class="muted">${fmtTime(e.commence)}</td>
      <td>${esc(e.bookName)}</td><td class="num">${e.pickem ? "pick'em" : am(e.am)}</td>
      <td class="num">${e.fairAm != null ? am(e.fairAm) : fairPct(e.fair)}</td>
      <td class="${ecls(e.edge)}"><span class="px">${pct(e.edge)}</span></td><td class="num">${e.ev != null ? pct(e.ev) : "–"}</td></tr>`;
  }
  appEl.innerHTML = h + `</tbody></table></div></div>`;
  bindEdges();
}
function bindEdges() {
  appEl.querySelectorAll("[data-el]").forEach((b) => b.onclick = () => { S.edgeLeague = b.dataset.el; renderEdges(); });
  const pk = $("#pk"); if (pk) pk.onclick = () => { S.pickem = !S.pickem; renderEdges(); };
}
