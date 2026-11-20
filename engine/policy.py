"""Edge policy (Nov 20, from the deep dive Vince approved): fixed rules on top of the record-based muting in
edgecard.rerank. Every edge is still logged and graded (the edge card keeps measuring them); these rules only decide
what reaches the main Edges list, alerts, hot games, the Lab and the picks.

Evidence (edge card, Oct 28 - Nov 20, 145k flagged edges, ROI clustered by game):
  * edges under 3%: -3.8% (t -3.9), 70% of all flags. 3-5%: -1.0%; at 3-5% only props the model backs paid (+8%).
  * alt lines priced by our model -4.3% (t -3.3), alternate game lines -9.0% (t -2.2), one-sided estimated fairs
    -15.4% (t -3.0).
  * taking at Fliff -12.9% (t -4.7), FanDuel Predicts -7.3% (t -3.6), ProphetX -4.8% (t -3.7) (ProphetX stays a maker venue).
  * NCAAF -8.1% (t -3.6), the fair 6 pts too high on flagged sides: only main game lines stay.
  * pick'em overs lose under 5% (3-5%: -3.9%, t -1.6) and win past it; pick'em unders win at every size (3-5%: +6.8%,
    t 3.0; Nov 14-20 holdout +8.3%). The over side of a pick'em line is priced off the main line, whose fair runs high on
    overs (alt overs priced the same way won 4-9 pts under their fair). So pick'em overs need 5%.
  * the game-line model points the wrong way when it disagrees with the market (its side -12% to -17% at 5+ pts), so it is
    no reason to keep a game-line edge (build.edges also stops ranking game lines by it).

e["muted"] = the reason and e["policy"] = True. Muted edges stay on the page in their own section."""

FLOOR = 0.03            # nothing under 3%
CORE = 0.05             # 3-5% needs model backing (props) or a pick'em line
NO_TAKE = {"fliff": "Fliff", "fanduel": "FanDuel Predicts", "prophetx": "ProphetX"}


def game_line(e):
    return not e.get("player") and not e.get("pickem")


def backed(e):
    """the model sits between the price and our fair, or near the fair: props only (the game-line model has no vote)"""
    return bool(e.get("player")) and e.get("supBand") in ("between", "near")


def reason(e):
    edge = e.get("edge") or 0
    pk = bool(e.get("pickem"))
    if edge < FLOOR:
        return "Under 3%"
    if e.get("est"):
        return "One-sided fair (estimated)"
    if e.get("model") and not pk:
        return "Alt line priced by our model"
    if game_line(e) and not e.get("main", True):
        return "Alternate game line"
    if e.get("league") == "NCAAF" and not (game_line(e) and e.get("main", True)):
        return "NCAAF outside main game lines"
    if not pk and e.get("book") in NO_TAKE:
        return f"Taking at {NO_TAKE[e['book']]}"
    if edge < CORE and not pk and not backed(e):
        return "3-5% without model backing"
    if edge < CORE and pk and e.get("side") in ("Over", "Yes"):
        return "Pick'em over under 5%"
    return None


def core(e):
    """an edge the rules keep: what alerts, hot games and the pick runs start from"""
    return not e.get("muted") and reason(e) is None


def apply(edges):
    n = 0
    for e in edges:
        r = reason(e)
        if r:
            e["muted"] = r
            e["policy"] = True
            n += 1
    edges.sort(key=lambda x: (bool(x.get("muted")), -(x.get("score") or 0)))
    return n
