//! remit_verus_cv.rs — CV lemmas for REMIT. DISCHARGED.
//! (Renamed from remit_verus_cv_rd.rs after the RD pair's deletion.
//! The RD congruence pair formerly in this file is DELETED: recover() was
//! defined over the write-set multiset, so "order-independence" carried no
//! proof content -- both lemmas verified with empty bodies. RD now lives as
//! theorems with inductive content in remit_verus_rd_interp.rs, with the
//! order-sensitive falsifying certificate in
//! negative/rd_ordersensitive_certificate.rs.)
//!
//! STATUS: DISCHARGED in this file (no `assume` anywhere; grep to confirm).
//! Verify:  verus crates/remit/proof/remit_verus_cv.rs
//! Expect:  "verification results:: N verified, 0 errors" -- record the
//! FRESH tally + date in VERIFICATION.md (the historical "4 verified" is
//! retired with the deleted RD pair; trust the run, not this comment).
//!
//! Toolchain: Verus 0.2026.05.03.8b81855 (the paper's pin).

use vstd::prelude::*;
use vstd::seq_lib::*;

verus! {

// ---------------------------------------------------------------------------
// Shared model fragments (duplicated from remit_verus.rs so this file stays
// independently checkable; remit_verus_all.rs is the unified composed target).
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

/// CV-1: the gate preserves the CV invariant on every step. With CV-2 this
/// yields: any log reachable from empty through the gate contains only
/// valid records — the machine-checked form of the live behavior probe 123
/// demonstrates (loud rejection, nothing invalid persisted).
pub proof fn lemma_cv_gate_preserves(log: DurableLog, rec: int)
    requires
        cv_inv(log),
    ensures
        cv_inv(gate_step(log, rec)),
{
    if valid(rec) {
        let stepped = gate_step(log, rec);
        assert(stepped.records == log.records.push(rec));
        assert forall|i: int| 0 <= i < stepped.records.len()
            implies valid(#[trigger] stepped.records[i]) by {
            if i < log.records.len() {
                assert(stepped.records[i] == log.records[i]);
            } else {
                assert(i == log.records.len() as int);
                assert(stepped.records[i] == rec);
            }
        }
    }
    // !valid(rec): gate_step(log, rec) == log, and cv_inv(log) is the requires.
}

/// CV-2: the empty log satisfies the invariant.
pub proof fn lemma_cv_init()
    ensures
        cv_inv(DurableLog { records: Seq::empty() }),
{
    let l = DurableLog { records: Seq::<int>::empty() };
    assert(l.records.len() == 0);
    // forall over the empty index range holds vacuously.
}

// ---------------------------------------------------------------------------
// RD: the former congruence pair (recover() DEFINED over the write-set
// multiset, making "order-independence" g(m) == g(m)) is DELETED. RD now
// lives as theorems with real inductive content -- adjacent-transposition
// invariance (the #8039 window) and write-set-count invariance of an
// INTERPRETED skip decision -- in remit_verus_rd_interp.rs, with the
// order-sensitive falsifying certificate in
// negative/rd_ordersensitive_certificate.rs.
// ---------------------------------------------------------------------------

} // verus!

fn main() {}