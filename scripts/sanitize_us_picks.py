import urllib.request, json, base64, os

TOK = os.environ.get("TOKEN") or os.environ.get("GITHUB_TOKEN")
API = "https://api.github.com/repos/hantu-zh/vibe-dashboard/contents/us_picks.json"

def clean(o):
    if isinstance(o, float):
        return None if (o != o or o in (float("inf"), float("-inf"))) else o
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, list):
        return [clean(v) for v in o]
    return o

def main():
    req = urllib.request.Request(API, headers={"Authorization": "token " + TOK,
        "Accept": "application/vnd.github+json"})
    d = json.load(urllib.request.urlopen(req))
    raw = base64.b64decode(d["content"]).decode("utf-8")
    if "NaN" not in raw and "Infinity" not in raw:
        print("us_picks.json clean, skip")
        return
    out = json.dumps(clean(json.loads(raw)), ensure_ascii=False, indent=2, allow_nan=False)
    body = {"message": "auto: sanitize us_picks.json (strip NaN)",
            "content": base64.b64encode(out.encode()).decode(),
            "sha": d["sha"], "branch": "main"}
    req2 = urllib.request.Request(API, data=json.dumps(body).encode(),
            headers={"Authorization": "token " + TOK,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json"}, method="PUT")
    r = urllib.request.urlopen(req2)
    print("sanitized, http", r.status)

if __name__ == "__main__":
    main()
