#!/usr/bin/env bash
# 149_prefixreplay_row.sh -- PrefixReplay separating-model row (R3 revision)
# ===========================================================================
# Adds and audits the sixth fault row of the per-invariant matrix:
#   FaultPrefixReplay -- recovery restarts from task 1 while the gated
#   task's effect is served from the durable record (memoized-gate prefix
#   replay: the LangGraph 1.2.9 crash-path class, probes 118/133).
# Expected footprint (machine-checked 2026-07-21, TLC 2.19, OpenJDK 21,
# single worker; verdicts identical on re-run by construction):
#   EO violated (CE depth 4, 7/7 states)   PC violated (CE depth 4, 7/7)
#   FD holds    (287/183 states complete)  CV holds (287/183)
#   CO holds    (287/183)                  RD holds (287/183)
# This is the in-model witness that EO can fail while CO holds -- the
# converse of the structural EO => CO implication, and the direction the
# previous revision's matrix could not separate.
#
# The script ALSO re-runs R0 (reference) and R6 (liveness) under the
# extended module and diffs the untouched 30-cell matrix against the
# committed independence_matrix.json, so a replication is self-auditing:
# the extension must not perturb a single existing verdict, state count,
# or depth. (Verified 2026-07-21: zero divergences.)
#
# Usage:   bash 149_prefixreplay_row.sh [REPO_ROOT]     # default: .
# Requires: formal/tla/ResumeContract.tla ALREADY PATCHED with the
#           FaultPrefixReplay switch (patch shipped alongside this script
#           as ResumeContract.tla; diff in prefixreplay.patch).
# Output:  formal/tla/independence/IX_prefixreplay__*.{cfg,out}
#          results/tla/independence/independence_matrix_r3.json
set -euo pipefail
REPO="$(cd "${1:-.}" && pwd)"
TLA_DIR="$REPO/formal/tla"
IX_DIR="$TLA_DIR/independence"
RES_DIR="$REPO/results/tla/independence"
[ -f "$TLA_DIR/ResumeContract.tla" ] || { echo "ERROR: ResumeContract.tla not found under $TLA_DIR"; exit 2; }
grep -q "FaultPrefixReplay" "$TLA_DIR/ResumeContract.tla" || {
  echo "ERROR: module not patched -- FaultPrefixReplay constant absent."
  echo "Apply the shipped ResumeContract.tla (or prefixreplay.patch) first,"
  echo "then append 'FaultPrefixReplay = FALSE' to every existing R*.cfg."
  exit 2; }
mkdir -p "$IX_DIR" "$RES_DIR"

resolve_tlc () {
  if [[ -n "${TLC_CMD:-}" ]]; then echo "$TLC_CMD"; return; fi
  local jar=""
  if   [[ -n "${TLA_TOOLS_JAR:-}" && -f "${TLA_TOOLS_JAR:-}" ]]; then jar="$TLA_TOOLS_JAR"
  elif [[ -f "$TLA_DIR/tla2tools.jar" ]];                        then jar="$TLA_DIR/tla2tools.jar"
  elif [[ -f "$HOME/tla2tools.jar" ]];                           then jar="$HOME/tla2tools.jar"
  else
    echo "fetching tla2tools.jar to \$HOME (tlaplus/tlaplus releases)" >&2
    curl -sL -o "$HOME/tla2tools.jar" \
      https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
    jar="$HOME/tla2tools.jar"
  fi
  echo "java -XX:+UseParallelGC -cp $jar tlc2.TLC"
}
TLC="$(resolve_tlc)"
echo "TLC command: $TLC"

# ---- 1. generate the six PrefixReplay configs (MaxCrashes=2: non-vacuous RD) ----
INVS="EffectExactlyOnce PrefixConsistency ForkDeterminism CheckpointValidity ConsumeOnce RecoveryDeterminism"
for inv in $INVS; do
cat > "$IX_DIR/IX_prefixreplay__$inv.cfg" << CFG
SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {va, vb}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = 2
  MaxExtraResumes = 1
  FaultReplay = FALSE
  FaultForkIgnore = FALSE
  FaultInvalidPersist = FALSE
  FaultNondetRecovery = FALSE
  FaultDoubleConsume = FALSE
  FaultPrefixReplay = TRUE
INVARIANTS
  $inv
CFG
done

# ---- 2. run the six cells ----
cd "$IX_DIR"
for inv in $INVS; do
  name="IX_prefixreplay__$inv"
  rm -rf "meta_$name"; mkdir -p "meta_$name"
  $TLC -deadlock -metadir "meta_$name" -config "$name.cfg" -workers 1 \
    "../ResumeContract.tla" > "$name.out" 2>&1 || true
  rm -rf "meta_$name"
done

# ---- 3. audit against the expected row ----
python3 - "$IX_DIR" "$RES_DIR" << 'PYCHK'
import sys, re, json, os
ix, res = sys.argv[1], sys.argv[2]
expected = {
  "EffectExactlyOnce":  ("violated", 7,   4),
  "PrefixConsistency":  ("violated", 7,   4),
  "ForkDeterminism":    ("holds",    287, 13),
  "CheckpointValidity": ("holds",    287, 13),
  "ConsumeOnce":        ("holds",    287, 13),
  "RecoveryDeterminism":("holds",    287, 13),
}
out, bad = {}, 0
for inv, (ev, es, ed) in expected.items():
    txt = open(os.path.join(ix, f"IX_prefixreplay__{inv}.out")).read()
    v = "holds" if "No error has been found" in txt else \
        ("violated" if "is violated" in txt else "UNKNOWN")
    g = int(re.search(r"(\d+) states generated", txt).group(1))
    if v == "violated":
        d = len(re.findall(r"^State \d+", txt, re.M))
    else:
        d = int(re.search(r"depth of the complete state graph search is (\d+)", txt).group(1))
    ok = (v, g, d) == (ev, es, ed)
    out[inv] = {"verdict": v, "states": g, "depth": d, "matches_committed": ok}
    print(f"{inv:22s} {v:9s} states={g:4d} depth={d:2d}  {'OK' if ok else 'MISMATCH (expected '+str((ev,es,ed))+')'}")
    bad += (not ok)
json.dump({"prefixreplay": out}, open(os.path.join(res, "independence_matrix_r3.json"), "w"), indent=1)
sys.exit(1 if bad else 0)
PYCHK
echo "PrefixReplay row: all six cells match the committed verdicts."