// n2_rd_ordersensitive.rs -- NEW EXPERIMENT N2 (part d): the RD NON-VACUITY
// CERTIFICATE. The #8039 hazard turned into a rule: decide by whichever
// record is FIRST. The same adjacent-swap obligation is FALSE here, so Verus
// must reject lemma_ordersensitive_swap -- certifying that n2_rd_interp.rs's
// clean discharge is a proof about the rule, not a definition restated.
// Verify:  verus n2_rd_ordersensitive.rs
// Expect:  N verified, 1 errors -- postcondition not satisfied at
//          lemma_ordersensitive_swap.
// Toolchain: Verus 0.2026.05.03.8b81855 (the paper's pin).

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

/// The ORDER-SENSITIVE rule (#8039 as a rule): skip t iff t is the FIRST
/// durable record.
pub open spec fn decides_first(log: DurableLog, t: int) -> bool {
    log.records.len() > 0 && log.records[0] == t
}

/// Sanity (expected to VERIFY), so the rejection below is semantic.
proof fn lemma_ordersensitive_functional(a: DurableLog, b: DurableLog)
    requires
        a.records =~= b.records,
    ensures
        forall|t: int| decides_first(a, t) == decides_first(b, t),
{
    assert(a.records == b.records);
}

/// EXPECTED TO FAIL: swap at i = 0 with records[0] != records[1] changes
/// which record is first, and the decision flips.
proof fn lemma_ordersensitive_swap(a: DurableLog, b: DurableLog, i: int)
    requires
        adjacent_swap(a.records, b.records, i),
    ensures
        forall|t: int| decides_first(a, t) == decides_first(b, t),
{
}

fn main() {}

} // verus!
