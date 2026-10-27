"""NFL matchup model from nflverse.

Teams (play-by-play): offensive and defensive EPA per play, success rate, pass rate over expected,
explosive rate, early-down EPA, plays per game. One round of opponent adjustment, current season
blended with last season (less weight when the starting QB changed), last season regressed 40%.
Points = league average + projected plays x 0.85 x (own offense + opponent defense, EPA/play), home 1.6.

Players (weekly stats): shares of team targets and carries (recent-weighted), efficiency per target,
carry and attempt, regressed TD rates; team volume from pace, pass rate and game script; opponent
EPA allowed nudges yardage. Injured (Out/Doubtful) players are removed and their share redistributed.
"""
import json
import math
import os
from collections import defaultdict

import stats as ST

HFA = 1.6
SD_MARGIN, SD_TOTAL = 13.5, 13.0
MARKETS = {"player_pass_yds": ["pass_yds"], "player_pass_attempts": ["att"], "player_pass_completions": ["cmp"],
           "player_pass_tds": ["ptd"], "player_pass_interceptions": ["int"], "player_rush_yds": ["rush_yds"],
           "player_rush_attempts": ["car"], "player_receptions": ["rec"], "player_reception_yds": ["rec_yds"],
           "player_rush_reception_yds": ["rush_yds", "rec_yds"], "player_pass_rush_yds": ["pass_yds", "rush_yds"],
           "player_anytime_td": ["td"], "player_tds_over": ["td"], "player_rush_reception_tds": ["td"],
           "player_reception_tds": ["rectd"], "player_rush_tds": ["rushtd"],
           "player_pass_rush_reception_yds": ["pass_yds", "rush_yds", "rec_yds"]}
LABEL = {"pass_yds": "PYD", "rush_yds": "RYD", "rec": "REC", "rec_yds": "RECYD", "td": "TD", "att": "ATT", "car": "CAR"}


def _f(r, k):
    try:
        v = r.get(k)
        return float(v) if v not in (None, "", "NA") else 0.0
    except ValueError:
        return 0.0


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


class NFL:
    SD_MARGIN, SD_TOTAL = SD_MARGIN, SD_TOTAL

    def __init__(self, data_dir, cur_year, espn_games, injuries):
        self.inj = injuries or {}
        self.games = sorted(espn_games, key=lambda g: g["date"])
        tw = lambda y: ST.load_json(data_dir, f"stats/nfl/nflverse/team_week_{y}.json") or []
        self.cur_tw, self.pri_tw = tw(cur_year), tw(cur_year - 1)
        self.cur_p = [r for r in ST.read_nfl_players(data_dir, cur_year) if r.get("season_type") in ("REG", "POST")]
        self.pri_p = [r for r in ST.read_nfl_players(data_dir, cur_year - 1) if r.get("season_type") in ("REG", "POST")]
        self._teams()
        self._players()

    # ---------------- teams
    @staticmethod
    def _agg(rows):
        T = defaultdict(lambda: defaultdict(float))
        for r in rows:
            t = T[r["team"]]
            t["g"] += 1
            for k, v in r.items():
                if isinstance(v, (int, float)) and k not in ("week",):
                    t[k] += v
        out = {}
        for team, t in T.items():
            if not t.get("o_plays") or not t.get("d_plays"):
                continue
            out[team] = {"g": t["g"], "off": t["o_epa"] / t["o_plays"], "def": t["d_epa"] / t["d_plays"],
                         "succ": t["o_succ"] / t["o_plays"], "dsucc": t["d_succ"] / t["d_plays"],
                         "passRate": t["o_pass"] / t["o_plays"], "proe": (t["o_pass"] - t["o_xpass"]) / t["o_xn"] if t.get("o_xn") else 0.0,
                         "expl": t["o_expl"] / t["o_plays"], "dexpl": t["d_expl"] / t["d_plays"],
                         "edEpa": t["o_ed_epa"] / t["o_ed_n"] if t.get("o_ed_n") else None,
                         "passEpa": t["o_pass_epa"] / max(1, t["o_pass"]), "rushEpa": t["o_rush_epa"] / max(1, t["o_plays"] - t["o_pass"]),
                         "dPassEpa": t["d_pass_epa"] / max(1, t["d_pass"]), "dRushEpa": t["d_rush_epa"] / max(1, t["d_plays"] - t["d_pass"]),
                         "plays": t["o_plays"] / t["g"]}
        return out

    def _teams(self):
        C, P = self._agg(self.cur_tw), self._agg(self.pri_tw)
        lg = lambda T, k: sum(t[k] for t in T.values()) / len(T) if T else 0.0
        # one round of opponent adjustment on the current season
        opp_def = defaultdict(list)
        opp_off = defaultdict(list)
        for r in self.cur_tw:
            if r["opp"] in C:
                opp_def[r["team"]].append(C[r["opp"]]["def"])
                opp_off[r["team"]].append(C[r["opp"]]["off"])
        lgO, lgD = lg(C, "off"), lg(C, "def")
        for team, t in C.items():
            if opp_def[team]:
                t["offAdj"] = t["off"] - (sum(opp_def[team]) / len(opp_def[team]) - lgD)
                t["defAdj"] = t["def"] - (sum(opp_off[team]) / len(opp_off[team]) - lgO)
        qb_now, qb_prev = self._main_qb(self.cur_p, recent=True), self._main_qb(self.pri_p)
        self.teams = {}
        pO, pD = lg(P, "off"), lg(P, "def")
        for team in set(C) | set(P):
            c, p = C.get(team), P.get(team)
            n = c["g"] if c else 0
            k = 4.0 if qb_now.get(team) and qb_now.get(team) == qb_prev.get(team) else 1.5

            def blend(key, cur_lg, pri_lg, adj=None):
                cv = None if not c else (c.get(adj) if adj and c.get(adj) is not None else c.get(key))
                cv = None if cv is None else cv - cur_lg
                pv = 0.6 * (p[key] - pri_lg) if p else 0.0
                if cv is None:
                    return pv
                return (cv * n + pv * k) / (n + k)
            self.teams[team] = {
                "abbr": team, "g": n, "qb": qb_now.get(team), "qbChanged": bool(qb_prev.get(team) and qb_now.get(team) != qb_prev.get(team)),
                "off": blend("off", lgO, pO, "offAdj"), "def": blend("def", lgD, pD, "defAdj"),
                "offRaw": c["off"] if c else None, "defRaw": c["def"] if c else None,
                "succ": c["succ"] if c else None, "dsucc": c["dsucc"] if c else None,
                "passRate": (c or p or {}).get("passRate", 0.58), "proe": c["proe"] if c else None,
                "expl": c["expl"] if c else None, "dexpl": c["dexpl"] if c else None, "edEpa": c["edEpa"] if c else None,
                "dPass": c["dPassEpa"] - lg(C, "dPassEpa") if c else 0.0, "dRush": c["dRushEpa"] - lg(C, "dRushEpa") if c else 0.0,
                "plays": ((c["plays"] * n + (p["plays"] if p else 62) * 2) / (n + 2)) if c else (p["plays"] if p else 62),
            }
        pts = [g[s]["score"] for g in self.games for s in ("home", "away") if g[s].get("score") is not None]
        self.lg_pts = sum(pts) / len(pts) if pts else 22.5
        for key, desc in (("off", True), ("def", False), ("succ", True), ("dsucc", False), ("expl", True), ("dexpl", False),
                          ("edEpa", True), ("proe", True), ("plays", True), ("offRaw", True), ("defRaw", False)):
            ranked = sorted([t for t in self.teams.values() if t.get(key) is not None], key=lambda t: t[key], reverse=desc)
            for i, t in enumerate(ranked):
                t.setdefault("rank", {})[key] = i + 1

    @staticmethod
    def _main_qb(rows, recent=False):
        by = defaultdict(lambda: defaultdict(float))
        last_week = defaultdict(int)
        for r in rows:
            if r.get("position") != "QB":
                continue
            wk = int(_f(r, "week"))
            last_week[r["team"]] = max(last_week[r["team"]], wk)
        for r in rows:
            if r.get("position") != "QB":
                continue
            wk = int(_f(r, "week"))
            w = (3.0 if wk == last_week[r["team"]] else 1.0) if recent else 1.0
            by[r["team"]][r["player_display_name"]] += _f(r, "attempts") * w
        return {t: max(q, key=q.get) for t, q in by.items() if q}

    # ---------------- players
    def _players(self):
        self.plog = defaultdict(list)
        for r in sorted(self.pri_p + self.cur_p, key=lambda r: (r["season"], int(_f(r, "week")))):
            self.plog[r["player_id"]].append(r)
        self.team_week = defaultdict(lambda: defaultdict(float))     # (team, season, week) -> totals
        for r in self.cur_p + self.pri_p:
            key = (r["team"], r["season"], r["week"])
            t = self.team_week[key]
            t["targets"] += _f(r, "targets"); t["carries"] += _f(r, "carries"); t["att"] += _f(r, "attempts")
        self.lg = {"ypt": 7.6, "catch": 0.65, "ypc": 4.3, "ypa": 6.8, "cmp": 0.645, "ptd": 0.045, "int": 0.022,
                   "rectd": 0.045, "rushtd": 0.03}

    def _player_rates(self, rows):
        """recent-weighted shares and per-opportunity rates for one player (current team only)."""
        if not rows:
            return None
        team = rows[-1]["team"]
        mine = [r for r in rows if r["team"] == team][-8:]
        w = [0.5 ** ((len(mine) - 1 - i) / 3) for i in range(len(mine))]     # half-life ~3 games
        tot = lambda key: sum(wi * _f(r, key) for wi, r in zip(w, mine))
        tw = [self.team_week[(r["team"], r["season"], r["week"])] for r in mine]
        team_tot = lambda key: sum(wi * t[key] for wi, t in zip(w, tw)) or 1.0
        tg, car, att = tot("targets"), tot("carries"), tot("attempts")

        def rate(num, den, prior, k):
            return (tot(num) + prior * k) / (den + k)
        return {
            "team": team, "pos": rows[-1].get("position"), "name": rows[-1]["player_display_name"], "id": rows[-1]["player_id"],
            "headshot": rows[-1].get("headshot_url"),
            "tgtShare": tg / team_tot("targets"), "carShare": car / team_tot("carries"), "attShare": att / team_tot("att"),
            "ypt": rate("receiving_yards", tg, self.lg["ypt"], 20), "catch": rate("receptions", tg, self.lg["catch"], 20),
            "ypc": rate("rushing_yards", car, self.lg["ypc"], 30), "ypa": rate("passing_yards", att, self.lg["ypa"], 60),
            "cmp": rate("completions", att, self.lg["cmp"], 60), "ptd": rate("passing_tds", att, self.lg["ptd"], 150),
            "int": rate("passing_interceptions", att, self.lg["int"], 150),
            "rectd": rate("receiving_tds", tg, self.lg["rectd"], 60), "rushtd": rate("rushing_tds", car, self.lg["rushtd"], 60),
            "lastWeek": (mine[-1]["season"], int(_f(mine[-1], "week"))),
        }

    def status(self, team, name):
        for t in self.inj.values():
            if t.get("abbr") == team:
                for x in t["list"]:
                    if x["name"] == name:
                        return x
        return None

    def roster(self, team):
        latest = max((pr["lastWeek"] for pr in [self._player_rates(rows) for rows in self.plog.values() if rows and rows[-1]["team"] == team] if pr),
                     default=None)
        out = []
        for pid, rows in self.plog.items():
            if not rows or rows[-1]["team"] != team:
                continue
            pr = self._player_rates(rows)
            if not pr or pr["pos"] not in ("QB", "RB", "WR", "TE", "FB"):
                continue
            if latest and (pr["lastWeek"][0] != latest[0] or latest[1] - pr["lastWeek"][1] > 3):
                continue            # not on the field lately
            inj = self.status(team, pr["name"])
            st = (inj or {}).get("status") or "Active"
            pr.update(status=st, note=(inj or {}).get("note"), out=st in ("Out", "Doubtful", "Injured Reserve") or "reserve" in st.lower())
            out.append(pr)
        # redistribute shares of players who are out within their position group
        for key in ("tgtShare", "carShare"):
            for grp in (("WR", "TE"), ("RB", "FB"), ("QB",)):
                ps = [p for p in out if p["pos"] in grp]
                lost = sum(p[key] for p in ps if p["out"])
                live = [p for p in ps if not p["out"]]
                tot = sum(p[key] for p in live) or 1
                for p in live:
                    p[key] += lost * p[key] / tot
        qb = max([p for p in out if p["pos"] == "QB" and not p["out"]], key=lambda p: p["attShare"], default=None)
        for p in out:
            p["attShare"] = 1.0 if p is qb else 0.0
        return out

    # ---------------- one game
    def project_game(self, home, away, rest=None):
        th, ta = self.teams.get(home), self.teams.get(away)
        if not th or not ta:
            return None
        plays_h = (th["plays"] + 62) / 2 * 0.5 + (ta["plays"] + 62) / 2 * 0.5
        plays_a = plays_h
        ph = self.lg_pts + plays_h * 0.85 * (th["off"] + ta["def"]) + HFA / 2
        pa = self.lg_pts + plays_a * 0.85 * (ta["off"] + th["def"]) - HFA / 2
        margin = ph - pa
        sides = {}
        for side, team, me, op, m in (("home", home, th, ta, margin), ("away", away, ta, th, -margin)):
            att = me["plays"] * me["passRate"] * (1 - 0.008 * m)          # trailing teams throw more
            car = me["plays"] * (1 - me["passRate"]) * (1 + 0.012 * m)
            pf = min(1.15, max(0.85, math.exp(1.5 * op["dPass"])))
            rf = min(1.15, max(0.85, math.exp(1.5 * op["dRush"])))
            players = []
            for p in self.roster(team):
                tg, cr = att * p["tgtShare"], car * p["carShare"]
                proj = {"targets": tg, "rec": tg * p["catch"], "rec_yds": tg * p["ypt"] * pf, "car": cr, "rush_yds": cr * p["ypc"] * rf}
                if p["attShare"]:
                    proj.update(att=att, cmp=att * p["cmp"], pass_yds=att * p["ypa"] * pf, ptd=att * p["ptd"] * pf, int=att * p["int"])
                proj["td"] = tg * p["rectd"] + cr * p["rushtd"]
                proj["rectd"], proj["rushtd"] = tg * p["rectd"], cr * p["rushtd"]
                if p["out"]:
                    proj = {k: 0.0 for k in proj}
                logs = [r for r in self.plog.get(p["id"], [])][-10:]
                players.append({"name": p["name"], "pos": p["pos"], "status": p["status"], "note": p["note"], "out": p["out"],
                                "headshotNfl": p["headshot"], "projMin": round(tg + cr + (att if p["attShare"] else 0), 1),
                                "proj": {k: round(v, 2) for k, v in proj.items()},
                                "last10": {"pass_yds": [_f(r, "passing_yards") for r in logs], "rush_yds": [_f(r, "rushing_yards") for r in logs],
                                           "rec": [_f(r, "receptions") for r in logs], "rec_yds": [_f(r, "receiving_yards") for r in logs],
                                           "td": [_f(r, "rushing_tds") + _f(r, "receiving_tds") for r in logs],
                                           "att": [_f(r, "attempts") for r in logs], "car": [_f(r, "carries") for r in logs],
                                           "cmp": [_f(r, "completions") for r in logs], "ptd": [_f(r, "passing_tds") for r in logs],
                                           "int": [_f(r, "passing_interceptions") for r in logs], "targets": [_f(r, "targets") for r in logs]}})
            order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FB": 4}
            players.sort(key=lambda x: (0 if x["proj"].get("att") else 1, -(x["proj"].get("rush_yds", 0) + x["proj"].get("rec_yds", 0)), order.get(x["pos"], 9)))
            sides[side] = {"players": players, "att": round(att, 1), "car": round(car, 1)}
        return {"home": {"pts": round(ph, 1)}, "away": {"pts": round(pa, 1)}, "margin": round(margin, 1),
                "total": round(ph + pa, 1), "sides": sides}

    @staticmethod
    def p_cover(margin, home_point):
        return 1 - _phi((-home_point - margin) / SD_MARGIN)

    @staticmethod
    def p_over(total, line):
        return 1 - _phi((line - total) / SD_TOTAL)
