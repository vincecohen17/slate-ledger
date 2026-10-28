"""One-time: make sure the project's default Realtime Database exists (free on the Spark plan) and record its URL in the
data repo (latest/rtdb.json). Live data and presence move there: it has no daily write count, unlike Firestore."""
import json
import os

PROJECT = "slate-ledger"
API = "https://firebasedatabase.googleapis.com/v1beta"


def main(data_dir, log=print):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    sa = json.loads(os.environ["FIREBASE_SERVICE_ACCOUNT"])
    cred = service_account.Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    s = AuthorizedSession(cred)
    out = {"steps": []}
    # the database management API is off by default in new projects: turn it on (free; needs the service account's rights)
    r = s.post(f"https://serviceusage.googleapis.com/v1/projects/{sa.get('project_id', PROJECT)}/services/firebasedatabase.googleapis.com:enable")
    out["steps"].append({"enable": r.status_code, "body": r.text[:300]})
    if r.ok:
        import time
        time.sleep(45)
    r = s.get(f"{API}/projects/{PROJECT}/locations/-/instances")
    out["steps"].append({"list": r.status_code, "body": r.text[:600]})
    inst = (r.json().get("instances") if r.ok else None) or []
    if not inst:
        for loc in ("us-central1",):
            r = s.post(f"{API}/projects/{PROJECT}/locations/{loc}/instances", params={"databaseId": f"{PROJECT}-default-rtdb"},
                       json={"type": "DEFAULT_DATABASE"})
            out["steps"].append({"create": r.status_code, "body": r.text[:600]})
            if r.ok:
                inst = [r.json()]
                break
    if inst:
        out["url"] = inst[0].get("databaseUrl")
        out["state"] = inst[0].get("state")
    os.makedirs(os.path.join(data_dir, "latest"), exist_ok=True)
    json.dump(out, open(os.path.join(data_dir, "latest", "rtdb.json"), "w"), indent=1)
    log(f"rtdb: {'ready' if out.get('url') else 'not created'}")
