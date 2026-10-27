"""College football: opponent-adjusted offense/defense ratings from every FBS score.

points(team vs opp) = league + O[team] - D[opp] + home edge, solved by ridge-regularized least squares
(this season weight 1, last season 0.3 and regressed), plus the same model on yards per play for the
tale of the tape. Margin sd 15.5, total sd 15.
"""
import math
from collections import defaultdict

SD_MARGIN, SD_TOTAL = 15.5, 15.0


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _solve(rows, lam=4.0, iters=60):
    """rows: (off_team, def_team, value, home_flag(+1/-1/0), weight). Returns (mu, O, D, h)."""
    mu = sum(r[2] * r[4] for r in rows) / max(1e-9, sum(r[4] for r in rows))
    O, Dd, h = defaultdict(float), defaultdict(float), 0.0
    for _ in range(iters):
        acc, wsum = defaultdict(float), defaultdict(float)
        for o, d, v, hf, w in rows:
            acc[o] += w * (v - mu + Dd[d] - h * hf); wsum[o] += w
        for t in acc:
            O[t] = acc[t] / (wsum[t] + lam)
        acc, wsum = defaultdict(float), defaultdict(float)
        for o, d, v, hf, w in rows:
            acc[d] += w * (mu + O[o] + h * hf - v); wsum[d] += w
        for t in acc:
            Dd[t] = acc[t] / (wsum[t] + lam)
        num = sum(w * hf * (v - mu - O[o] + Dd[d]) for o, d, v, hf, w in rows if hf)
        den = sum(w for o, d, v, hf, w in rows if hf) or 1
        h = num / den
    return mu, O, Dd, h


class CFB:
    SD_MARGIN, SD_TOTAL = SD_MARGIN, SD_TOTAL

    def __init__(self, cur_games, prior_games):
        rows_p, rows_y = [], []
        self.record = defaultdict(lambda: [0, 0])
        self.last = {}
        for games, w in ((prior_games, 0.3), (cur_games, 1.0)):
            for g in games:
                hs, as_ = g["home"].get("score"), g["away"].get("score")
                if hs is None or as_ is None:
                    continue
                neutral = False
                for side, other, hf in (("home", "away", 0 if neutral else 1), ("away", "home", 0 if neutral else -1)):
                    rows_p.append((g[side]["abbr"], g[other]["abbr"], g[side]["score"], hf, w))
                    st = g[side].get("stats", {})
                    yds, plays = st.get("totalYards"), st.get("totalOffensivePlays")
                    if yds and plays:
                        rows_y.append((g[side]["abbr"], g[other]["abbr"], yds / plays, hf, w))
                if w == 1.0:
                    win = "home" if hs > as_ else "away"
                    self.record[g[win]["abbr"]][0] += 1
                    self.record[g["away" if win == "home" else "home"]["abbr"]][1] += 1
                    for side in ("home", "away"):
                        self.last[g[side]["abbr"]] = g["date"][:10]
        self.mu, self.O, self.D, self.h = _solve(rows_p)
        self.ymu, self.yO, self.yD, _ = _solve(rows_y, lam=8.0) if rows_y else (0, {}, {}, 0)
        teams = set(self.O) | set(self.D)
        self.teams = {}
        for t in teams:
            self.teams[t] = {"abbr": t, "off": self.O.get(t, 0.0), "def": self.D.get(t, 0.0), "net": self.O.get(t, 0.0) + self.D.get(t, 0.0),
                             "ypp": self.ymu + self.yO.get(t, 0.0), "dypp": self.ymu - self.yD.get(t, 0.0),
                             "record": "{}-{}".format(*self.record[t])}
        for key, desc in (("net", True), ("off", True), ("def", True), ("ypp", True), ("dypp", False)):
            ranked = sorted(self.teams.values(), key=lambda t: t[key], reverse=desc)
            for i, t in enumerate(ranked):
                t.setdefault("rank", {})[key] = i + 1
        self.n = len(self.teams)

    def project_game(self, home, away, neutral=False):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta:
            return None
        hf = 0 if neutral else self.h
        ph = self.mu + th["off"] - ta["def"] + hf
        pa = self.mu + ta["off"] - th["def"] - (0 if neutral else 0)
        return {"home": {"pts": round(ph, 1)}, "away": {"pts": round(pa, 1)}, "margin": round(ph - pa, 1), "total": round(ph + pa, 1)}

    @staticmethod
    def p_cover(margin, home_point):
        return 1 - _phi((-home_point - margin) / SD_MARGIN)

    @staticmethod
    def p_over(total, line):
        return 1 - _phi((line - total) / SD_TOTAL)
