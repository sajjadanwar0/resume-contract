#!/usr/bin/env bash
# exp7_tlc_fd_nonvacuous.sh
# =========================
# Reviewer experiment 7 -- "Is the model's ForkDeterminism invariant checking
# anything, or is it an identity check?"
#
# In ResumeContract.tla the branch semantics f of Property 3 is hard-wired to
# the IDENTITY: Consume(v) appends v to forkOuts, ForkResume(v) appends v, and
#     ForkDeterminism == \A k : forkOuts[k] = forkVals[k]
# is therefore v = v -- true by construction of the actions, falsifiable only
# by the one hard-coded substitution (FaultForkIgnore serving forkOuts[1]).
#
# FDStrengthened.tla (shipped alongside this script) is the same module with
# ONE change: the decision function is explicit and NON-identity,
#     f(v) == <<v>>          (tuple wrap; injective)
# outcomes are f(v), and FD becomes  forkOuts[k] = f(forkVals[k]).
# The module also retains the paper's original invariant under the name
# FDWrongIdentity so it can be checked against the strengthened semantics.
#
# Three TLC runs:
#   A  reference semantics,  strengthened FD      -> expected: HOLDS, 87/59
#   B  FaultForkIgnore,      strengthened FD      -> expected: VIOLATED (CE)
#   C  reference semantics,  ORIGINAL identity-FD -> expected: VIOLATED
#
# Run C is the point: the paper's own FD invariant is falsified by the
# CONFORMANT model the moment f is not the identity. It was never checking
# determinism-of-routing; it was checking v = v.
#
# Usage:
#   REPO=/path/to/repo TLA_TOOLS_JAR=$HOME/tla2tools.jar bash exp7_tlc_fd_nonvacuous.sh
# Defaults: TLA_TOOLS_JAR=$HOME/tla2tools.jar (matches `alias tlc=...`);
#           FDStrengthened.tla is looked for next to this script;
#           REPO is only needed to source the original .cfg constants.

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
JAR="${TLA_TOOLS_JAR:-$HOME/tla2tools.jar}"
REPO="${REPO:-$HERE}"
MOD="$HERE/FDStrengthened.tla"

[ -f "$JAR" ] || { echo "ERROR: tla2tools.jar not found at $JAR (set TLA_TOOLS_JAR)"; exit 2; }
[ -f "$MOD" ] || { echo "ERROR: FDStrengthened.tla not found next to this script"; exit 2; }

WORK="$(mktemp -d)"
cp "$MOD" "$WORK/"
cd "$WORK"

mkcfg () {  # $1 = FaultForkIgnore TRUE/FALSE   $2 = invariant   $3 = out
cat > "$3" <<EOF
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
  FaultForkIgnore = $1
  FaultInvalidPersist = FALSE
  FaultNondetRecovery = FALSE
  FaultDoubleConsume = FALSE
  FaultPrefixReplay = FALSE
INVARIANTS
  $2
EOF
}

mkcfg FALSE ForkDeterminism  A.cfg
mkcfg TRUE  ForkDeterminism  B.cfg
mkcfg FALSE FDWrongIdentity  C.cfg

run () {
  java -cp "$JAR" tlc2.TLC -deadlock -config "$1" FDStrengthened 2>&1 \
    | egrep "No error|is violated|states generated|^Error" | head -4
  return 0
}

echo "=============================================================================="
echo " A. reference semantics  +  STRENGTHENED FD  (forkOuts[k] = f(forkVals[k]))"
echo "=============================================================================="
OUTA="$(run A.cfg)"; echo "$OUTA"
echo
echo "=============================================================================="
echo " B. FaultForkIgnore      +  STRENGTHENED FD"
echo "=============================================================================="
OUTB="$(run B.cfg)"; echo "$OUTB"
echo
echo "=============================================================================="
echo " C. reference semantics  +  the paper's ORIGINAL FD (identity check)"
echo "=============================================================================="
OUTC="$(run C.cfg)"; echo "$OUTC"
echo

ok_a=$(echo "$OUTA" | grep -c "No error");      
ok_b=$(echo "$OUTB" | grep -c "ForkDeterminism is violated");
ok_c=$(echo "$OUTC" | grep -c "FDWrongIdentity is violated");

echo "=============================================================================="
echo " VERDICT"
echo "=============================================================================="
if [ "$ok_a" -ge 1 ] && [ "$ok_b" -ge 1 ] && [ "$ok_c" -ge 1 ]; then
  cat <<'TXT'
  CONFIRMED (all three as predicted):
   A. Strengthened FD holds over the full reference space (same 87/59 as R0):
      making f explicit and non-identity costs NOTHING.
   B. The ForkIgnore fault still yields a counterexample: the strengthening
      keeps the separation the paper needs.
   C. The paper's original invariant  forkOuts[k] = forkVals[k]  is VIOLATED
      by the CONFORMANT semantics once outcomes are f(v) rather than v.

  READING: the original ForkDeterminism was an identity check, true by
  construction of the actions, not a determinism-of-routing property. The fix
  is one line in each of three places (this module is the patch): state FD as
  forkOuts[k] = f(forkVals[k]) with f explicit -- which is what Property 3 in
  the paper's own prose already says. Adopt FDStrengthened.tla's change into
  ResumeContract.tla and re-run the 39-cell matrix; every verdict should be
  unchanged, and the invariant finally says what the paper claims it says.
TXT
else
  echo "  UNEXPECTED OUTPUT -- inspect the three runs above (A=$ok_a B=$ok_b C=$ok_c)."
  echo "  Expected: A 'No error', B 'ForkDeterminism is violated', C 'FDWrongIdentity is violated'."
fi
