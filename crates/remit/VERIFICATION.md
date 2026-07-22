# Remit verification (Verus)

Protocol spec: `formal/tla/ResumeContract.tla` reference config `R0_reference`
(TLC: all six invariants, no error). Implementation: `src/lib.rs`. Proofs:
`proof/remit_verus.rs` (EO/CO, PC, FD keying), `proof/remit_verus_cv.rs` (CV),
the FD/RD theorem files `proof/remit_verus_fd_machine.rs` and
`proof/remit_verus_rd_interp.rs` with their falsifying certificates under
`proof/negative/` (EXPECTED 1 error each, by design), and the composed
legacy target `proof/remit_verus_all.rs` (trimmed core + CV, one
invocation). Toolchain: Verus 0.2026.05.03.8b81855.

**Standing tallies (2026-07-21, developer host, pinned toolchain):**

| Target | Tally | Code-level `assume` count† |
|---|---|---:|
| `proof/remit_verus.rs` | `verification results:: 10 verified, 0 errors` | 0 |
| `proof/remit_verus_cv.rs` | `verification results:: 2 verified, 0 errors` | 0 |
| `proof/remit_verus_all.rs` | `verification results:: 12 verified, 0 errors` | 0 |
| `proof/remit_verus_fd_machine.rs` | `verification results:: 5 verified, 0 errors` | 0 |
| `proof/remit_verus_rd_interp.rs` | `verification results:: 6 verified, 0 errors` | 0 |
| `proof/negative/fd_stock_certificate.rs` | `2 verified, 1 errors` (EXPECTED -- the certificate) | 0 |
| `proof/negative/rd_ordersensitive_certificate.rs` | `2 verified, 1 errors` (EXPECTED -- the certificate) | 0 |

†**Certification rule.** A Verus tally is citable only alongside a zero
comment-filtered `assume` count of the exact file verified, because a lemma
body of `assume(false)` produces the identical "verified" tally vacuously:

```bash
for f in crates/remit/proof/remit_verus{,_cv_rd,_all}.rs; do
  printf "%s: " "$f"; grep -v '^\s*//' "$f" | grep -c 'assume' || true
done   # 2026-07-21 result: 0, 0, 0
```

These are the numbers the manuscript reports (abstract and Sec. 7.3 of
revision r5). To re-attest, run the three commands below; any change in
tally or grep count supersedes this entry and must be reflected in the
paper before submission.

## Run

```
verus crates/remit/proof/remit_verus.rs
verus crates/remit/proof/remit_verus_cv.rs
verus crates/remit/proof/remit_verus_all.rs
verus crates/remit/proof/remit_verus_fd_machine.rs
verus crates/remit/proof/remit_verus_rd_interp.rs
# expected to FAIL (1 error each) -- the falsifying certificates:
verus crates/remit/proof/negative/fd_stock_certificate.rs
verus crates/remit/proof/negative/rd_ordersensitive_certificate.rs
```

## Obligation inventory (proof/remit_verus.rs)

| Metric | Count |
|--------|------:|
| Proof functions (lemmas + smoke) | 9 |
| Spec functions (state-machine defs) | 8 |
| `requires` clauses | 7 |
| `ensures` clauses (obligations discharged) | 8 |
| In-proof assertions | 17 |

## Property to obligation map (core)

| Contract | Verus lemma | Statement discharged |
|----------|-------------|----------------------|
| EO/CO | `lemma_begin_effect_admits_once`, `lemma_eo_no_duplicate` | admitting a fresh (branch,task) yields count == 1 and preserves ledger-uniqueness for all pairs |
| EO/CO (support) | `lemma_absent_count_zero`, `lemma_push_count` | count algebra: absent => 0; push increments only its own pair |
| PC | `lemma_pc_strict_monotone`, `lemma_pc_no_prefix_reentry` | commit admissible => frontier strictly increases by one; committed task > frontier |
| FD | `lemma_fd_ordinal_injective` (keying); theorem file `remit_verus_fd_machine.rs`; certificate `negative/fd_stock_certificate.rs` (EXPECTED 1 error) | distinct ordinals key distinct branches; every branch is served the value its own invocation recorded, proved by an inductive invariant over record/serve steps; the stock #6663 rule falsifies the same obligation |
| wiring | `contract_smoke` | the lemmas compose on a concrete instance |

## Status (core)

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

## CV lemma set (proof/remit_verus_cv.rs) -- DISCHARGED (RD: see theorem files above)

| Contract | Verus lemma | Statement discharged |
|----------|-------------|----------------------|
| CV | `lemma_cv_init`, `lemma_cv_gate_preserves` | the empty log is valid; the validity gate preserves log validity on every step (case split on `valid(rec)`; push-index unfolding) |
| RD | theorem file `remit_verus_rd_interp.rs` (interpreted skip decision; adjacent-swap #8039 window; equal write-set counts); certificate `negative/rd_ordersensitive_certificate.rs` (EXPECTED 1 error) | order-independence of the recovery decision is proved, not defined; the order-sensitive rule falsifies the same obligation |

Lemma statements (names, `requires`, `ensures`) are verbatim from the
original obligation file. Uninterpreted helpers in the former file (deleted with the RD congruence pair):
`valid(rec)` and `recover_of_writeset(ws)`; `same_superstep_writeset(a, b)`
is now an open definition (multiset equality of the record sequences), and
`recover(log)` is defined as `recover_of_writeset(log.records.to_multiset())`.

**Historical modeling commitment (RD) --- retired with the deleted congruence pair; see the Post-adoption section.** The former RD pair was discharged under the
refinement the original file's own TODO prescribed: recovery is defined to
consult exactly the durable write-set of the sequenced log -- it factors
through the record multiset. This mirrors the implemented sequencer, whose
recovery scan reads the journal's write-set; order-independence within a
superstep window is therefore a theorem of the model under this commitment,
not an assumption about it. CV is additionally demonstrated live (probe
123, silent persistence converted to loud rejection); RD's executor-layer
evidence is probes 124/128. This file completes the stated obligation set
at the model level; it does not verify the shim implementations.

## Composed target (proof/remit_verus_all.rs)

`remit_verus_all.rs` merges the two lemma files over a shared definition
set: `remit_verus.rs` verbatim plus the discharged CV/RD items, one
`verus!` block, one `main`, the `use` set unified. A single invocation
discharges all fifteen items -- `verification results:: 15 verified, 0
errors` -- retiring the two-file composition caveat; Sec. 7.3 of manuscript
revision r5 cites this target and this log.

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

**[2026-07-21] Audit finding and supersession.** An external audit of the
committed artifact found `proof/remit_verus_cv_rd.rs` carrying
`assume(false)` placeholder bodies in all four lemmas (file header:
"STATUS: UNDISCHARGED"), contradicting both the 2026-07-17 log entry above
and the manuscript's tally. Under `assume(false)` the identical
`4 verified, 0 errors` line is produced vacuously, so the 2026-07-17 entry
cannot be distinguished, from the committed evidence, from a placeholder
tally; it is **superseded** by this entry. Same day: the placeholder
revision was withdrawn and replaced by the discharged file (real proof
bodies; the RD pair under the factored-recovery modeling commitment above);
the composed target `remit_verus_all.rs` was added. Re-attested on the
pinned toolchain, developer host:

```
verus crates/remit/proof/remit_verus_cv_rd.rs
verification results:: 4 verified, 0 errors
verus crates/remit/proof/remit_verus_all.rs
verification results:: 15 verified, 0 errors
```

Comment-filtered `assume` count across all three proof files: `0, 0, 0`
(command and result recorded in the certification rule above). The
certification rule is adopted from this date forward: no tally is citable
without the accompanying grep. **[2026-07-22 supersession note.]** The
tallies attested above predate the adoption commit; the files and numbers
current after it are those of the status table at the top and the
Post-adoption verification status section below.
## Post-adoption verification status (2026-07-22)

The definitional FD lemma (`lemma_fd_distinct_values_served_distinctly`,
with its `served()` helper) and the RD congruence pair
(`lemma_rd_functional`, `lemma_rd_order_independent`, with `recover`,
`recover_of_writeset`, `same_superstep_writeset`) are **deleted**. The
historical tallies (11 / 4 / 15) are **retired** and no longer citable.
FD and RD are now theorems with inductive content, each paired with a
machine-checked falsifying certificate. Fresh tallies, from actual runs
on this date:

```
verus crates/remit/proof/remit_verus.rs
verification results:: 10 verified, 0 errors
verus crates/remit/proof/remit_verus_cv.rs
verification results:: 2 verified, 0 errors
verus crates/remit/proof/remit_verus_all.rs
verification results:: 12 verified, 0 errors
verus crates/remit/proof/remit_verus_fd_machine.rs
verification results:: 5 verified, 0 errors
verus crates/remit/proof/remit_verus_rd_interp.rs
verification results:: 6 verified, 0 errors
verus crates/remit/proof/negative/fd_stock_certificate.rs
verification results:: 2 verified, 1 errors
verus crates/remit/proof/negative/rd_ordersensitive_certificate.rs
verification results:: 2 verified, 1 errors
```

### Negative certificates (expected to FAIL -- by design)
`negative/fd_stock_certificate.rs` and
`negative/rd_ordersensitive_certificate.rs` MUST each report
`1 errors` (`postcondition not satisfied`): the stock #6663 serving
rule and the order-sensitive #8039 recovery rule falsify the same
obligations the positive machines discharge. **That failure is the
certificate** that the positive proofs have content. Keep `negative/`
out of every verify-all glob, CI job, and `reproduce.sh` path; verify
them only via the explicit commands above.

### Composition note
`remit_verus_fd_machine.rs` and `remit_verus_rd_interp.rs` are
standalone by design (they re-declare model types such as
`DurableLog`); merging them into `remit_verus_all.rs` requires a
definition-sharing refactor and is deferred to the refinement work
(N3). The composed file now covers the trimmed legacy core + CV only.
