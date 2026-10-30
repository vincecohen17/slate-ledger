// Formatting + small visual components shared by every screen.
export const TZ = "America/Los_Angeles";
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const fmtTime = (iso) => new Date(iso).toLocaleTimeString("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
export const fmtDay = (iso) => new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, weekday: "long", month: "short", day: "numeric" });
export const fmtShortDay = (iso) => new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, month: "short", day: "numeric" });
export const todayPT = () => new Date().toLocaleDateString("en-CA", { timeZone: TZ });
export const am = (x) => x == null || isNaN(x) ? "–" : (x > 0 ? `+${Math.round(x)}` : `${Math.round(x)}`);
export const pct = (x, d = 1) => x == null || isNaN(x) ? "–" : `${x > 0 ? "+" : ""}${(x * 100).toFixed(d)}%`;
export const prob = (x, d = 0) => x == null || isNaN(x) ? "–" : `${(x * 100).toFixed(d)}%`;
export const money = (x) => x == null || isNaN(x) ? "–" : `${x < 0 ? "-" : ""}$${Math.abs(x).toFixed(2)}`;
export const ago = (iso) => { const m = Math.round((Date.now() - new Date(iso)) / 60000); return m < 1 ? "just now" : m < 60 ? `${m}m ago` : m < 1440 ? `${Math.round(m / 60)}h ago` : `${Math.round(m / 1440)}d ago`; };
export const signPt = (p) => p == null ? "" : p > 0 ? `+${p}` : `${p}`;
export const mktName = (m) => (m || "").replace(/^(player|batter|pitcher)_/, "").replace(/_/g, " ");
export const edgeCls = (e) => e == null ? "" : e >= 0.005 ? "pos" : e <= -0.04 ? "neg" : "";

// odds conversions
export const decFromAm = (a) => a > 0 ? 1 + a / 100 : 1 + 100 / -a;
export const amFromDec = (d) => d >= 2 ? Math.round((d - 1) * 100) : Math.round(-100 / (d - 1));
export const probFromAm = (a) => 1 / decFromAm(a);
export const amFromProb = (p) => p <= 0 || p >= 1 ? null : p < 0.5 ? Math.round(100 * (1 - p) / p) : Math.round(-100 * p / (1 - p));

// ---------- visual atoms
const initials = (name) => (name || "?").split(/\s+/).filter(Boolean).slice(-1)[0].slice(0, 3).toUpperCase();

export function teamLogo(ref, name, size = 36) {
  const color = ref?.color || "var(--panel-3)";
  const ab = esc(ref?.abbr || initials(name));
  if (ref?.logo) {
    return `<span class="logo img" style="width:${size}px;height:${size}px"><img src="${esc(ref.logo)}" alt="${esc(name)}" loading="lazy"
      onerror="this.parentNode.classList.remove('img');this.parentNode.style.background='${esc(color)}';this.parentNode.textContent='${ab}'"></span>`;
  }
  return `<span class="logo" style="width:${size}px;height:${size}px;background:${esc(color)};font-size:${Math.max(9, size / 3.4)}px">${ab}</span>`;
}

export function headshot(row, size = 48) {
  const name = row.player || "";
  const ini = esc(name.split(" ").map((w) => w[0]).join("").slice(0, 2));
  if (row.headshot) {
    return `<span class="head" style="width:${size}px;height:${size}px"><img src="${esc(row.headshot)}" alt="" loading="lazy"
      onerror="this.parentNode.textContent='${ini}'"></span>`;
  }
  return `<span class="head" style="width:${size}px;height:${size}px">${ini}</span>`;
}

// ---------- books: logo from the book's own site icon, brand-colored monogram when there is none
export const BOOKS = {
  pinnacle: { d: "pinnacle.com", c: "#0b2a4a", m: "PIN" }, betfair_ex_eu: { d: "betfair.com", c: "#f6b908", m: "BF" },
  matchbook: { d: "matchbook.com", c: "#1d1d1d", m: "MB" }, novig: { d: "novig.us", c: "#5b3df5", m: "NV" },
  prophetx: { d: "prophetx.co", c: "#0d1b2a", m: "PX" }, kalshi: { d: "kalshi.com", c: "#00b386", m: "K" },
  polymarket_us: { d: "polymarket.com", c: "#2d6cdf", m: "PM" }, polymarket: { d: "polymarket.com", c: "#2d6cdf", m: "PM" },
  betopenly: { d: "betopenly.com", c: "#333", m: "BO" }, limitless: { d: "limitless.exchange", c: "#222", m: "LX" },
  lowvig: { d: "lowvig.ag", c: "#1a4d8f", m: "LV" }, betonlineag: { d: "betonline.ag", c: "#c8102e", m: "BOL" },
  circasports: { d: "circasports.com", c: "#111", m: "CIR" },
  draftkings: { d: "draftkings.com", c: "#53d337", m: "DK", twin: "DraftKings" }, fanduel: { d: "fanduel.com", c: "#1493ff", m: "FD", twin: "FanDuel" },
  fliff: { d: "getfliff.com", c: "#7b2ff7", m: "FL" }, courtside: { c: "#e4572e", m: "CS" }, bovada: { d: "bovada.lv", c: "#cc0000", m: "BOV" },
  betmgm: { d: "betmgm.com", c: "#b9975b", m: "MGM" }, williamhill_us: { d: "caesars.com", c: "#0f3b2e", m: "CZR" },
  betrivers: { d: "betrivers.com", c: "#123f78", m: "BR" }, espnbet: { d: "thescore.bet", c: "#1c1c1c", m: "SCR" },
  fanatics: { d: "fanatics.com", c: "#e21b23", m: "FAN" }, prizepicks: { d: "prizepicks.com", c: "#6c2bd9", m: "PP" },
  underdog: { d: "underdogfantasy.com", c: "#f2c94c", m: "UD" }, pick6: { d: "draftkings.com", c: "#53d337", m: "P6" },
  dabble_us_dfs: { d: "dabble.com", c: "#ff5a1f", m: "DB" }, betr_us_dfs: { d: "betr.app", c: "#ff2f6d", m: "BTR" },
  sleeper: { d: "sleeper.com", c: "#18202e", m: "SLP" }, chalkboard: { c: "#2b2b2b", m: "CB" }, boom: { d: "boomfantasy.com", c: "#ff6b00", m: "BM" },
  parlayplay: { d: "parlayplay.io", c: "#3b82f6", m: "PLP" }, onyx: { c: "#111", m: "ONX" }, bookmaker: { d: "bookmaker.eu", c: "#004b87", m: "BKM" },
};
export function bookLogo(key, name, size = 18) {
  const b = BOOKS[key] || {};
  const mono = esc(b.m || (name || key || "?").replace(/[^A-Za-z0-9]/g, "").slice(0, 3).toUpperCase());
  const st = `width:${size}px;height:${size}px;font-size:${Math.max(7, size / 2.6)}px;background:${esc(b.c || "var(--panel-3)")}`;
  if (!b.d) return `<span class="bk" style="${st}" title="${esc(name || key)}">${mono}</span>`;
  return `<span class="bk img" style="${st}" title="${esc(name || key)}${b.twin ? ` (same prices as ${esc(b.twin)})` : ""}"><img src="https://www.google.com/s2/favicons?domain=${b.d}&sz=64" alt="" loading="lazy"
    onerror="this.parentNode.classList.remove('img');this.parentNode.textContent='${mono}'"></span>`;
}
// book name with its logo; DK Predictions / FanDuel Predicts note their twin books
export const bookTag = (key, name, size = 16) => `<span class="bkt">${bookLogo(key, name, size)}<span class="ellip">${esc(name || key)}</span></span>`;

export function leagueLogo(url, league, size = 18) {
  return url ? `<img class="lg-logo" style="width:${size}px;height:${size}px" src="${esc(url)}" alt="${esc(league)}" onerror="this.remove()">` : "";
}

// diverging edge bar: teal right of zero (beats fair), orange left (overpriced)
export function edgeBar(edge, max = 0.06) {
  if (edge == null) return `<div class="eb"></div>`;
  const w = Math.min(100, Math.abs(edge) / max * 100);
  return `<div class="eb" title="${pct(edge)} vs fair"><div class="l">${edge < 0 ? `<i style="width:${w}%"></i>` : ""}</div><div class="r">${edge > 0 ? `<i style="width:${w}%"></i>` : ""}</div></div>`;
}

export function wpBar(away, home, pA, pH, refA, refH) {
  const cA = refA?.color || "var(--faint)", cH = refH?.color || "var(--muted)";
  return `<div class="wp"><div class="wp-bar"><i style="width:${(pA * 100).toFixed(1)}%;background:${esc(cA)}"></i><i style="width:${(pH * 100).toFixed(1)}%;background:${esc(cH)}"></i></div>
    <div class="wp-lab"><span><b>${prob(pA)}</b> ${esc(refA?.abbr || "")}</span><span class="tiny">win chance · sharp fair</span><span>${esc(refH?.abbr || "")} <b>${prob(pH)}</b></span></div></div>`;
}

export function units(u) {
  const n = Math.round((u || 0) * 4);
  return `<span class="units" title="${u}u">${[1, 2, 3, 4].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</span>`;
}

export function bookPill(q) {
  if (q.venue) return `<span class="pill v">yours</span>`;
  if (q.kind === "sharp") return `<span class="pill s">sharp</span>`;
  if (q.kind === "exchange") return `<span class="pill">exch</span>`;
  return "";
}

// ---------- tooltip, toast, sheet
let tipEl;
export function bindTips(root) {
  if (!tipEl) { tipEl = document.createElement("div"); tipEl.className = "tip"; tipEl.hidden = true; document.body.appendChild(tipEl); }
  $$("[data-tip]", root).forEach((el) => {
    el.addEventListener("pointerenter", () => { tipEl.innerHTML = el.dataset.tip; tipEl.hidden = false; });
    el.addEventListener("pointermove", (e) => {
      const x = Math.min(e.clientX + 14, innerWidth - tipEl.offsetWidth - 8);
      tipEl.style.left = x + "px"; tipEl.style.top = (e.clientY + 14) + "px";
    });
    el.addEventListener("pointerleave", () => { tipEl.hidden = true; });
  });
}

export function toast(msg) {
  const t = document.createElement("div"); t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 2200);
}

export function sheet(html, onMount) {
  const bg = document.createElement("div"); bg.className = "sheet-bg";
  bg.innerHTML = `<div class="sheet">${html}</div>`;
  bg.addEventListener("click", (e) => { if (e.target === bg) bg.remove(); });
  document.body.appendChild(bg);
  const close = () => bg.remove();
  onMount?.(bg.firstElementChild, close);
  return close;
}

export const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 7);

// American odds and implied % together, e.g. "+130 · 43.5%"
export const both = (a) => a == null || isNaN(a) ? "–" : `${am(a)} · ${(probFromAm(a) * 100).toFixed(1)}%`;
export const bothP = (p) => p == null || isNaN(p) ? "–" : `${am(amFromProb(p))} · ${(p * 100).toFixed(1)}%`;

// last-N sparkline with the bet line; returns "" when there is nothing to draw
export function spark(vals, line, opts = {}) {
  const v = (vals || []).filter((x) => x != null);
  if (v.length < 1) return "";
  const W = opts.w || 86, H = opts.h || 26;
  const max = Math.max(...v, line || 0) * 1.12 || 1;
  const X = (i) => v.length === 1 ? W / 2 : 3 + i * (W - 6) / (v.length - 1), Y = (x) => H - 3 - x / max * (H - 6);
  const side = opts.side === "Under" ? -1 : 1;
  const hit = (x) => line == null ? null : side > 0 ? x > line : x < line;
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" class="spark" aria-label="last ${v.length}">
    ${line != null ? `<line x1="0" x2="${W}" y1="${Y(line)}" y2="${Y(line)}" class="sp-ref"/>` : ""}
    ${v.length > 1 ? `<polyline points="${v.map((x, i) => `${X(i).toFixed(1)},${Y(x).toFixed(1)}`).join(" ")}" class="sp-line"/>` : ""}
    ${v.map((x, i) => `<circle cx="${X(i)}" cy="${Y(x)}" r="2.4" class="${hit(x) === true ? "sp-hi" : hit(x) === false ? "sp-miss" : "sp-lo"}"/>`).join("")}</svg>`;
}
export function hitRate(vals, line, side = "Over") {
  const v = (vals || []).filter((x) => x != null);
  if (!v.length || line == null) return "";
  const n = v.filter((x) => side === "Under" ? x < line : x > line).length;
  return `${n}/${v.length} ${side === "Under" ? "under" : "over"}`;
}

// ---------- market names (every Odds API game market)
const PERIOD = { q1: "1st quarter", q2: "2nd quarter", q3: "3rd quarter", q4: "4th quarter", h1: "1st half", h2: "2nd half",
  p1: "1st period", p2: "2nd period", p3: "3rd period", "1st_1_innings": "1st inning", "1st_3_innings": "first 3 innings",
  "1st_5_innings": "first 5 innings", "1st_7_innings": "first 7 innings", s1: "1st set", s2: "2nd set" };
const BASE = { h2h: "Moneyline", h2h_3way: "Regulation result (3-way)", h2h_3_way: "3-way result", spreads: "Spread", totals: "Total",
  team_totals: "Team total", btts: "Both teams to score", draw_no_bet: "Draw no bet", double_chance: "Double chance",
  corners: "Corners", cards: "Cards", correct_score: "Correct score", halftime_fulltime: "Half time / full time", corners_1x2: "Corners 1X2",
  set_spreads: "Set spread", set_totals: "Total sets", to_qualify: "To qualify", h2h_lay: "Moneyline (lay)" };
export function marketInfo(mk) {
  let m = mk, alt = false, period = "";
  if (m.startsWith("alternate_")) { alt = true; m = m.slice(10); }
  for (const p of Object.keys(PERIOD)) if (m.endsWith("_" + p)) { period = PERIOD[p]; m = m.slice(0, -p.length - 1); break; }
  const kind = m.includes("team_totals") ? "team_totals" : m.includes("spreads") ? "spreads" : m.includes("totals") ? "totals" : m.startsWith("h2h") ? "h2h" : "other";
  const sub = (m.match(/_(corners|cards)$/) || [])[1];
  const base = BASE[m] || (sub && BASE[m.replace(/_(corners|cards)$/, "")] ? `${BASE[m.replace(/_(corners|cards)$/, "")]} (${sub})` : null) || m.replace(/_/g, " ");
  const label = `${period ? period[0].toUpperCase() + period.slice(1) + " " : ""}${alt ? "alt " : ""}${period ? base.toLowerCase() : base}`;
  return { kind, alt, period, label: label[0].toUpperCase() + label.slice(1) };
}

// Kelly stake at a fair probability and decimal price, as a fraction of the bankroll (settings: kelly fraction, cap)
export function kellyStake(p, dec, set) {
  if (!set?.bankroll || p == null || !dec || dec <= 1) return null;
  const f = (p * dec - 1) / (dec - 1);
  if (f <= 0) return 0;
  const st = Math.min(set.bankroll * (set.maxPct ?? 0.05), set.bankroll * (set.kelly ?? 0.25) * f);
  return st >= 10 ? Math.round(st) : Math.round(st * 2) / 2;
}

// ---------- prediction-market prices: Kalshi, Polymarket and the DraftKings / FanDuel prediction apps quote a contract
// price in cents (the chance). Prices here are all-in (ask + fee); these show the app's own number and read it back.
export const PRED = new Set(["kalshi", "polymarket_us", "polymarket", "draftkings", "fanduel"]);
export function predFee(venue, p) {
  if (venue === "kalshi") return Math.ceil(0.07 * p * (1 - p) * 100 - 1e-9) / 100;
  if (venue === "polymarket_us" || venue === "polymarket") return 0.05 * p * (1 - p);
  return 0;
}
// the ask in cents behind an all-in American price
export function askCents(amAllIn, venue) {
  if (amAllIn == null || isNaN(amAllIn)) return null;
  const cost = 1 / decFromAm(+amAllIn);
  let lo = 0.005, hi = 0.995;
  for (let i = 0; i < 40; i++) { const m = (lo + hi) / 2; m + predFee(venue, m) > cost ? (hi = m) : (lo = m); }
  return Math.round(lo * 1000) / 10;
}
export const centsTag = (amAllIn, venue) => { if (!PRED.has(venue)) return ""; const c = askCents(amAllIn, venue); return c == null ? "" : `${+c.toFixed(1)}¢`; };
// "-110", "+150", "52c", "52¢", "52%", ".52" -> American odds, all-in (the venue's fee added to a cents price)
export function oddsFromInput(s, venue) {
  const t = String(s ?? "").trim().toLowerCase().replace(/\s+/g, "");
  if (!t) return NaN;
  let m = t.match(/^(\d{1,2}(?:\.\d+)?)(?:c|¢|%|cents?)$/), p = null;
  if (m) p = +m[1] / 100;
  else if ((m = t.match(/^0?\.(\d+)$/))) p = +("0." + m[1]);
  if (p != null) {
    if (!(p > 0 && p < 1)) return NaN;
    const cost = p + predFee(venue || "", p), d = 1 / cost;
    return Math.round(d >= 2 ? (d - 1) * 100 : -100 / (d - 1));
  }
  return parseFloat(t.replace(/^\+/, ""));
}

// dollars on offer at a price (exchanges publish it through the odds feed): $47, $8.2k, $290k
export const kUsd = (v) => v == null ? "" : v >= 9950 ? `$${Math.round(v / 1000)}k` : v >= 1000 ? `$${(v / 1000).toFixed(1)}k` : `$${Math.round(v)}`;
