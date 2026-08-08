
use vstd::prelude::*;
use vstd::seq::*;
use vstd::seq_lib::*;

verus! {

pub struct Effect {
    pub branch: nat,
    pub task: nat,
}

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

pub open spec fn ledger_unique(ledger: Seq<Effect>) -> bool {
    forall|b: nat, t: nat| count_effect(ledger, b, t) <= 1
}

pub open spec fn begin_effect(ledger: Seq<Effect>, b: nat, t: nat) -> Seq<Effect> {
    ledger.push(Effect { branch: b, task: t })
}

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
        assert(count_effect(pushed, b, t) == h_hit + count_effect(ptail, b, t));
        assert(count_effect(ledger, b, t) == h_hit + count_effect(ltail, b, t));
        assert(count_effect(ptail, b, t) == count_effect(ltail.push(e), b, t));
    }
}

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

proof fn lemma_eo_no_duplicate(ledger: Seq<Effect>, b: nat, t: nat)
    requires
        ledger_unique(ledger),
        !contains_effect(ledger, b, t),
    ensures
        count_effect(begin_effect(ledger, b, t), b, t) == 1,
{
    lemma_begin_effect_admits_once(ledger, b, t);
}

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

proof fn lemma_pc_no_prefix_reentry(frontier: nat, task: nat)
    requires commit_admissible(frontier, task),
    ensures task > frontier,
{
}

pub open spec fn branch_key(checkpoint: nat, ordinal: nat) -> (nat, nat) {
    (checkpoint, ordinal)
}

proof fn lemma_fd_ordinal_injective(checkpoint: nat, o1: nat, o2: nat)
    requires o1 != o2,
    ensures branch_key(checkpoint, o1) != branch_key(checkpoint, o2),
{
}

proof fn contract_smoke() {
    let empty = Seq::<Effect>::empty();
    assert(ledger_unique(empty)) by {
        assert forall|b: nat, t: nat| count_effect(empty, b, t) <= 1 by {
            assert(count_effect(empty, b, t) == 0);
        }
    }
    assert(!contains_effect(empty, 0, 1));
    lemma_eo_no_duplicate(empty, 0, 1);

    assert(commit_admissible(0, 1));
    lemma_pc_strict_monotone(0, 1);

    lemma_fd_ordinal_injective(7, 0, 1);
}

pub uninterp spec fn valid(rec: int) -> bool;

pub struct DurableLog {
    pub records: Seq<int>,
}

pub open spec fn gate_step(log: DurableLog, rec: int) -> DurableLog {
    if valid(rec) {
        DurableLog { records: log.records.push(rec) }
    } else {
        log
    }
}

pub open spec fn cv_inv(log: DurableLog) -> bool {
    forall|i: int| 0 <= i < log.records.len() ==> valid(#[trigger] log.records[i])
}

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
}

pub proof fn lemma_cv_init()
    ensures
        cv_inv(DurableLog { records: Seq::empty() }),
{
    let l = DurableLog { records: Seq::<int>::empty() };
    assert(l.records.len() == 0);
}

fn main() {}

}
