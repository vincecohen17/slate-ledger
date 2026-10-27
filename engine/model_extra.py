"""Skeleton models: soccer (Poisson team ratings), tennis (surface Elo), MLB (starters + team offense).
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
        rows = []
        d = os.path.join(data_dir, "stats", "tennis")
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if fn.endswith(".csv"):
                rows += list(csv.DictReader(io.StringIO(open(os.path.join(d, fn), encoding="utf-8", errors="ignore").read())))
        rows.sort(key=lambda r: (r.get("tourney_date", ""), r.get("match_num", "")))
        for r in rows:
            w, l, s = r.get("winner_name"), r.get("loser_name"), (r.get("surface") or "Hard")
            if not w or not l:
                continue
            for table, key_w, key_l in ((self.elo, w, l), (self.surf, (w, s), (l, s))):
                ew = 1 / (1 + 10 ** ((table[key_l] - table[key_w]) / 400))
                kw, kl = 250 / (self.n[w] + 5) ** 0.4, 250 / (self.n[l] + 5) ** 0.4
                table[key_w] += kw * (1 - ew)
                table[key_l] -= kl * (1 - ew)
            self.n[w] += 1; self.n[l] += 1
            self.form[w].append(1); self.form[l].append(0)

    @staticmethod
    def key(full_name):
        """'Jannik Sinner' -> 'Sinner J.' (tennis-data.co.uk naming)."""
        parts = (full_name or "").replace(".", "").split()
        if len(parts) < 2:
            return full_name
        return f"{' '.join(parts[1:])} {parts[0][0]}."

    def find(self, full_name):
        k = self.key(full_name)
        if self.n.get(k):
            return k
        last = full_name.split()[-1].lower()
        cands = [n for n in self.n if n.lower().startswith(last + " ") or (" " + last + " ") in (" " + n.lower() + " ")]
        return cands[0] if len(cands) == 1 else None

    def rating(self, name, surface="Hard"):
        return 0.5 * self.elo[name] + 0.5 * self.surf[(name, surface)]

    def p_win(self, a, b, surface="Hard"):
        ka, kb = self.find(a), self.find(b)
        if not ka or not kb:
            return None
        return 1 / (1 + 10 ** ((self.rating(kb, surface) - self.rating(ka, surface)) / 400))


# ---------------------------------------------------------------------------
# MLB

class MLB:
    def __init__(self, data_dir):
        self.sched = ST.load_json(data_dir, "stats/mlb/schedule.json") or {}
        teams = ST.load_json(data_dir, "stats/mlb/teams.json") or {}
        self.pitchers = ST.load_json(data_dir, "stats/mlb/pitchers.json") or {}
        self.off, self.pit = {}, {}
        for sp in (teams.get("hitting", {}).get("stats") or [{}])[0].get("splits", []):
            s, t = sp["stat"], sp["team"]["name"]
            self.off[t] = {"rpg": s.get("runs", 0) / max(1, s.get("gamesPlayed", 1)), "ops": float(s.get("ops", 0) or 0),
                           "kRate": s.get("strikeOuts", 0) / max(1, s.get("plateAppearances", 1))}
        for sp in (teams.get("pitching", {}).get("stats") or [{}])[0].get("splits", []):
            s, t = sp["stat"], sp["team"]["name"]
            self.pit[t] = {"era": float(s.get("era", 4.2) or 4.2)}
        self.lg_rpg = sum(t["rpg"] for t in self.off.values()) / len(self.off) if self.off else 4.4
        self.lg_k = sum(t["kRate"] for t in self.off.values()) / len(self.off) if self.off else 0.22

    def probable(self, team_name):
        for d in self.sched.get("dates", []):
            for g in d.get("games", []):
                for s in ("home", "away"):
                    t = g["teams"][s]
                    if t["team"]["name"] == team_name and t.get("probablePitcher"):
                        return t["probablePitcher"]
        return None

    def pitcher_line(self, pid):
        p = (self.pitchers.get(str(pid)) or {}).get("people", [{}])[0]
        for st in p.get("stats", []):
            if st.get("type", {}).get("displayName") == "season" and st.get("splits"):
                s = st["splits"][-1]["stat"]
                ip = float(s.get("inningsPitched", "0") or 0)
                gs = max(1, s.get("gamesStarted", 1) or 1)
                bf = s.get("battersFaced", 0) or 1
                hr, bb, k = s.get("homeRuns", 0), s.get("baseOnBalls", 0), s.get("strikeOuts", 0)
                fip = (13 * hr + 3 * bb - 2 * k) / max(1, ip) + 3.1 if ip else None
                return {"name": p.get("fullName"), "ip": ip, "ipPerStart": ip / gs, "kRate": k / bf, "bbRate": bb / bf,
                        "era": float(s.get("era", 0) or 0), "fip": fip, "bfPerStart": bf / gs}
        return None

    def project_game(self, home, away):
        oh, oa = self.off.get(home), self.off.get(away)
        if not oh or not oa:
            return None
        out = {"sp": {}}
        runs = {}
        for side, team, opp_off, opp in (("home", home, oa, away), ("away", away, oh, home)):
            pp = self.probable(team)
            pl = self.pitcher_line(pp["id"]) if pp else None
            out["sp"][side] = pl
            starter = (pl["fip"] if pl and pl.get("fip") else 4.2) / 4.2
            pen = self.pit.get(team, {}).get("era", 4.2) / 4.2
            share = min(0.7, (pl["ipPerStart"] if pl else 5.2) / 9)
            allow = share * starter + (1 - share) * pen
            runs[opp] = self.lg_rpg * (opp_off["rpg"] / self.lg_rpg) * allow
            if pl:
                pl["kProj"] = round(pl["kRate"] * (pl["bfPerStart"] or 22) * (opp_off["kRate"] / self.lg_k), 2)
        post = date.today().month in (10, 11)          # postseason: top starters and bullpens, colder nights
        rh, ra = runs[home] * 1.02 * (0.86 if post else 1), runs[away] * 0.98 * (0.86 if post else 1)
        margin = rh - ra
        out.update(home={"runs": round(rh, 2)}, away={"runs": round(ra, 2)}, margin=round(margin, 2), total=round(rh + ra, 2),
                   pHome=1 - _phi(-margin / 4.3))
        return out
