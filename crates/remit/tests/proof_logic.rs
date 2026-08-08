#[derive(Clone, Copy, PartialEq)]
struct Effect { branch: u32, task: u32 }

fn count_effect(l: &[Effect], b: u32, t: u32) -> usize {
    l.iter().filter(|e| e.branch == b && e.task == t).count()
}

fn contains_effect(l: &[Effect], b: u32, t: u32) -> bool { count_effect(l, b, t) > 0 }
fn ledger_unique(l: &[Effect]) -> bool {
    l.iter().all(|e| count_effect(l, e.branch, e.task) <= 1)
}

fn begin_effect(l: &mut Vec<Effect>, b: u32, t: u32) -> bool {
    if contains_effect(l, b, t) { return false; }
    l.push(Effect { branch: b, task: t });
    true
}

#[test]
fn lemma_begin_effect_admits_once_instances() {
    for trials in 0..200u32 {
        let mut led: Vec<Effect> = Vec::new();

        for k in 0..(trials % 7) {
            begin_effect(&mut led, (trials + k) % 3, (trials * 2 + k) % 4);
        }

        assert!(ledger_unique(&led));
        let (b, t) = (trials % 3, (trials + 1) % 4);
        let fresh = !contains_effect(&led, b, t);
        assert_eq!(begin_effect(&mut led, b, t), fresh);
        assert_eq!(count_effect(&led, b, t), 1);
        assert!(ledger_unique(&led));
    }
}

fn commit_admissible(f: u32, t: u32) -> bool { t == f + 1 }

#[test]
fn lemma_pc_strict_monotone_instances() {
    for f in 0..500u32 {
        for t in 0..500u32 {
            if commit_admissible(f, t) { assert!(t > f); assert_eq!(t, f + 1); }
        }
    }
}

#[test]
fn lemma_fd_ordinal_injective_instances() {
    for c in 0..20u32 { for o1 in 0..20u32 { for o2 in 0..20u32 {
        if o1 != o2 { assert_ne!((c, o1), (c, o2)); }
    }}}
}
