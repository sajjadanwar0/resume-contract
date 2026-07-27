#!/usr/bin/env bash
# 166_parked_crash_matrix.sh -- parked-crash companion module, full check
# ===========================================================================
# Runs TLC over formal/tla/ResumeContractParked.tla, the companion module
# that removes the base model's stated scope bound "a crash while the run
# is parked awaiting the human is outside the transition relation"
# (paper Sec. 4.2).  The module is the base module verbatim plus one
# constant (ParkDurable), one latch variable (emitted), and one action
# (CrashWhileParked) that follows the SAME recovery-mode disjunction as
# CrashRecover from the parked location.  Checks, in order:
#
#   1. P0_durable      reference semantics, ParkDurable=TRUE, all six
#                      invariants + TypeOK        -> expected: no error
#   2. P0_volatile     ParkDurable=FALSE, same     -> expected: no error
#                      (safety holds either way: nothing fires, nothing
#                      regresses)
#   3. PLIVE_durable   FairSpec + EventuallyCompletes, ParkDurable=TRUE
#                                                  -> expected: no error
#   4. PLIVE_volatile  ParkDurable=FALSE           -> expected: TEMPORAL
#                      VIOLATION (safety by deadness -- the pydantic-graph
#                      disposition of Sec. 6.4 as a model-level
#                      counterexample; its .out is the receipt)
#   5. PX_* (36 cells) per-invariant single-fault matrix, ParkDurable=TRUE,
#                      constants VERBATIM from 145_independence_matrix.sh
#                      (nondetrec/prefixreplay rows keep MaxCrashes=2)
#                      -> expected: verdict pattern AND counterexample
#                      depths IDENTICAL to the base module's Table 3.
#
# The script diffs fresh verdicts against the expected matrix embedded
# below (third-party container run 2026-07-26, OpenJDK 21, tla2tools
# latest release; a host replication is the authoritative second
# environment) and exits nonzero on any mismatch, so the replication is
# self-auditing.
#
# Usage:   bash 166_parked_crash_matrix.sh [REPO_ROOT] [--r8]
#          --r8 additionally runs the R8-scale durable reference check
#          (NTasks=10, IP=5, 4 values, 5 resumes, 4 crashes, 4 extras;
#          large -- expect a long single-worker run).  R8 is reported but
#          not part of the embedded-expectation audit.  Container run
#          2026-07-26 (16 workers): no error, 14,753,555 states generated,
#          7,435,360 distinct -- against the base module's R8 receipt
#          (results/tla/R8_scale.out: 14,753,520 / 7,435,360), the parked
#          crash adds exactly 35 transitions and zero new distinct states.
# Output:  REPO_ROOT/formal/tla/parked/{*.cfg,*.out,parked_matrix.json,
#            parked_matrix.md} and a copy of the two artifacts under
#            results/tla/parked/.
# TLC:     resolved like 116_run_tlc.sh ($TLC_CMD > $TLA_TOOLS_JAR >
#          ./tla2tools.jar > $HOME/tla2tools.jar > download from
#          tlaplus/tlaplus releases).
set -euo pipefail
REPO="$(cd "${1:-.}" && pwd)"
R8=0; [[ "${2:-}" == "--r8" || "${1:-}" == "--r8" ]] && R8=1
[[ "${1:-}" == "--r8" ]] && REPO="$(pwd)"
TLA_DIR="$REPO/formal/tla"
PK_DIR="$TLA_DIR/parked"
RES_DIR="$REPO/results/tla/parked"
[ -f "$TLA_DIR/ResumeContractParked.tla" ] || { echo "ERROR: $TLA_DIR/ResumeContractParked.tla not found -- pass the paper repo root"; exit 2; }
mkdir -p "$PK_DIR" "$RES_DIR"

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

# ---- 1. generate the configs ----------------------------------------------
python3 - "$PK_DIR" << 'PYGEN'
import sys, os
pk = sys.argv[1]
inv6 = ["TypeOK","EffectExactlyOnce","PrefixConsistency","ForkDeterminism",
        "CheckpointValidity","ConsumeOnce","RecoveryDeterminism"]
base = """SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {{va, vb}}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = {crashes}
  MaxExtraResumes = 1
  ParkDurable = {park}
  FaultReplay = {Replay}
  FaultForkIgnore = {ForkIgnore}
  FaultInvalidPersist = {InvalidPersist}
  FaultNondetRecovery = {NondetRecovery}
  FaultDoubleConsume = {DoubleConsume}
  FaultPrefixReplay = {PrefixReplay}
INVARIANTS
{invs}
"""
off = dict(Replay="FALSE", ForkIgnore="FALSE", InvalidPersist="FALSE",
           NondetRecovery="FALSE", DoubleConsume="FALSE", PrefixReplay="FALSE")
for tag, park in (("P0_durable", "TRUE"), ("P0_volatile", "FALSE")):
    open(os.path.join(pk, f"{tag}.cfg"), "w").write(
        base.format(crashes=1, park=park,
                    invs="\n".join("  " + i for i in inv6), **off))
live = """SPECIFICATION FairSpec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {{va, vb}}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = 1
  MaxExtraResumes = 1
  ParkDurable = {park}
  FaultReplay = FALSE
  FaultForkIgnore = FALSE
  FaultInvalidPersist = FALSE
  FaultNondetRecovery = FALSE
  FaultDoubleConsume = FALSE
  FaultPrefixReplay = FALSE
PROPERTIES
  EventuallyCompletes
"""
for tag, park in (("PLIVE_durable", "TRUE"), ("PLIVE_volatile", "FALSE")):
    open(os.path.join(pk, f"{tag}.cfg"), "w").write(live.format(park=park))
faults = {"replay": "Replay", "forkignore": "ForkIgnore",
          "invalidpersist": "InvalidPersist", "nondetrec": "NondetRecovery",
          "doubleconsume": "DoubleConsume", "prefixreplay": "PrefixReplay"}
for ftag, key in faults.items():
    flags = dict(off); flags[key] = "TRUE"
    crashes = 2 if ftag in ("nondetrec", "prefixreplay") else 1  # R4/R9 rows
    for inv in inv6[1:]:
        open(os.path.join(pk, f"PX_{ftag}__{inv}.cfg"), "w").write(
            base.format(crashes=crashes, park="TRUE", invs="  " + inv, **flags))
r8 = base.format(crashes=4, park="TRUE",
                 invs="\n".join("  " + i for i in inv6), **off)
r8 = (r8.replace("NTasks = 3", "NTasks = 10").replace("IP = 2", "IP = 5")
        .replace("{va, vb}", "{va, vb, vc, vd}")
        .replace("MaxResumes = 2", "MaxResumes = 5")
        .replace("MaxExtraResumes = 1", "MaxExtraResumes = 4"))
open(os.path.join(pk, "P8_durable.cfg"), "w").write(r8)
print("wrote 41 configs")
PYGEN

# ---- 2. expected verdicts (container run 2026-07-26) ----------------------
# name verdict depth   (depth = number of "State N" lines in the TLC trace;
#                       "-" for holds; liveness violation depth recorded as
#                       "temporal")
EXPECTED="
P0_durable holds -
P0_volatile holds -
PLIVE_durable holds -
PLIVE_volatile temporal_violation temporal
PX_replay__EffectExactlyOnce violated 4
PX_replay__PrefixConsistency violated 4
PX_replay__ForkDeterminism holds -
PX_replay__CheckpointValidity holds -
PX_replay__ConsumeOnce violated 7
PX_replay__RecoveryDeterminism holds -
PX_forkignore__EffectExactlyOnce holds -
PX_forkignore__PrefixConsistency holds -
PX_forkignore__ForkDeterminism violated 5
PX_forkignore__CheckpointValidity holds -
PX_forkignore__ConsumeOnce holds -
PX_forkignore__RecoveryDeterminism holds -
PX_invalidpersist__EffectExactlyOnce holds -
PX_invalidpersist__PrefixConsistency holds -
PX_invalidpersist__ForkDeterminism holds -
PX_invalidpersist__CheckpointValidity violated 5
PX_invalidpersist__ConsumeOnce holds -
PX_invalidpersist__RecoveryDeterminism holds -
PX_nondetrec__EffectExactlyOnce violated 4
PX_nondetrec__PrefixConsistency violated 4
PX_nondetrec__ForkDeterminism holds -
PX_nondetrec__CheckpointValidity holds -
PX_nondetrec__ConsumeOnce violated 6
PX_nondetrec__RecoveryDeterminism violated 4
PX_doubleconsume__EffectExactlyOnce violated 6
PX_doubleconsume__PrefixConsistency holds -
PX_doubleconsume__ForkDeterminism holds -
PX_doubleconsume__CheckpointValidity holds -
PX_doubleconsume__ConsumeOnce violated 6
PX_doubleconsume__RecoveryDeterminism holds -
PX_prefixreplay__EffectExactlyOnce violated 4
PX_prefixreplay__PrefixConsistency violated 4
PX_prefixreplay__ForkDeterminism holds -
PX_prefixreplay__CheckpointValidity holds -
PX_prefixreplay__ConsumeOnce holds -
PX_prefixreplay__RecoveryDeterminism holds -
"

# ---- 3. run TLC per cell ---------------------------------------------------
cd "$PK_DIR"
run_one () {
  local name="$1"
  set +e
  $TLC -deadlock -metadir "meta_$name" -config "$name.cfg" -workers 1 \
    ../ResumeContractParked.tla > "$name.out" 2>&1
  local rc=$?
  set -e
  # Classify by TLC exit status first (0 = OK, 12 = safety violation,
  # 13 = liveness violation), message text second: the wording differs
  # across tla2tools builds, the exit codes do not.
  if [[ $rc -eq 0 ]] && grep -q "No error has been found" "$name.out"; then
    echo "holds -"
  elif [[ $rc -eq 13 ]] || grep -q "Temporal properties were violated" "$name.out"; then
    echo "temporal_violation temporal"
  elif [[ $rc -eq 12 ]] || grep -q "is violated" "$name.out"; then
    echo "violated $(grep -c '^State [0-9]' "$name.out")"
  else
    echo "tlc_error rc=$rc"
  fi
}

FAIL=0
: > _rows.txt
while read -r name exp_v exp_d; do
  [[ -z "$name" ]] && continue
  got="$(run_one "$name")"
  got_v="${got%% *}"; got_d="${got##* }"
  status="ok"
  if [[ "$got_v" != "$exp_v" || "$got_d" != "$exp_d" ]]; then
    status="MISMATCH (expected: $exp_v $exp_d)"; FAIL=1
  fi
  printf '%-46s %-20s %-8s %s\n' "$name" "$got_v" "$got_d" "$status" | tee -a _rows.txt
done <<< "$EXPECTED"

# ---- 4. optional R8-scale durable reference -------------------------------
R8_LINE="P8_durable skipped -"
if [[ "$R8" == "1" ]]; then
  echo "running R8-scale durable reference (single worker; this is large)..."
  got="$(run_one P8_durable)"
  R8_LINE="P8_durable ${got} advisory"
  echo "$R8_LINE"
  grep "states generated" P8_durable.out | tail -1 || true
fi

# ---- 5. artifacts ----------------------------------------------------------
python3 - "$PK_DIR" "$RES_DIR" "$FAIL" "$R8_LINE" << 'PYART'
import json, sys, datetime, subprocess, os
pk, res, fail, r8line = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
rows = []
for line in open(os.path.join(pk, "_rows.txt")):
    parts = line.rstrip("\n").split(None, 3)
    if len(parts) >= 3:
        rows.append({"run": parts[0], "verdict": parts[1],
                     "depth": parts[2],
                     "audit": parts[3] if len(parts) > 3 else "ok"})
doc = {
    "script": "166_parked_crash_matrix.sh",
    "module": "formal/tla/ResumeContractParked.tla",
    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "invocation": "tlc2.TLC -deadlock -config <cfg> -workers 1 ResumeContractParked.tla",
    "audit_against": "container run 2026-07-26 (embedded)",
    "audit_pass": fail == "0",
    "r8": r8line,
    "cells": rows,
}
json.dump(doc, open(os.path.join(pk, "parked_matrix.json"), "w"), indent=2)
md = ["# Parked-crash matrix (ResumeContractParked.tla)", "",
      f"- audit vs embedded expectations: {'PASS' if fail=='0' else 'FAIL'}",
      f"- {r8line}", "", "| run | verdict | depth | audit |", "|---|---|---|---|"]
md += [f"| {r['run']} | {r['verdict']} | {r['depth']} | {r['audit']} |" for r in rows]
open(os.path.join(pk, "parked_matrix.md"), "w").write("\n".join(md) + "\n")
for f in ("parked_matrix.json", "parked_matrix.md"):
    open(os.path.join(res, f), "w").write(open(os.path.join(pk, f)).read())
print(f"artifacts: {pk}/parked_matrix.{{json,md}} (+ copies in {res}/)")
PYART

if [[ "$FAIL" == "1" ]]; then
  echo "PARKED MATRIX: MISMATCH vs embedded expectations -- inspect .out files" >&2
  exit 1
fi
echo "PARKED MATRIX: all 40 cells match the embedded expectations"
