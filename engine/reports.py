"""Where are the books off? Per-book stats against the sharp fair.

For every line with a trusted sharp fair (weight >= 2), each book's quote is
compared to fair:
  overpay  = cost - fair  (pp the book charges above fair; lower is better for you)
  plus     = count of quotes priced better than fair (edge > 0)
  best     = count of times the book has the best price for a side
  stale    = count of quotes flagged stale
Snapshot stats are written every run; a cumulative tally lives in the data repo.
"""
import json
import os


def _blank():
    return {"n": 0, "overpay": 0.0, "plus": 0, "best": 0, "stale": 0, "edgeSum": 0.0}


def snapshot(game_docs, prop_docs, min_weight=2.0):
    by = {}
    meta = {}

    def scan(line, scope):
        if not line.get("fair") or line.get("fairWeight", 0) < min_weight:
            return
        best = {o: b["book"] for o, b in (line.get("best") or {}).items()}
        for q in line["quotes"]:
            meta[q["book"]] = {"name": q["name"], "kind": q["kind"], "venue": q["venue"]}
            for o, pe in q["prices"].items():
                if pe.get("edge") is None:
                    continue
                for key in (q["book"], f"{q['book']}|{scope}"):
                    s = by.setdefault(key, _blank())
                    s["n"] += 1
                    s["overpay"] += -pe["edge"]
                    s["plus"] += pe["edge"] > 0
                    s["best"] += best.get(o) == q["book"]
                    s["stale"] += q["stale"]
                    s["edgeSum"] += max(0.0, pe["edge"])

    for g in game_docs:
        for l in g["lines"]:
            scan(l, f"{g['league']}:{l['market']}")
    for p in prop_docs:
        for l in p["rows"]:
            if not l.get("modelFair"):
                scan(l, f"{p['league']}:props")
    return by, meta


def summarize(by, meta):
    out = []
    for key, s in by.items():
        if "|" in key or s["n"] < 5:
            continue
        scopes = sorted(
            ({"scope": k.split("|", 1)[1], "n": v["n"], "overpay": round(v["overpay"] / v["n"], 4),
              "plusRate": round(v["plus"] / v["n"], 4)} for k, v in by.items()
             if k.startswith(key + "|") and v["n"] >= 5),
            key=lambda x: x["overpay"])
        out.append(dict(book=key, **meta.get(key, {"name": key}), n=s["n"],
                        overpay=round(s["overpay"] / s["n"], 4), plusRate=round(s["plus"] / s["n"], 4),
                        bestRate=round(s["best"] / s["n"], 4), staleRate=round(s["stale"] / s["n"], 4),
                        scopes=scopes[:8]))
    out.sort(key=lambda x: x["overpay"])
    return out


def update(game_docs, prop_docs, data_dir, stamp):
    by, meta = snapshot(game_docs, prop_docs)
    fn = os.path.join(data_dir, "stats", "books_total.json")
    os.makedirs(os.path.dirname(fn), exist_ok=True)
    try:
        tot = json.load(open(fn))
    except Exception:
        tot = {"since": stamp, "runs": 0, "by": {}, "meta": {}}
    if tot.get("last") != stamp:  # rebuilds of the same snapshot are not double counted
        for k, s in by.items():
            t = tot["by"].setdefault(k, _blank())
            for f in s:
                t[f] += s[f]
        tot["meta"].update(meta)
        tot["runs"] += 1
        tot["last"] = stamp
        json.dump(tot, open(fn, "w"), separators=(",", ":"))
    return {"updated": stamp, "now": summarize(by, meta),
            "total": summarize(tot["by"], tot["meta"]), "since": tot["since"], "runs": tot["runs"]}
