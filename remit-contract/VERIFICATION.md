# VERIFICATION.md — what is proved, what is mirrored, what is tested

This package sits below a machine-checked model and above a decision-free
Python veneer. This file states the correspondence precisely, so the claim
can be audited rather than trusted.

## The chain

| Layer | Artifact | Checker | Status |
|---|---|---|---|
| Protocol spec | `formal/tla/ResumeContract.tla` (paper artifact) | TLC | R0 reference run: all six invariants, 0 errors; R8 scaled run 7.4 M distinct states (14.7 M generated), depth 24 |
| Abstract model | `crates/remit/proof/remit_verus.rs` (paper artifact) | Verus 0.2026.05.03.8b81855 | 10 verified, 0 errors (post-2026-07-22 lemma audit; the historical 11-item tally is retired in the paper artifact's ledger) |
| Companion lemma files | `remit_verus_cv.rs`, `remit_verus_all.rs`, `remit_verus_fd_machine.rs`, `remit_verus_rd_interp.rs` (paper artifact) | Verus | 2 + 12 + 5 + 6 verified, 0 errors |
| Executable decision cores | `remit_verus_recover_exec.rs`, `remit_verus_ledger_exec.rs` (paper artifact) | Verus (exec mode) | 7 + 11 verified, 0 errors; the recover body is line-identical to `remit-core`'s (byte-level CI sync gate) |
| Production core | `crates/remit-core` (this repo) | rustc + test suites below | mirrors the model item-for-item |
| PyO3 surface | `crates/remit-py` | rustc | type/exception translation only |
| Framework veneer | `python/remit/langgraph_shim.py` | pytest | applies core verdicts (fork view, validity, sequencing, consumption); no contract branch of its own |

Negative certificates (paper artifact, `proof/negative/`) each fail in the
expected shape — `2 verified, 1 errors` — witnessing that the lemma
statements are falsifiable, not vacuous.

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
Python veneer — the cross-process gate's Python glue included. That is the
residual gap, stated as such in the paper's threats-to-validity section.

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
| — (CO across processes: attempt rule, probe-165) | a read of a parked consumable under the enabled gate, without fork or read intent, must attempt the shared-store claim; every other read passes | free fn `consume_view` → `AttemptClaim`/`Pass` | `co_consume_view_matches_probe_165`; Python G1–G6, G9; probe 159 arms E/F |
| — (CO across processes: outcome rule) | the compare-and-swap winner is served; the loser is refused loudly before any node executes | free fn `consume_claim_verdict` → `Serve`/`Conflict` (surfaced as `RemitConsumeConflict`) | `co_claim_verdict_is_the_cas_outcome`; Python G1/G1b/G7; probe 159 arms E/F: `{1:10}` on both durable backends, one typed refusal per rep |
| — (sequencer substrate) | durable submissions totally ordered per plane; checkpoint-before-declared-writes refused | `Plane::sequence_op`, `Plane::declare_writes` (`RemitError::OrderViolation`) | `sequencer_total_order_is_gap_free_under_contention` (16 × 200 ops); `rd_sequencer_refuses_put_before_declared_writes` |
| — (FI, Definition 2) | fork-intent view rule (probe 134) | free fn `fork_view` | `fi_fork_view_matches_probe_134`; Python `test_fi_fork_view_matches_probe_134_cells`; LangGraph integration T1/T1b/T2/T3 |

## Cross-process consumption gate (v0.1.2, opt-in)

`wrap(..., cross_process_gate=True)` promotes the paper's probe-165
read-path repair into the shipped shim. The claim key is
`(thread_id, checkpoint_id of the loaded parked checkpoint)`, taken with
one `INSERT` under a uniqueness constraint in a `remit_claims` table
created lazily **in the saver's own database** — the shared store both
racers already use, which is what makes the insert a cross-process
compare-and-swap (SQLite primary key; Postgres `ON CONFLICT DO NOTHING`).
Both decisions are the core's: `consume_view` decides whether a read
attempts the claim; `consume_claim_verdict` maps the CAS outcome to
serve-or-refuse, surfaced as a typed `RemitConsumeConflict` raised inside
`get_tuple`, before any node executes.

Measured (paper artifact, probe 159, pins `remit-contract==0.1.2`): the
gated race arms fire `{1:10}` on both durable backends with exactly one
`RemitConsumeConflict` per rep and the loser never served the other
value; the **ungated** shim arm is retained and still measures `{2:10}`,
the permanent differential.

Scope and hardening, stated plainly:

* **Opt-in.** The default wrap is bit-for-bit the pre-gate behavior.
* **Read intent.** A bare inspection of a parked thread takes the claim —
  confirmed live: `get_state` on a parked thread consumed it in this
  package's own regression suite — so inspection reads pass
  `{"configurable": {..., "remit_inspect": True}}` (G5, and the
  fork-address fetch in G4).
* **Fork intent** bypasses the gate; fork-addressed deliveries claim a
  fresh branch key through the FD machinery instead (G4).
* **Backends.** Synchronous savers exposing `.conn`; Postgres connections
  must be autocommit (both refusals are loud `RuntimeError`s, G8 and the
  PG transactional-connection test).
* **Fresh-database DDL race.** Two processes' first claims can race the
  `CREATE TABLE IF NOT EXISTS` itself; Postgres refuses one creator at
  the catalog (SQLSTATE `42P07`/`23505`) before the IF NOT EXISTS check
  settles — caught live by probe 159's PG rep 0 on 0.1.1. The shim
  absorbs exactly those two codes as "the table exists" and proceeds to
  the claim; any other SQLSTATE propagates (G9).
* **Secondary latch.** The `__resume__` journal write re-takes the same
  key — idempotent for the winner, a late stop for read-less paths —
  retained exactly as probe 165 retains its falsified v1: secondary,
  never primary.

## Decision-freedom of the Python surface

`python/remit/langgraph_shim.py` contains exactly four interposition
sites, each a mechanical application of core verdicts:

1. `get_tuple`/`aget_tuple`, fork view: `_core.fork_view(explicit, flag,
   has_recorded)` → `"strip"` removes recorded `__resume__` writes from
   the returned tuple; `"keep"` returns it unchanged.
2. `get_tuple`/`aget_tuple`, consumption gate (opt-in):
   `_core.consume_view(has_pending_interrupt, gate_enabled, fork_intent,
   inspect_intent)` → `"attempt"` executes the shared-store CAS and
   reports the outcome to `_core.consume_claim_check`, which serves the
   winner and raises `RemitConsumeConflict` for the loser; `"pass"` does
   nothing.
3. `put`/`aput`: the user validator's answer (raise/no-raise) is reported
   to `Core.validity_gate`, which raises `RemitValidityError` on
   "invalid" before delegation; then `Core.sequence_op("put", …)`
   journals.
4. `put_writes`/`aput_writes`: the secondary consumption latch routes the
   same two consumption verdicts for `__resume__` submissions, then
   `Core.sequence_op("put_writes", …)` journals, then delegates.

Audit procedure: `grep -n "if" python/remit/langgraph_shim.py` — every
conditional either routes a core verdict, extracts a config field, or
guards an optional dependency (including the two absorbed DDL-race
SQLSTATEs, whose only effect is to accept the table-exists
postcondition); none encodes a contract rule.

## Reproducing the evidence

```bash
./BUILD_AND_TEST.sh                                       # the one-shot gate
# or piecewise:
cargo test -p remit-core                                  # 19 tests
REMIT_MODEL_CASES=20000 cargo test -p remit-core --release
REMIT_STRESS_THREADS=64 REMIT_STRESS_OPS=500 \
  cargo test -p remit-core --release --test concurrency
maturin build --release && pip install target/wheels/*.whl
pytest tests/ -v                                          # bindings + LangGraph + gate
REMIT_TEST_PG_DSN="postgresql://..." pytest tests/ -v     # + the two live-Postgres cells
```

Verus re-attestation of the abstract model lives in the paper artifact
(`crates/remit/proof/`, toolchain pin in its `VERIFICATION.md`); this
package intentionally does not duplicate the proof files, so there is one
source of truth for what was verified.
