"""Guard us_picks.json against NaN / Infinity.

Background: the sync bot rewrites us_picks.json from a snapshot. If that
snapshot is stale, the regenerated payload can carry non-finite floats
(NaN / Infinity), which are not valid JSON and blow up JSON.parse() on the
Pages front-end. This job strips them (each non-finite number -> null) and
commits the sanitized file.

Runs every 10 minutes. Exits 0 immediately when the file is already clean.
"""
import base64
import json
import os
import time
import urllib.error
import urllib.request

TOK = os.environ.get("TOKEN") or os.environ.get("GITHUB_TOKEN")
FILE = "us_picks.json"
API = f"https://api.github.com/repos/hantu-zh/vibe-dashboard/contents/{FILE}"

HEADERS = {
    "Authorization": "token " + (TOK or ""),
    "Accept": "application/vnd.github+json",
    "User-Agent": "sanitize-us-picks",
}


def clean(o):
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o


def fetch():
    """Return (sha, raw_text) of the current us_picks.json on the default branch."""
    req = urllib.request.Request(API, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d["sha"], base64.b64decode(d["content"]).decode("utf-8")


def put(sha, text):
    body = {
        "message": "auto: sanitize us_picks.json (strip NaN)",
        "content": base64.b64encode(text.encode("utf-8")).decode(),
        "branch": "main",
        "sha": sha,
    }
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode("utf-8"),
        headers=dict(HEADERS, **{"Content-Type": "application/json"}),
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status


def main():
    if not TOK:
        raise SystemExit("no TOKEN / GITHUB_TOKEN in env")

    # The sync bot pushes to main constantly, so a 409 (sha moved between our
    # read and write) is expected rather than exceptional -> re-read and retry.
    for attempt in range(1, 6):
        sha, raw = fetch()

        if "NaN" not in raw and "Infinity" not in raw:
            print("us_picks.json clean, skip")
            return

        out = json.dumps(
            clean(json.loads(raw)), ensure_ascii=False, indent=2, allow_nan=False
        )
        try:
            print("sanitized, http", put(sha, out))
            return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422) and attempt < 5:
                print(f"attempt {attempt}: sha conflict (HTTP {e.code}), retrying")
                time.sleep(3)
                continue
            raise

    raise SystemExit("gave up after 5 attempts")


if __name__ == "__main__":
    main()
