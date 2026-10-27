// Prop distributions, ported from engine/build.py (p_over, DISPERSION, _sd_normal, gamma yardage) so the site can price
// any line (discounted pick'em lines, alt lines, promo specials) from the sharp mean. Keep in sync with build.py.

const NBA = { player_points: 2.0, player_rebounds: 1.5, player_assists: 1.5, player_threes: 1.3, player_points_rebounds_assists: 2.5,
  player_points_rebounds: 2.2, player_points_assists: 2.2, player_rebounds_assists: 1.7, player_blocks: 1.2, player_steals: 1.2,
  player_blocks_steals: 1.25, player_turnovers: 1.2, player_points_q1: 1.6, player_rebounds_q1: 1.3, player_assists_q1: 1.3,
  player_fantasy_points: 2.5 };
const NFL = { player_receptions: 1.2, player_pass_completions: 1.3, player_pass_attempts: 1.4, player_rush_attempts: 1.3,
  player_pass_tds: 1.1, player_pass_interceptions: 1.05, player_sacks: 1.2, player_tackles_assists: 1.3, player_tds_over: 1.0,
  player_rush_tds: 1.0, player_reception_tds: 1.0, player_rush_reception_tds: 1.0, player_pass_rush_reception_tds: 1.0 };
const DISPERSION = {
  basketball_nba: NBA, basketball_wnba: NBA, basketball_ncaab: NBA,
  icehockey_nhl: { player_points: 1.05, player_goals: 1.03, player_assists: 1.05, player_shots_on_goal: 1.15, player_blocked_shots: 1.2,
    player_power_play_points: 1.03, player_total_saves: 1.6 },
  baseball_mlb: { batter_hits: 1.0, batter_total_bases: 1.6, batter_rbis: 1.3, batter_runs_scored: 1.1, batter_hits_runs_rbis: 1.6,
    batter_walks: 1.05, batter_strikeouts: 1.05, pitcher_strikeouts: 1.3, pitcher_outs: 1.5, pitcher_hits_allowed: 1.2,
    pitcher_earned_runs: 1.4, pitcher_walks: 1.1 },
  americanfootball_nfl: NFL, americanfootball_ncaaf: NFL,
};
const SKEWED = new Set(["player_reception_yds", "player_rush_yds", "player_rush_reception_yds"]);
const isNormal = (m) => m.endsWith("_yds") || m.includes("longest");
const sdNormal = (m, mean) => m === "player_pass_yds" ? 0.16 * mean + 18 : m === "player_rush_reception_yds" ? 0.42 * mean + 9 : 0.45 * mean + 8;
const phi = (x) => 0.5 * (1 + erf(x / Math.SQRT2));
function erf(x) {   // Abramowitz-Stegun 7.1.26
  const s = Math.sign(x); x = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * x);
  return s * (1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-x * x));
}
function lgamma(z) {  // Lanczos
  const g = 7, c = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
    12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  if (z < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * z)) - lgamma(1 - z);
  z -= 1; let x = c[0];
  for (let i = 1; i < g + 2; i++) x += c[i] / (z + i);
  const t = z + g + 0.5;
  return 0.5 * Math.log(2 * Math.PI) + (z + 0.5) * Math.log(t) - t + Math.log(x);
}
function gammaQ(a, x) {   // regularized upper incomplete gamma
  if (x <= 0) return 1;
  const gl = lgamma(a);
  if (x < a + 1) {
    let term = 1 / a, s = term, ap = a;
    for (let i = 0; i < 500; i++) { ap += 1; term *= x / ap; s += term; if (Math.abs(term) < Math.abs(s) * 1e-12) break; }
    return 1 - s * Math.exp(-x + a * Math.log(x) - gl);
  }
  let b = x + 1 - a, c = 1e300, d = 1 / b, h = d;
  for (let i = 1; i < 500; i++) {
    const an = -i * (i - a); b += 2;
    d = an * d + b; if (Math.abs(d) < 1e-300) d = 1e-300;
    c = b + an / c; if (Math.abs(c) < 1e-300) c = 1e-300;
    d = 1 / d; h *= d * c; if (Math.abs(d * c - 1) < 1e-12) break;
  }
  return Math.exp(-x + a * Math.log(x) - gl) * h;
}
function nbTail(mean, k, t) {   // P(X >= t), negative binomial with variance k * mean (Poisson at k = 1)
  if (t <= 0) return 1;
  mean = Math.max(mean, 1e-6);
  let cdf = 0;
  if (k <= 1.0001) { let p = Math.exp(-mean); for (let x = 0; x < t; x++) { cdf += p; p *= mean / (x + 1); } }
  else {
    const r = mean / (k - 1), q = 1 / k; let lp = r * Math.log(q);
    for (let x = 0; x < t; x++) { cdf += Math.exp(lp); lp += Math.log((x + r) / (x + 1)) + Math.log(1 - q); }
  }
  return Math.max(0, Math.min(1, 1 - cdf));
}

// P(over `line`) for a prop with sharp mean `mean`; pushes removed on whole-number lines (matches the engine)
export function pOver(market, mean, line, sport = "") {
  if (mean == null || line == null) return null;
  if (SKEWED.has(market) && mean > 0) {
    const sd = sdNormal(market, mean), shape = (mean / sd) ** 2, scale = sd * sd / mean;
    return gammaQ(shape, Math.max(line, 0) / scale);
  }
  if (isNormal(market)) return 1 - phi((line - mean) / sdNormal(market, mean));
  const k = DISPERSION[sport]?.[market] ?? 1.3;
  if (line === Math.floor(line)) {
    const over = nbTail(mean, k, line + 1), push = nbTail(mean, k, line) - over;
    return over / Math.max(1e-9, 1 - push);
  }
  return nbTail(mean, k, Math.floor(line) + 1);
}
