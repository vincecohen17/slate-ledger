"""Finish a conflicted `git pull --rebase` of the data repo snapshot (run inside the data repo).

The watcher (concurrency group "live") commits a checkout up to 50 minutes old while auto/rebuild runs keep pushing,
so state files both touch (latest/alerts_state.json, steam_state, live_state...) conflict and the whole watcher
snapshot used to be dropped. Each conflicted file is resolved here:
  .json   dicts deep-merged (keys from both sides; on a clash this run's value wins), anything else: this run's
  .jsonl  the remote lines plus this run's lines it doesn't have
  other   this run's version
In a rebase stage 2 is the remote (upstream) side and stage 3 is this run's commit. Exit 1 if it can't finish."""
import json
import subprocess
import sys


def git(*a, inp=None):
    return subprocess.run(["git", *a], input=inp, capture_output=True)


def show(stage, path):
    r = git("show", f":{stage}:{path}")
    return r.stdout if r.returncode == 0 else None


def deep(remote, mine):
    if isinstance(remote, dict) and isinstance(mine, dict):
        out = dict(remote)
        for k, v in mine.items():
            out[k] = deep(remote[k], v) if k in remote else v
        return out
    return mine


def resolve(path, remote, mine):
    if mine is None:
        return None
    if remote is None:
        return mine
    try:
        if path.endswith(".jsonl"):
            have = set(remote.decode().splitlines())
            extra = [ln for ln in mine.decode().splitlines() if ln and ln not in have]
            txt = remote.decode()
            if extra:
                txt = txt + ("" if not txt or txt.endswith("\n") else "\n") + "\n".join(extra) + "\n"
            return txt.encode()
        if path.endswith(".json"):
            return json.dumps(deep(json.loads(remote), json.loads(mine)), separators=(",", ":")).encode()
    except Exception:
        pass
    return mine


def main():
    for _ in range(200):
        files = [f for f in git("diff", "--name-only", "--diff-filter=U").stdout.decode().splitlines() if f]
        if not files:
            r = git("-c", "core.editor=true", "rebase", "--continue")
            if r.returncode == 0:
                return 0
            if b"No rebase in progress" in r.stderr or b"no rebase in progress" in r.stderr:
                return 0
            files = [f for f in git("diff", "--name-only", "--diff-filter=U").stdout.decode().splitlines() if f]
            if not files:
                # nothing left to resolve but the rebase stopped (e.g. an empty commit): skip it
                if git("rebase", "--skip").returncode != 0:
                    return 1
                continue
        for f in files:
            out = resolve(f, show(2, f), show(3, f))
            if out is None:
                git("rm", "-q", "--", f)
            else:
                open(f, "wb").write(out)
                git("add", "--", f)
        print(f"snapmerge: resolved {len(files)} conflicted file(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
