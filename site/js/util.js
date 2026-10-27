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
