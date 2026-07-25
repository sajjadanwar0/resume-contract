#!/usr/bin/env bash
# sweep_nonlg.sh -- v3.1. Back- and forward-sweep of the non-LangGraph
# headline cells. Changes vs v2: the v3 summary-splice corruption is gone
# (file regenerated whole); summary auto-salvages JSON embedded in polluted
# stdout (CrewAI >=1.15.3 prints rich console boxes to stdout) and, for a
# truly empty receipt, prints the stderr sidecar's last line; `--summary`
# re-runs adjudication without touching any cell; release dates for the
# three packages are dumped to results/sweep/release_dates.json as the
# receipt for pin-currency claims; crewai gains 1.15.6, pydantic-graph
# gains a 2.x forward cell (a crash there is an API-break scope statement,
# not a failure).
#
# Modes:
#   ./sweep_nonlg.sh                 run missing cells, dump dates, summarize
#   ./sweep_nonlg.sh --summary       adjudicate existing receipts only
#   ./sweep_nonlg.sh --versions PKG  final releases of PKG, numeric order
#
# Reporting rules: verdicts reproduce -> Sec 2 comparability sentence; a
# verdict FLIP -> new regression pair for Sec 6.6(iii); a key absent at an
# older release with a leg error -> feature-introduction boundary; a crash
# at a newer MAJOR -> API-break scope statement.
set -euo pipefail
OUT="results/sweep"

versions_helper() {
  local pkg="${1:?usage: sweep_nonlg.sh --versions <package>}"
  curl -s "https://pypi.org/pypi/$pkg/json" | python3 -c "
import json, sys
try:
    from packaging.version import Version
    key = Version
    def final(v): return not Version(v).is_prerelease and not Version(v).is_devrelease
except Exception:
    key = str
    def final(v): return all(t not in v for t in ('a','b','rc','dev'))
rs = sorted((v for v in json.load(sys.stdin)['releases'] if final(v)), key=key)
print(rs[-15:])"
}

dump_dates() {
  python3 - <<'PYEOF'
import json, urllib.request
out = {}
for pkg in ("crewai", "llama-index-workflows", "pydantic-graph"):
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{pkg}/json", timeout=20) as r:
            d = json.load(r)["releases"]
    except Exception as e:
        out[pkg] = {"error": str(e)}
        continue
    try:
        from packaging.version import Version
        key = Version
        def final(v): return not Version(v).is_prerelease and not Version(v).is_devrelease
    except Exception:
        key = str
        def final(v): return all(t not in v for t in ("a", "b", "rc", "dev"))
    out[pkg] = {v: (d[v][0]["upload_time"][:10] if d[v] else "?")
                for v in sorted((x for x in d if final(x)), key=key)}
json.dump(out, open("results/sweep/release_dates.json", "w"), indent=1)
print("release dates -> results/sweep/release_dates.json")
PYEOF
}

summarize() {
  python3 - <<'PYEOF'
import json, glob, os
dec = json.JSONDecoder()

def load_or_salvage(path):
    raw = open(path, errors='replace').read()
    try:
        return json.loads(raw), 'clean'
    except Exception:
        pass
    starts = ([0] if raw.startswith('{') else []) + \
             [i + 1 for i in range(len(raw)) if raw.startswith('\n{', i)]
    best = None
    for i in starts:
        try:
            obj, _ = dec.raw_decode(raw[i:])
        except Exception:
            continue
        if isinstance(obj, dict) and any(
                'violation' in k or 'verdict' in k or '_version' in k for k in obj):
            best = obj
    return best, 'salvaged'

for f in sorted(glob.glob('results/sweep/*.json')):
    if f.endswith('release_dates.json'):
        continue
    d, how = load_or_salvage(f)
    if d is None:
        side = f[:-5] + '.stderr.log'
        tail = '(no sidecar)'
        if os.path.exists(side):
            lines = [l for l in open(side, errors='replace').read().splitlines()
                     if l.strip()]
            tail = (lines[-1][:100] if lines else '(empty sidecar)')
        print(f"{os.path.basename(f):58s} NO JSON (size={os.path.getsize(f)}b) -- {tail}")
        continue
    flat, errs = {}, {}
    def walk(o, p=''):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, p + '/' + k)
        elif isinstance(o, bool) and ('violation' in p or 'verdict' in p):
            flat[p] = o
        elif isinstance(o, str) and o and (
                p.endswith('resume_error') or p.endswith('corrupt_load_error')
                or 'probe_error' in p or p.endswith('_error')):
            errs[p] = o.splitlines()[-1][:80]
    walk(d)
    line = ', '.join(f"{k.split('/')[-1]}={v}" for k, v in sorted(flat.items()))
    for k, v in sorted(errs.items()):
        line += f"  |  {k.split('/')[-1]}: {v}"
    if how == 'salvaged':
        line += '  [salvaged from polluted stdout]'
    print(f"{os.path.basename(f):58s} {line or 'no verdict fields'}")
PYEOF
}

if [[ "${1:-}" == "--versions" ]]; then versions_helper "${2:-}"; exit 0; fi
if [[ "${1:-}" == "--summary"  ]]; then echo "== verdict summary =="; summarize; exit 0; fi

mkdir -p "$OUT"

sweep() {
  local pkg="$1"; shift
  local probe_list="$1"; shift
  local versions=("$@")
  for v in "${versions[@]}"; do
    local tag="${pkg//[^a-zA-Z0-9]/_}_$v"
    local env=".sweep-$tag"
    echo "== $pkg==$v =="
    local todo=0
    for probe in $probe_list; do
      [[ -s "$OUT/$(basename "$probe" .py)__$tag.json" ]] || todo=1
    done
    if [[ $todo -eq 0 ]]; then echo "  receipts present -- skipping"; continue; fi
    uv venv "$env" --python 3.12 >/dev/null
    # shellcheck disable=SC1090
    source "$env/bin/activate"
    if ! uv pip install -q "$pkg==$v" 2>"$OUT/install_$tag.stderr.log"; then
      echo "  install failed for $pkg==$v (recorded)"
      echo "{\"skipped\": \"install failed\", \"package\": \"$pkg\", \"version\": \"$v\"}" \
        > "$OUT/install_$tag.json"
      deactivate; rm -rf "$env"; continue
    fi
    for probe in $probe_list; do
      local base; base=$(basename "$probe" .py)
      local rc=0
      python "$probe" > "$OUT/${base}__$tag.json" \
                     2> "$OUT/${base}__$tag.stderr.log" || rc=$?
      [[ $rc -ne 0 ]] && echo "  probe $base exited $rc for $pkg==$v (output + stderr recorded)"
    done
    deactivate
    rm -rf "$env"
  done
}

# --- back-sweep (prior releases) + forward-sweep (post-pin finals) ----------
# crewai pin 1.15.2 (2026-07-08). Prior minor: 1.14.1 (restore API absent
# there -- feature boundary, expected leg error). Forward finals: 1.15.3
# (2026-07-16, pin day), 1.15.4, 1.15.5, 1.15.6 (2026-07-24).
sweep "crewai" \
  "probes/115_p2_conformance_crewai.py probes/115b_p2_crewai_checkpointconfig.py" \
  1.14.1 1.15.2 1.15.3 1.15.4 1.15.5 1.15.6

# llama-index-workflows pin 2.22.2 == newest final: pin is current.
sweep "llama-index-workflows" \
  "probes/114_p2_conformance_llamaindex.py" \
  2.20.0 2.21.0 2.22.2

# pydantic-graph pin 1.107.1; the 2.x line post-dates it (releases daily,
# 2.14-2.18 over 2026-07-21..25). One forward cell at a 2.x final: a crash
# is an API-break scope statement, not a failure.
sweep "pydantic-graph" \
  "probes/119_p2_conformance_pydantic_graph.py" \
  1.105.0 1.106.0 1.107.1 2.18.0

dump_dates || echo "  (dates dump skipped -- network?)"
echo
echo "== verdict summary =="
summarize
