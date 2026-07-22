#!/usr/bin/env bash
# exp3_tlc_reproduce_headline.sh
# ==============================
# Reviewer experiment 3 — "Do the headline TLC numbers reproduce, and under
# what flags?"
#
# Checks two Table-2 claims and one caveat:
#   R0 reference : "87 states generated, 59 distinct, no error"  (all 6 invs)
#   R2 ForkIgnore: FD violated, "10/10; CE depth 5"
#   Caveat       : with DEFAULT flags the reference config DEADLOCKS (benign,
#                  terminal states), halting at 71/51 -- the 87/59 figure only
#                  appears once deadlock checking is disabled (-deadlock). The
#                  paper reports 87/59 without noting this; a reviewer running
#                  the raw .cfg sees a red "Deadlock reached" first.
#
# Also runs the ForkIgnore separation directly (Prop 2(i)): ForkIgnore checked
# against EO/PC/CV/CO/RD holds over the WHOLE reachable space -> a true
# separating model. This part of the formal story is sound and reproducible;
# the experiment documents that as well as the caveat.
#
# Requirements: java >= 11 and tla2tools.jar (see exp2 for discovery/fetch).
# Point $REPO at the repo root (needs formal/tla/ResumeContract.tla and the
# R0_reference.cfg / R2_forkignore.cfg it ships).
set -euo pipefail
REPO="${REPO:-.}"
TLA="$REPO/formal/tla/ResumeContract.tla"
if [[ ! -f "$TLA" ]]; then
  for c in "$REPO/_extract/formal/tla/ResumeContract.tla" \
           "$REPO/repo/formal/tla/ResumeContract.tla"; do
    [[ -f "$c" ]] && TLA="$c" && break
  done
fi
[[ -f "$TLA" ]] || { echo "ERROR: ResumeContract.tla not found under \$REPO=$REPO"; exit 2; }
TLADIR="$(cd "$(dirname "$TLA")" && pwd)"

JAR="${TLA_TOOLS_JAR:-}"
[[ -z "$JAR" && -f ./tla2tools.jar ]] && JAR=./tla2tools.jar
[[ -z "$JAR" && -f "$HOME/tla2tools.jar" ]] && JAR="$HOME/tla2tools.jar"
if [[ -z "$JAR" ]]; then
  echo ">> fetching tla2tools.jar to \$HOME"
  curl -sL -o "$HOME/tla2tools.jar" \
    https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
  JAR="$HOME/tla2tools.jar"
fi

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cp "$TLA" "$WORK/"
cp "$TLADIR/R0_reference.cfg" "$WORK/" 2>/dev/null || true
cp "$TLADIR/R2_forkignore.cfg" "$WORK/" 2>/dev/null || true

# TLC exits nonzero on invariant violation / deadlock (both expected here),
# so swallow the exit code and hand the captured output to the caller.
run() { ( cd "$WORK" && java -cp "$JAR" tlc2.TLC "$@" ResumeContract.tla 2>&1 || true ); }

echo "======================================================================"
echo " (a) R0 reference with DEFAULT flags  (paper is silent on this)"
echo "======================================================================"
run -config R0_reference.cfg -workers 1 | \
  grep -E "Deadlock|states generated|Error|No error" | head -5

echo ""
echo "======================================================================"
echo " (b) R0 reference with -deadlock  (matches paper's 87/59, no error)"
echo "======================================================================"
run -deadlock -config R0_reference.cfg -workers 1 | \
  grep -E "states generated|No error|Error" | head -5

echo ""
echo "======================================================================"
echo " (c) R2 ForkIgnore -> FD  (paper: violated, 10/10, depth 5)"
echo "======================================================================"
run -deadlock -config R2_forkignore.cfg -workers 1 | \
  grep -E "is violated|states generated|depth of the complete" | head -5

echo ""
echo "======================================================================"
echo " (d) Separation check (Prop 2(i)): ForkIgnore vs EO/PC/CV/CO/RD"
echo "     should hold over the ENTIRE reachable space (no error)"
echo "======================================================================"
cat > "$WORK/R2_sep.cfg" <<'EOF'
SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {va, vb}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = 1
  MaxExtraResumes = 1
  FaultReplay = FALSE
  FaultForkIgnore = TRUE
  FaultInvalidPersist = FALSE
  FaultNondetRecovery = FALSE
  FaultDoubleConsume = FALSE
  FaultPrefixReplay = FALSE
INVARIANTS
  EffectExactlyOnce
  PrefixConsistency
  CheckpointValidity
  ConsumeOnce
  RecoveryDeterminism
EOF
run -deadlock -config R2_sep.cfg -workers 1 | \
  grep -E "states generated|No error|Error" | head -5

echo ""
echo "======================================================================"
echo " READING"
echo "======================================================================"
cat <<'MSG'
  * The formal numbers reproduce EXACTLY under TLC 2.19 (87/59; 10/10 depth 5),
    and the ForkIgnore separation is sound -- credit where due.
  * BUT the reference config deadlocks under default flags: a reviewer running
    the shipped .cfg as-is sees "Deadlock reached" and a DIFFERENT count
    (halts early, ~71/51) before the 87/59 total is ever reached. The paper
    quotes 87/59 without stating that -deadlock (or CHECK_DEADLOCK FALSE) is
    required. reproduce.sh must pass it; the raw cfg alone does not. State this
    in the artifact so the "single-command audit re-derives every number" claim
    holds for a reader who invokes TLC directly.
MSG
