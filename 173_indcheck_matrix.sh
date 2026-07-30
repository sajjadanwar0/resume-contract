#!/usr/bin/env bash
# 173_indcheck_matrix.sh -- inductive-invariant matrix for ResumeContract.tla
#
# Runs IndCheck.tla at three constant sets, extracts ONLY the stable verdict
# fields, writes a stable-view receipt, and self-audits every field against
# the values committed below. Exits nonzero on any mismatch.
#
# WHY THE FIELD SELECTION MATTERS. TLC prints a fresh seed and fresh
# fingerprint-collision estimates on every invocation; both are
# invocation-dependent and neither is a verdict. A cross-environment guard
# that diffs whole TLC logs will read those as divergence. This script
# extracts the verdict line, the generated/distinct counts, and the search
# depth -- and nothing else.
#
# Worker count does not affect these counts: the run enumerates all states
# satisfying the invariant as initial states rather than racing a BFS to a
# counterexample, so there is no parallel-search nondeterminism to control
# for. The repository's single-worker convention for counterexample depths
# does not apply to this module.
#
# Idempotent. Safe to re-run. Usage:  ./173_indcheck_matrix.sh [workers]

set -euo pipefail

WORKERS="${1:-4}"
TLA_DIR="formal/tla"
RAW_DIR="${TLA_DIR}/indcheck"
OUT_DIR="results/tla/indcheck"
JAR="${TLA2TOOLS:-$HOME/tla2tools.jar}"

[[ -f "$JAR" ]] || { echo "FATAL: tla2tools.jar not found at $JAR (set TLA2TOOLS)"; exit 2; }
[[ -f "${TLA_DIR}/IndCheck.tla" ]] || { echo "FATAL: ${TLA_DIR}/IndCheck.tla missing"; exit 2; }

mkdir -p "$RAW_DIR" "$OUT_DIR"

# cfg | label | expected generated | expected distinct | constants (for the receipt)
CELLS=(
  "IndCheck.cfg|R0|19659|8610|N=3 IP=2 |V|=2 MaxResumes=2 MaxCrashes=1 MaxExtraResumes=1"
  "IndCheck_R2.cfg|R2|1009076|450926|N=4 IP=3 |V|=2 MaxResumes=2 MaxCrashes=2 MaxExtraResumes=1"
  "IndCheck_R3.cfg|R3|249915|97800|N=3 IP=2 |V|=3 MaxResumes=3 MaxCrashes=1 MaxExtraResumes=2"
)

fail=0
json_cells=""

for cell in "${CELLS[@]}"; do
  IFS='|' read -r cfg label exp_gen exp_dist consts <<< "$cell"

  cfg_path="${TLA_DIR}/${cfg}"
  [[ -f "$cfg_path" ]] || { echo "FATAL: ${cfg_path} missing"; exit 2; }

  # CHECK_DEADLOCK FALSE is mandatory here: an invariant-state with no
  # enabled action is normal (e.g. waiting with the resume budget spent) and
  # is not a defect. With deadlock checking on, TLC errors at depth 1 and
  # the run establishes nothing.
  grep -q "CHECK_DEADLOCK FALSE" "$cfg_path" || {
    echo "FATAL: ${cfg} lacks CHECK_DEADLOCK FALSE -- run would be meaningless"; exit 2; }

  raw="${RAW_DIR}/IndCheck_${label}.out"
  meta="${TLA_DIR}/.states_${label}"
  rm -rf "$meta"

  echo "== ${label}: ${consts}"
  ( cd "$TLA_DIR" && java -XX:+UseParallelGC -cp "$JAR" tlc2.TLC \
      -workers "$WORKERS" -metadir ".states_${label}" \
      -config "$cfg" IndCheck.tla ) > "$raw" 2>&1 || true
  rm -rf "$meta"

  # ---- stable fields only ------------------------------------------------
  if grep -q "Model checking completed. No error has been found." "$raw"; then
    verdict="no_error"
  elif grep -q "Error: Invariant" "$raw"; then
    verdict="violated:$(grep -m1 'Error: Invariant' "$raw" | sed 's/.*Invariant \([A-Za-z_]*\).*/\1/')"
  else
    verdict="indeterminate"
  fi
  gen=$(grep -oE '^[0-9]+ states generated' "$raw" | tail -1 | grep -oE '^[0-9]+' || echo "")
  dist=$(grep -oE '[0-9]+ distinct states found' "$raw" | tail -1 | grep -oE '^[0-9]+' || echo "")
  depth=$(grep -oE 'depth of the complete state graph search is [0-9]+' "$raw" \
            | grep -oE '[0-9]+$' || echo "")

  ok=1
  [[ "$verdict" == "no_error" ]] || { echo "   MISMATCH verdict: $verdict"; ok=0; }
  [[ "$gen"  == "$exp_gen"  ]] || { echo "   MISMATCH generated: got '$gen' want $exp_gen"; ok=0; }
  [[ "$dist" == "$exp_dist" ]] || { echo "   MISMATCH distinct:  got '$dist' want $exp_dist"; ok=0; }
  [[ "$depth" == "1" ]] || { echo "   MISMATCH depth: got '$depth' want 1"; ok=0; }

  if [[ $ok -eq 1 ]]; then
    echo "   OK  verdict=${verdict} generated=${gen} distinct=${dist} depth=${depth}"
  else
    fail=1
  fi

  json_cells="${json_cells}    {\"label\":\"${label}\",\"constants\":\"${consts}\","
  json_cells="${json_cells}\"verdict\":\"${verdict}\",\"generated\":${gen:-null},"
  json_cells="${json_cells}\"distinct\":${dist:-null},\"depth\":${depth:-null},"
  json_cells="${json_cells}\"expected_generated\":${exp_gen},\"expected_distinct\":${exp_dist},"
  json_cells="${json_cells}\"audit\":\"$([[ $ok -eq 1 ]] && echo pass || echo FAIL)\"},\n"
done

# ---- stable-view receipt -------------------------------------------------
# Deliberately omits seed and fingerprint-collision estimates: both are
# invocation-dependent and would make a cross-host comparison of this file
# report divergence where there is none.
{
  echo "{"
  echo "  \"artifact\": \"IndCheck.tla inductive-invariant matrix\","
  echo "  \"method\": \"INIT <- InvFast, NEXT <- Next: every invariant-state taken as an initial state and every successor checked, so a clean run establishes Inv /\\\\ [Next]_vars => Inv' at the configured constants\","
  echo "  \"also_checked\": [\"EquivGuard (InvFast <=> Inv)\", \"InvImpliesContract\", \"InitImpliesInv\"],"
  echo "  \"constant_coverage\": \"each of the six configuration bounds varies in at least one pair across R0/R2/R3\","
  echo "  \"stable_fields\": [\"verdict\", \"generated\", \"distinct\", \"depth\"],"
  echo "  \"excluded_fields\": [\"seed\", \"fingerprint_collision_estimates\"],"
  echo "  \"workers_affect_counts\": false,"
  echo "  \"cells\": ["
  printf "%b" "$json_cells" | sed '$ s/,$//'
  echo "  ],"
  echo "  \"matrix_audit\": \"$([[ $fail -eq 0 ]] && echo pass || echo FAIL)\""
  echo "}"
} > "${OUT_DIR}/indcheck_matrix.json"

echo
if [[ $fail -eq 0 ]]; then
  echo "MATRIX AUDIT: pass (3/3 cells match committed verdicts)"
  echo "  raw    : ${RAW_DIR}/IndCheck_{R0,R2,R3}.out"
  echo "  receipt: ${OUT_DIR}/indcheck_matrix.json"
  exit 0
else
  echo "MATRIX AUDIT: FAIL -- see mismatches above"
  exit 1
fi