"""First-version models: soccer (Poisson team ratings) and tennis (surface Elo). MLB lives in model_mlb.py.
Each returns model lines for the board; they are first versions to be tuned with backtests."""
import csv
import io
import math
import os
from collections import defaultdict
from datetime import date

import stats as ST
from model_cfb import _solve


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pois(mu, n=12):
    p, out = math.exp(-mu), []
    for k in range(n):
        out.append(p)
        p *= mu / (k + 1)
    return out


# ---------------------------------------------------------------------------
# Soccer

class Soccer:
    def __init__(self, cur_games, prior_games):
        rows, self.record = [], defaultdict(lambda: [0, 0, 0])
        for games, w in ((prior_games, 0.4), (cur_games, 1.0)):
            for g in games:
                hs, as_ = g["home"].get("score"), g["away"].get("score")
                if hs is None or as_ is None:
                    continue
                rows.append((g["home"]["abbr"], g["away"]["abbr"], hs, 1, w))
                rows.append((g["away"]["abbr"], g["home"]["abbr"], as_, -1, w))
                if w == 1.0:
                    r = self.record
                    if hs > as_:
                        r[g["home"]["abbr"]][0] += 1; r[g["away"]["abbr"]][2] += 1
                    elif hs < as_:
                        r[g["away"]["abbr"]][0] += 1; r[g["home"]["abbr"]][2] += 1
                    else:
                        r[g["home"]["abbr"]][1] += 1; r[g["away"]["abbr"]][1] += 1
        self.mu, self.O, self.D, self.h = _solve(rows, lam=6.0) if rows else (1.35, {}, {}, 0.15)
        self.teams = {t: {"abbr": t, "att": self.O.get(t, 0.0), "def": self.D.get(t, 0.0), "record": "{}-{}-{}".format(*self.record[t])}
                      for t in set(self.O) | set(self.D)}
        for key in ("att", "def"):
            for i, t in enumerate(sorted(self.teams.values(), key=lambda t: -t[key])):
                t.setdefault("rank", {})[key] = i + 1
        self.n = len(self.teams)

    def project_game(self, home, away):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta:
            return None
        lh = max(0.25, self.mu + th["att"] - ta["def"] + self.h)
        la = max(0.2, self.mu + ta["att"] - th["def"] - self.h)
        ph, pa = _pois(lh), _pois(la)
        hw = sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i > j)
        dr = sum(ph[i] * pa[i] for i in range(12))
        self._d = (ph, pa)
        return {"home": {"goals": round(lh, 2)}, "away": {"goals": round(la, 2)}, "pHome": hw, "pDraw": dr, "pAway": 1 - hw - dr,
                "total": round(lh + la, 2), "btts": (1 - ph[0]) * (1 - pa[0])}

    def p_over(self, line):
        ph, pa = self._d
        return sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i + j > line)

    def p_home_cover(self, home_point):
        ph, pa = self._d
        return sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i - j + home_point > 0)


# ---------------------------------------------------------------------------
# Tennis

class Tennis:
    """Elo with FiveThirtyEight-style decaying K; final rating = 50% overall + 50% surface."""
    def __init__(self, data_dir):
        self.elo, self.surf, self.n = defaultdict(lambda: 1500.0), defaultdict(lambda: 1500.0), defaultdict(int)
        self.form = defaultdict(list)
        rows = ST.load_tennis(data_dir)
        rows.sort(key=lambda r: r.get("date", ""))
        for r in rows:
            w, l, s = r.get("winner_name"), r.get("loser_name"), (r.get("surface") or "Any")
            if not w or not l:
                continue
            for table, key_w, key_l in ((self.elo, w, l), (self.surf, (w, s), (l, s))):
                ew = 1 / (1 + 10 ** ((table[key_l] - table[key_w]) / 400))
                kw, kl = 250 / (self.n[w] + 5) ** 0.4, 250 / (self.n[l] + 5) ** 0.4
                table[key_w] += kw * (1 - ew)
                table[key_l] -= kl * (1 - ew)
            self.n[w] += 1; self.n[l] += 1
            self.form[w].append(1); self.form[l].append(0)

    def find(self, full_name):
        if self.n.get(full_name):
            return full_name
        import refs as R
        k = R.norm(full_name)
        cands = [n for n in self.n if R.norm(n) == k]
        if not cands:
            last = full_name.split()[-1].lower()
            cands = [n for n in self.n if n.lower().split()[-1] == last and n[0].lower() == full_name[0].lower()]
        return cands[0] if len(cands) == 1 else None

    def rating(self, name, surface="Any"):
        # surface is not always reported, so overall Elo carries the weight until it is
        return 0.75 * self.elo[name] + 0.25 * self.surf[(name, surface)]

    def p_win(self, a, b, surface="Any"):
        ka, kb = self.find(a), self.find(b)
        if not ka or not kb:
            return None
        return 1 / (1 + 10 ** ((self.rating(kb, surface) - self.rating(ka, surface)) / 400))
