"""Phone alerts through ntfy (free; the ntfy app subscribes to a private topic name).

The topic lives only in the private data repo (config/alerts.json), never in this public repo or the logs:
  {"topic": "...", "quietStart": 23, "quietEnd": 7, "minScore": 0.04, "results": false, "click": "https://slate-ledger.web.app"}
Each run sends at most one message per kind, and each item once (latest/alerts_state.json):
  - new strong edges: logic agrees, not an estimated fair, rank score >= minScore, track record not negative
  - arbs and middles that pass the sanity check
  - (results: true) bets that just graded
Quiet hours (Pacific) hold edges back; arbs still go out (they don't wait).
"""
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone


def _cfg(data_dir):
    try:
        return json.load(open(os.path.join(data_dir, "config", "alerts.json")))
    except Exception:
        return None


def _state(data_dir):
    try:
        return json.load(open(os.path.join(data_dir, "latest", "alerts_state.json")))
    except Exception:
        return {"sent": {}}


def _save(data_dir, st, now):
    cut = (now - timedelta(hours=36)).isoformat()
    st["sent"] = {k: v for k, v in st["sent"].items() if v > cut}
    json.dump(st, open(os.path.join(data_dir, "latest", "alerts_state.json"), "w"), separators=(",", ":"))


def _post(cfg, title, body, tags="", priority=3, click=None):
    req = urllib.request.Request(f"https://ntfy.sh/{cfg['topic']}", data=body.encode("utf-8"), method="POST",
                                 headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": tags, "Priority": str(priority),
                                          **({"Click": click} if click else {})})
    urllib.request.urlopen(req, timeout=10).read()


def _quiet(cfg, now):
    h = (now - timedelta(hours=7)).hour       # Pacific (PDT); an hour off in winter is fine for quiet hours
    a, b = cfg.get("quietStart", 23), cfg.get("quietEnd", 7)
    return (h >= a or h < b) if a > b else (a <= h < b)


def _am(x):
    return "" if x is None else (f"+{x}" if x > 0 else str(x))


def send(data_dir, now, edges=(), opps=(), graded=(), log=print):
    cfg = _cfg(data_dir)
    if not cfg or not cfg.get("topic"):
        return 0
    st = _state(data_dir)
    t = now.isoformat()
    click = cfg.get("click", "https://slate-ledger.web.app")
    n = 0
    try:
        # arbs and middles: go out any time
        fresh = []
        for o in opps:
            if o.get("suspect"):
                continue
            k = "o|" + o["kind"] + "|" + "|".join(l["book"] + l["label"] for l in o["legs"])
            if k in st["sent"]:
                continue
            if o["kind"] == "arb" and o["profit"] < cfg.get("minArb", 0.01):
                continue
            if o["kind"] == "middle" and o["ev"] < cfg.get("minMiddle", 0.02):
                continue
            fresh.append((k, o))
        if fresh:
            lines = []
            for k, o in fresh[:5]:
                head = f"ARB +{o['profit'] * 100:.1f}%" if o["kind"] == "arb" else f"MIDDLE EV +{o['ev'] * 100:.1f}% ({o['pMiddle'] * 100:.0f}% to hit)"
                legs = " | ".join(f"{l['label']} {_am(l['am'])} @ {l['name']} ({s * 100:.0f}%)" for l, s in zip(o["legs"], o["stakes"]))
                lines.append(f"{head} · {o['league']} {o['matchup']}\n{legs}")
            _post(cfg, f"{len(fresh)} arb/middle{'s' if len(fresh) > 1 else ''} open", "\n\n".join(lines) + "\n\n(% = share of the total stake per leg)",
                  tags="moneybag", priority=4, click=click + "/#/edges")
            for k, o in fresh[:5]:
                st["sent"][k] = t
            n += 1
        # strong edges: not during quiet hours
        if not _quiet(cfg, now):
            good = []
            for e in edges:
                if e.get("live") or e.get("est") or e.get("logic") != "agrees" or (e.get("score") or 0) < cfg.get("minScore", 0.04):
                    continue
                tr = e.get("track") or {}
                if (tr.get("gap") if tr.get("gap") is not None else tr.get("clv", 0)) < 0:     # that kind of edge loses more than its fair says
                    continue
                k = f"e|{e['gameId']}|{e['label']}|{e['book']}"
                if k in st["sent"]:
                    continue
                good.append((k, e))
            if good:
                body = "\n".join(f"{e['label']} {'pickem' if e.get('pickem') else _am(e.get('am'))} @ {e['bookName']} · edge {e['edge'] * 100:.1f}% · {e['league']}"
                                 for k, e in good[:6])
                _post(cfg, f"{len(good)} new edge{'s' if len(good) > 1 else ''} (model agrees)", body, tags="chart_with_upwards_trend", click=click + "/#/edges")
                for k, e in good[:6]:
                    st["sent"][k] = t
                n += 1
        if graded and cfg.get("results"):
            body = "\n".join(f"{g['result'].upper()} {g['label']} {g.get('profit', '')}" for g in graded[:10])
            _post(cfg, f"{len(graded)} bet{'s' if len(graded) > 1 else ''} graded", body, tags="white_check_mark", click=click + "/#/ledger")
            n += 1
    except Exception as e:
        log(f"alerts: send failed ({type(e).__name__})")
    _save(data_dir, st, now)
    return n
