//! remit_verus_cv_rd.rs — CV and RD lemma STATEMENTS for REMIT.
//!
//! STATUS: UNDISCHARGED. The paper (Sec. 8.2) cites this file as the stated,
//! unproven obligations. Do NOT claim verification numbers for these lemmas
//! until `verus proof/remit_verus_cv_rd.rs` reports "N verified, 0 errors"
//! on your toolchain host, and record the run in VERIFICATION.md.
//!
//! Run:  verus proof/remit_verus_cv_rd.rs
//! Toolchain used for the discharged core: Verus 0.2026.05.03.8b81855.

use vstd::prelude::*;

verus! {

// ---------------------------------------------------------------------------
// Shared model fragments (duplicated from remit_verus.rs to keep this file
// independently checkable; unify into one crate module when discharging).
// ---------------------------------------------------------------------------

/// A persisted record is either schema-valid or not (abstract predicate).
pub uninterp spec fn valid(rec: int) -> bool;

/// The durable log as a sequence of accepted records.
pub struct DurableLog {
    pub records: Seq<int>,
}

/// REMIT's validity gate: the *only* constructor of accepted records.
/// Gate semantics: a write is appended iff `valid(rec)`; otherwise the
/// sequencer returns a loud rejection and the log is unchanged.
pub open spec fn gate_step(log: DurableLog, rec: int) -> DurableLog {
    if valid(rec) {
        DurableLog { records: log.records.push(rec) }
    } else {
        log
    }
}

// ---------------------------------------------------------------------------
// CV: Checkpoint validity — every persisted record is schema-valid.
// ---------------------------------------------------------------------------

/// Invariant: all records in the log satisfy `valid`.
pub open spec fn cv_inv(log: DurableLog) -> bool {
    forall|i: int| 0 <= i < log.records.len() ==> valid(#[trigger] log.records[i])
}

/// CV-1 (UNDISCHARGED): the gate preserves the CV invariant on every step.
/// With CV-2 this yields: any log reachable from empty through the gate
/// contains only valid records — the machine-checked form of the live
/// behavior probe 123 demonstrates.
pub proof fn lemma_cv_gate_preserves(log: DurableLog, rec: int)
    requires
        cv_inv(log),
    ensures
        cv_inv(gate_step(log, rec)),
{
    // TODO(discharge): case split on valid(rec); push-lemma for Seq.
    assume(false); // placeholder — remove when proving
}

/// CV-2 (UNDISCHARGED): the empty log satisfies the invariant.
pub proof fn lemma_cv_init()
    ensures
        cv_inv(DurableLog { records: Seq::empty() }),
{
    assume(false); // placeholder — remove when proving
}

// ---------------------------------------------------------------------------
// RD: Recovery determinism — the recovery decision is a function of durable
// state alone. REMIT enforces RD by construction: recovery consults only the
// sequencer's totally ordered log. The lemma states functionality.
// ---------------------------------------------------------------------------

/// Abstract recovery decision from a durable log (frontier + branch plan).
pub uninterp spec fn recover(log: DurableLog) -> int;

/// RD-1 (UNDISCHARGED): recovery is a pure function of the durable log —
/// two recoveries over equal logs decide identically, independent of any
/// non-durable input (scheduler order, wall clock, executor interleaving).
/// This is the property probes 118/128 certify at the checkpointer API and
/// #8039's unbarriered pool would violate below it.
pub proof fn lemma_rd_functional(a: DurableLog, b: DurableLog)
    requires
        a.records =~= b.records,
    ensures
        recover(a) == recover(b),
{
    // TODO(discharge): follows from `recover` being spec-level (no ambient
    // state); make the statement non-trivial by refining `recover` to the
    // sequencer's concrete frontier scan, then prove scan determinism.
    assume(false); // placeholder — remove when proving
}

/// RD-2 (UNDISCHARGED): recovery commutes with legal durable-order choice.
/// Model the #8039 pair as two logs that are permutations within one
/// superstep window carrying the same write-set; recovery must agree.
pub uninterp spec fn same_superstep_writeset(a: DurableLog, b: DurableLog) -> bool;

pub proof fn lemma_rd_order_independent(a: DurableLog, b: DurableLog)
    requires
        same_superstep_writeset(a, b),
    ensures
        recover(a) == recover(b),
{
    assume(false); // placeholder — remove when proving
}

} // verus!

fn main() {}
