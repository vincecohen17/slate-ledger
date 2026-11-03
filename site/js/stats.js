// Statistics for the Analysis, Our picks and Reports pages: means with standard errors, Wilson intervals, bootstrap
// intervals, normal p-values, scoring rules and a small logistic calibration fit. Pure functions, no DOM.

export const sum = (xs) => xs.reduce((a, b) => a + b, 0);
export const mean = (xs) => (xs.length ? sum(xs) / xs.length : null);
export function sd(xs) {
  if (xs.length < 2) return null;
  const m = mean(xs);
  return Math.sqrt(sum(xs.map((x) => (x - m) ** 2)) / (xs.length - 1));
}
// standard normal CDF (Abramowitz-Stegun 7.1.26 through erf)
export function phi(z) {
  const t = 1 / (1 + 0.3275911 * Math.abs(z) / Math.SQRT2);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-(z * z) / 2);
  return z >= 0 ? 0.5 * (1 + y) : 0.5 * (1 - y);
}
export const pTwo = (z) => (z == null || !isFinite(z) ? null : 2 * (1 - phi(Math.abs(z))));
export const pOne = (z) => (z == null || !isFinite(z) ? null : 1 - phi(z));

// mean with standard error, 95% interval and t (normal approximation)
export function meanCI(xs) {
  const n = xs.length, m = mean(xs), s = sd(xs);
  if (m == null) return { n, m: null };
  const se = s != null ? s / Math.sqrt(n) : null;
  return { n, m, sd: s, se, lo: se != null ? m - 1.96 * se : null, hi: se != null ? m + 1.96 * se : null, t: se ? m / se : null, p: se ? pTwo(m / se) : null };
}

export function wilson(k, n, z = 1.96) {
  if (!n) return { p: null, lo: null, hi: null };
  const ph = k / n, d = 1 + z * z / n, c = (ph + z * z / (2 * n)) / d, h = z * Math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d;
  return { p: ph, lo: Math.max(0, c - h), hi: Math.min(1, c + h) };
}

// seeded PRNG so the bootstrap interval doesn't jump on every render
function rng(seed) { let s = seed >>> 0 || 1; return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 1e9) / 1e9; }; }
// percentile bootstrap of a ratio statistic sum(a)/sum(b) over rows (ROI = profit / stake)
export function bootRatio(rows, a, b, reps = 2000, seed = 7) {
  const n = rows.length;
  if (n < 5) return { lo: null, hi: null };
  const r = rng(seed), out = [];
  for (let k = 0; k < reps; k++) {
    let sa = 0, sb = 0;
    for (let i = 0; i < n; i++) { const x = rows[Math.floor(r() * n)]; sa += a(x); sb += b(x); }
    if (sb) out.push(sa / sb);
  }
  out.sort((x, y) => x - y);
  return { lo: out[Math.floor(out.length * 0.025)], hi: out[Math.floor(out.length * 0.975)] };
}

const clip = (p) => Math.min(1 - 1e-6, Math.max(1e-6, p));
export const logLoss = (ps, ys) => (ps.length ? -mean(ps.map((p, i) => (ys[i] ? Math.log(clip(p)) : Math.log(1 - clip(p))))) : null);
export const brier = (ps, ys) => (ps.length ? mean(ps.map((p, i) => (p - ys[i]) ** 2)) : null);
const lg = (p) => Math.log(clip(p) / (1 - clip(p)));
const sig = (x) => 1 / (1 + Math.exp(-x));

// logistic recalibration y ~ sigmoid(a + b logit(p)) by Newton's method; b < 1 = the forecasts were too confident
export function calibFit(ps, ys) {
  if (ps.length < 10) return null;
  let a = 0, b = 1;
  const xs = ps.map(lg);
  for (let it = 0; it < 30; it++) {
    let ga = 0, gb = 0, haa = 1e-6, hab = 0, hbb = 1e-6;
    xs.forEach((x, i) => { const p = sig(a + b * x), r = p - ys[i], w = p * (1 - p); ga += r; gb += r * x; haa += w; hab += w * x; hbb += w * x * x; });
    const det = haa * hbb - hab * hab;
    if (Math.abs(det) < 1e-12) break;
    const da = (hbb * ga - hab * gb) / det, db = (haa * gb - hab * ga) / det;
    a -= da; b -= db;
    if (Math.abs(da) + Math.abs(db) < 1e-8) break;
  }
  // standard errors from the inverse Hessian
  let haa = 0, hab = 0, hbb = 0;
  xs.forEach((x) => { const p = sig(a + b * x), w = p * (1 - p); haa += w; hab += w * x; hbb += w * x * x; });
  const det = haa * hbb - hab * hab;
  return { a, b, seA: det > 0 ? Math.sqrt(hbb / det) : null, seB: det > 0 ? Math.sqrt(haa / det) : null };
}

// reliability bins: equal width on the forecast, with Wilson intervals
export function relBins(ps, ys, nb = 10) {
  const out = [];
  for (let i = 0; i < nb; i++) {
    const lo = i / nb, hi = (i + 1) / nb, idx = ps.map((p, j) => [p, j]).filter(([p]) => p >= lo && (p < hi || (i === nb - 1 && p <= 1)));
    if (!idx.length) continue;
    const k = idx.reduce((a, [, j]) => a + ys[j], 0), w = wilson(k, idx.length);
    out.push({ n: idx.length, p: mean(idx.map(([p]) => p)), o: k / idx.length, lo: w.lo, hi: w.hi });
  }
  return out;
}

// the Pacific day of a timestamp, YYYY-MM-DD
export const ptDay = (iso) => (iso ? new Date(iso).toLocaleDateString("en-CA", { timeZone: "America/Los_Angeles" }) : null);

// max drawdown of a running series
export function drawdown(vals) {
  let c = 0, peak = 0, dd = 0;
  for (const v of vals) { c += v; peak = Math.max(peak, c); dd = Math.max(dd, peak - c); }
  return dd;
}

export const fmtP = (p) => (p == null ? "–" : p < 0.001 ? "<0.001" : p.toFixed(3));
export const fmtT = (t) => (t == null || !isFinite(t) ? "–" : `${t >= 0 ? "+" : ""}${t.toFixed(2)}`);
