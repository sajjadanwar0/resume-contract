
use vstd::prelude::*;
use vstd::seq_lib::*;

verus! {

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

}

fn main() {}
