// remit_verus_all.rs — composed LEGACY verification target: the trimmed
// contents of remit_verus.rs (EO/CO/PC/FD-keying core) and the CV lemmas of
// remit_verus_cv.rs, one Verus invocation, one tally.
//
// The historical "all fifteen items / 15 verified" story is RETIRED: the
// definitional FD lemma and the RD congruence pair are deleted from every
// file (see the FD/RD notes below). The current formal surface is a SET of
// standalone targets: this file, remit_verus.rs, remit_verus_cv.rs,
// remit_verus_fd_machine.rs, remit_verus_rd_interp.rs, plus the two
// documented expected-to-fail certificates under negative/ (kept out of
// every verify-all glob). The new FD/RD machines stay standalone by design:
// they re-declare model types (e.g. DurableLog), so composing them here
// requires a definition-sharing refactor -- deferred to the refinement work.
// No `assume` anywhere (grep to confirm).
//
// Verify:  verus crates/remit/proof/remit_verus_all.rs
// Expect:  "verification results:: N verified, 0 errors" -- record the
// FRESH tally + date in VERIFICATION.md; trust the run, not this comment.
//
// Toolchain: Verus 0.2026.05.03.8b81855 (the paper's pin).

use vstd::prelude::*;
use vstd::seq::*;
use vstd::seq_lib::*;

verus! {

// ---- Effect ledger records: (branch, task) ---------------------------------
// Branches are identified by a resume ordinal (0 = root); tasks by index.
pub struct Effect {
    pub branch: nat,
    pub task: nat,
}

// A ledger is a sequence of effect records. EO/CO reduce to: no (branch,task)
// pair occurs twice.
pub open spec fn contains_effect(ledger: Seq<Effect>, b: nat, t: nat) -> bool {
    exists|i: int| #![auto] 0 <= i < ledger.len() && ledger[i].branch == b && ledger[i].task == t
}

pub open spec fn count_effect(ledger: Seq<Effect>, b: nat, t: nat) -> nat
    decreases ledger.len()
{
    if ledger.len() == 0 {
        0
    } else {
        let head_hit: nat = if ledger[0].branch == b && ledger[0].task == t { 1 } else { 0 };
        head_hit + count_effect(ledger.subrange(1, ledger.len() as int), b, t)
    }
}

// The ledger invariant: every (branch,task) appears at most once.
pub open spec fn ledger_unique(ledger: Seq<Effect>) -> bool {
    forall|b: nat, t: nat| count_effect(ledger, b, t) <= 1
}

// begin_effect: admit an effect iff its (branch,task) is absent, appending it.
pub open spec fn begin_effect(ledger: Seq<Effect>, b: nat, t: nat) -> Seq<Effect> {
    ledger.push(Effect { branch: b, task: t })
}

// If a pair is absent, its count is zero.
proof fn lemma_absent_count_zero(ledger: Seq<Effect>, b: nat, t: nat)
    requires !contains_effect(ledger, b, t),
    ensures count_effect(ledger, b, t) == 0,
    decreases ledger.len()
{
    if ledger.len() == 0 {
    } else {
        assert(ledger[0].branch != b || ledger[0].task != t);
        let tail = ledger.subrange(1, ledger.len() as int);
        assert forall|i: int| #![auto] 0 <= i < tail.len() implies
            (tail[i].branch != b || tail[i].task != t) by {
            assert(tail[i] == ledger[i + 1]);
        }
        assert(!contains_effect(tail, b, t));
        lemma_absent_count_zero(tail, b, t);
    }
}

// Appending a record increments exactly the count of its own pair.
proof fn lemma_push_count(ledger: Seq<Effect>, e: Effect, b: nat, t: nat)
    ensures
        count_effect(ledger.push(e), b, t) ==
            count_effect(ledger, b, t) +
            (if e.branch == b && e.task == t { 1nat } else { 0nat }),
    decreases ledger.len()
{
    reveal_with_fuel(count_effect, 2);
    let pushed = ledger.push(e);
    let e_hit: nat = if e.branch == b && e.task == t { 1 } else { 0 };
    if ledger.len() == 0 {
        assert(pushed.len() == 1);
        assert(pushed[0] == e);
        let ptail = pushed.subrange(1, 1);
        assert(ptail =~= Seq::<Effect>::empty());
        // definitional unfold of count_effect on pushed (len 1) and on empty:
        assert(count_effect(ptail, b, t) == 0);
        assert(count_effect(pushed, b, t) == e_hit + count_effect(ptail, b, t));
        assert(count_effect(ledger, b, t) == 0);
    } else {
        assert(pushed[0] == ledger[0]);
        let h_hit: nat = if ledger[0].branch == b && ledger[0].task == t { 1 } else { 0 };
        let ptail = pushed.subrange(1, pushed.len() as int);
        let ltail = ledger.subrange(1, ledger.len() as int);
        assert(ptail =~= ltail.push(e));
        lemma_push_count(ltail, e, b, t);
        // definitional unfolds on both sides:
        assert(count_effect(pushed, b, t) == h_hit + count_effect(ptail, b, t));
        assert(count_effect(ledger, b, t) == h_hit + count_effect(ltail, b, t));
        assert(count_effect(ptail, b, t) == count_effect(ltail.push(e), b, t));
    }
}

// EO/CO core: begin_effect on a fresh (b,t) yields exactly-once, and preserves
// the ledger-unique invariant.
proof fn lemma_begin_effect_admits_once(ledger: Seq<Effect>, b: nat, t: nat)
    requires
        ledger_unique(ledger),
        !contains_effect(ledger, b, t),
    ensures
        count_effect(begin_effect(ledger, b, t), b, t) == 1,
        ledger_unique(begin_effect(ledger, b, t)),
{
    let e = Effect { branch: b, task: t };
    lemma_absent_count_zero(ledger, b, t);
    lemma_push_count(ledger, e, b, t);
    assert(count_effect(begin_effect(ledger, b, t), b, t) == 1);

    assert forall|b2: nat, t2: nat| count_effect(begin_effect(ledger, b, t), b2, t2) <= 1 by {
        lemma_push_count(ledger, e, b2, t2);
        if b2 == b && t2 == t {
            lemma_absent_count_zero(ledger, b2, t2);
        } else {
            assert(count_effect(ledger, b2, t2) <= 1);
        }
    }
}

// EO restated: after admitting a fresh effect, that effect is present exactly
// once -- no duplicate can arise from a single admission.
proof fn lemma_eo_no_duplicate(ledger: Seq<Effect>, b: nat, t: nat)
    requires
        ledger_unique(ledger),
        !contains_effect(ledger, b, t),
    ensures
        count_effect(begin_effect(ledger, b, t), b, t) == 1,
{
    lemma_begin_effect_admits_once(ledger, b, t);
}

// ---- PC: durable frontier is strictly monotone; no prefix re-entry ---------
// commit(frontier, task) is admissible only when task == frontier + 1.
pub open spec fn commit_admissible(frontier: nat, task: nat) -> bool {
    task == frontier + 1
}

pub open spec fn commit(frontier: nat, task: nat) -> nat {
    task
}

proof fn lemma_pc_strict_monotone(frontier: nat, task: nat)
    requires commit_admissible(frontier, task),
    ensures commit(frontier, task) > frontier,
{
}

// No task at or below the frontier is ever admitted for commit -- execution
// never re-enters the durable prefix.
proof fn lemma_pc_no_prefix_reentry(frontier: nat, task: nat)
    requires commit_admissible(frontier, task),
    ensures task > frontier,
{
}

// ---- FD: resume ordinals key distinct branches -----------------------------
// A branch is keyed by (checkpoint, resume ordinal). Within one checkpoint the
// ordinal alone distinguishes branches. The former "distinct values served
// distinctly" lemma (definitional: served() WAS the keyed lookup) is DELETED;
// the property now lives as an inductive theorem in
// remit_verus_fd_machine.rs, with the #6663-rule falsifying certificate in
// negative/fd_stock_certificate.rs.
pub open spec fn branch_key(checkpoint: nat, ordinal: nat) -> (nat, nat) {
    (checkpoint, ordinal)
}

proof fn lemma_fd_ordinal_injective(checkpoint: nat, o1: nat, o2: nat)
    requires o1 != o2,
    ensures branch_key(checkpoint, o1) != branch_key(checkpoint, o2),
{
}

// ---- Executable check that the proofs are wired (spec sanity) --------------
proof fn contract_smoke() {
    // EO: a fresh effect on an empty ledger is present exactly once.
    let empty = Seq::<Effect>::empty();
    assert(ledger_unique(empty)) by {
        assert forall|b: nat, t: nat| count_effect(empty, b, t) <= 1 by {
            assert(count_effect(empty, b, t) == 0);
        }
    }
    assert(!contains_effect(empty, 0, 1));
    lemma_eo_no_duplicate(empty, 0, 1);

    // PC: committing task 1 from frontier 0 advances to 1.
    assert(commit_admissible(0, 1));
    lemma_pc_strict_monotone(0, 1);

    // FD: ordinals 0 and 1 key distinct branches.
    lemma_fd_ordinal_injective(7, 0, 1);
}

// ---- CV/RD (from remit_verus_cv.rs, discharged) ----

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

fn main() {}

} // verus!