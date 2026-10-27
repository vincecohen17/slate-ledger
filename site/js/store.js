// Live data from Firestore. Engine docs carry their body as a JSON string in `payload`.
import { db, doc, collection, onSnapshot, query, where, setDoc, deleteDoc } from "./fb.js";

export const S = {
  games: new Map(), status: null, edges: [], reports: null,
  picks: new Map(), bets: new Map(), promos: new Map(), matchups: new Map(), previews: new Map(),
  live: null, liveGames: new Map(),   // in-play: board/live summary + live/{gameId} docs
  finals: [], finalDocs: new Map(),   // finished games: board/finals list + finals/{key} loaded when opened
  props: new Map(),            // docId -> prop doc
  propsLoadedFor: new Set(),   // gameIds subscribed
  allProps: false,
  denied: false,
};
const subs = [];
const listeners = new Set();
let timer;
export const onChange = (fn) => listeners.add(fn);
const changed = () => { clearTimeout(timer); timer = setTimeout(() => listeners.forEach((f) => f()), 80); };

const parse = (d) => { const x = d.data(); return x.payload ? JSON.parse(x.payload) : x; };
function onErr(e) { if (e.code === "permission-denied") { S.denied = true; changed(); } else console.error(e); }

function watchCol(name, map, mapFn = parse) {
  subs.push(onSnapshot(collection(db, name), (qs) => {
    qs.docChanges().forEach((c) => c.type === "removed" ? map.delete(c.doc.id) : map.set(c.doc.id, { id: c.doc.id, ...mapFn(c.doc) }));
    changed();
  }, onErr));
}

export function start() {
  stop();
  subs.push(onSnapshot(doc(db, "board", "status"), (d) => { S.status = d.exists() ? d.data() : null; changed(); }, onErr));
  subs.push(onSnapshot(doc(db, "board", "edges"), (d) => { S.edges = d.exists() ? parse(d).items : []; changed(); }, onErr));
  subs.push(onSnapshot(doc(db, "board", "reports"), (d) => { S.reports = d.exists() ? parse(d) : null; changed(); }, onErr));
  subs.push(onSnapshot(doc(db, "board", "live"), (d) => { S.live = d.exists() ? parse(d) : null; changed(); }, onErr));
  subs.push(onSnapshot(doc(db, "board", "finals"), (d) => { S.finals = d.exists() ? parse(d).games || [] : []; changed(); }, onErr));
  watchCol("live", S.liveGames);
  watchCol("games", S.games);
  watchCol("picks", S.picks);
  watchCol("matchups", S.matchups);
  watchCol("previews", S.previews);
  watchCol("bets", S.bets, (d) => d.data());
  watchCol("promos", S.promos, (d) => d.data());
}

export function stop() { subs.splice(0).forEach((u) => u()); finalSubs.clear(); S.games.clear(); S.props.clear(); S.matchups.clear(); S.propsLoadedFor.clear(); S.allProps = false; }

const finalSubs = new Set();
export function loadFinal(key) {
  if (!key || finalSubs.has(key)) return;
  finalSubs.add(key);
  subs.push(onSnapshot(doc(db, "finals", key), (d) => { if (d.exists()) S.finalDocs.set(key, parse(d)); changed(); }, onErr));
}

export function loadProps(gameId) {
  if (S.allProps || S.propsLoadedFor.has(gameId)) return;
  S.propsLoadedFor.add(gameId);
  subs.push(onSnapshot(query(collection(db, "props"), where("gameId", "==", gameId)), (qs) => {
    qs.docChanges().forEach((c) => c.type === "removed" ? S.props.delete(c.doc.id) : S.props.set(c.doc.id, parse(c.doc)));
    changed();
  }, onErr));
}

export function loadAllProps() {
  if (S.allProps) return;
  S.allProps = true;
  subs.push(onSnapshot(collection(db, "props"), (qs) => {
    qs.docChanges().forEach((c) => c.type === "removed" ? S.props.delete(c.doc.id) : S.props.set(c.doc.id, parse(c.doc)));
    changed();
  }, onErr));
}

export const propsFor = (gameId) => [...S.props.values()].filter((p) => p.id === gameId);

export const saveBet = (b) => setDoc(doc(db, "bets", b.id), JSON.parse(JSON.stringify(b)));
export const deleteBet = (id) => deleteDoc(doc(db, "bets", id));
export const savePromo = (p) => setDoc(doc(db, "promos", p.id), JSON.parse(JSON.stringify(p)));
export const deletePromo = (id) => deleteDoc(doc(db, "promos", id));

// ---------- presence: tells the engine the site is open, so it refreshes prices only while you use it
let beat = null;
// Instant refresh (Vince's call): he keeps a GitHub token that can only run this repo's workflows in the Firestore doc
// private/dispatch (field "token"), which only his signed-in account can read. With it, the site starts an engine run
// when it opens, every 5 minutes while it stays open, and right away on the refresh button. Without it, nothing changes.
let dispatchToken = null, lastDispatch = 0;
onSnapshot(doc(db, "private", "dispatch"), (d) => { dispatchToken = d.exists() ? (d.data().token || "").trim() : null; S.instant = !!dispatchToken; if (dispatchToken) kick(); changed(); },
  () => { S.instant = false; });
function dispatch(mode) {
  return fetch("https://api.github.com/repos/vincecohen17/slate-ledger/actions/workflows/pull.yml/dispatches", {
    method: "POST", headers: { Authorization: `Bearer ${dispatchToken}`, Accept: "application/vnd.github+json" },
    body: JSON.stringify({ ref: "main", inputs: { mode } }),
  }).then((r) => { S.dispatch = r.ok ? "ok" : `GitHub said ${r.status}`; changed(); }).catch(() => { S.dispatch = "could not reach GitHub"; changed(); });
}
function kick(force = false) {
  if (!dispatchToken || document.visibilityState !== "visible" || (!force && Date.now() - lastDispatch < 290000)) return;
  lastDispatch = Date.now();
  dispatch(force ? "refresh" : "auto");
}
// live: while the Live screen or a game in play is open, ask for in-play prices every minute
let view = { view: "board", game: null }, lastLive = 0, liveTimer = null;
function liveKick() {
  if (!dispatchToken || document.visibilityState !== "visible" || !view.live || Date.now() - lastLive < 55000) return;
  lastLive = Date.now();
  setDoc(doc(db, "presence", "owner"), { at: new Date().toISOString(), view: view.view, game: view.game }, { merge: true })
    .then(() => dispatch("live")).catch(() => {});
}
export function setView(v, game, isLive) {
  const was = view.live;
  view = { view: v, game: game || null, live: !!isLive };
  if (isLive && !was) { liveKick(); clearInterval(liveTimer); liveTimer = setInterval(liveKick, 60000); }
  if (!isLive && was) { clearInterval(liveTimer); liveTimer = null; }
}
const ping = (extra = {}) => setDoc(doc(db, "presence", "owner"), { at: new Date().toISOString(), view: view.view, game: view.game, ...extra }, { merge: true })
  .then(() => kick(!!extra.force)).catch(() => {});
export function startPresence() {
  const on = () => { if (document.visibilityState === "visible") { ping(); clearInterval(beat); beat = setInterval(ping, 120000); } else { clearInterval(beat); beat = null; } };
  document.addEventListener("visibilitychange", on);
  on();
}
export function stopPresence() { clearInterval(beat); beat = null; }
export function requestRefresh() { S.refreshAsked = new Date().toISOString(); return ping({ force: S.refreshAsked }); }
