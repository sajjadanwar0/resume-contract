# Remit verification (Verus)

Protocol spec: `formal/tla/ResumeContract.tla` reference config `R0_reference`
(TLC: all six invariants, no error). Implementation: `src/lib.rs`. Proof:
`proof/remit_verus.rs`, targeting Verus 0.2026.05.03.8b81855.

## Run

```
verus crates/remit/proof/remit_verus.rs
```

Expected: `verification results:: N verified, 0 errors`.

## Obligation inventory (from proof/remit_verus.rs)

| Metric | Count |
|--------|------:|
| Proof functions (lemmas + smoke) | 9 |
| Spec functions (state-machine defs) | 8 |
| `requires` clauses | 7 |
| `ensures` clauses (obligations discharged) | 8 |
| In-proof assertions | 17 |

## Property to obligation map

| Contract | Verus lemma | Statement discharged |
|----------|-------------|----------------------|
| EO/CO | `lemma_begin_effect_admits_once`, `lemma_eo_no_duplicate` | admitting a fresh (branch,task) yields count == 1 and preserves ledger-uniqueness for all pairs |
| EO/CO (support) | `lemma_absent_count_zero`, `lemma_push_count` | count algebra: absent => 0; push increments only its own pair |
| PC | `lemma_pc_strict_monotone`, `lemma_pc_no_prefix_reentry` | commit admissible => frontier strictly increases by one; committed task > frontier |
| FD | `lemma_fd_ordinal_injective`, `lemma_fd_distinct_values_served_distinctly` | distinct ordinals key distinct branches; distinct supplied values served distinctly |
| wiring | `contract_smoke` | the lemmas compose on a concrete instance |

## Status

First discharge on the developer host (Verus 0.2026.05.03.8b81855):
`verification results:: 10 verified, 1 errors`. The single failure was the
postcondition of `lemma_push_count` -- a definitional-unfolding (fuel)
obligation on the recursive `count_effect`, not a logic error (the lemma's
computational content passes the executable cross-check in
`tests/proof_logic.rs`). The repair adds `reveal_with_fuel(count_effect, 2)`
and explicit one-step unfolding assertions on both sides of the equation;
auto-trigger sites are annotated `#![auto]`. Re-discharge on the developer
host, confirmed: `verification results:: 11 verified, 0 errors`
(Verus 0.2026.05.03.8b81855). All stated obligations for EO/CO, PC, and FD
are machine-checked; the proof is unbounded and structural.

## CV and RD lemma set (proof/remit_verus_cv_rd.rs)

Run:

```
verus crates/remit/proof/remit_verus_cv_rd.rs
```

Expected: `verification results:: 4 verified, 0 errors`.

| Contract | Verus lemma | Statement discharged |
|----------|-------------|----------------------|
| CV | `lemma_cv_init`, `lemma_cv_gate_preserves` | the empty log is valid; the guarded commit preserves log validity for every record it admits |
| RD | `lemma_rd_functional`, `lemma_rd_order_independent` | recovery is a pure function of the durable log; recovery is invariant under reordering of same-superstep write sets |

Three spec helpers are declared `uninterp`: `valid(rec)`, `recover(log)`,
and `same_superstep_writeset(a, b)`. The first discharge already passed
but emitted three deprecation warnings (bodyless `spec` functions without
the marker become hard errors in future Verus); the modernization and
clean re-discharge are logged below. CV is additionally
demonstrated live (probe 123, silent persistence converted to loud
rejection); RD's executor-layer evidence is probes 124/128. This file
completes the stated obligation set at the model level; it does not verify
the shim implementations.

## Verification log

**[2026-07-17]** `proof/remit_verus_cv_rd.rs` first discharged on the
developer host: `4 verified, 0 errors`, with three deprecation warnings
(bodyless `spec` functions not yet marked `uninterp`: `valid`, `recover`,
`same_superstep_writeset`). Markers applied via the recorded `sed`
edits; clean re-discharge the same day: `4 verified, 0 errors`, no
warnings --- and the core file re-attested fresh in the same session:
`proof/remit_verus.rs` --- `11 verified, 0 errors`.

**[2026-07-18]** Toolchain attestation on the developer host, recorded
verbatim from `verus --version`:

```
Verus
  Version: 0.2026.05.03.8b81855
  Profile: release
  Platform: linux_x86_64
  Toolchain: 1.95.0-x86_64-unknown-linux-gnu
```

Standing tallies on this toolchain: `proof/remit_verus.rs` --- 11
verified, 0 errors; `proof/remit_verus_cv_rd.rs` --- 4 verified, 0
errors. These are the numbers the manuscript reports (abstract and
Sec. 8.2). To re-attest, run both commands above; any change in tally
supersedes this entry and must be reflected in the paper before
submission.