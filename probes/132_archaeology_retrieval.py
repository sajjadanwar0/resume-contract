#!/usr/bin/env python3
"""
132_archaeology_retrieval.py  (rev 2, 2026-07-18)
Candidate retrieval for the DEFERRED issue-archaeology study (paper Sec. 7).
Produces the candidate pool only; it claims no counts and codes nothing.
Coding requires two human raters + the codebook (archaeology/CODEBOOK.md).

Usage (GITHUB_TOKEN effectively required: 30 search req/min vs 10, and the
unauthenticated secondary limit WILL 403 partway through the 50-query frame):
    export GITHUB_TOKEN=ghp_...
    python3 probes/132_archaeology_retrieval.py --out archaeology/candidates.jsonl

Rev 2 changes vs the run that produced the first 244-candidate pool:
  * run-llama/workflows removed from the frame: the GitHub search API rejects
    it with 422 ("resources do not exist or you do not have permission").
    llama-index-workflows issue traffic is covered via run-llama/llama_index;
    if you locate a searchable dedicated repo, add it with --repos.
  * created:>= window (docstring promised it; query now implements it).
  * Per-query pacing + 403/429 backoff honoring Retry-After / X-RateLimit-Reset
    (the first run's CopilotKit 403s were the unauthenticated secondary limit).
  A rev-2 run therefore SUPERSEDES the first pool; regenerate before coding.

Retrieval frame (per protocol):
  repos: langchain-ai/langgraph, crewAIInc/crewAI, run-llama/llama_index,
         pydantic/pydantic-ai, CopilotKit/CopilotKit  (+ --repos extras)
  query space: keyword hits on persistence/interrupt/resume terms
  window: issues created --since (default 2024-01-01) .. run date
  dedup: by issue node id; cross-linked duplicates kept, flagged
"""
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
    # "run-llama/workflows": rejected by the search API (422, unsearchable);
    # see rev-2 note above. Add a corrected name via --repos if one exists.
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
                    wait = 65  # secondary limit window
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
    pace = 2.2 if TOKEN else 6.5  # stay under 30/min authed, 10/min unauthed
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