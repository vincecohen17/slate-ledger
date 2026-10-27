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
from datetime import datetime, timedelta
from collections import defaultdict

import stats as ST

MP2ESPN = {"LAK": "LA", "NJD": "NJ", "SJS": "SJ", "TBL": "TB"}
K_TEAM = 20
HOME = 1.04
MARKETS = {"player_shots_on_goal": ["sog"], "player_points": ["pts"], "player_goals": ["g"], "player_assists": ["a"],
           "player_blocked_shots": ["blk"], "player_total_saves": ["saves"], "player_goal_scorer_anytime": ["g"],
           "player_power_play_points": ["ppp"]}
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
        self.data_dir, self.cur_year = data_dir, cur_year
        self.inj = injuries or {}
        self.cur = sorted(cur_games, key=lambda g: g["date"])
        self.prior = prior_games
        self._teams(data_dir, cur_year)
        self._goalies(data_dir, cur_year)
        self._players()
        self._ppp(data_dir, cur_year)

    # ---------------- power-play points per game (MoneyPuck 5-on-4), this season regressed to last
    def _ppp(self, d, y):
        import refs as R
        cur = {R.norm(r["name"]): (_f(r, "I_F_points"), _f(r, "games_played")) for r in ST.read_moneypuck(d, "skaters", y, "5on4")}
        pri = {R.norm(r["name"]): (_f(r, "I_F_points"), _f(r, "games_played")) for r in ST.read_moneypuck(d, "skaters", y - 1, "5on4")}
        self.ppp, self.norm = {}, R.norm
        for n in set(cur) | set(pri):
            cp, cg = cur.get(n, (0, 0))
            pp, pg = pri.get(n, (0, 0))
            prior = (pp + 0.08 * 20) / (pg + 20) if pg else 0.08
            self.ppp[n] = (cp + prior * 15) / (cg + 15)

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
        ev_c, _ = self._season(ST.read_moneypuck(d, "teams", y, "5on5"))
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
            if ab in ev_c:      # even strength (5 on 5) per 60
                t["evXgf60"], t["evXga60"] = ev_c[ab]["xgf"], ev_c[ab]["xga"]
                t["evXgfPct"] = ev_c[ab]["xgf"] / ((ev_c[ab]["xgf"] + ev_c[ab]["xga"]) or 1)
            self.teams[ab] = t
        # ranks on the numbers shown (this season); the model itself uses the blended values
        for key, asc in (("off", False), ("def", True), ("xgfPct", False), ("xgf60", False), ("xga60", True), ("ppXgf60", False), ("pkXga60", True),
                         ("evXgf60", False), ("evXga60", True), ("evXgfPct", False)):
            ranked = sorted([t for t in self.teams.values() if t.get(key) is not None], key=lambda t: t[key], reverse=not asc)
            for i, t in enumerate(ranked):
                t.setdefault("rank", {})[key] = i + 1

    # ---------------- goalies
    def _goalies(self, d, y):
        """GSAx (expected goals minus goals allowed) by situation, current season plus half of last
        season, shrunk toward zero with K minutes so a hot week does not look like a star."""
        K = {"all": 1500, "5on5": 1200, "4on5": 250}
        G = {}
        for sit in ("all", "5on5", "4on5"):
            acc = defaultdict(lambda: {"gsax": 0.0, "min": 0.0, "team": None})
            for yr, w in ((y, 1.0), (y - 1, 0.5)):
                for r in ST.read_moneypuck(d, "goalies", yr, sit):
                    a = acc[r["name"]]
                    a["gsax"] += (_f(r, "xGoals") - _f(r, "goals")) * w
                    a["min"] += _f(r, "icetime") / 60 * w
                    if yr == y:
                        a["team"] = MP2ESPN.get(r["team"], r["team"])
            for n, a in acc.items():
                g = G.setdefault(n, {"name": n, "team": a["team"]})
                g[sit] = a["gsax"] / (a["min"] + K[sit]) * 60
                g[sit + "_min"] = a["min"]
                g["team"] = g["team"] or a["team"]
        for g in G.values():
            g["gsax60"] = g.get("all", 0.0)
            g["ev60"] = g.get("5on5")
            g["pk2"] = g["4on5"] * 2 / 60 if g.get("4on5") is not None else None
        # ranks among goalies with real workload this season or last
        pool = [g for g in G.values() if g.get("all_min", 0) >= 600]
        for key, desc in (("ev60", True), ("pk2", True), ("gsax60", True)):
            ranked = sorted([g for g in pool if g.get(key) is not None], key=lambda g: g[key], reverse=desc)
            for i, g in enumerate(ranked):
                g.setdefault("rank", {})[key] = i + 1
        self.goalie_pool = len(pool)
        self.goalie = G
        # per-start logs from box scores
        self.glogs = defaultdict(list)
        for gm in self.cur:
            for p in gm["players"]:
                if p.get("goalie") and (p.get("timeOnIce") or 0) >= 30:
                    opp = gm["away"]["abbr"] if p["team"] == gm["home"]["abbr"] else gm["home"]["abbr"]
                    sa, sv = p.get("shotsAgainst") or 0, p.get("saves") or 0
                    self.glogs[p["name"]].append({"date": gm["date"][:10], "opp": opp, "sa": sa, "sv": sv,
                                                  "ga": p.get("goalsAgainst") or 0, "svp": round(sv / sa, 3) if sa else None})

    goalie_override = {}        # {team abbr: confirmed goalie name} from verified news

    def likely_goalie(self, abbr):
        starts = []
        for g in reversed(self.cur):
            if abbr not in (g["home"]["abbr"], g["away"]["abbr"]):
                continue
            gs = [p for p in g["players"] if p.get("goalie") and p["team"] == abbr and (p.get("timeOnIce") or 0) >= 30]
            if gs:
                p = gs[0]
                opp = g["away"]["abbr"] if g["home"]["abbr"] == abbr else g["home"]["abbr"]
                sa, sv = p.get("shotsAgainst") or 0, p.get("saves") or 0
                starts.append({"name": p["name"], "date": g["date"][:10], "opp": opp, "sa": sa, "sv": sv,
                               "ga": p.get("goalsAgainst") or 0, "svp": round(sv / sa, 3) if sa else None})
            if len(starts) >= 10:
                break
        if self.goalie_override.get(abbr):
            return self.goalie_override[abbr], starts
        if not starts:
            return None, []
        counts = defaultdict(float)
        for i, s_ in enumerate(starts[:5]):      # the last five starts decide who is likely
            counts[s_["name"]] += 1 + (0.1 if i == 0 else 0)
        return max(counts, key=counts.get), starts

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
    def rest_days(self, abbr, commence):
        if not commence:
            return None
        last = None
        for g in reversed(self.cur):
            if abbr in (g["home"]["abbr"], g["away"]["abbr"]):
                last = g["date"]
                break
        if not last:
            return None
        to_pt = lambda iso: (datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(hours=7)).date()
        return (to_pt(commence) - to_pt(last if "T" in last else last + "T12:00Z")).days

    def project_game(self, home, away, commence=None):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta:
            return None
        rest = {"home": self.rest_days(home, commence), "away": self.rest_days(away, commence)}
        gh, gh_starts = self.likely_goalie(home)
        ga, ga_starts = self.likely_goalie(away)
        gsh = (self.goalie.get(gh) or {}).get("gsax60", 0.0)
        gsa = (self.goalie.get(ga) or {}).get("gsax60", 0.0)
        mu_h = max(1.2, self.lg_gpg * th["off"] * ta["def"] * HOME - 0.8 * gsa)
        mu_a = max(1.2, self.lg_gpg * ta["off"] * th["def"] / HOME - 0.8 * gsh)
        # second night of a back-to-back: tired legs score less and allow more
        if rest["home"] == 1:
            mu_h, mu_a = mu_h * 0.96, mu_a * 1.03
        if rest["away"] == 1:
            mu_a, mu_h = mu_a * 0.96, mu_h * 1.03
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
                        "blk": r["blk"] * t * (op["sf"] ** 0.5), "hits": r["hits"] * t,
                        "ppp": (self.ppp.get(self.norm(p["name"]), 0.0) if t else 0.0) * env}
                logs = self.logs.get(p["id"], [])[-10:]
                players.append({**p, "proj": {k: round(v, 2) for k, v in proj.items()},
                                "last10": {k: [round(x[k], 1) for x in logs] for k in ("sog", "pts", "g", "a", "blk", "hits", "toi", "pp")}})
            sog_against = self.lg_sog * op["sf"] * me["sa"]
            gname = gh if side == "home" else ga
            G = self.goalie.get(gname) or {}
            logs = self.glogs.get(gname, [])[-10:]
            sides[side] = {"players": players, "goalie": {
                "name": gname, "starts": gh_starts if side == "home" else ga_starts,
                "gsax60": round(G.get("gsax60", 0), 3), "ev60": None if G.get("ev60") is None else round(G["ev60"], 3),
                "pk2": None if G.get("pk2") is None else round(G["pk2"], 3), "rank": G.get("rank", {}), "pool": self.goalie_pool,
                "last10": {"sv": [x["sv"] for x in logs], "sa": [x["sa"] for x in logs], "ga": [x["ga"] for x in logs],
                           "svp": [x["svp"] for x in logs], "date": [x["date"] for x in logs], "opp": [x["opp"] for x in logs]},
                "proj": {"saves": round(max(0.0, sog_against - mu_op), 1), "sogA": round(sog_against, 1)}}}
        return {"home": {"goals": round(mu_h, 2)}, "away": {"goals": round(mu_a, 2)},
                "pHomeWin": win + tie * 0.52, "rest": rest, "pTie": tie, "total": round(mu_h + mu_a + tie, 2),
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
