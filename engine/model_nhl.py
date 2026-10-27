"""NHL matchup model: MoneyPuck expected goals + goalie saves above expected + ESPN box scores.

Team strength (relative to league, current season blended with last season, which is itself
regressed 30% to average): offense = 70% xGF/60 + 30% GF/60 (finishing), defense = xGA/60.
Goalie: goals saved above expected per 60, shrunk hard toward zero; the likely starter is the
goalie with the most starts in the team's last five games (confirm before betting).
Game: independent Poisson regulation scores; overtime/shootout won by home 52%; the total adds
one goal when regulation ends tied.
Players: per-60 rates from box scores x projected TOI x opponent and game-environment factors.
"""
import math
from collections import defaultdict

import stats as ST

MP2ESPN = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
K_TEAM = 20
HOME = 1.04
MARKETS = {"player_shots_on_goal": ["sog"], "player_points": ["pts"], "player_goals": ["g"], "player_assists": ["a"],
           "player_blocked_shots": ["blk"], "player_total_saves": ["saves"], "player_goal_scorer_anytime": ["g"]}
LABEL = {"sog": "SOG", "pts": "PTS", "g": "G", "a": "A", "blk": "BLK", "hits": "HIT", "saves": "SV"}


def _f(r, k):
    try:
        return float(r.get(k) or 0)
    except ValueError:
        return 0.0


def _poisson(mu, n=12):
    p, out = math.exp(-mu), []
    for k in range(n):
        out.append(p)
        p *= mu / (k + 1)
    return out


class NHL:
    def __init__(self, data_dir, cur_games, prior_games, injuries, cur_year=2026):
        self.inj = injuries or {}
        self.cur = sorted(cur_games, key=lambda g: g["date"])
        self.prior = prior_games
        self._teams(data_dir, cur_year)
        self._goalies(data_dir, cur_year)
        self._players()

    # ---------------- teams
    def _season(self, rows):
        T = {}
        for r in rows:
            sec = _f(r, "iceTime")
            if not sec:
                continue
            ab = MP2ESPN.get(r["team"], r["team"])
            T[ab] = {"gp": _f(r, "games_played"), "xgf": _f(r, "xGoalsFor") / sec * 3600, "xga": _f(r, "xGoalsAgainst") / sec * 3600,
                     "gf": _f(r, "goalsFor") / sec * 3600, "ga": _f(r, "goalsAgainst") / sec * 3600,
                     "sf": _f(r, "shotsOnGoalFor") / sec * 3600, "sa": _f(r, "shotsOnGoalAgainst") / sec * 3600,
                     "gpg": _f(r, "goalsFor") / max(1, _f(r, "games_played"))}
        if not T:
            return T, None
        L = {k: sum(t[k] for t in T.values()) / len(T) for k in ("xgf", "xga", "gf", "ga", "sf", "sa", "gpg")}
        return T, L

    def _teams(self, d, y):
        C, LC = self._season(ST.read_moneypuck(d, "teams", y))
        P, LP = self._season(ST.read_moneypuck(d, "teams", y - 1))
        pp_c, _ = self._season(ST.read_moneypuck(d, "teams", y, "5on4"))
        pk_c, _ = self._season(ST.read_moneypuck(d, "teams", y, "4on5"))
        L = LC or LP
        n_c = sum(t["gp"] for t in C.values()) / 2 if C else 0
        w = n_c / (n_c + 200) if LP else 1.0
        self.lg_gpg = (w * LC["gpg"] + (1 - w) * LP["gpg"]) if LC and LP else L["gpg"]
        self.lg_sog = (w * LC["sf"] + (1 - w) * LP["sf"]) if LC and LP else L["sf"]
        self.teams = {}
        for ab in set(C) | set(P):
            c, p = C.get(ab), P.get(ab)
            n = c["gp"] if c else 0

            def rel(k):
                cv = c[k] / LC[k] if c and LC else None
                pv = p[k] / LP[k] if p and LP else 1.0
                pv = 0.7 * pv + 0.3
                return pv if cv is None else (cv * n + pv * K_TEAM) / (n + K_TEAM)
            off = 0.7 * rel("xgf") + 0.3 * rel("gf")
            t = {"abbr": ab, "gp": n, "off": off, "def": rel("xga"), "sf": rel("sf"), "sa": rel("sa"),
                 "xgfPct": (c["xgf"] / (c["xgf"] + c["xga"])) if c else None,
                 "xgf60": c["xgf"] if c else None, "xga60": c["xga"] if c else None,
                 "gf60": c["gf"] if c else None, "ga60": c["ga"] if c else None}
            if ab in pp_c and pp_c[ab]["xgf"]:
                t["ppXgf60"] = pp_c[ab]["xgf"]
            if ab in pk_c:
                t["pkXga60"] = pk_c[ab]["xga"]
            self.teams[ab] = t
        for key, asc in (("off", False), ("def", True), ("xgfPct", False), ("ppXgf60", False), ("pkXga60", True)):
            ranked = sorted([t for t in self.teams.values() if t.get(key) is not None], key=lambda t: t[key], reverse=not asc)
            for i, t in enumerate(ranked):
                t.setdefault("rank", {})[key] = i + 1

    # ---------------- goalies
    def _goalies(self, d, y):
        G = defaultdict(lambda: {"gsax": 0.0, "min": 0.0, "name": None, "team": None})
        for yr, w in ((y, 1.0), (y - 1, 0.5)):
            for r in ST.read_moneypuck(d, "goalies", yr):
                g = G[r["name"]]
                g["gsax"] += (_f(r, "xGoals") - _f(r, "goals")) * w
                g["min"] += _f(r, "icetime") / 60 * w
                g["name"] = r["name"]
                if yr == y:
                    g["team"] = MP2ESPN.get(r["team"], r["team"])
        self.goalie = {n: {"name": n, "team": g["team"], "gsax60": g["gsax"] / (g["min"] + 1500) * 60, "min": g["min"]}
                       for n, g in G.items()}

    def likely_goalie(self, abbr):
        starts = []
        for g in reversed(self.cur):
            if abbr not in (g["home"]["abbr"], g["away"]["abbr"]):
                continue
            gs = [p for p in g["players"] if p.get("goalie") and p["team"] == abbr and (p.get("timeOnIce") or 0) >= 30]
            if gs:
                starts.append(gs[0]["name"])
            if len(starts) >= 5:
                break
        if not starts:
            return None, []
        counts = defaultdict(int)
        for i, n in enumerate(starts):
            counts[n] += 1 + (0.1 if i == 0 else 0)
        best = max(counts, key=counts.get)
        return best, starts

    # ---------------- players
    def _players(self):
        keys = {"sog": "shotsTotal", "g": "goals", "a": "assists", "blk": "blockedShots", "hits": "hits"}
        self.logs = defaultdict(list)
        for g in self.cur:
            for p in g["players"]:
                if p.get("goalie") or not p.get("timeOnIce"):
                    continue
                row = {k: p.get(v) or 0 for k, v in keys.items()}
                row.update(pts=row["g"] + row["a"], toi=p["timeOnIce"], pp=p.get("powerPlayTimeOnIce") or 0,
                           team=p["team"], name=p["name"], pos=p.get("pos"), date=g["date"][:10])
                self.logs[p["id"]].append(row)
        self.prior_p = defaultdict(lambda: defaultdict(float))
        for g in self.prior:
            for p in g["players"]:
                if p.get("goalie") or not p.get("timeOnIce"):
                    continue
                q = self.prior_p[p["id"]]
                q["toi"] += p["timeOnIce"]; q["gp"] += 1
                for k, v in keys.items():
                    q[k] += p.get(v) or 0
                q["pts"] += (p.get("goals") or 0) + (p.get("assists") or 0)
                q["team"] = p["team"]
        agg = defaultdict(float)
        for logs in self.logs.values():
            for r in logs:
                agg["toi"] += r["toi"]
                for k in ("sog", "g", "a", "pts", "blk", "hits"):
                    agg[k] += r[k]
        self.lg_rate = {k: agg[k] / agg["toi"] for k in ("sog", "g", "a", "pts", "blk", "hits")} if agg["toi"] else {}

    def rates(self, pid):
        logs, pr = self.logs.get(pid, []), self.prior_p.get(pid)
        ct = sum(r["toi"] for r in logs)
        out = {}
        for k in ("sog", "g", "a", "pts", "blk", "hits"):
            cs = sum(r[k] for r in logs)
            if pr and pr["toi"] > 200:
                prior, km = pr[k] / pr["toi"], 400
            else:
                prior, km = self.lg_rate.get(k, 0), 150
            out[k] = (cs + prior * km) / (ct + km)
        return out

    def proj_toi(self, pid):
        logs = self.logs.get(pid, [])
        if len(logs) >= 3:
            l5 = [r["toi"] for r in logs[-5:]]
            return 0.6 * sum(l5) / len(l5) + 0.4 * sum(r["toi"] for r in logs) / len(logs)
        pr = self.prior_p.get(pid)
        if logs:
            return sum(r["toi"] for r in logs) / len(logs)
        return pr["toi"] / pr["gp"] if pr and pr["gp"] else 0

    def status_map(self, abbr):
        out = {}
        for t in self.inj.values():
            if t.get("abbr") == abbr:
                for x in t["list"]:
                    out[x["id"]] = x
                    out["name:" + (x["name"] or "")] = x
        return out

    def roster(self, abbr):
        st = self.status_map(abbr)
        out = []
        for pid, logs in self.logs.items():
            if not logs or logs[-1]["team"] != abbr:
                continue
            inj = st.get(pid) or st.get("name:" + logs[-1]["name"])
            status = (inj or {}).get("status") or "Active"
            is_out = status in ("Out", "Injured Reserve", "Suspension") or "out" in status.lower() or "reserve" in status.lower()
            # skaters who have not played in the team's last 3 games are likely scratched
            team_dates = sorted({g["date"][:10] for g in self.cur if abbr in (g["home"]["abbr"], g["away"]["abbr"])})[-3:]
            scratched = logs[-1]["date"] not in team_dates
            out.append({"id": pid, "name": logs[-1]["name"], "pos": logs[-1]["pos"], "status": "Scratched?" if scratched and not is_out else status,
                        "note": (inj or {}).get("note"), "out": bool(is_out or scratched), "projToi": 0 if (is_out or scratched) else round(self.proj_toi(pid), 1)})
        out.sort(key=lambda p: -p["projToi"])
        return out

    # ---------------- one game
    def project_game(self, home, away):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta:
            return None
        gh, gh_starts = self.likely_goalie(home)
        ga, ga_starts = self.likely_goalie(away)
        gsh = (self.goalie.get(gh) or {}).get("gsax60", 0.0)
        gsa = (self.goalie.get(ga) or {}).get("gsax60", 0.0)
        mu_h = max(1.2, self.lg_gpg * th["off"] * ta["def"] * HOME - 0.8 * gsa)
        mu_a = max(1.2, self.lg_gpg * ta["off"] * th["def"] / HOME - 0.8 * gsh)
        ph, pa = _poisson(mu_h), _poisson(mu_a)
        win = sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i > j)
        tie = sum(ph[i] * pa[i] for i in range(12))
        self._dist = (ph, pa)
        sides = {}
        for side, ab, me, op, mu, mu_op in (("home", home, th, ta, mu_h, mu_a), ("away", away, ta, th, mu_a, mu_h)):
            players = []
            for p in self.roster(ab):
                r = self.rates(p["id"])
                t = p["projToi"]
                env = mu / self.lg_gpg
                proj = {"sog": r["sog"] * t * (op["sa"] ** 0.7) * (me["sf"] ** 0.3),
                        "g": r["g"] * t * env, "a": r["a"] * t * env, "pts": r["pts"] * t * env,
                        "blk": r["blk"] * t * (op["sf"] ** 0.5), "hits": r["hits"] * t}
                logs = self.logs.get(p["id"], [])[-10:]
                players.append({**p, "proj": {k: round(v, 2) for k, v in proj.items()},
                                "last10": {k: [x[k] for x in logs] for k in ("sog", "pts", "g", "toi")}})
            sog_against = self.lg_sog * op["sf"] * me["sa"]
            gname = gh if side == "home" else ga
            sides[side] = {"players": players, "goalie": {"name": gname, "starts": gh_starts if side == "home" else ga_starts,
                                                          "gsax60": round((self.goalie.get(gname) or {}).get("gsax60", 0), 3),
                                                          "proj": {"saves": round(max(0.0, sog_against - mu_op), 1), "sogA": round(sog_against, 1)}}}
        return {"home": {"goals": round(mu_h, 2)}, "away": {"goals": round(mu_a, 2)},
                "pHomeWin": win + tie * 0.52, "pTie": tie, "total": round(mu_h + mu_a + tie, 2),
                "margin": round(mu_h - mu_a, 2), "sides": sides}

    def p_home_cover(self, home_point):
        """Puck line: home -1.5 -> P(home wins by 2+ in regulation)."""
        ph, pa = self._dist
        need = -home_point
        return sum(ph[i] * pa[j] for i in range(12) for j in range(12) if i - j > need)

    def p_over(self, line):
        ph, pa = self._dist
        p = 0.0
        for i in range(12):
            for j in range(12):
                tot = i + j + (1 if i == j else 0)
                if tot > line:
                    p += ph[i] * pa[j]
        return p
