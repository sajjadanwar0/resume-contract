
use vstd::prelude::*;
use vstd::seq::*;

verus! {

pub open spec fn spec_max(s: Seq<u32>) -> int
    decreases s.len()
{
    if s.len() == 0 {
        0
    } else {
        let m = spec_max(s.drop_last());
        if s.last() as int > m { s.last() as int } else { m }
    }
}

pub open spec fn adjacent_swap(a: Seq<u32>, b: Seq<u32>, i: int) -> bool {
    0 <= i && i + 1 < a.len()
        && b =~= a.update(i, a[i + 1]).update(i + 1, a[i])
}

pub fn recover_core(tasks: &Vec<u32>) -> (next: u32)
    requires
        forall|k: int| 0 <= k < tasks@.len() ==> tasks@[k] < 0xFFFF_FFFFu32,
    ensures
        next as int == spec_max(tasks@) + 1,
{
    // N3-CORE-BODY-BEGIN
    let mut f: u32 = 0;
    let mut i: usize = 0;
    // VERUS-ONLY-BEGIN
    proof {
        assert(tasks@.subrange(0, 0) =~= Seq::<u32>::empty());
    }
    // VERUS-ONLY-END
    while i < tasks.len()
        // VERUS-ONLY-BEGIN
        invariant
            i <= tasks@.len(),
            forall|k: int| 0 <= k < tasks@.len() ==> tasks@[k] < 0xFFFF_FFFFu32,
            f < 0xFFFF_FFFFu32,
            f as int == spec_max(tasks@.subrange(0, i as int)),
        decreases tasks@.len() - i,
        // VERUS-ONLY-END
    {
        let t = tasks[i];
        // VERUS-ONLY-BEGIN
        proof {
            assert(tasks@.subrange(0, (i + 1) as int).drop_last()
                   =~= tasks@.subrange(0, i as int));
            assert(tasks@.subrange(0, (i + 1) as int).last() == tasks@[i as int]);
        }
        // VERUS-ONLY-END
        if t > f {
            f = t;
        }
        i = i + 1;
    }
    // VERUS-ONLY-BEGIN
    proof {
        assert(tasks@.subrange(0, tasks@.len() as int) =~= tasks@);
    }
    // VERUS-ONLY-END
    f + 1
    // N3-CORE-BODY-END
}

proof fn lemma_max_is_ub(s: Seq<u32>)
    ensures
        forall|j: int| 0 <= j < s.len() ==> s[j] as int <= spec_max(s),
    decreases s.len()
{
    if s.len() > 0 {
        lemma_max_is_ub(s.drop_last());
        assert forall|j: int| 0 <= j < s.len() implies
            s[j] as int <= spec_max(s) by {
            if j == s.len() - 1 {
                assert(s[j] == s.last());
            } else {
                assert(s.drop_last()[j] == s[j]);
                assert(s[j] as int <= spec_max(s.drop_last()));
            }
        }
    }
}

proof fn lemma_max_witness(s: Seq<u32>)
    requires
        s.len() > 0,
    ensures
        exists|j: int| 0 <= j < s.len() && s[j] as int == spec_max(s),
    decreases s.len()
{
    if s.len() == 1 {
        assert(s[0] == s.last());
        assert(spec_max(s.drop_last()) == 0);
        assert(0 <= 0 < s.len() && s[0] as int == spec_max(s));
    } else {
        if s.last() as int >= spec_max(s.drop_last()) {
            let j = s.len() - 1;
            assert(s[j] == s.last());
            assert(0 <= j < s.len() && s[j] as int == spec_max(s));
        } else {
            lemma_max_witness(s.drop_last());
            let j = choose|j: int|
                0 <= j < s.drop_last().len()
                && s.drop_last()[j] as int == spec_max(s.drop_last());
            assert(s.drop_last()[j] == s[j]);
            assert(0 <= j < s.len() && s[j] as int == spec_max(s));
        }
    }
}

proof fn lemma_recover_adjacent_swap(a: Seq<u32>, b: Seq<u32>, i: int)
    requires
        adjacent_swap(a, b, i),
    ensures
        spec_max(a) == spec_max(b),
{
    assert(b == a.update(i, a[i + 1]).update(i + 1, a[i]));
    assert(b.len() == a.len());
    lemma_max_is_ub(a);
    lemma_max_is_ub(b);
    lemma_max_witness(a);
    lemma_max_witness(b);
    let ja = choose|j: int| 0 <= j < a.len() && a[j] as int == spec_max(a);
    let ja2: int = if ja == i { i + 1 } else if ja == i + 1 { i } else { ja };
    if ja == i {
        assert(b[i + 1] == a[i]);
    } else if ja == i + 1 {
        assert(b[i] == a[i + 1]);
    } else {
        assert(b[ja] == a[ja]);
    }
    assert(b[ja2] == a[ja]);
    assert(spec_max(a) <= spec_max(b));
    let jb = choose|j: int| 0 <= j < b.len() && b[j] as int == spec_max(b);
    let jb2: int = if jb == i { i + 1 } else if jb == i + 1 { i } else { jb };
    if jb == i {
        assert(b[i] == a[i + 1]);
    } else if jb == i + 1 {
        assert(b[i + 1] == a[i]);
    } else {
        assert(b[jb] == a[jb]);
    }
    assert(a[jb2] == b[jb]);
    assert(spec_max(b) <= spec_max(a));
}

fn main() {}

}
