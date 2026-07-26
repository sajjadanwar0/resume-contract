#!/usr/bin/env bash
###############################################################################
# reproduce.sh -- single-command audit for the Resume Contract artifact.
#
# Re-derives every headline number in the paper from committed inputs:
#   [1] TLC verification matrix (Table 2 + Prop. 2 witnesses): R0 reference and
#       R6 liveness = no error; R1-R5 = counterexample for exactly the targeted
#       invariant; LGF_* = the fork model as implemented vs. keyed; SEP_* = the
#       three conjunction-independence witnesses (R10_Separations.tla).
#   [2] Committed matrix receipts, verified without re-running: the 39-cell
#       per-invariant fault matrix (161, reference and R8 bounds) and the
#       28-cell separations matrix (162). Seconds; the runs themselves are
#       minutes and hours respectively, so this step audits their JSON.
#   [3] Conformance plan (matrix.toml): every planned probe re-run inside its
#       pinned per-framework env; stable verdict fields diffed against the
#       committed baselines (live-keyed probes skip without API keys).
#   [4] Remit skeleton invariants: cargo test (seven property-named tests).
#   [5] Remit Verus proofs: every positive target must discharge with
#       0 errors; every negative certificate must fail in exactly the
#       expected shape (2 verified, 1 errors).
# Modes:
#   ./reproduce.sh            full audit ([1]..[5]); syncs envs on demand
#   ./reproduce.sh --tlc-only [1]+[2] only (no Python env setup, no Rust)
#   ./reproduce.sh --scaled   re-runs 161 (both bound sets) and 162 first,
#                             then [1]+[2] -- hours, not minutes
#
# WORKER COUNT IS LOAD-BEARING. TLC runs below are pinned to -workers 1. At
# reference bounds this costs nothing; at scale it is the difference between a
# reproducible receipt and an unstable one. Measured 2026-07-25: the R8 fork
# fault reports a 9-state trace under 16 workers and an 8-state trace under
# one, the longer trace carrying a CrashRecover step the shorter reaches the
# same violation without -- parallel breadth-first search returns non-minimal
# witnesses once levels stop draining before workers race ahead. Verdicts are
# worker-invariant; depths are not. Every depth this artifact reports, and
# every depth the paper cites, is a single-worker depth.
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
SCALED=0
case "${1:-}" in
  --tlc-only) TLC_ONLY=1 ;;
  --scaled)   TLC_ONLY=1; SCALED=1 ;;
esac

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

# ------------------------------------------------- [0] optional re-derive ---
if [[ $SCALED -eq 1 ]]; then
  note "[--scaled] re-running the matrices before auditing their receipts"
  bash 162_separations_matrix.sh . || fail=1
  bash 161_r8_independence_matrix.sh . --bounds reference || fail=1
  bash 161_r8_independence_matrix.sh . --bounds r8 || fail=1
fi

# ---------------------------------------------------------------- [1] TLC ---
note "[1/5] TLC verification matrix (ResumeContract, LangGraphFork, R10)"
TLC="$(resolve_tlc)"
echo "TLC command: $TLC   (-workers 1: see header)"
pushd formal/tla > /dev/null
declare -A MODULE=(
  [R0_reference]=ResumeContract [R1_replay]=ResumeContract
  [R2_forkignore]=ResumeContract [R3_invalidpersist]=ResumeContract
  [R4_nondetrec]=ResumeContract [R5_doubleconsume]=ResumeContract
  [R6_liveness]=ResumeContract
  [LGF_AsImplemented]=LangGraphFork [LGF_ForkKeyed]=LangGraphFork
  [SEP_reference]=R10_Separations [SEP_regate]=R10_Separations
  [SEP_rebuild]=R10_Separations [SEP_redeliver]=R10_Separations
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
  [SEP_reference]="Model checking completed. No error has been found."
  [SEP_regate]="Invariant RecoveryDeterminism is violated"
  [SEP_rebuild]="Invariant PrefixConsistency is violated"
  [SEP_redeliver]="Invariant EffectExactlyOnce is violated"
)
# The four SEP_* headline cells are the conjunction-independence witnesses
# (Proposition 2, clause (v)). Every SEP config checks ALL SEVEN invariants
# (the R0_reference convention, not R1-R5's single-invariant one), so a fault
# cell reporting its target property violated is also evidence that no other
# invariant breaks first on the way there. What these four cells do NOT
# establish is that the other five hold over the faulty model's entire
# reachable state space -- that needs one complete run per (switch,
# invariant) pair, which is 162_separations_matrix.sh, audited in [2].
for cfg in R0_reference R1_replay R2_forkignore R3_invalidpersist \
           R4_nondetrec R5_doubleconsume R6_liveness \
           LGF_AsImplemented LGF_ForkKeyed \
           SEP_reference SEP_regate SEP_rebuild SEP_redeliver; do
  $TLC -deadlock -config "$cfg.cfg" -workers 1 "${MODULE[$cfg]}.tla" \
       > "$cfg.audit.out" 2>&1 || true
  if grep -q "${EXPECT[$cfg]}" "$cfg.audit.out"; then
    echo "  $cfg: OK (${EXPECT[$cfg]})"
  else
    echo "  $cfg: FAIL -- expected: ${EXPECT[$cfg]}"; fail=1
  fi
done
popd > /dev/null

# ------------------------------------------------- [2] committed receipts ---
note "[2/5] Committed matrix receipts (161 independence, 162 separations)"
python3 - << 'PYAUDIT' || fail=1
import json, os, sys

# 161: per-invariant fault matrix. The SAME verdict pattern is required at the
# reference bounds and at R8 -- that identity is what makes the separations
# bound-relative rather than bound-dependent.
IX = {
 "replay":        {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"holds"},
 "forkignore":    {"EffectExactlyOnce":"holds","PrefixConsistency":"holds","ForkDeterminism":"violated","CheckpointValidity":"holds","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "invalidpersist":{"EffectExactlyOnce":"holds","PrefixConsistency":"holds","ForkDeterminism":"holds","CheckpointValidity":"violated","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "nondetrec":     {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"violated"},
 "doubleconsume": {"EffectExactlyOnce":"violated","PrefixConsistency":"holds","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"holds"},
 "prefixreplay":  {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "staterebuild":  {"TypeOK":"holds","EffectExactlyOnce":"holds","PrefixContinuation":"violated"},
}
INVS = ["TypeOK","EffectExactlyOnce","PrefixConsistency","ForkDeterminism",
        "CheckpointValidity","ConsumeOnce","RecoveryDeterminism"]
# 162: each witness isolates exactly one property; the reference cell holds on
# all six, which is the module's sanity check against a broken encoding.
SEP = {
 "reference": {i: "holds" for i in INVS},
 "regate":    dict({i: "holds" for i in INVS}, RecoveryDeterminism="violated"),
 "rebuild":   dict({i: "holds" for i in INVS}, PrefixConsistency="violated"),
 "redeliver": dict({i: "holds" for i in INVS}, EffectExactlyOnce="violated"),
}

def audit(path, expected, label):
    """Return 0 ok, 1 fail, 2 absent."""
    if not os.path.exists(path):
        print(f"  {label}: SKIP -- no receipt at {path}")
        return 2
    d = json.load(open(path))
    m, bad, n = d.get("matrix", {}), [], 0
    for grp, row in expected.items():
        for inv, want in row.items():
            got = m.get(grp, {}).get(inv, {}).get("verdict")
            n += 1
            if got != want:
                bad.append(f"{grp} x {inv}: receipt says {got}, expected {want}")
    if bad:
        print(f"  {label}: FAIL ({len(bad)}/{n} cells disagree)")
        for b in bad[:8]:
            print(f"      {b}")
        return 1
    bounds = d.get("meta", {}).get("bounds", "n/a")
    print(f"  {label}: OK ({n} cells, bounds={bounds})")
    return 0

rc, absent = 0, 0
for path, exp, lab in [
    ("formal/tla/independence_ref_rerun/r8_matrix.json", IX,  "161 @ reference bounds"),
    ("formal/tla/independence_r8/r8_matrix.json",        IX,  "161 @ R8 bounds"),
    ("formal/tla/separations/separations_matrix.json",   SEP, "162 separations"),
]:
    r = audit(path, exp, lab)
    rc = max(rc, 1 if r == 1 else 0)
    absent += (r == 2)

# p14 stable blocks: the campaign whose probes are too slow (or too
# kill-happy) for the conformance runner, gated here on their committed
# receipts. Values are the two-environment result reported in the paper.
P14 = {
  "results/parkkill/158_stable.json":
    {"all_park_kill_cells_conformant": True},
  "results/parkkill/158b_stable.json":
    {"all_park_kill_cells_conformant": True},
  "results/parkkill/158c_stable.json":
    {"between_node_snapshot_restorable_after_kill": True, "outcome": 11,
     "b_execs_total": 1},
  "results/parkkill/160_stable.json":
    {"points_with_duplicate_effect": [1, 2, 3, 4], "points_unrecoverable": [],
     "all_points_recoverable": True, "freeze_exact_at_every_point": True},
  "results/multiproc/159_stable.json":
    {"race_same_gate_fire_distribution": {"2": 10},
     "race_diff_gate_fire_distribution": {"2": 10},
     "contention_per_thread_exactly_once": True,
     "shim_race_inert_all_reps": False},
  "results/multiproc/159_stable_pg.json":
    {"race_same_gate_fire_distribution": {"2": 10},
     "race_diff_gate_fire_distribution": {"2": 10},
     "contention_per_thread_exactly_once": True,
     "shim_race_inert_all_reps": False},
}
for path, want in P14.items():
    lab = "p14 " + (os.path.basename(path)
                    .replace("_stable_pg.json", " (postgres)")
                    .replace("_stable.json", ""))
    if not os.path.exists(path):
        print(f"  {lab}: SKIP -- no receipt at {path}"); absent += 1; continue
    got = json.load(open(path)).get("stable", {})
    bad = [f"{k}: {got.get(k)!r} != {v!r}" for k, v in want.items()
           if got.get(k) != v]
    if bad:
        print(f"  {lab}: FAIL"); [print("      " + b) for b in bad]; rc = 1
    else:
        print(f"  {lab}: OK ({len(want)} fields)")

if absent:
    print("  (missing receipts are not a failure -- re-derive with:")
    print("     bash 161_r8_independence_matrix.sh . --bounds reference")
    print("     bash 161_r8_independence_matrix.sh . --bounds r8")
    print("     bash 162_separations_matrix.sh .")
    print("   p14 receipts come from probes/158*, 159, 160 -- see matrix.toml")
    print("   or ./reproduce.sh --scaled to run all three matrices, then audit.)")
sys.exit(rc)
PYAUDIT

if [[ $TLC_ONLY -eq 1 ]]; then
  [[ $fail -eq 0 ]] && echo "TLC-only audit: CLEAN" || echo "TLC-only audit: FAILED"
  exit $fail
fi

# -------------------------------------------------- [3] conformance plan ---
note "[3/5] Conformance plan (matrix.toml vs committed baselines)"
uv sync --quiet
# langgraph-durable carries the durable-backend and p14 probes (157-160) and
# declares remit-contract, whose shim arms degrade silently if it is absent.
for env in langgraph langgraph-durable llamaindex crewai pydantic-graph \
           openai-agents langgraph-live langgraph-1.1; do
  [[ -d "envs/$env" ]] && uv sync --quiet --project "envs/$env"
done
uv run python -m conformance.runner \
  --plan matrix.toml --baseline results/pilot || fail=1

# --------------------------------------------------------- [4] Remit tests ---
note "[4/5] Remit invariants + line-identical core (cargo test + n3 gate)"
# Toolchain absence is a SKIP, not a failure -- the same posture step [5]
# takes for Verus. A reviewer without a Rust toolchain should see which
# evidence was not exercised, not a red AUDIT RESULT on an intact artifact.
if command -v cargo >/dev/null 2>&1; then
  cargo test --workspace --quiet || fail=1
else
  echo "  Rust toolchain not on PATH -- skipping cargo test (7 property-named"
  echo "  tests + the differential suite). Install: https://rustup.rs, re-run."
fi
# The sync gate is pure text comparison and runs without a toolchain.
bash n3_sync_check.sh . || fail=1

# ------------------------------------------------------ [5] Verus proofs ----
note "[5/5] Remit Verus proofs (crates/remit/proof)"
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
  echo "(Sections reporting SKIP were not exercised -- missing API keys or"
  echo " toolchains -- and are listed above; nothing skipped is a verdict.)"
else
  echo "FAILED: see sections above."
fi
exit $fail
