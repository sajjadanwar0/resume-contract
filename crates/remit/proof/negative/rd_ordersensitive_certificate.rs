
use vstd::prelude::*;
use vstd::seq::*;

verus! {

pub struct DurableLog {
    pub records: Seq<int>,
}

pub open spec fn adjacent_swap(a: Seq<int>, b: Seq<int>, i: int) -> bool {
    0 <= i && i + 1 < a.len()
        && b =~= a.update(i, a[i + 1]).update(i + 1, a[i])
}

pub open spec fn decides_first(log: DurableLog, t: int) -> bool {
    log.records.len() > 0 && log.records[0] == t
}

proof fn lemma_ordersensitive_functional(a: DurableLog, b: DurableLog)
    requires
        a.records =~= b.records,
    ensures
        forall|t: int| decides_first(a, t) == decides_first(b, t),
{
    assert(a.records == b.records);
}

proof fn lemma_ordersensitive_swap(a: DurableLog, b: DurableLog, i: int)
    requires
        adjacent_swap(a.records, b.records, i),
    ensures
        forall|t: int| decides_first(a, t) == decides_first(b, t),
{
}

fn main() {}

}
