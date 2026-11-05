"""The promo desk: Vince describes a promo in words on the Promos page (Firestore promoreq/{id}); Claude's hourly desk
run turns it into a promo the site's promo engine understands (or asks one question) and the engine applies the answer.

Each engine run (auto, refresh, rebuild):
  inbox  -> private data repo promodesk/inbox.json: requests waiting for Claude {id, text, at, thread}, plus
            promodesk/promos.json (the promos already saved: id, title, kind, venue, used, expires) for context
  outbox <- promodesk/outbox/<reqId>.json written by the desk run:
            {reqId, action: "create" | "question" | "failed", promo?: {...as the site saves it}, text: reply for Vince,
             atThread: number of thread messages it answered}
            create: saves promos/{promo.id} and marks the request done; question: asks; failed: says why.
            Applied once, then moved to promodesk/done/.
Firestore stays light: one query per run, a write only when the desk answered.
"""
import glob
import json
import os
import shutil
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sync(db, data_dir, log=print):
    if not db:
        return 0
    d = os.path.join(data_dir, "promodesk")
    os.makedirs(os.path.join(d, "outbox"), exist_ok=True)
    os.makedirs(os.path.join(d, "done"), exist_ok=True)
    reqs = {}
    try:
        for snap in db.collection("promoreq").stream():
            reqs[snap.id] = snap.to_dict() or {}
    except Exception as e:
        log(f"promodesk: requests not read ({type(e).__name__})")
        return 0
    # answers from the desk
    applied = 0
    for fn in sorted(glob.glob(os.path.join(d, "outbox", "*.json"))):
        try:
            a = json.load(open(fn))
        except Exception:
            continue
        rid = a.get("reqId")
        r = reqs.get(rid)
        if not r:
            shutil.move(fn, os.path.join(d, "done", os.path.basename(fn)))
            continue
        thread = list(r.get("thread") or [])
        if a.get("atThread") is not None and len(thread) > a["atThread"]:
            # Vince added to the thread after the desk read it: keep the request waiting for the next desk run
            shutil.move(fn, os.path.join(d, "done", os.path.basename(fn)))
            continue
        thread.append({"from": "claude", "text": a.get("text") or "", "at": _now()})
        up = {"thread": thread, "answeredAt": _now()}
        if a.get("action") == "create" and isinstance(a.get("promo"), dict) and a["promo"].get("id"):
            p = dict(a["promo"])
            p.setdefault("createdAt", _now())
            p["fromDesk"] = rid
            db.document(f"promos/{p['id']}").set(p)
            up.update({"status": "done", "promoId": p["id"]})
        elif a.get("action") == "question":
            up["status"] = "question"
        else:
            up["status"] = "failed"
        db.document(f"promoreq/{rid}").update(up)
        reqs[rid] = {**r, **up}
        shutil.move(fn, os.path.join(d, "done", os.path.basename(fn)))
        applied += 1
    # what's waiting for the desk
    inbox = [{"id": k, "text": v.get("text"), "at": v.get("at"), "thread": v.get("thread") or []}
             for k, v in reqs.items() if v.get("status") == "waiting"]
    json.dump(sorted(inbox, key=lambda x: x.get("at") or ""), open(os.path.join(d, "inbox.json"), "w"), indent=1, ensure_ascii=False)
    try:
        promos = [{k: v.get(k) for k in ("id", "title", "kind", "venue", "used", "expires", "fromDesk")}
                  for v in (s.to_dict() or {} for s in db.collection("promos").stream())] if inbox else None
        if promos is not None:
            json.dump(promos, open(os.path.join(d, "promos.json"), "w"), indent=1, ensure_ascii=False)
    except Exception:
        pass
    if applied or inbox:
        log(f"promodesk: {applied} answers applied, {len(inbox)} waiting")
    return applied
