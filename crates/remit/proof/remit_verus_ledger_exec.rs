// remit_verus_ledger_exec.rs
// ==========================
// N3 (part b) -- EO admission and the PC commit gate as VERIFIED EXECUTABLE
// functions, mirroring the decision logic of lib.rs::begin_effect and
// lib.rs::commit_checkpoint over primitive keys ((u64 branch, u32 task);
// the String-keyed BranchKey translation is the unverified veneer).
//
// FUNCTIONAL FORM, deliberately: state in, state out, no `&mut` anywhere in
// a contract -- portable across Verus versions' &mut-postcondition rules.
// The shipped &mut methods are this logic's direct imperative
// transcription, bridged by the exhaustive differential tests
// (tests/n3_differential.rs).
//
// What is proved, executable:
//   admit:  fresh iff (branch, task) absent; on fresh the returned ledger
//           view is exactly input.push(key); otherwise unchanged. Derived
//           corollaries: a fresh admission's key counts exactly one
//           afterward, and admission preserves ledger uniqueness -- the
//           exec-level EO/CO lemmas.
//   commit: succeeds iff task == frontier+1 AND the validity bit holds; on
//           success the frontier becomes task and the log view is exactly
//           input.push(task); on failure both are unchanged -- PC's
//           strict-next advance and CV's validate-before-append, executable.
//
// Verify:  verus remit_verus_ledger_exec.rs
// Expect:  "verification results:: N verified, 0 errors" (N ~ 9 incl. main).
// Exec tuple comparison is component-wise (x.0/x.1): this Verus pin's vstd
// has no exec PartialEq spec for tuples, and an assume_specification would
// violate the crate's no-assume discipline. Spec-level tuple equality is
// builtin and used in the proof blocks.
// Assert failure = SMT-trigger nudge: report the exact line.
//
// Toolchain: the paper's pin, Verus 0.2026.05.03.8b81855.

use vstd::prelude::*;
use vstd::seq::*;

verus! {

pub type Key = (u64, u32);

// ---- write-set count, drop_last recursion so push unfolds in one step ----
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

// ---------------------------------------------------------------------------
// EO admission: the executable decision core of begin_effect (functional).
// ---------------------------------------------------------------------------

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

/// Exec-level EO corollary: a fresh admission's key counts exactly one
/// afterward, and uniqueness is preserved -- the shipped-form of
/// remit_verus.rs's ledger lemmas.
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

// ---------------------------------------------------------------------------
// PC + CV gate: the executable decision core of commit_checkpoint
// (functional). `valid` is the CheckpointValidator's answer, computed by
// the caller BEFORE this gate -- validation precedes any append, by
// signature.
// ---------------------------------------------------------------------------

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

/// PC, executable form: a successful commit strictly advances the frontier
/// and never re-enters the durable prefix.
proof fn lemma_commit_strictly_advances(f_old: u32, task: u32)
    requires
        task == f_old + 1,
    ensures
        task > f_old,
{
}

fn main() {}

} // verus!
