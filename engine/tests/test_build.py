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

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
    print("all tests passed")
