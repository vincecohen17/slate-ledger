"""The playbook: what the graded record says to lean on and to avoid right now (stats/playbook.json, Reports data
"playbook", the Edges page's "What's working" card, and the pick runs read it first). Rebuilt every run from the edge
report card (long-run and recency-weighted results), the logic study, the self-correcting fair and the model trust
weights, so the advice moves with the results instead of living in a prompt.
"""
import json
import os

DIMS = {"kind": "Line type", "venueKind": "Venue x type", "venue": "Venue", "timing": "Time before start", "edge": "Edge size",
        "supportSize": "Model support", "logicSize": "Logic", "league": "Sport", "market": "Market", "fairShift": "Fair",
        "venueLeague": "Venue x sport"}
MIN_N = 150
MIN_GAMES = 20          # a cell needs this many separate games before its clustered t means anything
LEAN_T, AVOID_Z = 1.5, -2.0    # game-clustered: flat ROI t to lean on a cell, results-vs-close z to avoid one


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def _score(r):
    """expected flat ROI, half long-run and half lately, shrunk toward 0 by sample size"""
    roi = r.get("roi") or 0.0
    if r.get("roiR") is not None:
        roi = 0.5 * roi + 0.5 * r["roiR"]
    n = r.get("graded") or 0
    return roi * n / (n + 300)


def build(data_dir, card, params=None):
    pb = {"lean": [], "avoid": [], "rules": []}
    if card and card.get("n"):
        seen = set()
        cand = []
        for dim, label in DIMS.items():
            for r in card.get(dim) or []:
                n = r.get("graded") or 0
                if n < MIN_N or r.get("roi") is None:
                    continue
                cand.append({"dim": dim, "dimName": label, "k": r["k"], "n": n, "roi": r["roi"], "roiR": r.get("roiR"),
                             "gap": r.get("gap"), "gapR": r.get("gapR"), "z": r.get("zG"), "roiT": r.get("roiT"), "games": r.get("games"),
                             "score": round(_score(r), 4)})
        for c in sorted(cand, key=lambda c: -c["score"]):
            if c["score"] <= 0.005 or (c["roiR"] is not None and c["roiR"] <= 0) or (c["gap"] or 0) < -0.02:
                continue
            # significance clustered by game (edgecard.clus): five venues' copies of one prop are one bet, not five
            if (c["games"] or 0) < MIN_GAMES or c["roiT"] is None or c["roiT"] < LEAN_T:
                continue
            # the same edges under two names (pick6 the venue and pick6 · Pick'em) count once
            if (c["dim"], c["k"]) in seen or (c["n"], c["roi"]) in seen:
                continue
            seen.add((c["dim"], c["k"]))
            seen.add((c["n"], c["roi"]))
            pb["lean"].append(c)
            if len(pb["lean"]) >= 10:
                break
        for c in sorted(cand, key=lambda c: c["score"]):
            if c["z"] is None or c["z"] > AVOID_Z or c["roi"] >= 0 or (c["games"] or 0) < MIN_GAMES:
                continue
            pb["avoid"].append(c)
            if len(pb["avoid"]) >= 10:
                break
        tm = sorted([r for r in card.get("timing") or [] if (r.get("graded") or 0) >= MIN_N], key=lambda r: -(r.get("roi") or -9))
        if tm:
            pb["rules"].append({"id": "timing", "text": f"Best timing so far: edges flagged {tm[0]['k']} before the start (flat ROI {tm[0]['roi'] * 100:+.1f}% over {tm[0]['graded']}); worst {tm[-1]['k']} ({tm[-1]['roi'] * 100:+.1f}%)."})
    pol = _load(os.path.join(data_dir, "stats", "policytest.json")) or {}
    lg = pol.get("logic") or {}
    curve = next((c["rows"] for c in lg.get("supCurve") or [] if c.get("size") == "5%+"), [])
    def comb(keys):
        rs = [r for r in curve if r["k"] in keys and r.get("n")]
        n = sum(r["n"] for r in rs)
        return (n, sum(r["roi"] * r["n"] for r in rs) / n, sum(r["gap"] * r["n"] for r in rs) / n) if n else None
    a, b = comb({"0 – 0.5", "0.5 – 1", "1 – 1.5"}), comb({"1.5 – 2.5", "past 2.5x", "under the price (< 0)"})
    if a and b:
        pb["rules"].append({"id": "support", "text": f"5%+ edges with the model between price and fair (support 0-1.5): won {a[2] * 100:+.1f} pts vs fair, ROI {a[1] * 100:+.0f}% over {a[0]}. "
                            f"Model under the price or 1.5x+ past the fair: {b[2] * 100:+.1f} pts, ROI {b[1] * 100:+.0f}% over {b[0]}. Never pick a below/beyond edge."})
    ll = {r["k"]: r for r in lg.get("leagueLogic") or [] if r.get("n", 0) >= 100}
    good = [k.split(" · ")[0] for k, r in ll.items() if k.split(" · ")[-1] == "agrees" and r.get("gap", -1) >= 0]
    bad = [k.split(" · ")[0] for k, r in ll.items() if k.split(" · ")[-1] == "agrees" and r.get("gap", 0) < -0.03]
    if good or bad:
        pb["rules"].append({"id": "logicLeague", "text": f"Model agreement on 5%+ edges has paid in {', '.join(good) or 'no sport yet'}"
                            + (f"; it has not in {', '.join(bad)} (there the model's vote is no reason to bet)." if bad else ".")})
    if lg.get("matched"):
        pb["rules"].append({"id": "logicMatched", "text": f"Across all edges, model-agrees beat model-disagrees by {lg['matched']['diff'] * 100:+.1f} pts (z {lg['matched']['z']}) inside the same sport, line type and size."})
    fa = _load(os.path.join(data_dir, "calib", "fairadj.json")) or {}
    on = [k.replace("|*", "").replace("|", " ") for k, v in (fa.get("eval") or {}).items() if v.get("on")]
    if on:
        sh = {k: v for k, v in (fa.get("shifts") or {}).items() if k.endswith("|*")}
        txt = "; ".join(f"{k.replace('|*', '').replace('|', ' ')} {'overs' if v < 0 else 'unders'} shaded {abs(v) * 25:.1f} pts at even odds" for k, v in sh.items())
        pb["rules"].append({"id": "fairadj", "text": f"Self-correcting fair is live for {', '.join(on)} ({txt}). The fair shown already includes it (fairRaw is the old even-margin fair); never price a pick off fairRaw."})
    w_on = sorted(k for k, v in (params or {}).items() if isinstance(v, dict) and (v.get("w") or 0) > 0)
    pb["rules"].append({"id": "model", "text": ("The model earns a say (w > 0) only in: " + ", ".join(w_on) + ". Everywhere else fair = market and checks.model is null.") if w_on
                        else "The model has earned no say anywhere right now (w = 0 everywhere): fair = market; use the model only as the support check on 5%+ edges."})
    ai = pol.get("ai") or {}
    allp = (ai.get("picks") or [{}])[0]
    if allp.get("n"):
        cv = allp.get("clvCal") if allp.get("clvCal") is not None else allp.get("clv")
        pb["rules"].append({"id": "picks", "text": f"Our picks so far: {allp.get('w')}-{allp.get('l')}, {allp.get('units'):+.2f}u over {allp['n']} graded; "
                            f"CLV {(cv or 0) * 100:+.1f}% {'calibrated' if allp.get('clvCal') is not None else 'raw'} (raw {(allp.get('clv') or 0) * 100:+.1f}%) on {allp.get('nClv', 0)} fresh closes: "
                            "too few to judge flags; keep stakes small and let the edge card lead."})
    pb["at"] = (card or {}).get("updated")
    os.makedirs(os.path.join(data_dir, "stats"), exist_ok=True)
    json.dump(pb, open(os.path.join(data_dir, "stats", "playbook.json"), "w"), indent=1)
    return pb


def _row(e, now):
    from datetime import datetime
    hrs = 0.0
    try:
        hrs = (datetime.fromisoformat(e["commence"].replace("Z", "+00:00")) - now).total_seconds() / 3600
    except Exception:
        pass
    return {"venue": e.get("book"), "kind": "pick'em" if e.get("pickem") else "prop" if e.get("player") else "line",
            "est": bool(e.get("est")), "model": bool(e.get("model")), "main": bool(e.get("main", True)), "mk": e.get("market") or "",
            "edge": e.get("edge") or 0, "hrs": max(0.0, hrs), "league": e.get("league"), "logic": e.get("logic"), "sup": e.get("support"),
            "fs": e.get("fairShift"), "side": e.get("side"), "steam": e.get("fairMove")}


def tag(edges, pb, now):
    """stamp each edge with the playbook cells it falls in (e.lean / e.avoid: short labels) and nudge its rank: x1.1 per
    lean cell (up to x1.3), x0.8 per avoid cell (down to x0.5). Muted edges stay muted."""
    import edgecard as EC
    lean = {(c["dim"], c["k"]): c for c in pb.get("lean") or []}
    avoid = {(c["dim"], c["k"]): c for c in pb.get("avoid") or []}
    if not lean and not avoid:
        return
    for e in edges:
        if e.get("live"):
            continue
        r = _row(e, now)
        L, A = [], []
        for dim in DIMS:
            try:
                k = EC.DIMS[dim](r)
            except Exception:
                continue
            if (dim, k) in lean:
                c = lean[(dim, k)]
                L.append({"k": k, "roi": c["roi"], "roiR": c["roiR"], "n": c["n"]})
            if (dim, k) in avoid:
                c = avoid[(dim, k)]
                A.append({"k": k, "roi": c["roi"], "n": c["n"]})
        if L:
            e["lean"] = L[:3]
        if A:
            e["avoid"] = A[:3]
        if L or A:
            m = min(1.3, 1.1 ** len(L)) * max(0.5, 0.8 ** len(A))
            e["score"] = round((e.get("score") or 0) * m, 4)
    edges.sort(key=lambda x: (bool(x.get("muted")), -(x.get("score") or 0)))
