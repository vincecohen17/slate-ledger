"""Tracking audit (mode audit): every bet, promo and wallet entry from Firestore plus every pick file, copied to the private
data repo (latest/audit/) with a summary of what each record is missing, so gaps in the tracking can be found and fixed.
Logs print counts only."""
import glob
import json
import os
from collections import Counter

import livebets as LB


def _missing_bet(b, data_dir):
    miss = []
    for k in ("venue", "stake", "dec", "am", "createdAt", "status"):
        if b.get(k) in (None, ""):
            miss.append(k)
    if b.get("status") in ("won", "lost", "push", "void"):
        for k in ("settledAt", "profit"):
            if b.get(k) is None:
                miss.append(k)
        if b.get("clv") is None and not (b.get("auto") or {}).get("clv") is not None:
            miss.append("clv")
    if b.get("fairAtLog") is None:
        miss.append("fairAtLog")
    if not b.get("leagues") and not b.get("league"):
        miss.append("league")
    legs = b.get("legs") or [b]
    for i, l in enumerate(legs):
        gid = l.get("gameId") or b.get("gameId")
        if not gid:
            miss.append(f"leg{i}.gameId")
            continue
        for k in ("market", "side"):
            if not l.get(k):
                miss.append(f"leg{i}.{k}")
        if not l.get("commence") and not b.get("commence"):
            miss.append(f"leg{i}.commence")
        try:
            r = LB.resolve(dict(l, gameId=gid), data_dir)
        except Exception:
            r = None
        if not r:
            miss.append(f"leg{i}.unresolved")
    return miss


def run(db, data_dir, log=print):
    out_dir = os.path.join(data_dir, "latest", "audit")
    os.makedirs(out_dir, exist_ok=True)
    bets = []
    for snap in db.collection("bets").stream():
        b = snap.to_dict() or {}
        b["id"] = snap.id
        b["_missing"] = _missing_bet(b, data_dir)
        bets.append(b)
    promos = [dict(s.to_dict() or {}, id=s.id) for s in db.collection("promos").stream()]
    wallet = [dict(s.to_dict() or {}, id=s.id) for s in db.collection("wallet").stream()]
    try:
        settings = db.document("settings/owner").get().to_dict() or {}
    except Exception:
        settings = {}
    picks = []
    for fn in sorted(glob.glob(os.path.join(data_dir, "picks", "*.json"))):
        try:
            picks += json.load(open(fn))
        except Exception:
            pass
    pmiss = Counter()
    for p in picks:
        if p.get("tier") == "likely":
            continue
        for k in ("market", "side", "fair", "marketFair", "modelP", "checks", "pillars", "units", "commence"):
            if p.get(k) in (None, "", {}):
                pmiss[k] += 1
        if p.get("status") in ("won", "lost", "push"):
            for k in ("clv", "closeFair", "profit"):
                if p.get(k) is None:
                    pmiss["graded." + k] += 1
    bmiss = Counter(m.split(".")[-1] if m.startswith("leg") else m for b in bets for m in b["_missing"])
    summary = {"bets": len(bets), "byStatus": Counter(b.get("status") for b in bets), "betsMissing": bmiss,
               "picks": len(picks), "picksMissing": pmiss, "promos": len(promos), "wallet": len(wallet)}
    json.dump(bets, open(os.path.join(out_dir, "bets.json"), "w"), indent=1, default=str)
    json.dump(promos, open(os.path.join(out_dir, "promos.json"), "w"), indent=1, default=str)
    json.dump(wallet, open(os.path.join(out_dir, "wallet.json"), "w"), indent=1, default=str)
    json.dump(settings, open(os.path.join(out_dir, "settings.json"), "w"), indent=1, default=str)
    json.dump(summary, open(os.path.join(out_dir, "summary.json"), "w"), indent=1, default=str)
    log(f"audit: {len(bets)} bets, {len(picks)} picks, {len(promos)} promos, {len(wallet)} wallet entries")
    return summary
