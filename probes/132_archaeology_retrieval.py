import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

REPOS = [
    "langchain-ai/langgraph",
    "crewAIInc/crewAI",
    "run-llama/llama_index",
    "pydantic/pydantic-ai",
    "CopilotKit/CopilotKit",
]
KEYWORDS = [
    "checkpoint resume", "interrupt resume", "resume duplicate",
    "re-execute checkpoint", "replay side effect", "checkpointer state",
    "persistence corrupt", "resume ignored", "fork checkpoint",
    "human in the loop resume",
]
SEEDS = ["6663", "6491", "6791", "6792", "7361", "7714", "8039", "2315"]
API = "https://api.github.com/search/issues"
TOKEN = os.environ.get("GITHUB_TOKEN")

def gh(url, tries=4):
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(url)
        req.add_header("Accept", "application/vnd.github+json")
        if TOKEN:
            req.add_header("Authorization", f"Bearer {TOKEN}")
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt < tries:
                retry = e.headers.get("Retry-After")
                reset = e.headers.get("X-RateLimit-Reset")
                if retry:
                    wait = int(retry) + 1
                elif reset:
                    wait = max(int(reset) - int(time.time()), 5) + 1
                else:
                    wait = 65
                print(f"[rate-limit] HTTP {e.code}; sleeping {wait}s (attempt {attempt}/{tries})")
                time.sleep(wait)
                continue
            raise

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="archaeology/candidates.jsonl")
    ap.add_argument("--max-pages", type=int, default=3)
    ap.add_argument("--since", default="2024-01-01",
                    help="only issues created on/after this date (protocol window)")
    ap.add_argument("--repos", default="",
                    help="comma-separated extra repos to add to the frame")
    a = ap.parse_args()
    repos = REPOS + [r.strip() for r in a.repos.split(",") if r.strip()]
    if not TOKEN:
        print("[WARN] no GITHUB_TOKEN: 10 search req/min; the 50-query frame "
              "will crawl and may still 403. Export a token (no scopes needed).")
    pace = 2.2 if TOKEN else 6.5
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    seen, rows, failures = set(), [], []
    for repo in repos:
        for kw in KEYWORDS:
            q = urllib.parse.quote(f'repo:{repo} is:issue "{kw}" created:>={a.since}')
            for page in range(1, a.max_pages + 1):
                try:
                    data = gh(f"{API}?q={q}&per_page=100&page={page}")
                except Exception as e:
                    print(f"[warn] {repo} '{kw}' p{page}: {e}")
                    failures.append({"repo": repo, "keyword": kw, "page": page,
                                     "error": str(e)})
                    break
                items = data.get("items", [])
                for it in items:
                    nid = it["node_id"]
                    if nid in seen:
                        continue
                    seen.add(nid)
                    rows.append({
                        "repo": repo,
                        "number": it["number"],
                        "title": it["title"],
                        "state": it["state"],
                        "created_at": it["created_at"],
                        "labels": [l["name"] for l in it.get("labels", [])],
                        "url": it["html_url"],
                        "matched_keyword": kw,
                        "is_seed": str(it["number"]) in SEEDS,
                    })
                time.sleep(pace)
                if len(items) < 100:
                    break
    with open(a.out, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    manifest = {
        "frame_repos": repos, "keywords": KEYWORDS, "since": a.since,
        "run_date": time.strftime("%Y-%m-%d"), "candidates": len(rows),
        "query_failures": failures, "token_used": bool(TOKEN),
    }
    with open(a.out + ".manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {len(rows)} candidates -> {a.out}")
    print(f"frame manifest ({len(failures)} query failures) -> {a.out}.manifest.json")
    if failures:
        print("[WARN] frame INCOMPLETE: do not treat this pool as the protocol "
              "population until zero query failures.")
    print("Next: two-rater coding per archaeology/CODEBOOK.md; report kappa + CIs.")

if __name__ == "__main__":
    main()
