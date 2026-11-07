"""Self-tests on synthetic data (no real odds in this public repo)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import build, oddsmath as M

T = "2026-10-27T20:00:00Z"
def bm(key, markets, t=T):
    return {"key": key, "last_update": t, "markets": [{"key": k, "last_update": t, "outcomes": o} for k, o in markets.items()]}

def test_math():
    assert M.american_from_decimal(2.5) == 150 and M.american_from_decimal(1.5) == -200
    assert abs(M.kalshi_fee(0.40) - 0.02) < 1e-9          # ceil(1.68c) = 2c
    assert abs(sum(M.no_vig([0.55, 0.50])) - 1) < 1e-12

def test_game():
    ev = {"id": "g1", "sport_key": "basketball_nba", "commence_time": T, "home_team": "H", "away_team": "A",
          "bookmakers": [
            bm("pinnacle", {"h2h": [{"name": "H", "price": 1.60}, {"name": "A", "price": 2.45}],
                            "spreads": [{"name": "H", "price": 1.92, "point": -4.5}, {"name": "A", "price": 1.92, "point": 4.5}],
                            "totals": [{"name": "Over", "price": 1.95, "point": 221.5}, {"name": "Under", "price": 1.89, "point": 221.5}]}),
            bm("draftkings", {"h2h": [{"name": "H", "price": 1.55}, {"name": "A", "price": 2.70}]}),
            bm("kalshi", {"h2h": [{"name": "H", "price": 1.64}, {"name": "A", "price": 2.50}]}),
            bm("fanduel", {"h2h": [{"name": "H", "price": 1.50}, {"name": "A", "price": 2.40}]}, t="2026-10-27T19:00:00Z"),
          ]}
    g = build.build_game(ev, "NBA")
    ml = [l for l in g["lines"] if l["market"] == "h2h"][0]
    assert ml["fairSource"] == "sharp" and abs(sum(ml["fair"].values()) - 1) < 1e-3
    dk = [q for q in ml["quotes"] if q["book"] == "draftkings"][0]
    assert dk["prices"]["A"]["edge"] > 0.02                 # +170 vs a ~+150 fair
    fd = [q for q in ml["quotes"] if q["book"] == "fanduel"][0]
    assert fd["stale"]
    ks = [q for q in ml["quotes"] if q["book"] == "kalshi"][0]
    assert ks["prices"]["A"]["cost"] > 0.40                 # fee added
    assert any(l.get("isMain") for l in g["lines"] if l["market"] == "spreads")
    e = build.edges([g], [])
    assert e and e[0]["book"] == "draftkings"

def test_three_way_kept_apart():
    ev = {"id": "g2", "sport_key": "icehockey_nhl", "commence_time": T, "home_team": "H", "away_team": "A",
          "bookmakers": [bm("pinnacle", {"h2h": [{"name": "H", "price": 1.85}, {"name": "A", "price": 2.05}]}),
                         bm("unibet_se", {"h2h": [{"name": "H", "price": 2.38}, {"name": "A", "price": 2.7}, {"name": "Draw", "price": 4.2}]})]}
    g = build.build_game(ev, "NHL")
    ml = [l for l in g["lines"] if l["market"] == "h2h"][0]
    assert ml["outcomes"] == ["A", "H"] and abs(sum(ml["fair"].values()) - 1) < 1e-3 and ml["fair"]["H"] > 0.5
    assert any(l["market"] == "h2h_3way" for l in g["lines"])

def test_extra_markets_and_alt_props():
    ev = {"id": "g3", "sport_key": "basketball_nba", "commence_time": T, "home_team": "H", "away_team": "A",
          "bookmakers": [bm("pinnacle", {
              "alternate_spreads": [{"name": "H", "price": 2.6, "point": -9.5}, {"name": "A", "price": 1.5, "point": 9.5}],
              "team_totals": [{"name": "Over", "description": "H", "price": 1.9, "point": 112.5}, {"name": "Under", "description": "H", "price": 1.9, "point": 112.5}],
              "h2h_q1": [{"name": "H", "price": 1.8}, {"name": "A", "price": 2.0}],
              "player_points": [{"name": "Over", "description": "P1", "price": 1.9, "point": 24.5}, {"name": "Under", "description": "P1", "price": 1.9, "point": 24.5}],
              "player_points_alternate": [{"name": "Over", "description": "P1", "price": 3.0, "point": 29.5}]})]}
    g = {"lines": []}
    build.add_extra_lines(ev, g)
    mk = {l["market"] for l in g["lines"]}
    assert {"alternate_spreads", "team_totals", "h2h_q1"} <= mk and "player_points" not in mk
    tt = [l for l in g["lines"] if l["market"] == "team_totals"][0]
    assert tt["team"] == "H" and abs(sum(tt["fair"].values()) - 1) < 1e-3
    docs = build.build_props(ev, "NBA")
    assert list(docs) == ["player_points"] and {r["point"] for r in docs["player_points"]["rows"]} == {24.5, 29.5}

def test_props():
    def ou(p, line, over, under, who="P1"):
        return [{"name": "Over", "description": who, "price": over, "point": line},
                {"name": "Under", "description": who, "price": under, "point": line}]
    ev = {"id": "g1", "sport_key": "basketball_nba", "commence_time": T, "home_team": "H", "away_team": "A",
          "bookmakers": [
            bm("fanduel", {"player_points": ou(0, 24.5, 1.87, 1.95)}),
            bm("draftkings", {"player_points": ou(0, 24.5, 1.91, 1.91)}),
            bm("novig", {"player_points": ou(0, 24.5, 1.95, 2.0)}),
            bm("prizepicks", {"player_points": [{"name": "Over", "description": "P1", "price": 1.0, "point": 22.5},
                                                {"name": "Under", "description": "P1", "price": 1.0, "point": 22.5}]}),
          ]}
    docs = build.build_props(ev, "NBA")
    rows = docs["player_points"]["rows"]
    main = [r for r in rows if r.get("isMain")][0]
    assert 23.5 < main["mean"] < 26
    dfs_row = [r for r in rows if r["dfs"]][0]
    d = dfs_row["dfs"][0]
    assert d["side"] == "Over" and d["p"] > 0.58          # 2 points under the market line
    e = build.edges([], [docs["player_points"]])
    assert any(x.get("pickem") for x in e)


def test_live_flag_needs_a_started_game():
    import ledger, tracker
    legs = [{"gameId": "g1", "commence": "2026-11-05T03:10:00Z"}]
    assert not ledger.is_live({"live": True, "createdAt": "2026-11-05T02:44:14Z", "legs": legs}, "/nonexistent")
    assert ledger.is_live({"live": True, "createdAt": "2026-11-05T03:30:00Z", "legs": legs}, "/nonexistent")
    assert not ledger.is_live({"live": False, "createdAt": "2026-11-05T03:30:00Z", "legs": legs}, "/nonexistent")
    p = {"rushing.rushingTouchdowns": 0, "receiving.receivingTouchdowns": 1}
    assert tracker.stat("americanfootball_nfl", "player_tds", p) == 1


def test_calibration_fit_stays_bounded():
    """heavy-tailed, overconfident model logits made the plain Newton fit diverge (a = -85.9 on NFL pass yds)"""
    import random, tracker, modelstats, numpy as np
    rnd = random.Random(7)
    xs, ys = [], []
    for _ in range(4000):
        x = rnd.choice([-6.9, 6.9]) if rnd.random() < 0.1 else rnd.gauss(0, 2.5)
        xs.append(x)
        ys.append(1.0 if rnd.random() < 1 / (1 + 2.718281828 ** (-0.05 * x)) else 0.0)
    for a, b in (tracker._fit_ab(xs, ys), modelstats._fit_ab(np.array(xs), np.array(ys))):
        assert abs(a) < 1 and 0.2 <= b <= 1.5, (a, b)
        assert b == 0.2           # the best b is under the floor: held there with a refit for it

def test_team_names_match_espn():
    """sportsbook team names that ESPN spells differently still resolve (their games never graded before)"""
    import refs
    mk = lambda i, ab, name, short: {"id": i, "abbr": ab, "name": name, "short": short}
    recs = [mk("1", "LAC", "LA Clippers", "LA Clippers"), mk("2", "LAL", "Los Angeles Lakers", "Lakers"),
            mk("3", "KSU", "Kansas State Wildcats", "Kansas St"), mk("4", "KU", "Kansas Jayhawks", "Kansas"),
            mk("5", "SLP", "Slavia Prague", "Slavia Prague"), mk("6", "BOU", "AFC Bournemouth", "Bournemouth"),
            mk("7", "LA", "LA Galaxy", "LA"), mk("8", "LAFC", "LAFC", "LAFC")]
    teams = {refs.norm(r["name"]): r for r in recs}
    want = {"Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Kansas St Wildcats": "KSU", "Kansas Jayhawks": "KU",
            "Slavia Praha": "SLP", "Bournemouth": "BOU", "Los Angeles FC": "LAFC", "Duke Blue Devils": None}
    for name, ab in want.items():
        t = refs.find(teams, name)
        assert (t and t["abbr"]) == ab, (name, t)


def test_box_score_names_match_bet_names():
    import refs
    rows = [("Matt Hibner", "BAL"), ("Bryan Thomas Jr.", "JAX"), ("Chris Rodriguez Jr.", "JAX"), ("Mike Smith", "A"), ("Michael Smith", "B")]
    box = {refs.norm(n): {"name": n, "team": t} for n, t in rows}
    want = [("Matthew Hibner", None, "Matt Hibner"), ("Brian Thomas Jr", "JAX", None), ("Christopher Rodriguez", "JAX", "Chris Rodriguez Jr."),
            ("Mikey Smith", None, None), ("Michael Smith", "B", "Michael Smith"), ("Matthew Hibner", "JAX", None)]
    for name, team, hit in want:
        p = refs.find_player(box, name, team)
        assert (p and p["name"]) == hit, (name, team, p)


def test_football_zero_stat_markets():
    import ledger
    assert ledger._football_counting("americanfootball_nfl", "player_reception_yds")
    assert ledger._football_counting("americanfootball_nfl", "player_rush_attempts")
    assert not ledger._football_counting("americanfootball_nfl", "player_1st_td")


def test_nba_points_shape():
    # NBA points / PRA use the wider Nov 6 shape pre-game; WNBA, NCAAB and in-play keep the old one
    assert build.DISPERSION["basketball_nba"]["player_points"] == 2.8
    assert build.DISPERSION["basketball_wnba"]["player_points"] == 2.0 and build.DISPERSION["basketball_ncaab"]["player_points_rebounds_assists"] == 2.5
    wide, old = build.p_over("player_points", 20, 26.5, "basketball_nba"), build.p_over("player_points", 20, 26.5, "basketball_wnba")
    assert wide > old
    assert abs(build.p_over("player_points", 20, 26.5, "basketball_nba", inplay=True) - old) < 1e-12
    assert abs(build.p_over("player_assists", 8, 9.5, "basketball_nba", inplay=True) - build.p_over("player_assists", 8, 9.5, "basketball_nba")) < 1e-12


def test_nba_rebounds_market_vs_model_shape():
    # Nov 7: market pricing of NBA rebounds (from the sharp mean) is narrower (1.3); the stat model, in-play and WNBA keep 1.5
    assert build.DISPERSION["basketball_nba"]["player_rebounds"] == 1.3
    assert build.DISPERSION["basketball_wnba"]["player_rebounds"] == 1.5
    mkt = build.p_over("player_rebounds", 8, 11.5, "basketball_nba")
    mdl = build.p_over("player_rebounds", 8, 11.5, "basketball_nba", model=True)
    old = build.p_over("player_rebounds", 8, 11.5, "basketball_wnba")
    assert mkt < mdl and abs(mdl - old) < 1e-12
    assert abs(build.p_over("player_rebounds", 8, 11.5, "basketball_nba", inplay=True) - old) < 1e-12
    assert abs(build.p_over("player_points", 20, 26.5, "basketball_nba", model=True) - build.p_over("player_points", 20, 26.5, "basketball_nba")) < 1e-12


def test_fairadj_gate_needs_applied_shifts():
    # a sport scored only on days before any shift was fit must not pass the gate on float noise (UEL, Nov 7)
    import fairadj
    live = [{"d": "2026-11-05", "g": "soccer_x|line|totals", "f": 0.3 + 0.004 * i, "y": float(i % 2), "gid": f"g{i % 8}"} for i in range(120)]
    import tempfile, json
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "track", "graded"))
    with open(os.path.join(d, "track", "graded", "soccer_x.jsonl"), "w") as fh:
        for r in live:
            fh.write(json.dumps(r) + "\n")
    res = fairadj.compute(d, log=lambda *a: None)
    assert not res["shifts"] and not any(e["on"] for e in res["eval"].values())


def test_suspended_tennis_is_not_final():
    import finals, json, tempfile
    from datetime import datetime, timezone
    now = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "finals"))
    day = "2026-11-05"
    susp = {"tennis": True, "home": {"name": "A B", "winner": False, "score": 1}, "away": {"name": "C D", "winner": False, "score": 0}}
    wo = {"tennis": True, "home": {"name": "E F", "winner": False, "score": 0}, "away": {"name": "G H", "winner": True, "score": 0}}
    json.dump({"tennis_atp_x|1": susp, "tennis_atp_x|2": wo}, open(os.path.join(d, "finals", f"{day}.json"), "w"))
    json.dump({"v": 2, "at": now.isoformat(), "done": {f"tennis/atp|{day}": True}}, open(os.path.join(d, "finals", "state.json"), "w"))
    out = finals.collect(d, [], [], now, log=lambda *a: None)
    assert "tennis_atp_x|1" not in out and "tennis_atp_x|2" in out
    assert not json.load(open(os.path.join(d, "finals", "state.json"))).get("done", {}).get(f"tennis/atp|{day}")


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("all tests passed")
