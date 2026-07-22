#!/usr/bin/env bash
# exp2_tlc_prefixreplay_gate_check.sh
# ===================================
# Reviewer experiment 2 — "Does FaultPrefixReplay actually model the
# LangGraph memoized-gate crash path it is sold as?"
#
# The paper introduces FaultPrefixReplay (R9) as
#   "recovery restarts from task 1 while the gated task's effect is served
#    from the durable record -- memoized-gate prefix replay, the LangGraph
#    1.2.9 crash-path class"
# and uses it as the separating witness for EO vs CO (Prop 2(iv)).
#
# This experiment runs TLC on the R9 EO invariant, extracts the counter-
# example TLC actually finds, and checks whether the gate is ever consumed
# on that trace (consumedVal != NoVal) and whether the gated task's memoized
# effect is ever the thing that duplicates.
#
# RESULT (reproduced by the reviewer): the shortest EO counterexample is pure
# prefix re-execution of a NON-gated task with consumedVal == NoVal in EVERY
# state -- the interrupt is never consumed, the gate is never reached, and the
# special "serve IP's effect from the record" clause is dead code on the trace.
# The separation (EO fails, CO holds) is real; the "memoized-gate /
# LangGraph-crash-path shadow" story attached to it is not what the model
# witnesses.
#
# Requirements: java >= 11. The script finds tla2tools.jar via $TLA_TOOLS_JAR,
# ./tla2tools.jar, $HOME/tla2tools.jar, or downloads it (needs network to
# github.com). Point $REPO at the repo root (default: current dir); it needs
# formal/tla/ResumeContract.tla.
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

# locate or fetch tla2tools.jar
JAR="${TLA_TOOLS_JAR:-}"
[[ -z "$JAR" && -f ./tla2tools.jar ]] && JAR=./tla2tools.jar
[[ -z "$JAR" && -f "$HOME/tla2tools.jar" ]] && JAR="$HOME/tla2tools.jar"
if [[ -z "$JAR" ]]; then
  echo ">> fetching tla2tools.jar to \$HOME (tlaplus/tlaplus releases)"
  curl -sL -o "$HOME/tla2tools.jar" \
    https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
  JAR="$HOME/tla2tools.jar"
fi
echo ">> TLA module : $TLA"
echo ">> TLC jar    : $JAR"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cp "$TLA" "$WORK/ResumeContract.tla"

cat > "$WORK/R9_eo.cfg" <<'EOF'
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
  FaultForkIgnore = FALSE
  FaultInvalidPersist = FALSE
  FaultNondetRecovery = FALSE
  FaultDoubleConsume = FALSE
  FaultPrefixReplay = TRUE
INVARIANTS
  EffectExactlyOnce
EOF

echo ""
echo ">> Running TLC on R9 / EffectExactlyOnce (expect: violated) ..."
OUT="$WORK/out.txt"
( cd "$WORK" && java -cp "$JAR" tlc2.TLC -deadlock -config R9_eo.cfg -workers 1 \
    ResumeContract.tla > "$OUT" 2>&1 ) || true

echo "----------------------------------------------------------------------"
grep -E "Invariant .* is violated|states generated|depth of the complete" "$OUT" || true
echo "----------------------------------------------------------------------"
echo ">> Counterexample trace (effects / consumedVal / pc per state):"
grep -E "^/\\\\ (effects|consumedVal|pc) " "$OUT" | sed 's/^/   /'
echo "----------------------------------------------------------------------"

# Assertions on the trace TLC produced.
if ! grep -q "Invariant EffectExactlyOnce is violated" "$OUT"; then
  echo "UNEXPECTED: EO was not violated. See $OUT"; exit 1
fi
NONNOVAL=$(grep -E "^/\\\\ consumedVal " "$OUT" | grep -vc "NoVal" || true)
echo ""
echo ">> States on the counterexample where the gate WAS consumed"
echo "   (consumedVal != NoVal): $NONNOVAL"
if [[ "$NONNOVAL" -eq 0 ]]; then
  cat <<'MSG'

VERDICT: CONFIRMED.
  The EO-violating trace TLC finds under FaultPrefixReplay never consumes the
  interrupt (consumedVal is NoVal in every state), never reaches the gated
  task, and duplicates a NON-gated task's effect via plain prefix replay. The
  clause that makes R9 distinct from R1 -- "serve IP's effect from the record"
  -- is never exercised on the witnessed trace. The EO<->CO separation the
  paper draws from R9 is valid, but calling R9 "the memoized-gate discipline of
  LangGraph's measured crash path" / "the in-model shadow" overstates the
  correspondence: the model witnesses generic prefix re-execution, not a
  memoized gate.
MSG
else
  echo "VERDICT: the gate WAS consumed on the trace; re-examine the model."
fi
