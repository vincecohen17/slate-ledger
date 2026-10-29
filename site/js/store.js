// Live data from Firestore. Engine docs carry their body as a JSON string in `payload`.
import { db, rtdb, doc, collection, onSnapshot, query, where, setDoc, deleteDoc, rref, onValue, rupdate } from "./fb.js";

export const S = {
  games: new Map(), status: null, edges: [], reports: null,
  picks: new Map(), bets: new Map(), promos: new Map(), wallet: new Map(), matchups: new Map(), previews: new Map(),
  live: null, liveGames: new Map(),   // in-play: board/live summary + live/{gameId} docs
  finals: [], finalDocs: new Map(),   // finished games: board/finals list + finals/{key} loaded when opened
  props: new Map(),            // docId -> prop doc
  propsLoadedFor: new Set(),   // gameIds subscribed
  allProps: false,
  denied: false,
  settings: { bankroll: 0, kelly: 0.25, maxPct: 0.05, unit: 0 }, opps: [],
};
const subs = [];
const listeners = new Set();
let timer;
export const onChange = (fn) => listeners.add(fn);
const changed = () => { clearTimeout(timer); timer = setTimeout(() => listeners.forEach((f) => f()), 80); };

const parse = (d) => { const x = d.data(); return x.payload ? JSON.parse(x.payload) : x; };
function onErr(e) { if (/permission.denied/i.test(e?.code || e?.message || "")) { S.denied = true; changed(); } else console.error(e); }

function watchCol(name, map, mapFn = parse, after = changed) {
  subs.push(onSnapshot(collection(db, name), (qs) => {
    qs.docChanges().forEach((c) => c.type === "removed" ? map.delete(c.doc.id) : map.set(c.doc.id, { id: c.doc.id, ...mapFn(c.doc) }));
    after();
  }, onErr));
}

// ---------- the Realtime Database: what the engine publishes. Values are strings: JSON, or "z:" + base64 deflated JSON
// (6-7x smaller; the free plan allows 10 GB of downloads a month). Only pieces that changed travel.
//   pre/status, pre/edges, pre/reports; pre/g/<id> (a game with its main lines); pre/ga/<id> (its alternate lines, on
//   demand); pre/p/<id>/<market> (main prop rows); pre/pa/<id>/<market> (alternate rows, on demand); pre/mu/<id>
//   (matchups); pre/fin/board, pre/fin/d/<key> (finished games)
//   live/board, live/g/<id>/{m, e, l, p/<market>} (in play)
const zc = new Map();
async function dec(path, s) {
  const c = zc.get(path);
  if (c && c.s === s) return c.v;
  let v;
  if (typeof s === "string" && s.startsWith("z:")) {
    const bin = Uint8Array.from(atob(s.slice(2)), (ch) => ch.charCodeAt(0));
    v = JSON.parse(await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream("deflate"))).text());
  } else v = typeof s === "string" ? JSON.parse(s) : s;
  zc.set(path, { s, v });
  return v;
}
// one listener; the newest snapshot wins even if an older one finishes decoding later
function listen(path, fn) {
  let seq = 0;
  subs.push(onValue(rref(rtdb, path), async (sn) => {
    const my = ++seq, val = sn.val();
    try { const out = await fn(val); if (my === seq && out !== false) changed(); } catch (e) { console.error("rtdb", path, e); }
    return my;
  }, onErr));
  return () => seq;
}
const ents = (o) => Object.entries(o || {});

const PRE = { games: new Map(), altLines: new Map(), propDocs: new Map(), altRows: new Map(), altGames: new Set(), allAlt: false };
function watchPre() {
  listen("pre/status", async (v) => { S.status = v ? await dec("pre/status", v) : null; });
  listen("pre/edges", async (v) => { S.edges = v ? (await dec("pre/edges", v)).items : []; });
  listen("pre/reports", async (v) => { S.reports = v ? await dec("pre/reports", v) : null; });
  listen("pre/fin/board", async (v) => { S.finals = v ? (await dec("pre/fin/board", v)).games || [] : []; });
  listen("pre/mu", async (v) => {
    const m = new Map();
    await Promise.all(ents(v).map(async ([gid, x]) => m.set(gid, { id: gid, ...(await dec(`mu/${gid}`, x)) })));
    S.matchups = m;
  });
  listen("pre/g", async (v) => {
    const m = new Map();
    await Promise.all(ents(v).map(async ([gid, x]) => m.set(gid, await dec(`g/${gid}`, x))));
    PRE.games = m; buildGames();
  });
}
function buildGames() {
  const games = new Map();
  for (const [gid, g] of PRE.games) {
    const alt = PRE.altLines.get(gid);
    games.set(gid, { ...g, id: gid, lines: alt ? [...g.lines, ...alt].sort((a, b) => a._i - b._i) : g.lines });
  }
  S.games = games;
}
function buildProps() {
  const out = new Map();
  for (const [id, d] of PRE.propDocs) {
    const alt = PRE.altRows.get(id);
    out.set(id, alt ? { ...d, rows: [...d.rows, ...alt].sort((a, b) => a._i - b._i) } : d);
  }
  S.props = out;
}
// main prop rows for one game (pre/p/<id>) or every game (pre/p)
function rtProps(path, gidFixed) {
  const mine = new Set();
  listen(path, async (v) => {
    const seen = new Set(), jobs = [];
    for (const [gid, mks] of ents(gidFixed ? { [gidFixed]: v } : v))
      for (const [mk, x] of ents(mks)) {
        const id = `${gid}__${mk}`; seen.add(id);
        jobs.push(dec(`p/${id}`, x).then((d) => PRE.propDocs.set(id, d)));
      }
    await Promise.all(jobs);
    for (const id of mine) if (!seen.has(id)) PRE.propDocs.delete(id);
    mine.clear(); seen.forEach((x) => mine.add(x));
    buildProps();
  });
}
// alternate lines and prop rows for one game, or every game (the Lab's switch)
function loadAlt(gid) {
  if (PRE.allAlt || PRE.altGames.has(gid)) return;
  PRE.altGames.add(gid);
  listen(`pre/ga/${gid}`, async (v) => { if (v) PRE.altLines.set(gid, await dec(`ga/${gid}`, v)); else PRE.altLines.delete(gid); buildGames(); });
  listen(`pre/pa/${gid}`, async (v) => {
    const got = await Promise.all(ents(v).map(async ([mk, x]) => [`${gid}__${mk}`, await dec(`pa/${gid}__${mk}`, x)]));
    for (const k of [...PRE.altRows.keys()]) if (k.startsWith(gid + "__")) PRE.altRows.delete(k);
    got.forEach(([k, rows]) => PRE.altRows.set(k, rows));
    buildProps();
  });
}
export function loadAllAlt() {
  if (PRE.allAlt) return;
  PRE.allAlt = true; S.allAlt = true;
  listen("pre/ga", async (v) => {
    PRE.altLines = new Map(await Promise.all(ents(v).map(async ([gid, x]) => [gid, await dec(`ga/${gid}`, x)])));
    buildGames();
  });
  listen("pre/pa", async (v) => {
    const jobs = [];
    for (const [gid, mks] of ents(v)) for (const [mk, x] of ents(mks)) jobs.push(dec(`pa/${gid}__${mk}`, x).then((rows) => [`${gid}__${mk}`, rows]));
    PRE.altRows = new Map(await Promise.all(jobs));
    buildProps();
  });
}

// ---------- in play (live/), with the old Firestore copies as a fallback: whichever was updated last wins
const LV = { fsBoard: null, fsGames: new Map(), rtBoard: null, rtGames: new Map() };
function watchLive() {
  listen("live", async (t) => {
    t = t || {};
    LV.rtBoard = t.board ? await dec("live/board", t.board) : null;
    const games = new Map();
    await Promise.all(ents(t.g).map(async ([gid, g]) => {
      if (!g.m) return;
      const [m, lines, edges, props] = await Promise.all([dec(`lv/${gid}/m`, g.m), g.l ? dec(`lv/${gid}/l`, g.l) : [], g.e ? dec(`lv/${gid}/e`, g.e) : [],
        Promise.all(ents(g.p).map(([mk, x]) => dec(`lv/${gid}/p/${mk}`, x)))]);
      games.set(gid, { ...m, id: gid, lines, props, edges });
    }));
    LV.rtGames = games;
    pickLive();
  });
}
function pickLive() {
  const rt = LV.rtBoard?.updated || "", fs = LV.fsBoard?.updated || "";
  const useRt = rt && rt >= fs;
  S.live = useRt ? LV.rtBoard : LV.fsBoard;
  S.liveGames = useRt ? LV.rtGames : LV.fsGames;
}

export function start() {
  stop();
  watchPre();
  watchLive();
  // Firestore: what Vince writes (bets, promos) and our picks and previews
  watchCol("picks", S.picks);
  watchCol("previews", S.previews);
  watchCol("bets", S.bets, (d) => d.data());
  watchCol("promos", S.promos, (d) => d.data());
  watchCol("wallet", S.wallet, (d) => d.data());
  subs.push(onSnapshot(doc(db, "settings", "owner"), (d) => { S.settings = { ...SETTINGS0, ...(d.exists() ? d.data() : {}) }; changed(); }, onErr));
  listen("pre/opps", async (v) => { S.opps = v ? (await dec("pre/opps", v)).items || [] : []; });
  listen("pre/pickslive", async (v) => { S.picksLive = v ? await dec("pre/pickslive", v) : null; });
}

export function stop() {
  subs.splice(0).forEach((u) => u()); zc.clear(); finalSubs.clear();
  S.games = new Map(); S.props = new Map(); S.matchups = new Map(); S.propsLoadedFor.clear(); S.allProps = false; S.allAlt = false;
  Object.assign(PRE, { games: new Map(), altLines: new Map(), propDocs: new Map(), altRows: new Map(), altGames: new Set(), allAlt: false });
}

const finalSubs = new Set();
export function loadFinal(key) {
  if (!key || finalSubs.has(key)) return;
  finalSubs.add(key);
  listen(`pre/fin/d/${key}`, async (v) => { if (v) S.finalDocs.set(key, await dec(`fin/${key}`, v)); });
}

export function loadProps(gameId) {
  if (!gameId) return;
  loadAlt(gameId);
  if (S.allProps || S.propsLoadedFor.has(gameId)) return;
  S.propsLoadedFor.add(gameId);
  rtProps(`pre/p/${gameId}`, gameId);
}

// every game's main prop rows (Promos, the Lab); alternates come with loadAllAlt
export function loadAllProps() {
  if (S.allProps) return;
  S.allProps = true;
  rtProps("pre/p", null);
}

export const propsFor = (gameId) => [...S.props.values()].filter((p) => p.id === gameId);

// saving a bet also nudges the engine (presence.betsAt) so a new live bet is tracked on the next live pull
// bankroll and sizing (Ledger > Analysis): one doc, read everywhere stakes are suggested
const SETTINGS0 = { bankroll: 0, kelly: 0.25, maxPct: 0.05, unit: 0 };
export const saveSettings = (x) => setDoc(doc(db, "settings", "owner"), { ...S.settings, ...x });

// the sports a bet is on: each leg's league from its game (board, live feed, finished games), else the engine's backfill
// (bet.leagues, read from the pre-game snapshot or the names in the label), else the bet's own league
const NL = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
export function leaguesOf(b) {
  if (b.legsB) return [...new Set(b.legsB.flatMap(leaguesOf))];                 // an arb or middle pair
  const out = new Set();
  for (const l of (b.legs && b.legs.length ? b.legs : [b])) {
    const gid = l.gameId || (b.legs?.length > 1 ? null : b.gameId);
    const lg = l.league || (gid && (S.games.get(gid)?.league || S.liveGames.get(gid)?.league || (S.finals || []).find((x) => x.gameId === gid)?.league));
    if (lg) { out.add(lg); continue; }
    // no game: a team name in the leg's label
    const lab = NL(l.label || b.label);
    if (lab) for (const g of S.games.values()) if ([g.home, g.away].some((t) => t && lab.includes(NL(t)))) { out.add(g.league); break; }
  }
  if (b.leagues?.length && (!out.size || b.leagues.length > out.size)) b.leagues.forEach((x) => out.add(x));
  if (!out.size && b.league) out.add(b.league);
  return [...out];
}
// the rows a bet belongs to in a by-sport table: its sport, or each sport of a multi-sport parlay plus "Multi-sport"
export function sportKeys(b) {
  const L = leaguesOf(b);
  return !L.length ? ["Unknown"] : L.length === 1 ? L : ["Multi-sport", ...L];
}

export const saveBet = (b) => setDoc(doc(db, "bets", b.id), JSON.parse(JSON.stringify(b.leagues?.length ? b : { ...b, leagues: leaguesOf(b) })))
  .then((r) => { rupdate(rref(rtdb, "presence"), { betsAt: new Date().toISOString() }).catch(() => {}); return r; });
export const deleteBet = (id) => deleteDoc(doc(db, "bets", id));
export const savePromo = (p) => setDoc(doc(db, "promos", p.id), JSON.parse(JSON.stringify(p)));
export const deletePromo = (id) => deleteDoc(doc(db, "promos", id));
// wallet entries: balance check-ins, deposits, withdrawals, bonus credits (see wallet.js)
export const saveWallet = (w) => setDoc(doc(db, "wallet", w.id), JSON.parse(JSON.stringify(w)));
export const deleteWallet = (id) => deleteDoc(doc(db, "wallet", id));

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
  beatOut({ at: new Date().toISOString(), view: view.view, game: view.game }).finally(() => dispatch("live"));
}
export function setView(v, game, isLive) {
  const was = view.live;
  view = { view: v, game: game || null, live: !!isLive };
  if (isLive && !was) { liveKick(); clearInterval(liveTimer); liveTimer = setInterval(liveKick, 60000); }
  if (!isLive && was) { clearInterval(liveTimer); liveTimer = null; }
}
// Firebase's free daily write allowance can run out; the engine is still started directly so the data repo keeps
// updating, and the header says why the screen isn't changing
function fsFail(e) { if (/resource-exhausted|quota/i.test(`${e?.code} ${e?.message}`)) { if (!S.fsLimit) { S.fsLimit = true; changed(); } } }
function fsOk() { if (S.fsLimit) { S.fsLimit = false; changed(); } }
// the heartbeat goes to the Realtime Database (free to write as often as needed); Firestore only if that fails
const beatOut = (x) => rupdate(rref(rtdb, "presence"), x)
  .catch(() => setDoc(doc(db, "presence", "owner"), x, { merge: true }).then(() => fsOk(), fsFail));
const ping = (extra = {}) => beatOut({ at: new Date().toISOString(), view: view.view, game: view.game, ...extra }).finally(() => kick(!!extra.force));
export function startPresence() {
  const on = () => { if (document.visibilityState === "visible") { ping(); clearInterval(beat); beat = setInterval(ping, 120000); } else { clearInterval(beat); beat = null; } };
  document.addEventListener("visibilitychange", on);
  on();
}
export function stopPresence() { clearInterval(beat); beat = null; }
export function requestRefresh() { S.refreshAsked = new Date().toISOString(); return ping({ force: S.refreshAsked }); }
