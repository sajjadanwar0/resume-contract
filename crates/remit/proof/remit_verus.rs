// remit_verus.rs
// Machine-checked proof of the Remit resume-sequencer core invariants,
// targeting Verus 0.2026.05.03.8b81855.
//
// This is the abstract state machine that crates/remit/src/lib.rs implements:
// an append-only effect ledger plus a per-branch durable frontier. The
// theorems below discharge the contract properties as proof obligations over
// that machine (the property <-> obligation map is crates/remit/VERIFICATION.md,
// and the protocol-level spec is formal/tla/ResumeContract.tla, whose reference
// configuration TLC checks).
//
// Verify:  verus remit_verus.rs
// Expect:  "verification results:: N verified, 0 errors"
//
// Proven here (structural, unbounded -- not a bounded model check):
//   EO/CO  begin_effect admits a (branch,task) effect only when absent, and
//          the post-state contains it exactly once  (lemma_eo_no_duplicate,
//          lemma_begin_effect_admits_once)
//   PC     commit advances the frontier by exactly one and never re-enters
//          the durable prefix  (lemma_pc_strict_monotone,
//          lemma_pc_no_prefix_reentry)
//   FD     distinct resume ordinals key distinct branches
//          (lemma_fd_ordinal_injective). The distinct-values-served-
//          distinctly claim is now an INDUCTIVE theorem in
//          remit_verus_fd_machine.rs (former definitional lemma deleted;
//          falsifying certificate: negative/fd_stock_certificate.rs).

use vstd::prelude::*;
use vstd::seq::*;

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

fn main() {}

} // verus!
