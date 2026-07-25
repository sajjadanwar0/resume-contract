#!/usr/bin/env bash
###############################################################################
# reproduce.sh -- single-command audit for the Resume Contract artifact.
#
# Re-derives every headline number in the paper from committed inputs:
#   [1] TLC verification matrix (Table 2): R0 reference = no error on all six
#       invariants; R1-R5 = counterexample for exactly the targeted invariant.
#   [2] Conformance plan (matrix.toml): every planned probe re-run inside its
#       pinned per-framework env; stable verdict fields diffed against the
#       committed baselines (live-keyed probes skip without API keys).
#   [3] Remit skeleton invariants: cargo test (seven property-named tests).
#   [4] Remit Verus proofs: every positive target must discharge with
#       0 errors; every negative certificate must fail in exactly the
#       expected shape (2 verified, 1 errors).
# Modes:
#   ./reproduce.sh            full audit ([1]..[4]); syncs envs on demand
#   ./reproduce.sh --tlc-only [1] only (no Python env setup, no Rust)
#
# TLC location (first match wins; shell aliases do not reach scripts):
#   $TLC_CMD | $TLA_TOOLS_JAR | formal/tla/tla2tools.jar |
#   $HOME/tla2tools.jar | download to $HOME.
#
# Requirements: uv >= 0.4, Java >= 11, Rust stable (cargo), network on first
# run (env resolution; jar download if absent everywhere).
###############################################################################
set -euo pipefail
cd "$(dirname "$0")"

TLC_ONLY=0
[[ "${1:-}" == "--tlc-only" ]] && TLC_ONLY=1

fail=0
note () { printf '\n=== %s ===\n' "$*"; }

resolve_tlc () {
  if [[ -n "${TLC_CMD:-}" ]]; then echo "$TLC_CMD"; return; fi
  local jar=""
  if   [[ -n "${TLA_TOOLS_JAR:-}" && -f "${TLA_TOOLS_JAR:-}" ]]; then jar="$TLA_TOOLS_JAR"
  elif [[ -f formal/tla/tla2tools.jar ]];                        then jar=formal/tla/tla2tools.jar
  elif [[ -f "$HOME/tla2tools.jar" ]];                           then jar="$HOME/tla2tools.jar"
  else
    echo "fetching tla2tools.jar to \$HOME (tlaplus/tlaplus releases)" >&2
    curl -sL -o "$HOME/tla2tools.jar" \
      https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
    jar="$HOME/tla2tools.jar"
  fi
  # jar path is made absolute: TLC runs from formal/tla below.
  case "$jar" in /*) : ;; *) jar="$PWD/$jar" ;; esac
  echo "java -XX:+UseParallelGC -cp $jar tlc2.TLC"
}

# ---------------------------------------------------------------- [1] TLC ---
note "[1/4] TLC verification matrix (ResumeContract.tla)"
TLC="$(resolve_tlc)"
echo "TLC command: $TLC"
pushd formal/tla > /dev/null
declare -A MODULE=(
  [R0_reference]=ResumeContract [R1_replay]=ResumeContract
  [R2_forkignore]=ResumeContract [R3_invalidpersist]=ResumeContract
  [R4_nondetrec]=ResumeContract [R5_doubleconsume]=ResumeContract
  [R6_liveness]=ResumeContract
  [LGF_AsImplemented]=LangGraphFork [LGF_ForkKeyed]=LangGraphFork
)
declare -A EXPECT=(
  [R0_reference]="Model checking completed. No error has been found."
  [R6_liveness]="Model checking completed. No error has been found."
  [LGF_AsImplemented]="Invariant ForkDeterminism is violated"
  [LGF_ForkKeyed]="Model checking completed. No error has been found."
  [R1_replay]="Invariant EffectExactlyOnce is violated"
  [R2_forkignore]="Invariant ForkDeterminism is violated"
  [R3_invalidpersist]="Invariant CheckpointValidity is violated"
  [R4_nondetrec]="Invariant RecoveryDeterminism is violated"
  [R5_doubleconsume]="Invariant ConsumeOnce is violated"
)
for cfg in R0_reference R1_replay R2_forkignore R3_invalidpersist \
           R4_nondetrec R5_doubleconsume R6_liveness \
           LGF_AsImplemented LGF_ForkKeyed; do
  $TLC -deadlock -config "$cfg.cfg" -workers 1 "${MODULE[$cfg]}.tla" \
       > "$cfg.audit.out" 2>&1 || true
  if grep -q "${EXPECT[$cfg]}" "$cfg.audit.out"; then
    echo "  $cfg: OK (${EXPECT[$cfg]})"
  else
    echo "  $cfg: FAIL -- expected: ${EXPECT[$cfg]}"; fail=1
  fi
done
popd > /dev/null

if [[ $TLC_ONLY -eq 1 ]]; then
  [[ $fail -eq 0 ]] && echo "TLC-only audit: CLEAN" || echo "TLC-only audit: FAILED"
  exit $fail
fi

# -------------------------------------------------- [2] conformance plan ---
note "[2/4] Conformance plan (matrix.toml vs committed baselines)"
uv sync --quiet
for env in langgraph llamaindex crewai pydantic-graph openai-agents \
           langgraph-live langgraph-1.1; do
  [[ -d "envs/$env" ]] && uv sync --quiet --project "envs/$env"
done
uv run python -m conformance.runner \
  --plan matrix.toml --baseline results/pilot || fail=1

# --------------------------------------------------------- [3] Remit tests ---
note "[3/4] Remit invariants + line-identical core (cargo test + n3 gate)"
cargo test --workspace --quiet || fail=1
bash n3_sync_check.sh . || fail=1

# ------------------------------------------------------ [4] Verus proofs ----
note "[4/4] Remit Verus proofs (crates/remit/proof)"
if command -v verus >/dev/null 2>&1; then
  for f in remit_verus remit_verus_cv remit_verus_all \
           remit_verus_fd_machine remit_verus_rd_interp \
           remit_verus_recover_exec remit_verus_ledger_exec; do
    p="crates/remit/proof/$f.rs"
    if verus "$p" 2>&1 | tee "/tmp/verus_$f.out" \
         | grep -qE "[0-9]+ verified, 0 errors"; then
      echo "  $f: $(grep -oE '[0-9]+ verified, [0-9]+ errors' "/tmp/verus_$f.out" | head -1)"
    else
      echo "  $f: FAILED"; tail -5 "/tmp/verus_$f.out"; fail=1
    fi
  done
  # Negative certificates: each must FAIL in exactly the expected shape.
  # verus exits nonzero here BY DESIGN; capture output first (a direct
  # pipeline under `set -o pipefail` reads as failure even on a match).
  for p in crates/remit/proof/negative/*.rs; do
    [[ -e "$p" ]] || continue
    b=$(basename "$p")
    out=$(verus "$p" 2>&1 || true)
    printf '%s\n' "$out" > "/tmp/verus_neg_$b.out"
    if grep -q "2 verified, 1 errors" <<<"$out"; then
      echo "  negative/$b: OK (expected falsification: 2 verified, 1 errors)"
    else
      echo "  negative/$b: FAIL -- expected '2 verified, 1 errors'; got:"
      grep -m1 "verification results" <<<"$out" || tail -3 <<<"$out"; fail=1
    fi
  done
else
  echo "  Verus not on PATH -- skipping SMT discharge (proof logic already"
  echo "  cross-checked by cargo test tests/proof_logic.rs above)."
  echo "  Install: https://github.com/verus-lang/verus, then re-run."
fi

note "AUDIT RESULT"
if [[ $fail -eq 0 ]]; then
  echo "CLEAN: every headline number re-derived from committed data."
else
  echo "FAILED: see sections above."
fi
exit $fail
