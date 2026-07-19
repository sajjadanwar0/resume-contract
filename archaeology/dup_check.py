#!/usr/bin/env python3
"""dup_check.py -- search a GitHub repo's issues for potential duplicates
before filing. Stdlib only (urllib), no token needed for light use
(unauthenticated search API: ~10 requests/min).

Usage:
  python3 dup_check.py owner/repo "query one" "query two" ...
  GITHUB_TOKEN=ghp_xxx python3 dup_check.py ...   (higher rate limits)

Each query is searched in title+body across open AND closed issues
(closed matters: your bug may already be fixed or rejected).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request

API = "https://api.github.com/search/issues"


def search(repo: str, query: str):
    q = f"repo:{repo} is:issue {query}"
    url = f"{API}?q={urllib.parse.quote(q)}&per_page=20&sort=updated"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "dup-check-script",
        **({"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}"}
           if os.environ.get("GITHUB_TOKEN") else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    repo, queries = sys.argv[1], sys.argv[2:]
    seen: dict[int, dict] = {}
    hits_per_issue: dict[int, int] = {}
    for q in queries:
        try:
            data = search(repo, q)
        except Exception as e:
            print(f"[query failed] {q!r}: {e}", file=sys.stderr)
            continue
        print(f"-- query: {q!r}  ({data.get('total_count', '?')} total matches)")
        for item in data.get("items", []):
            n = item["number"]
            seen[n] = item
            hits_per_issue[n] = hits_per_issue.get(n, 0) + 1
        time.sleep(7)  # stay under unauthenticated search rate limit

    print("\n== Candidates, ranked by how many of your queries they matched ==")
    ranked = sorted(seen.values(),
                    key=lambda i: (-hits_per_issue[i["number"]], -i["number"]))
    for item in ranked:
        n = item["number"]
        print(f"[{hits_per_issue[n]} hits] #{n} ({item['state']}) "
              f"{item['title'][:90]}\n         {item['html_url']}")
    if not seen:
        print("(no candidates found)")


if __name__ == "__main__":
    main()
