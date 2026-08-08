
use vstd::prelude::*;
use vstd::seq::*;

verus! {

pub type Key = (u64, u32);

pub open spec fn count_key(s: Seq<Key>, k: Key) -> nat
    decreases s.len()
{
    if s.len() == 0 {
        0
    } else {
        let rest = count_key(s.drop_last(), k);
        if s.last() == k { rest + 1 } else { rest }
    }
}

pub open spec fn contains_key(s: Seq<Key>, k: Key) -> bool {
    exists|j: int| #![auto] 0 <= j < s.len() && s[j] == k
}

pub open spec fn unique_keys(s: Seq<Key>) -> bool {
    forall|i: int, j: int| 0 <= i < j < s.len() ==> s[i] != s[j]
}

proof fn lemma_count_push(s: Seq<Key>, x: Key, k: Key)
    ensures
        count_key(s.push(x), k)
            == count_key(s, k) + (if x == k { 1nat } else { 0nat }),
{
    assert(s.push(x).drop_last() =~= s);
    assert(s.push(x).last() == x);
}

proof fn lemma_count_pos_iff_contains(s: Seq<Key>, k: Key)
    ensures
        count_key(s, k) > 0 <==> contains_key(s, k),
    decreases s.len()
{
    if s.len() == 0 {
        assert(!contains_key(s, k));
    } else {
        let rest = s.drop_last();
        lemma_count_pos_iff_contains(rest, k);
        if s.last() == k {
            assert(0 <= s.len() - 1 < s.len() && s[s.len() - 1] == k);
            assert(contains_key(s, k));
            assert(count_key(s, k) >= 1);
        } else {
            assert(count_key(s, k) == count_key(rest, k));
            if contains_key(rest, k) {
                let j = choose|j: int| 0 <= j < rest.len() && rest[j] == k;
                assert(rest[j] == s[j]);
                assert(0 <= j < s.len() && s[j] == k);
                assert(contains_key(s, k));
            }
            if contains_key(s, k) {
                let j = choose|j: int| 0 <= j < s.len() && s[j] == k;
                assert(j != s.len() - 1);
                assert(rest[j] == s[j]);
                assert(0 <= j < rest.len() && rest[j] == k);
                assert(contains_key(rest, k));
            }
        }
    }
}

proof fn lemma_unique_push(s: Seq<Key>, x: Key)
    requires
        unique_keys(s),
        !contains_key(s, x),
    ensures
        unique_keys(s.push(x)),
{
    let p = s.push(x);
    assert forall|i: int, j: int| 0 <= i < j < p.len() implies p[i] != p[j] by {
        if j == p.len() - 1 {
            assert(p[j] == x);
            assert(p[i] == s[i]);
            if s[i] == x {
                assert(0 <= i < s.len() && s[i] == x);
                assert(contains_key(s, x));
            }
        } else {
            assert(p[i] == s[i]);
            assert(p[j] == s[j]);
        }
    }
}

pub fn admit(entries: Vec<Key>, branch: u64, task: u32) -> (r: (bool, Vec<Key>))
    ensures
        r.0 == !contains_key(entries@, (branch, task)),
        r.0 ==> r.1@ =~= entries@.push((branch, task)),
        !r.0 ==> r.1@ =~= entries@,
{
    let key: Key = (branch, task);
    let mut e = entries;
    let mut i: usize = 0;
    let mut found: bool = false;
    while i < e.len()
        invariant
            i <= e@.len(),
            e@ == entries@,
            key == (branch, task),
            found ==> contains_key(entries@, key),
            !found ==> forall|j: int| 0 <= j < i ==> entries@[j] != key,
        decreases e@.len() - i,
    {
        let x = e[i];
        if x.0 == branch && x.1 == task {
            proof {
                assert(x == (x.0, x.1));
                assert(x == key);
                assert(0 <= i < entries@.len() && entries@[i as int] == key);
            }
            found = true;
        }
        i = i + 1;
    }
    if found {
        return (false, e);
    }
    proof {
        if contains_key(entries@, key) {
            let j = choose|j: int|
                0 <= j < entries@.len() && entries@[j] == key;
            assert(entries@[j] != key);
        }
    }
    e.push(key);
    (true, e)
}

proof fn lemma_admit_fresh_counts_one(s: Seq<Key>, k: Key)
    requires
        !contains_key(s, k),
    ensures
        count_key(s.push(k), k) == 1,
{
    lemma_count_pos_iff_contains(s, k);
    lemma_count_push(s, k, k);
}

proof fn lemma_admit_preserves_unique(s: Seq<Key>, k: Key)
    requires
        unique_keys(s),
        !contains_key(s, k),
    ensures
        unique_keys(s.push(k)),
{
    lemma_unique_push(s, k);
}

pub fn commit(frontier: u32, log: Vec<u32>, task: u32, valid: bool)
    -> (r: (bool, u32, Vec<u32>))
    requires
        frontier < 0xFFFF_FFFFu32,
    ensures
        r.0 == (task == frontier + 1 && valid),
        r.0 ==> r.1 == task && r.2@ =~= log@.push(task),
        !r.0 ==> r.1 == frontier && r.2@ =~= log@,
{
    let mut l = log;
    if task != frontier + 1 {
        return (false, frontier, l);
    }
    if !valid {
        return (false, frontier, l);
    }
    l.push(task);
    (true, task, l)
}

proof fn lemma_commit_strictly_advances(f_old: u32, task: u32)
    requires
        task == f_old + 1,
    ensures
        task > f_old,
{
}

fn main() {}

}
