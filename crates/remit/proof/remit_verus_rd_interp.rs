
use vstd::prelude::*;
use vstd::seq::*;

verus! {

pub struct DurableLog {
    pub records: Seq<int>,
}

pub open spec fn count_rec(s: Seq<int>, t: int) -> nat
    decreases s.len()
{
    if s.len() == 0 {
        0
    } else {
        let head_hit: nat = if s[0] == t { 1 } else { 0 };
        head_hit + count_rec(s.subrange(1, s.len() as int), t)
    }
}

pub open spec fn contains_rec(s: Seq<int>, t: int) -> bool {
    exists|j: int| #![auto] 0 <= j < s.len() && s[j] == t
}

pub open spec fn decides_skip(log: DurableLog, t: int) -> bool {
    contains_rec(log.records, t)
}

pub open spec fn adjacent_swap(a: Seq<int>, b: Seq<int>, i: int) -> bool {
    0 <= i && i + 1 < a.len()
        && b =~= a.update(i, a[i + 1]).update(i + 1, a[i])
}

proof fn lemma_count_pos_iff_contains(s: Seq<int>, t: int)
    ensures
        count_rec(s, t) > 0 <==> contains_rec(s, t),
    decreases s.len()
{
    if s.len() == 0 {
        assert(!contains_rec(s, t));
    } else {
        let tail = s.subrange(1, s.len() as int);
        lemma_count_pos_iff_contains(tail, t);
        if s[0] == t {
            assert(0 <= 0 < s.len() && s[0] == t);
            assert(contains_rec(s, t));
            assert(count_rec(s, t) >= 1);
        } else {
            assert(count_rec(s, t) == count_rec(tail, t));
            if contains_rec(tail, t) {
                let j = choose|j: int| 0 <= j < tail.len() && tail[j] == t;
                assert(0 <= j < tail.len() && tail[j] == t);
                assert(tail[j] == s[j + 1]);
                assert(0 <= j + 1 < s.len() && s[j + 1] == t);
                assert(contains_rec(s, t));
            }
            if contains_rec(s, t) {
                let j = choose|j: int| 0 <= j < s.len() && s[j] == t;
                assert(0 <= j < s.len() && s[j] == t);
                assert(j != 0);
                assert(tail[j - 1] == s[j]);
                assert(0 <= j - 1 < tail.len() && tail[j - 1] == t);
                assert(contains_rec(tail, t));
            }
        }
    }
}

proof fn lemma_rd_functional_interp(a: DurableLog, b: DurableLog)
    requires
        a.records =~= b.records,
    ensures
        forall|t: int| decides_skip(a, t) == decides_skip(b, t),
{
    assert(a.records == b.records);
}

proof fn lemma_rd_adjacent_swap(a: DurableLog, b: DurableLog, i: int)
    requires
        adjacent_swap(a.records, b.records, i),
    ensures
        forall|t: int| decides_skip(a, t) == decides_skip(b, t),
{
    let ar = a.records;
    let br = b.records;
    assert(br == ar.update(i, ar[i + 1]).update(i + 1, ar[i]));
    assert(br.len() == ar.len());
    assert forall|t: int| decides_skip(a, t) == decides_skip(b, t) by {
        if contains_rec(ar, t) {
            let j = choose|j: int| 0 <= j < ar.len() && ar[j] == t;
            assert(0 <= j < ar.len() && ar[j] == t);
            let j2: int = if j == i { i + 1 } else if j == i + 1 { i } else { j };
            if j == i {
                assert(br[i + 1] == ar[i]);
            } else if j == i + 1 {
                assert(br[i] == ar[i + 1]);
            } else {
                assert(br[j] == ar[j]);
            }
            assert(0 <= j2 < br.len() && br[j2] == t);
            assert(contains_rec(br, t));
        }
        if contains_rec(br, t) {
            let j = choose|j: int| 0 <= j < br.len() && br[j] == t;
            assert(0 <= j < br.len() && br[j] == t);
            let j2: int = if j == i { i + 1 } else if j == i + 1 { i } else { j };
            if j == i {
                assert(br[i] == ar[i + 1]);
            } else if j == i + 1 {
                assert(br[i + 1] == ar[i]);
            } else {
                assert(br[j] == ar[j]);
            }
            assert(0 <= j2 < ar.len() && ar[j2] == t);
            assert(contains_rec(ar, t));
        }
    }
}

pub open spec fn same_writeset_counts(a: Seq<int>, b: Seq<int>) -> bool {
    forall|t: int| count_rec(a, t) == count_rec(b, t)
}

proof fn lemma_rd_writeset(a: DurableLog, b: DurableLog)
    requires
        same_writeset_counts(a.records, b.records),
    ensures
        forall|t: int| decides_skip(a, t) == decides_skip(b, t),
{
    assert forall|t: int| decides_skip(a, t) == decides_skip(b, t) by {
        lemma_count_pos_iff_contains(a.records, t);
        lemma_count_pos_iff_contains(b.records, t);
        assert(count_rec(a.records, t) == count_rec(b.records, t));
    }
}

fn main() {}

}
