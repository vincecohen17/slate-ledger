"""NBA matchup model from box scores.

Team ratings: possessions = FGA - OREB + TOV + 0.44*FTA; offensive/defensive rating per 100,
pace per 48, four factors, last-10 net rating, rest. Current season blended with last season
(K games of prior weight) so early-season numbers are not noise.

Player projections: per-minute rates (current blended with prior), projected minutes
(recent + season, injuries removed and their minutes redistributed), opponent position
factor (what the opponent allows to that position group, shrunk), and game pace.

Game lines: expected points from pace x (own offense x opponent defense / league), home
court, rest and injury adjustments; normal margin (sd 12.5) and total (sd 18.5).
"""
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

K_TEAM = 15          # prior games of weight on team ratings (prior = returning roster's rating, else league average)
K_MIN = 300          # prior-season minutes of weight on player rates
K_OPP = 2000         # league-average minutes of weight on opponent position factors
HCA = 2.0            # home court, points
SD_MARGIN, SD_TOTAL = 12.5, 18.5
STATS = {"pts": "points", "reb": "rebounds", "ast": "assists", "stl": "steals", "blk": "blocks", "tov": "turnovers"}
MARKETS = {"player_points": ["pts"], "player_rebounds": ["reb"], "player_assists": ["ast"], "player_threes": ["fg3"],
           "player_blocks": ["blk"], "player_steals": ["stl"], "player_turnovers": ["tov"],
           "player_points_rebounds_assists": ["pts", "reb", "ast"], "player_points_rebounds": ["pts", "reb"],
           "player_points_assists": ["pts", "ast"], "player_rebounds_assists": ["reb", "ast"],
           "player_blocks_steals": ["blk", "stl"]}
LABEL = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3": "3PM", "stl": "STL", "blk": "BLK", "tov": "TOV"}


def _pg(pos):
    p = (pos or "").upper()
    return "C" if p == "C" else "G" if "G" in p else "F"


def _row_stats(p):
    fg3 = (p.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or [0, 0])[0] or 0
    out = {k: p.get(v) or 0 for k, v in STATS.items()}
    out["fg3"] = fg3
    return out


def _team_line(t, opp):
    s, o = t.get("stats", {}), opp.get("stats", {})
    fgm, fga = s.get("fieldGoalsMade-fieldGoalsAttempted") or [0, 0]
    tpm, _ = s.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted") or [0, 0]
    ftm, fta = s.get("freeThrowsMade-freeThrowsAttempted") or [0, 0]
    orb, tov = s.get("offensiveRebounds") or 0, s.get("totalTurnovers") or s.get("turnovers") or 0
    odrb = o.get("defensiveRebounds") or 0
    poss = (fga or 0) - orb + tov + 0.44 * (fta or 0)
    return {"pts": t.get("score") or 0, "poss": poss, "fga": fga or 0, "fgm": fgm or 0, "tpm": tpm or 0,
            "fta": fta or 0, "tov": tov, "orb": orb, "orb_opp": odrb}


class NBA:
    def __init__(self, cur_games, prior_games, injuries, today):
        self.today = today
        self.inj = injuries or {}
        self.cur = sorted(cur_games, key=lambda g: g["date"])
        self.prior = prior_games
        self._players()
        self._teams()
        self._opp()

    # ---------------- teams
    def _accumulate(self, games):
        T = defaultdict(lambda: defaultdict(float))
        for g in games:
            if "stats" not in g["home"] or "stats" not in g["away"]:
                continue
            mins = 53 if g.get("ot") else 48
            for side, other in (("home", "away"), ("away", "home")):
                a, b = _team_line(g[side], g[other]), _team_line(g[other], g[side])
                poss = (a["poss"] + b["poss"]) / 2
                t = T[g[side]["abbr"]]
                t["g"] += 1; t["poss"] += poss; t["min"] += mins
                t["pf"] += a["pts"]; t["pa"] += b["pts"]
                t["fga"] += a["fga"]; t["efg_n"] += a["fgm"] + 0.5 * a["tpm"]; t["tov"] += a["tov"]
                t["orb"] += a["orb"]
                t["fta"] += a["fta"]
                t["o_fga"] += b["fga"]; t["o_efg_n"] += b["fgm"] + 0.5 * b["tpm"]; t["o_tov"] += b["tov"]; t["o_fta"] += b["fta"]
                t["w"] += a["pts"] > b["pts"]
        return T

    def _teams(self):
        """Ratings are relative to each season's league average, then blended, so a change in how
        possessions are counted (or in league scoring) between seasons cannot leak into projections."""
        C, P = self._accumulate(self.cur), self._accumulate(self.prior)

        def league(T):
            poss = sum(t["poss"] for t in T.values()) or 1
            mins = sum(t["min"] for t in T.values()) or 1
            g = sum(t["g"] for t in T.values()) or 1
            return {"rtg": 100 * sum(t["pf"] for t in T.values()) / poss, "pace": 48 * poss / mins,
                    "ppg": sum(t["pf"] for t in T.values()) / g, "games": g / 2}
        LC, LP = league(C) if C else None, league(P) if P else None
        lc = LC or LP
        # league scoring environment: current season, leaning on last season until ~150 games
        if LC and LP:
            w = LC["games"] / (LC["games"] + 150)
            self.lg_ppg = w * LC["ppg"] + (1 - w) * LP["ppg"]
        else:
            self.lg_ppg = lc["ppg"]
        self.lg_rtg, self.lg_pace = lc["rtg"], lc["pace"]
        # roster continuity: share of this season's minutes played by players who were on the team last season
        cont_min, all_min = defaultdict(float), defaultdict(float)
        for pid, logs in self.logs.items():
            pr_team = (self.prior_p.get(pid) or {}).get("team")
            for r in logs:
                all_min[r["team"]] += r["min"]
                if pr_team == r["team"]:
                    cont_min[r["team"]] += r["min"]
        self.teams = {}
        for abbr in set(C) | {a for a, t in P.items() if t["g"] >= 20}:
            c, p = C.get(abbr, {}), P.get(abbr, {})
            n = c.get("g", 0)
            cont = cont_min[abbr] / all_min[abbr] if all_min[abbr] else 1.0
            # prior = last season's rating for the part of the roster that returned, league average for the rest

            def rel(num, den, scale, L_c, L_p, key):
                cv = c.get(num, 0) / c[den] * scale / L_c[key] if c.get(den) and L_c else None
                pv = p.get(num, 0) / p[den] * scale / L_p[key] if p.get(den) and L_p else None
                if cv is None and pv is None:
                    return None
                if pv is None:
                    pv = 1.0
                pv = cont * pv + (1 - cont) * 1.0
                if cv is None:
                    return pv
                return (cv * n + pv * K_TEAM) / (n + K_TEAM)

            def plain(num, den):
                cv = c.get(num, 0) / c[den] if c.get(den) else None
                pv = p.get(num, 0) / p[den] if p.get(den) else None
                if cv is None or pv is None:
                    return cv if pv is None else pv
                return (cv * n + pv * K_TEAM) / (n + K_TEAM)
            ro = rel("pf", "poss", 100, LC, LP, "rtg")
            rd = rel("pa", "poss", 100, LC, LP, "rtg")
            rp = rel("poss", "min", 48, LC, LP, "pace")
            last = [g for g in self.cur if abbr in (g["home"]["abbr"], g["away"]["abbr"])]
            last10 = []
            for g in last[-10:]:
                me, op = ("home", "away") if g["home"]["abbr"] == abbr else ("away", "home")
                last10.append((g[me]["score"] or 0) - (g[op]["score"] or 0))
            ortg = ro * self.lg_rtg if ro else None
            drtg = rd * self.lg_rtg if rd else None
            self.teams[abbr] = {
                "abbr": abbr, "games": n, "w": int(c.get("w", 0)), "l": int(n - c.get("w", 0)),
                "continuity": round(cont, 2), "relO": ro, "relD": rd, "relPace": rp,
                "ortg": ortg, "drtg": drtg, "net": (ortg - drtg) if ortg and drtg else None,
                "pace": rp * self.lg_pace if rp else None,
                "efg": plain("efg_n", "fga"), "tovp": plain("tov", "poss"), "ftr": plain("fta", "fga"),
                "o_efg": plain("o_efg_n", "o_fga"), "o_tovp": plain("o_tov", "poss"), "o_ftr": plain("o_fta", "o_fga"),
                "last10": last10, "lastGame": last[-1]["date"][:10] if last else None,
            }
        for key in ("ortg", "drtg", "net", "pace", "efg", "tovp", "o_efg", "o_tovp"):
            asc = key in ("drtg", "tovp", "o_efg")      # lower is better
            ranked = sorted([t for t in self.teams.values() if t.get(key) is not None], key=lambda t: t[key], reverse=not asc)
            for i, t in enumerate(ranked):
                t.setdefault("rank", {})[key] = i + 1

    # ---------------- players
    def _players(self):
        cur = defaultdict(list)
        for g in self.cur:
            for p in g["players"]:
                if p.get("dnp") or not p.get("minutes"):
                    continue
                cur[p["id"]].append({"date": g["date"][:10], "team": p["team"], "min": p["minutes"], "starter": p.get("starter"),
                                     "pos": p.get("pos"), "name": p["name"], **_row_stats(p)})
        prior = defaultdict(lambda: defaultdict(float))
        for g in self.prior:
            for p in g["players"]:
                if p.get("dnp") or not p.get("minutes"):
                    continue
                q = prior[p["id"]]
                q["min"] += p["minutes"]; q["g"] += 1
                for k, v in _row_stats(p).items():
                    q[k] += v
                q["name"] = p["name"]; q["pos"] = p.get("pos"); q["team"] = p["team"]
        self.logs, self.prior_p = cur, prior
        # league per-minute rates by position group (for players without history)
        agg = defaultdict(lambda: defaultdict(float))
        for logs in cur.values():
            for r in logs:
                a = agg[_pg(r["pos"])]
                a["min"] += r["min"]
                for k in list(STATS) + ["fg3"]:
                    a[k] += r[k]
        self.lg_pos_rate = {gp: {k: a[k] / a["min"] for k in list(STATS) + ["fg3"]} for gp, a in agg.items() if a["min"]}

    def rates(self, pid, pos):
        logs = self.logs.get(pid, [])
        cm = sum(r["min"] for r in logs)
        pr = self.prior_p.get(pid)
        base = self.lg_pos_rate.get(_pg(pos), {})
        out = {}
        for k in list(STATS) + ["fg3"]:
            cs = sum(r[k] for r in logs)
            if pr and pr["min"] > 100:
                prior_rate, kmin = pr[k] / pr["min"], K_MIN
            else:
                prior_rate, kmin = base.get(k, 0), 120
            out[k] = (cs + prior_rate * kmin) / (cm + kmin) if (cm + kmin) else 0
        return out

    def proj_minutes(self, pid):
        logs = self.logs.get(pid, [])
        if len(logs) >= 3:
            l5 = [r["min"] for r in logs[-5:]]
            return 0.55 * sum(l5) / len(l5) + 0.45 * sum(r["min"] for r in logs) / len(logs)
        pr = self.prior_p.get(pid)
        if logs:
            prior_m = pr["min"] / pr["g"] if pr and pr["g"] else logs[-1]["min"]
            cur_m = sum(r["min"] for r in logs) / len(logs)
            return (cur_m * len(logs) + prior_m * 2) / (len(logs) + 2)
        return pr["min"] / pr["g"] * 0.9 if pr and pr["g"] else 0

    # ---------------- opponent position factors
    def _opp(self):
        allowed = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))
        lg = defaultdict(lambda: defaultdict(float))
        for games, w in ((self.cur, 1.0), (self.prior, 0.35)):
            for g in games:
                for p in g["players"]:
                    if p.get("dnp") or not p.get("minutes"):
                        continue
                    opp = g["away"]["abbr"] if p["team"] == g["home"]["abbr"] else g["home"]["abbr"]
                    gp = _pg(p.get("pos"))
                    s = _row_stats(p)
                    a, l_ = allowed[opp][gp], lg[gp]
                    a["min"] += p["minutes"] * w; l_["min"] += p["minutes"] * w
                    for k, v in s.items():
                        a[k] += v * w; l_[k] += v * w
        self.opp = {}
        for team, groups in allowed.items():
            for gp, a in groups.items():
                l_ = lg[gp]
                for k in list(STATS) + ["fg3"]:
                    lr = l_[k] / l_["min"] if l_["min"] else 0
                    if lr <= 0:
                        continue
                    f = ((a[k] + lr * K_OPP) / (a["min"] + K_OPP)) / lr
                    self.opp.setdefault(team, {}).setdefault(gp, {})[k] = f

    # ---------------- roster + injuries for a team in a game
    def status_map(self, abbr):
        out = {}
        for t in self.inj.values():
            if t.get("abbr") != abbr:
                continue
            for x in t["list"]:
                out[x["id"]] = x
                out["name:" + (x["name"] or "")] = x
        return out

    def roster(self, abbr):
        latest = {}
        for pid, logs in self.logs.items():
            if logs and logs[-1]["team"] == abbr:
                latest[pid] = logs[-1]
        if not latest:   # first game of a season: fall back to last season's roster
            for pid, q in self.prior_p.items():
                if q.get("team") == abbr and q["g"] >= 10:
                    latest[pid] = {"name": q["name"], "pos": q["pos"]}
        st = self.status_map(abbr)
        team_games = sum(1 for g in self.cur if abbr in (g["home"]["abbr"], g["away"]["abbr"]))
        players = []
        for pid, last in latest.items():
            inj = st.get(pid) or st.get("name:" + last["name"])
            status = (inj or {}).get("status") or "Active"
            m = self.proj_minutes(pid)
            gp = len(self.logs.get(pid, []))
            out = status in ("Out", "Doubtful", "Suspension") or (inj and "out" in status.lower())
            players.append({"id": pid, "name": last["name"], "pos": last.get("pos"), "status": status,
                            "note": (inj or {}).get("note"), "baseMin": round(m, 1), "out": bool(out),
                            "gp": gp, "teamGames": team_games})
        # redistribute minutes of players who are out (only if they have been playing)
        freed = sum(p["baseMin"] for p in players if p["out"] and p["gp"] >= max(1, team_games // 2))
        rot = [p for p in players if not p["out"] and p["baseMin"] >= 10]
        tot = sum(p["baseMin"] for p in rot) or 1
        for p in players:
            if p["out"]:
                p["projMin"] = 0.0
            elif p in rot:
                p["projMin"] = round(min(42.0, p["baseMin"] + freed * p["baseMin"] / tot), 1)
            else:
                p["projMin"] = p["baseMin"]
            if p["status"] == "Questionable":
                p["projMin"] = round(p["projMin"] * 0.8, 1)
        players.sort(key=lambda p: -p["projMin"])
        return players, freed

    # ---------------- projections for one game
    def project_game(self, home, away, commence):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta or not th.get("relO") or not ta.get("relO"):
            return None
        gdate = commence[:10]
        pace_rel = (th["relPace"] or 1) * (ta["relPace"] or 1)
        pace = pace_rel * self.lg_pace

        def rest(t):
            if not t.get("lastGame"):
                return None
            return (date.fromisoformat(gdate) - date.fromisoformat(t["lastGame"])).days
        rh, ra = rest(th), rest(ta)
        out = {"pace": round(pace, 1), "rest": {"home": rh, "away": ra}}
        sides = {}
        for side, abbr, me, op in (("home", home, th, ta), ("away", away, ta, th)):
            players, freed = self.roster(abbr)
            teamp = []
            for p in players:
                if p["projMin"] <= 0 and not p["out"]:
                    continue
                r = self.rates(p["id"], p["pos"])
                of = self.opp.get(op["abbr"], {}).get(_pg(p["pos"]), {})
                pf = pace / me["pace"] if me.get("pace") else 1.0
                proj = {k: round(r[k] * p["projMin"] * (of.get(k, 1.0) ** 0.6) * pf, 2) for k in r}
                logs = self.logs.get(p["id"], [])[-10:]
                teamp.append({**p, "proj": proj,
                              "last10": {k: [round(x[k], 1) for x in logs] for k in ("pts", "reb", "ast", "fg3", "stl", "blk", "tov", "min")}})
            sides[side] = {"players": teamp, "freedMin": round(freed, 1)}
        # expected points
        ph = self.lg_ppg * th["relO"] * ta["relD"] * pace_rel + HCA / 2
        pa = self.lg_ppg * ta["relO"] * th["relD"] * pace_rel - HCA / 2
        for side, rr in (("home", rh), ("away", ra)):
            if rr == 1:
                if side == "home":
                    ph -= 1.2
                else:
                    pa -= 1.2
        # injuries: points of out players not yet reflected in ratings (they played most games)
        for side in ("home", "away"):
            lost = sum(self.rates(p["id"], p["pos"])["pts"] * p["baseMin"]
                       for p in sides[side]["players"] if p["out"] and p["gp"] >= max(1, p["teamGames"] // 2))
            adj = 0.3 * lost
            if side == "home":
                ph -= adj
            else:
                pa -= adj
            sides[side]["injuryPts"] = round(adj, 1)
        out.update({"home": {"pts": round(ph, 1)}, "away": {"pts": round(pa, 1)},
                    "margin": round(ph - pa, 1), "total": round(ph + pa, 1), "sides": sides})
        return out

    @staticmethod
    def p_cover(margin, home_point):
        """P(home covers home_point) e.g. home -3.5 -> P(margin > 3.5)."""
        return 1 - _phi((-home_point - margin) / SD_MARGIN)

    @staticmethod
    def p_over(total, line):
        return 1 - _phi((line - total) / SD_TOTAL)


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))
