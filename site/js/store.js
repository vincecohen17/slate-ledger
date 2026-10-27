// Live data from Firestore. Engine docs carry their body as a JSON string in `payload`.
import { db, doc, collection, onSnapshot, query, where, setDoc, deleteDoc } from "./fb.js";

export const S = {
  games: new Map(), status: null, edges: [], reports: null,
  picks: new Map(), bets: new Map(), promos: new Map(), matchups: new Map(), previews: new Map(),
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
  watchCol("games", S.games);
  watchCol("picks", S.picks);
  watchCol("matchups", S.matchups);
  watchCol("previews", S.previews);
  watchCol("bets", S.bets, (d) => d.data());
  watchCol("promos", S.promos, (d) => d.data());
}

export function stop() { subs.splice(0).forEach((u) => u()); S.games.clear(); S.props.clear(); S.matchups.clear(); S.propsLoadedFor.clear(); S.allProps = false; }

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
const ping = (extra = {}) => setDoc(doc(db, "presence", "owner"), { at: new Date().toISOString(), ...extra }, { merge: true }).catch(() => {});
export function startPresence() {
  const on = () => { if (document.visibilityState === "visible") { ping(); clearInterval(beat); beat = setInterval(ping, 120000); } else { clearInterval(beat); beat = null; } };
  document.addEventListener("visibilitychange", on);
  on();
}
export function stopPresence() { clearInterval(beat); beat = null; }
export function requestRefresh() { S.refreshAsked = new Date().toISOString(); return ping({ force: S.refreshAsked }); }
