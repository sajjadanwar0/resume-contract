# VERIFICATION.md — what is proved, what is mirrored, what is tested

This package sits below a machine-checked model and above a decision-free
Python veneer. This file states the correspondence precisely, so the claim
can be audited rather than trusted.

## The chain

| Layer | Artifact | Checker | Status |
|---|---|---|---|
| Protocol spec | `formal/tla/ResumeContract.tla` (paper artifact) | TLC | R0 reference run: all six invariants, 0 errors; R8 scaled run 14.7 M states, depth 24 |
| Abstract model | `crates/remit/proof/remit_verus.rs` (paper artifact) | Verus 0.2026.05.03.8b81855 | 11 verified, 0 errors |
| CV/RD lemmas | `crates/remit/proof/remit_verus_cv.rs` (paper artifact) | Verus | 2 verified, 0 errors |
| Production core | `crates/remit-core` (this repo) | rustc + test suites below | mirrors the model item-for-item |
| PyO3 surface | `crates/remit-py` | rustc | type/exception translation only |
| Framework veneer | `python/remit/langgraph_shim.py` | pytest | applies core verdicts; no contract branch of its own |

**Claimed:** (i) an item-for-item structural mirror between the verified
model and `remit-core`, tabulated below; (ii) executable conformance of
`remit-core` to the model's transition relation under a seeded randomized
harness that re-checks all six invariants after every action
(`tests/model_conformance.rs`; 20 000 sequences × ≤ 48 actions in the
heavy configuration); (iii) invariant preservation under concurrent load
(`tests/concurrency.rs`); (iv) that every contract decision reachable from
Python is computed in Rust.

**Not claimed:** a mechanized refinement proof between the Verus model and
the compiled `remit-core`, or verification of the PyO3 boundary and the
Python veneer. That is the residual gap, stated as such in the paper's
threats-to-validity section.

## Lemma ↔ function correspondence

| Verus item (verified, 0 errors) | Model obligation | `remit-core` realization | Exercised by |
|---|---|---|---|
| `lemma_begin_effect_admits_once` | first admission appends exactly one ledger record | `Plane::begin_effect` success path | `eo_duplicate_effect_rejected`; harness EO check |
| `lemma_eo_no_duplicate` | second admission for `(branch, task)` is refused, ledger unchanged | `Plane::begin_effect` refusal path (`RemitError::DuplicateEffect`) | same; `eo_admission_is_exactly_once_under_contention` (32 threads) |
| `lemma_pc_strict_monotone` | frontier advances by exactly one, never decreases | `Plane::commit_checkpoint` frontier guard | `pc_prefix_regression_rejected`; harness PC check |
| `lemma_pc_no_prefix_reentry` | commit at/below frontier refused | `RemitError::PrefixViolation` path | same |
| `lemma_fd_ordinal_injective` | `(checkpoint, ordinal)` branch keys are injective | `Plane::fork` ordinal assignment | `fd_concurrent_forks_get_distinct_branches_and_correct_outcomes` (contiguous, gap-free ordinals under 32-thread contention) |
| `lemma_fd_distinct_values_served_distinctly` | branch outcome source = its own supplied value | `Plane::fork` + `Plane::outcome`; `Plane::resolve_resume` fork arm | `fd_second_resume_value_yields_distinct_branch_and_outcome`; harness FD check (`forkOuts = forkVals`) |
| `lemma_cv_init` | empty log is valid | `Plane::default` | harness CV bookkeeping check |
| `lemma_cv_gate_preserves` | validation precedes append; rejected state persists nothing, loudly | `Plane::commit_checkpoint` gate ordering; `Plane::validity_gate` | `cv_invalid_state_rejected_and_nothing_persisted`; harness `InvalidPersistAttempt` action |
| `lemma_rd_functional` | recovery is a function of the durable log | free fn `recover` (no `&mut`, no ambient state) | `rd_recovery_is_a_pure_order_independent_function` |
| `lemma_rd_order_independent` | decision invariant under log permutation | `recover_is_order_independent` | same; harness RD check on reversed logs |
| — (module invariant CO) | discriminator-free delivery to consumed interrupt / completed run is effect-inert | `Plane::resolve_resume` ordinary arm → `Inert`; `Plane::complete` | `co_stray_resume_on_completed_run_is_inert`; `co_racing_stray_resumes_are_inert`; harness `ExtraResume` action |
| — (sequencer substrate) | durable submissions totally ordered per plane; checkpoint-before-declared-writes refused | `Plane::sequence_op`, `Plane::declare_writes` (`RemitError::OrderViolation`) | `sequencer_total_order_is_gap_free_under_contention` (16 × 200 ops); `rd_sequencer_refuses_put_before_declared_writes` |
| — (FI, Definition 2) | fork-intent view rule (probe 134) | free fn `fork_view` | `fi_fork_view_matches_probe_134`; Python `test_fi_fork_view_matches_probe_134_cells`; LangGraph integration T1/T1b/T2/T3 |

## Decision-freedom of the Python surface

`python/remit/langgraph_shim.py` contains exactly three interposition
sites, each a mechanical application of a core verdict:

1. `get_tuple`/`aget_tuple`: `_core.fork_view(explicit, flag, has_recorded)`
   → `"strip"` removes recorded `__resume__` writes from the returned
   tuple; `"keep"` returns it unchanged.
2. `put`/`aput`: the user validator's answer (raise/no-raise) is reported to
   `Core.validity_gate`, which raises `RemitValidityError` on "invalid"
   before delegation; then `Core.sequence_op("put", …)` journals.
3. `put_writes`/`aput_writes`: `Core.sequence_op("put_writes", …)` journals,
   then delegates.

Audit procedure: `grep -n "if" python/remit/langgraph_shim.py` — every
conditional either routes a core verdict, extracts a config field, or
guards an optional dependency; none encodes a contract rule.

## Reproducing the evidence

```bash
cargo test -p remit-core                                  # 16 tests
REMIT_MODEL_CASES=20000 cargo test -p remit-core --release
REMIT_STRESS_THREADS=64 REMIT_STRESS_OPS=500 \
  cargo test -p remit-core --release --test concurrency
maturin build --release && pip install target/wheels/*.whl
pytest tests/ -v                                          # bindings + LangGraph
```

Verus re-attestation of the abstract model lives in the paper artifact
(`crates/remit/proof/`, toolchain pin in its `VERIFICATION.md`); this
package intentionally does not duplicate the proof files, so there is one
source of truth for what was verified.
