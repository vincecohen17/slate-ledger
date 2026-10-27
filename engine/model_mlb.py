"""MLB model built on Statcast (Baseball Savant) and MLB StatsAPI.

Per plate appearance, every batter has a rate for each outcome (K, BB, 1B, 2B, 3B, HR):
  * this season's rates, half actual and half Statcast-expected (xBA, xSLG, barrels), regressed toward
    last season and the league with stabilization points per outcome (K settles fast, singles slowly);
  * a platoon factor vs the starter's hand from Statcast splits (this season + last), regressed toward
    the league platoon gap.
Starters and bullpens get the same treatment for what they allow. Batter vs pitcher is combined with
the odds-ratio method (batter rate x pitcher rate / league rate), then the park (Statcast park factors,
per outcome) and weather (temperature, wind) move each outcome.

Plate appearances come from the lineup slot and the team's on-base rate; the starter faces the order
until his usual batters-faced count (from his recent starts), then the bullpen pitches. Runs come from
linear weights per outcome, scored as a negative binomial per team, which gives win, run line, totals,
team totals and first-five lines. Batter props (hits, total bases, home runs, singles, doubles, walks,
strikeouts) are exact distributions over each plate appearance; runs, RBIs and H+R+RBI use their means.
Pitcher props (strikeouts, hits, walks, outs, earned runs) walk through the batters he is expected to face.
"""
import math
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import stats as ST

EV = ("k", "bb", "s1", "s2", "s3", "hr")
LW = {"bb": 0.69, "s1": 0.88, "s2": 1.24, "s3": 1.56, "hr": 1.99}     # linear weights (runs above an out, wOBA scale)
WOBA_SCALE = 1.23
STAB_BAT = {"k": 60, "bb": 120, "s1": 290, "s2": 500, "s3": 900, "hr": 170}
STAB_PIT = {"k": 70, "bb": 170, "s1": 600, "s2": 900, "s3": 1500, "hr": 400}
SLOT_RBI = [0.85, 0.9, 1.08, 1.12, 1.08, 1.02, 0.96, 0.92, 0.88]
SLOT_RUN = [1.1, 1.08, 1.04, 1.0, 0.97, 0.95, 0.93, 0.92, 0.95]
K_TEAM_FULL, K_TEAM_F5 = 2.1, 1.8          # variance / mean of team runs
HOME_XTRA = 0.52                            # home share of games tied after nine
HEADSHOT = "https://img.mlbstatic.com/mlb-photos/image/upload/d_people:generic:headshot:67:current.png/w_213,q_auto:best/v1/people/{}/headshot/67/current"


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _nb_pmf(mean, k, n=30):
    """negative binomial pmf with variance k*mean (Poisson when k <= 1)"""
    mean = max(mean, 1e-6)
    out = []
    if k <= 1.0001:
        p = math.exp(-mean)
        for x in range(n):
            out.append(p)
            p *= mean / (x + 1)
        return out
    r, q = mean / (k - 1), 1.0 / k
    logp = r * math.log(q)
    for x in range(n):
        out.append(math.exp(logp))
        logp += math.log((x + r) / (x + 1)) + math.log(1 - q)
    return out


def _norm(name):
    import refs as R
    return R.norm(name or "")


class MLB:
    def __init__(self, data_dir):
        self.dd = data_dir
        today = (datetime.now(timezone.utc) - timedelta(hours=7)).date()
        self.year = today.year if today.month >= 3 else today.year - 1
        J = lambda rel: ST.load_json(data_dir, f"stats/mlb/{rel}") or {}
        self.sched = J("schedule.json")
        self.people = J("people.json")
        self.bat = {y: J(f"savant/{y}/batters.json") for y in (self.year, self.year - 1)}
        self.pit = {y: J(f"savant/{y}/pitchers.json") for y in (self.year, self.year - 1)}
        self.arsenal = J("savant/arsenal.json")
        self.splits = J("savant/splits.json")
        self.parks = J("savant/parks.json")
        teams = J("teams.json")
        self.inj = (ST.load_json(data_dir, "stats/mlb/injuries.json") or {}).get("teams", {})
        self._league()
        self._team_pitching(teams)
        self._platoon()
        self._pitcher_ranks()

    # ---------------- league baselines
    def _league(self):
        tot = defaultdict(float)
        for b in self.bat[self.year].values() or self.bat[self.year - 1].values():
            for k_src, k in (("pa", "pa"), ("strikeout", "k"), ("walk", "bb"), ("single", "s1"), ("double", "s2"), ("triple", "s3"),
                             ("home_run", "hr"), ("ab", "ab")):
                tot[k] += b.get(k_src) or 0
            tot["xwoba_n"] += (b.get("xwoba") or 0) * (b.get("pa") or 0)
            tot["barrel_n"] += (b.get("barrel_batted_rate") or 0) * max(0, (b.get("ab") or 0) - (b.get("strikeout") or 0))
            tot["bbe"] += max(0, (b.get("ab") or 0) - (b.get("strikeout") or 0))
        pa = tot["pa"] or 1
        self.lg = {k: tot[k] / pa for k in EV} if tot["pa"] else {"k": 0.222, "bb": 0.084, "s1": 0.142, "s2": 0.043, "s3": 0.0035, "hr": 0.030}
        self.lg["bb"] += 0.011          # hit by pitch rides with walks everywhere
        self.lg_xwoba = tot["xwoba_n"] / pa if tot["pa"] else 0.315
        self.lg_barrel = tot["barrel_n"] / tot["bbe"] if tot["bbe"] else 8.0
        self.lg_woba = self._woba(self.lg)
        self.lg_reach = reach(self.lg)
        self.lg_rpg = 4.4
        self.lg_pa_g = 38.0

    def _woba(self, r):
        return sum(LW[k] * r.get(k, 0) for k in LW) / max(1e-9, 1 - r.get("hbp", 0)) if r else 0.315

    def _team_pitching(self, teams):
        self.team_pit, self.team_hit, games, runs, pa = {}, {}, 0, 0, 0
        for sp in ((teams.get("hitting") or {}).get("stats") or [{}])[0].get("splits", []):
            s = sp["stat"]
            self.team_hit[sp["team"]["name"]] = {"rpg": s.get("runs", 0) / max(1, s.get("gamesPlayed", 1)), "ops": float(s.get("ops", 0) or 0)}
            games += s.get("gamesPlayed", 0); runs += s.get("runs", 0); pa += s.get("plateAppearances", 0)
        if games:
            self.lg_rpg, self.lg_pa_g = runs / games, pa / games
        for sp in ((teams.get("pitching") or {}).get("stats") or [{}])[0].get("splits", []):
            s, name = sp["stat"], sp["team"]["name"]
            bf = s.get("battersFaced") or 0
            if not bf:
                continue
            h, hr = s.get("hits", 0), s.get("homeRuns", 0)
            d2, d3 = s.get("doubles", 0), s.get("triples", 0)
            raw = {"k": s.get("strikeOuts", 0) / bf, "bb": (s.get("baseOnBalls", 0) + s.get("hitByPitch", 0)) / bf, "hr": hr / bf,
                   "s2": d2 / bf, "s3": d3 / bf, "s1": max(0, h - hr - d2 - d3) / bf}
            # bullpen proxy: the whole staff, regressed halfway to the league
            self.team_pit[name] = {"rates": {k: 0.5 * raw[k] + 0.5 * self.lg[k] for k in EV}, "era": float(s.get("era", 4.2) or 4.2)}

    def _platoon(self):
        """league platoon factors: xwOBA (and K%) vs same-hand and opposite-hand pitchers relative to overall"""
        acc = defaultdict(lambda: [0.0, 0.0, 0.0])        # (same?) -> [xwoba*pa, k*pa, pa]
        for pid, s in (self.splits.get("batter") or {}).items():
            bats = (self.people.get(pid) or {}).get("bats")
            if bats not in ("L", "R"):
                continue
            for h in ("L", "R"):
                v = s.get("v" + h) or {}
                if v.get("pa") and v.get("xwoba") is not None:
                    key = "same" if h == bats else "opp"
                    acc[key][0] += v["xwoba"] * v["pa"]; acc[key][1] += (v.get("k_percent") or 0) / 100 * v["pa"]; acc[key][2] += v["pa"]
        tot = [sum(a[i] for a in acc.values()) for i in range(3)]
        base_x, base_k = (tot[0] / tot[2], tot[1] / tot[2]) if tot[2] else (0.315, 0.22)
        self.plat_lg = {}
        for key in ("same", "opp"):
            a = acc.get(key)
            self.plat_lg[key] = (a[0] / a[2] / base_x, a[1] / a[2] / base_k) if a and a[2] > 1000 else ((0.93, 1.08) if key == "same" else (1.03, 0.96))

    # ---------------- rates
    def _rates(self, pid, table, stab, kind):
        """per-PA outcome rates, actual + Statcast-expected, regressed to last season and the league"""
        pid = str(pid)
        cur, pri = table[self.year].get(pid), table[self.year - 1].get(pid)

        def obs(r):
            pa = r.get("pa") or 0
            if not pa:
                return None, 0
            ab = r.get("ab") or max(0, pa - (r.get("walk") or 0) - 0.012 * pa)
            k, bb = (r.get("strikeout") or 0) / pa, (r.get("walk") or 0) / pa + 0.011
            hr = (r.get("home_run") or 0) / pa
            if kind == "bat":
                s1, s2, s3 = (r.get("single") or 0) / pa, (r.get("double") or 0) / pa, (r.get("triple") or 0) / pa
            else:
                h = (r.get("hit") or 0) / pa
                nonhr = max(0.0, h - hr) if h else None
                lg_nonhr = self.lg["s1"] + self.lg["s2"] + self.lg["s3"]
                f = (nonhr / lg_nonhr) if nonhr is not None and lg_nonhr else 1.0
                s1, s2, s3 = self.lg["s1"] * f, self.lg["s2"] * f, self.lg["s3"] * f
            act = {"k": k, "bb": bb, "s1": s1, "s2": s2, "s3": s3, "hr": hr}
            # Statcast-expected: hits from xBA, total bases from xSLG, home runs from barrels
            xba, xslg, brl = r.get("xba"), r.get("xslg"), r.get("barrel_batted_rate")
            if xba is not None and xslg is not None and ab:
                bbe = max(0.0, ab - (r.get("strikeout") or 0)) / pa
                xhr = 0.55 * (brl if brl is not None else self.lg_barrel) / 100 * bbe
                xh, xtb = xba * ab / pa, xslg * ab / pa
                x3 = s3
                x2 = max(0.0, xtb - xh - 3 * xhr - 2 * x3)
                x1 = max(0.0, xh - x2 - x3 - xhr)
                exp = {"k": k, "bb": bb, "s1": x1, "s2": x2, "s3": x3, "hr": xhr}
                act = {e: 0.5 * act[e] + 0.5 * exp[e] for e in EV}
            return act, pa
        c, nc = obs(cur) if cur else (None, 0)
        p, npr = obs(pri) if pri else (None, 0)
        out = {}
        for e in EV:
            prior = self.lg[e]
            if p:
                w = 0.7 * npr
                prior = (p[e] * w + self.lg[e] * stab[e]) / (w + stab[e])
            out[e] = (c[e] * nc + prior * stab[e]) / (nc + stab[e]) if c else prior
        out["_pa"], out["_paPrior"] = nc, npr
        return out

    def batter(self, pid):
        return self._rates(pid, self.bat, STAB_BAT, "bat")

    def pitcher(self, pid):
        return self._rates(pid, self.pit, STAB_PIT, "pit")

    def _plat(self, pid, side_key, hand_vs, own_hand):
        """multiplier on reach/power outcomes and on K for this player vs a hand (regressed)"""
        if own_hand not in ("L", "R") or hand_vs not in ("L", "R"):
            return 1.0, 1.0
        lgx, lgk = self.plat_lg["same" if own_hand == hand_vs else "opp"]
        s = (self.splits.get(side_key) or {}).get(str(pid)) or {}
        v, o = s.get("v" + hand_vs) or {}, s.get("v" + ("R" if hand_vs == "L" else "L")) or {}
        pa, pa2 = v.get("pa") or 0, o.get("pa") or 0
        if pa and v.get("xwoba") is not None and pa2 and o.get("xwoba") is not None:
            allx = (v["xwoba"] * pa + o["xwoba"] * pa2) / (pa + pa2)
            allk = ((v.get("k_percent") or 0) * pa + (o.get("k_percent") or 0) * pa2) / (pa + pa2)
            ix = v["xwoba"] / allx if allx else 1
            ik = (v.get("k_percent") or 0) / allk if allk else 1
            mx = (ix * pa + lgx * 350) / (pa + 350)
            mk = (ik * pa + lgk * 200) / (pa + 200)
            return mx, mk
        return lgx, lgk

    def _pitcher_ranks(self):
        pool = [(pid, r) for pid, r in self.pit[self.year].items() if (r.get("p_formatted_ip") or 0) >= 50]
        self.pit_pool = len(pool)
        self.pit_rank = defaultdict(dict)
        for key, desc in (("xera", False), ("xwoba", False), ("k_percent", True), ("bb_percent", False), ("whiff_percent", True),
                          ("barrel_batted_rate", False), ("hard_hit_percent", False)):
            ranked = sorted([x for x in pool if x[1].get(key) is not None], key=lambda x: x[1][key], reverse=desc)
            for i, (pid, _) in enumerate(ranked):
                self.pit_rank[pid][key] = i + 1

    # ---------------- schedule / lineups
    def game(self, home, away, commence):
        """the StatsAPI game for an Odds API matchup (closest start time)"""
        nh, na = _norm(home), _norm(away)
        best, bd = None, 1e9
        t0 = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        for d in self.sched.get("dates", []):
            for g in d.get("games", []):
                th, ta = g["teams"]["home"]["team"]["name"], g["teams"]["away"]["team"]["name"]
                if _norm(th) == nh and _norm(ta) == na or (_norm(th).split()[-1:] == nh.split()[-1:] and _norm(ta).split()[-1:] == na.split()[-1:]):
                    dt = abs((datetime.fromisoformat(g["gameDate"].replace("Z", "+00:00")) - t0).total_seconds())
                    if dt < bd:
                        best, bd = g, dt
        return best if bd < 18 * 3600 else None

    def lineup(self, g, side):
        """posted lineup, else the team's most recent posted lineup (projected)"""
        key = "homePlayers" if side == "home" else "awayPlayers"
        lu = (g.get("lineups") or {}).get(key) or []
        if lu:
            return [{"id": p["id"], "name": p.get("fullName"), "pos": (p.get("primaryPosition") or {}).get("abbreviation")} for p in lu[:9]], True
        tid = g["teams"][side]["team"]["id"]
        last = None
        for d in self.sched.get("dates", []):
            for x in d.get("games", []):
                if x["gamePk"] == g["gamePk"] or x["gameDate"] >= g["gameDate"]:
                    continue
                for s2, k2 in (("home", "homePlayers"), ("away", "awayPlayers")):
                    if x["teams"][s2]["team"]["id"] == tid and (x.get("lineups") or {}).get(k2):
                        if not last or x["gameDate"] > last[0]:
                            last = (x["gameDate"], x["lineups"][k2])
        if last:
            return [{"id": p["id"], "name": p.get("fullName"), "pos": (p.get("primaryPosition") or {}).get("abbreviation")} for p in last[1][:9]], False
        # nothing posted recently: best nine hitters on the roster by plate appearances
        roster = [(pid, p) for pid, p in self.people.items() if p.get("team") == tid and p.get("pos") not in ("P", "TWP") and p.get("hit")]
        roster.sort(key=lambda x: -sum((r.get("pa") or 0) for r in x[1]["hit"]))
        return [{"id": int(pid), "name": p["name"], "pos": p.get("pos")} for pid, p in roster[:9]], False

    def _bf_mean(self, pid):
        logs = [r for r in ((self.people.get(str(pid)) or {}).get("pit") or []) if r.get("gs")]
        bfs = [r["bf"] for r in logs[-8:] if r.get("bf")]
        return (sum(bfs) + 22.5 * 3) / (len(bfs) + 3) if bfs else 22.5

    # ---------------- per-PA matchup
    def _pa(self, b, p, plat_b, plat_p, park, wx):
        out = {}
        for e in EV:
            lg = self.lg[e] or 1e-6
            m = b[e] * p[e] / lg
            if e == "k":
                m *= plat_b[1] * plat_p[1]
            else:
                m *= plat_b[0] * plat_p[0]
            pf = park.get({"k": "so", "bb": "bb", "s1": "1b", "s2": "2b", "s3": "3b", "hr": "hr"}[e])
            if pf:
                m *= 1 + 0.8 * (pf / 100 - 1)
            if e == "hr":
                m *= wx
            out[e] = m
        tot = sum(out.values())
        if tot > 0.75:
            out = {e: v * 0.75 / tot for e, v in out.items()}
        out["out"] = 1 - sum(out[e] for e in EV)
        return out

    def _weather(self, g, park):
        w = g.get("weather") or {}
        cond = (w.get("condition") or "").lower()
        if any(x in cond for x in ("dome", "roof closed")):
            return 1.0, "roof closed"
        try:
            temp = float(w.get("temp"))
        except (TypeError, ValueError):
            return 1.0, None
        f = 1 + 0.01 * (temp - 70)
        m = re.match(r"(\d+)\s*mph,\s*(.*)", w.get("wind") or "")
        note = f"{int(temp)}°F"
        if m:
            mph, dirn = int(m.group(1)), m.group(2).lower()
            if "out" in dirn and mph > 5:
                f *= 1 + 0.015 * (mph - 5)
            elif "in " in dirn + " " and mph > 5:
                f *= 1 - 0.015 * (mph - 5)
            note += f", wind {mph} mph {m.group(2)}"
        return max(0.8, min(1.25, f)), note

    def _side(self, lineup, sp, sp_hand, pen, park, wx, outs):
        """plate appearances and expected outcomes for one batting team"""
        rows = []
        pen_r = (pen or {}).get("rates") or self.lg
        for i, b in enumerate(lineup):
            info = self.people.get(str(b["id"])) or {}
            bats = info.get("bats")
            hand = ("R" if sp_hand == "L" else "L") if bats == "S" else bats
            br = self.batter(b["id"])
            pb = self._plat(b["id"], "batter", sp_hand, hand)
            ps = self._plat(sp["id"], "pitcher", hand, sp_hand) if sp else (1.0, 1.0)
            vs_sp = self._pa(br, sp["rates"] if sp else self.lg, pb, ps, park, wx)
            pb_pen = (self.plat_lg["opp"][0] * 0.7 + self.plat_lg["same"][0] * 0.3, 1.0)
            vs_pen = self._pa(br, pen_r, pb_pen, (1.0, 1.0), park, wx)
            rows.append({**b, "bats": bats, "rates": br, "vsSp": vs_sp, "vsPen": vs_pen, "platX": round(pb[0], 3)})
        # team PA from on-base rate vs this pitching, scaled so an average offense gets the league's PA per game
        rch = sum(reach(r["vsSp"]) for r in rows) / max(1, len(rows))
        team_pa = self.lg_pa_g * (outs / 26.4) * (1 - self.lg_reach) / max(0.5, 1 - rch)
        bf = sp["bf"] if sp else 0
        for i, r in enumerate(rows):
            slot = i + 1
            pas = []
            for k in range(7):
                n = slot + 9 * k
                p_exist = _phi((team_pa - n + 0.5) / 2.6)
                p_sp = _phi((bf - n + 0.5) / 3.5) if bf else 0.0
                if p_exist < 0.01:
                    break
                q = min(1.0, p_sp / p_exist) if p_exist else 0
                mix = {e: q * r["vsSp"][e] + (1 - q) * r["vsPen"][e] for e in list(EV) + ["out"]}
                pas.append((p_exist, mix, min(p_exist, p_sp)))
            r["pas"] = pas
            r["paExp"] = sum(p for p, _, _ in pas)
            r["paVsSp"] = sum(s for _, _, s in pas)
        return rows, team_pa

    def _runs(self, rows, innings_frac=1.0, f5=False):
        """expected runs for a batting team from linear weights"""
        lg_r_pa = self.lg_rpg / self.lg_pa_g
        tot = 0.0
        for r in rows:
            for p_exist, mix, p_sp in r["pas"]:
                woba = sum(LW[e] * mix[e] for e in LW)
                w = p_exist
                if f5:
                    w = min(p_exist, p_sp) if p_sp else 0
                tot += w * (lg_r_pa + (woba - self.lg_woba) / WOBA_SCALE)
        return max(0.3, tot)

    # ---------------- game projection
    def project_game(self, home, away, commence):
        g = self.game(home, away, commence)
        if not g:
            return None
        venue = str((g.get("venue") or {}).get("id"))
        park = self.parks.get(venue) or {}
        wx, wnote = self._weather(g, park)
        out = {"gamePk": g["gamePk"], "venue": (g.get("venue") or {}).get("name"), "park": park, "weather": g.get("weather"), "wxNote": wnote,
               "wx": round(wx, 3), "series": g.get("seriesDescription"), "sides": {}}
        sps = {}
        for side in ("home", "away"):
            pp = g["teams"][side].get("probablePitcher")
            if pp:
                info = self.people.get(str(pp["id"])) or {}
                sps[side] = {"id": pp["id"], "name": pp.get("fullName") or info.get("name"), "hand": info.get("throws"),
                             "rates": self.pitcher(pp["id"]), "bf": self._bf_mean(pp["id"])}
        for side, opp in (("home", "away"), ("away", "home")):
            lineup, confirmed = self.lineup(g, side)
            sp = sps.get(opp)
            pen = self.team_pit.get(g["teams"][opp]["team"]["name"])
            if pen and g.get("gameType") in ("F", "D", "L", "W"):
                # postseason: the best relievers pitch a bigger share of the innings
                pen = dict(pen, rates={e: v * (1.06 if e == "k" else 0.93) for e, v in pen["rates"].items()})
            outs = 25.8 if side == "home" else 27.0
            rows, team_pa = self._side(lineup, sp, (sp or {}).get("hand"), pen, park, wx, outs)
            out["sides"][side] = {"lineup": rows, "confirmed": confirmed, "teamPa": team_pa, "sp": sps.get(side),
                                  "pen": pen, "team": g["teams"][side]["team"], "record": g["teams"][side].get("leagueRecord")}
        for side, opp in (("home", "away"), ("away", "home")):
            rows = out["sides"][side]["lineup"]
            out["sides"][side]["runs"] = self._runs(rows)
            out["sides"][side]["runsF5"] = self._runs(rows, f5=True) if sps.get(opp) else out["sides"][side]["runs"] * 5 / 9
            self._starter_line(out, opp, side)
        # runs scale: keep the league-average matchup at the league scoring level
        rh, ra = out["sides"]["home"]["runs"], out["sides"]["away"]["runs"]
        out["home"], out["away"] = {"runs": round(rh, 2)}, {"runs": round(ra, 2)}
        self._dist(out)
        self._batter_props(out)
        return out

    def _starter_line(self, out, side, batting_side):
        """the side's starter facing batting_side's lineup: K, BB, H, outs, earned runs"""
        sp = out["sides"][side].get("sp")
        if not sp:
            return
        rows = out["sides"][batting_side]["lineup"]
        if not rows:
            return
        k = bb = h = outs = runs = 0.0
        dist_k = [1.0]
        bf = sp["bf"]
        lg_r_pa = self.lg_rpg / self.lg_pa_g
        for j in range(1, 40):
            p_face = _phi((bf - j + 0.5) / 3.5)
            if p_face < 0.01:
                break
            r = rows[(j - 1) % len(rows)]["vsSp"]
            k += p_face * r["k"]; bb += p_face * r["bb"]; h += p_face * (r["s1"] + r["s2"] + r["s3"] + r["hr"])
            outs += p_face * r["out"] * 1.03 + p_face * r["k"]         # outs incl. a few double plays / runners thrown out
            woba = sum(LW[e] * r[e] for e in LW)
            runs += p_face * (lg_r_pa + (woba - self.lg_woba) / WOBA_SCALE)
            pk = p_face * r["k"]
            nd = [0.0] * (len(dist_k) + 1)
            for i, v in enumerate(dist_k):
                nd[i] += v * (1 - pk); nd[i + 1] += v * pk
            dist_k = nd
        sp["proj"] = {"k": round(k, 2), "bb": round(bb, 2), "h": round(h, 2), "outs": round(min(outs, 27), 1), "er": round(runs * 0.92, 2),
                      "bf": round(bf, 1)}
        sp["kDist"] = [round(x, 5) for x in dist_k[:20]]

    def _dist(self, out):
        H, A = out["sides"]["home"]["runs"], out["sides"]["away"]["runs"]
        ph, pa = _nb_pmf(H, K_TEAM_FULL), _nb_pmf(A, K_TEAM_FULL)
        h5, a5 = _nb_pmf(out["sides"]["home"]["runsF5"], K_TEAM_F5), _nb_pmf(out["sides"]["away"]["runsF5"], K_TEAM_F5)
        out["_pmf"] = {"full": (ph, pa), "f5": (h5, a5)}
        win = sum(ph[i] * pa[j] for i in range(30) for j in range(30) if i > j)
        tie = sum(ph[i] * pa[i] for i in range(30))
        out["pHome"] = win + HOME_XTRA * tie
        out["margin"] = round(H - A, 2)
        out["total"] = round(H + A, 2)

    def prob(self, out, kind, period, point=None):
        """kind: win | win3 | cover (home, point) | over (total) | team_over (side, point)"""
        ph, pa = out["_pmf"]["f5" if period == "f5" else "full"]
        N = range(30)
        if kind == "win":
            w = sum(ph[i] * pa[j] for i in N for j in N if i > j)
            t = sum(ph[i] * pa[i] for i in N)
            return w + HOME_XTRA * t if period != "f5" else w / max(1e-9, 1 - t)
        if kind == "win3":
            w = sum(ph[i] * pa[j] for i in N for j in N if i > j)
            t = sum(ph[i] * pa[i] for i in N)
            return w, t, 1 - w - t
        if kind == "cover":     # home + point > away, pushes removed
            c = sum(ph[i] * pa[j] for i in N for j in N if i + point > j)
            push = sum(ph[i] * pa[j] for i in N for j in N if abs(i + point - j) < 1e-9)
            return c / max(1e-9, 1 - push)
        if kind == "over":
            o = sum(ph[i] * pa[j] for i in N for j in N if i + j > point)
            push = sum(ph[i] * pa[j] for i in N for j in N if abs(i + j - point) < 1e-9)
            return o / max(1e-9, 1 - push)
        if kind == "team_over":
            side, line = point
            pm = ph if side == "home" else pa
            o = sum(pm[i] for i in N if i > line)
            push = sum(pm[i] for i in N if abs(i - line) < 1e-9)
            return o / max(1e-9, 1 - push)

    # ---------------- batter props
    def _batter_props(self, out):
        for side in ("home", "away"):
            S = out["sides"][side]
            for i, r in enumerate(S["lineup"]):
                tb_d, h_d, hr_d, s1_d, s2_d, s3_d, bb_d, k_d = ([1.0] for _ in range(8))
                mean = defaultdict(float)
                for p_exist, mix, _ in r["pas"]:
                    ph = mix["s1"] + mix["s2"] + mix["s3"] + mix["hr"]
                    tb_d = _conv(tb_d, p_exist, {1: mix["s1"], 2: mix["s2"], 3: mix["s3"], 4: mix["hr"]})
                    h_d = _conv(h_d, p_exist, {1: ph})
                    hr_d = _conv(hr_d, p_exist, {1: mix["hr"]})
                    s1_d = _conv(s1_d, p_exist, {1: mix["s1"]})
                    s2_d = _conv(s2_d, p_exist, {1: mix["s2"]})
                    s3_d = _conv(s3_d, p_exist, {1: mix["s3"]})
                    bb_d = _conv(bb_d, p_exist, {1: mix["bb"]})
                    k_d = _conv(k_d, p_exist, {1: mix["k"]})
                    for e in EV:
                        mean[e] += p_exist * mix[e]
                    mean["rbi"] += p_exist * (1.55 * mix["hr"] + 0.75 * mix["s3"] + 0.6 * mix["s2"] + 0.3 * mix["s1"] + 0.05 * mix["bb"] + 0.04 * mix["out"]) * SLOT_RBI[i % 9]
                    mean["r"] += p_exist * (mix["hr"] + 0.6 * mix["s3"] + 0.45 * mix["s2"] + 0.27 * (mix["s1"] + mix["bb"])) * SLOT_RUN[i % 9]
                h = mean["s1"] + mean["s2"] + mean["s3"] + mean["hr"]
                tb = mean["s1"] + 2 * mean["s2"] + 3 * mean["s3"] + 4 * mean["hr"]
                sb_rate = self._sb_rate(r["id"])
                r["proj"] = {"h": round(h, 3), "tb": round(tb, 3), "hr": round(mean["hr"], 3), "s1": round(mean["s1"], 3), "s2": round(mean["s2"], 3),
                             "s3": round(mean["s3"], 4), "bb": round(mean["bb"], 3), "k": round(mean["k"], 3), "r": round(mean["r"], 3),
                             "rbi": round(mean["rbi"], 3), "hrr": round(h + mean["r"] + mean["rbi"], 3),
                             "sb": round(sb_rate * (mean["s1"] + mean["bb"]), 3), "pa": round(r["paExp"], 2)}
                r["dist"] = {"tb": tb_d, "h": h_d, "hr": hr_d, "s1": s1_d, "s2": s2_d, "s3": s3_d, "bb": bb_d, "k": k_d}

    def _sb_rate(self, pid):
        b = self.bat[self.year].get(str(pid)) or self.bat[self.year - 1].get(str(pid)) or {}
        on = (b.get("single") or 0) + (b.get("walk") or 0)
        return (b.get("r_total_stolen_base") or 0) / on if on else 0.03


def reach(r):
    return sum(r.get(e, 0) for e in ("bb", "s1", "s2", "s3", "hr"))


def _conv(dist, p_exist, outcomes):
    """add one plate appearance (present with p_exist) to a distribution of a count"""
    step = max(outcomes) if outcomes else 1
    nd = [0.0] * (len(dist) + step)
    p_none = 1 - p_exist + p_exist * (1 - sum(outcomes.values()))
    for i, v in enumerate(dist):
        if not v:
            continue
        nd[i] += v * p_none
        for val, p in outcomes.items():
            nd[i + val] += v * p_exist * p
    while len(nd) > 1 and nd[-1] < 1e-9:
        nd.pop()
    return nd


def p_over_dist(dist, line):
    """P(X > line) from a pmf list, pushes removed on whole-number lines"""
    over = sum(v for i, v in enumerate(dist) if i > line)
    push = sum(v for i, v in enumerate(dist) if abs(i - line) < 1e-9)
    return over / max(1e-9, 1 - push)
