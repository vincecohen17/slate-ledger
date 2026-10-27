import { auth, GoogleAuthProvider, signInWithPopup, signInWithRedirect, onAuthStateChanged, signOut } from "./fb.js";
import { S, start, stop, onChange, startPresence, stopPresence, requestRefresh } from "./store.js";
import { $, $$, esc, ago } from "./util.js";
import { renderBoard } from "./views/board.js";
import { renderGame } from "./views/game.js";
import { renderEdges } from "./views/edges.js";
import { renderPicks } from "./views/picks.js";
import { renderLab } from "./views/lab.js";
import { renderPromos } from "./views/promos.js";
import { renderLedger } from "./views/ledger.js";
import { renderReports } from "./views/reports.js";

const app = $("#app");
const ROUTES = { "": renderBoard, edges: renderEdges, picks: renderPicks, lab: renderLab, promos: renderPromos, ledger: renderLedger, reports: renderReports };

async function doSignIn() {
  const p = new GoogleAuthProvider();
  try { await signInWithPopup(auth, p); }
  catch (e) {
    if (String(e.code).includes("popup")) await signInWithRedirect(auth, p);
    else { const m = $("#signin-msg"); if (m) m.textContent = e.message; }
  }
}

function signinView() {
  $("#nav").hidden = true; $("#status").innerHTML = "";
  app.innerHTML = `<section class="signin"><h1>Slate<span style="color:var(--accent)">·</span>Ledger</h1>
    <p class="muted">Every line, every venue, priced against the sharp market.</p>
    <button class="btn" id="si">Sign in with Google</button><p class="muted small" id="signin-msg"></p></section>`;
  $("#si").onclick = doSignIn;
}

function deniedView() {
  app.innerHTML = `<section class="signin"><h1>Not this account</h1><p class="muted">This board only opens for its owner.</p>
    <button class="btn ghost" id="so">Sign out</button></section>`;
  $("#so").onclick = () => signOut(auth);
}

function renderStatus() {
  const s = S.status;
  if (!s) { $("#status").innerHTML = `<span class="muted">waiting for first pull</span>`; return; }
  const old = Date.now() - new Date(s.updated) > 30 * 60000;
  const waiting = S.refreshAsked && S.refreshAsked > s.updated && Date.now() - new Date(S.refreshAsked) < 15 * 60000;
  $("#status").innerHTML = `<span title="Prices update every few minutes while the site is open"><i class="dot ${old ? "old" : ""}"></i>prices ${ago(s.updated)}</span>
    <button class="btn sm ghost" id="rf" title="${S.instant ? "Pull fresh prices now (about a minute)" : "Ask for fresh prices now (next engine run, usually within 5 minutes)"}" ${waiting ? "disabled" : ""}>${waiting ? (S.instant ? "refreshing…" : "refresh queued") : "↻ refresh"}</button>
    ${S.instant ? `<span class="hide-sm ${S.dispatch && S.dispatch !== "ok" ? "neg" : "pos"}" title="${S.dispatch && S.dispatch !== "ok" ? "Instant refresh problem: " + S.dispatch : "Instant refresh is on: opening the site starts a price check"}">⚡ ${S.dispatch && S.dispatch !== "ok" ? "check token" : "instant"}</span>` : ""}
    <span class="hide-sm">${s.plan === "free" || s.mode === "mock" ? "free plan · " : ""}${s.creditsRemaining ?? "?"} credits</span>
    <button class="btn sm ghost hide-sm" id="so2" title="Sign out">Sign out</button>`;
  const so = $("#so2"); if (so) so.onclick = () => signOut(auth);
  const rf = $("#rf"); if (rf) rf.onclick = async () => { await requestRefresh(); renderStatus(); };
}

let lastRoute = "", scrollKeep = {};
function route() {
  if (!auth.currentUser) return;
  if (S.denied) return deniedView();
  renderStatus();
  const h = (location.hash || "#/").slice(2);
  const [base, a, b] = h.split("/");
  $$("#nav a").forEach((x) => x.classList.toggle("on", x.dataset.r === (base === "game" ? "" : base)));
  const key = h;
  if (key !== lastRoute) { scrollKeep[lastRoute] = scrollY; }
  // keep focus/scroll when live data re-renders the same screen
  const y = key === lastRoute ? scrollY : (scrollKeep[key] || 0);
  if (base === "game") renderGame(app, a, b || "overview");
  else (ROUTES[base] || renderBoard)(app);
  if (key !== lastRoute) scrollTo(0, y); 
  lastRoute = key;
}

window.addEventListener("hashchange", route);
onChange(() => {
  // do not re-render while the user is typing in a field
  const ae = document.activeElement;
  if (ae && ["INPUT", "TEXTAREA", "SELECT"].includes(ae.tagName) && app.contains(ae)) { renderStatus(); return; }
  route();
});

onAuthStateChanged(auth, (user) => {
  if (!user) { stop(); stopPresence(); return signinView(); }
  $("#nav").hidden = false;
  start();
  startPresence();
  route();
});
