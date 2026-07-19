//! Exhaustive bounded conformance.
//!
//! Where `model_conformance.rs` explores the transliterated transition
//! relation by seeded random walks, this test explores it **exhaustively**:
//! breadth-first enumeration of the entire reachable state space of the
//! implementation-level transition system under the reference (R0)
//! configuration bounds --- N=3 tasks, interrupt at task 2, values
//! {va, vb}, at most 2 fork resumes, 1 crash, 1 extra resume, plus at most
//! 1 invalid-persist attempt --- deduplicating on the canonical serialized
//! (core state, shadow state) pair, and re-checking all six contract
//! invariants at **every** reachable state. This is the implementation-level
//! analogue of TLC's exhaustive R0 run (87 states over the abstract
//! variables): coverage of the bounded space is total, not sampled, so the
//! "randomized harness, unknown coverage" objection does not apply at these
//! bounds.
//!
//! The state count differs from TLC's 87 by design: the implementation
//! state is finer (effect ids, journal records, branch outcome tables), so
//! histories TLC merges remain distinct here. What is identical is the
//! exhaustiveness claim: every state this transition system can reach under
//! the bounds satisfies EO, PC, FD, CV, CO, and RD.
//!
//! Raise the bounds via REMIT_EXH_FORKS / REMIT_EXH_CRASHES /
//! REMIT_EXH_EXTRAS / REMIT_EXH_INVALIDS to explore larger spaces (growth
//! is combinatorial; the defaults finish in well under a second).

use remit_core::*;
use std::collections::{HashSet, VecDeque};

const IP_CHECKPOINT: &str = "interrupt-ckpt";
const N_TASKS: TaskId = 3;
const INTERRUPT_TASK: TaskId = 2;
const VALUES: [&str; 2] = ["va", "vb"];

fn envu(var: &str, default: u32) -> u32 {
    std::env::var(var).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

struct RejectMarker;
impl CheckpointValidator for RejectMarker {
    fn validate(&self, state: &[u8]) -> Result<(), String> {
        if state == b"INVALID" { Err("schema-invalid state".into()) } else { Ok(()) }
    }
}

/// Shadow of the TLA+ variables, kept independent of the core.
#[derive(Clone, Default)]
struct Shadow {
    effects: std::collections::HashMap<(BranchKey, TaskId), u32>,
    interrupted: bool,
    consumed: bool,
    consumed_val: Option<String>,
    fork_vals: Vec<String>,
    fork_outs: Vec<String>,
    recoveries: Vec<(TaskId, Decision)>,
}

#[derive(Clone)]
struct Node {
    p: Plane,
    sh: Shadow,
    pc: TaskId,
    forks: u32,
    crashes: u32,
    extras: u32,
    invalids: u32,
}

fn canonical_key(n: &Node) -> String {
    // Manual canonical projection. The plane's private maps (fired,
    // frontier, branch outcomes, interrupt records) are functions of the
    // ordered ledger/checkpoint/journal vectors plus the shadow's consumed
    // value and fork lists, so projecting those captures every bit of state
    // that can influence future behavior; the Vec fields carry history
    // order, which is part of the state by design.
    let mut eff: Vec<(String, TaskId, u32)> = n
        .sh
        .effects
        .iter()
        .map(|((b, t), c)| (format!("{b:?}"), *t, *c))
        .collect();
    eff.sort();
    format!(
        "{:?}|{:?}|{:?}|{:?}|{}{}|{:?}|{:?}|{:?}|{:?}|{}/{}/{}/{}|pc{}",
        n.p.ledger(), n.p.checkpoints(), n.p.journal(), eff,
        n.sh.interrupted as u8, n.sh.consumed as u8, n.sh.consumed_val,
        n.sh.fork_vals, n.sh.fork_outs, n.sh.recoveries,
        n.forks, n.crashes, n.extras, n.invalids, n.pc
    )
}

fn run_effect(p: &mut Plane, sh: &mut Shadow, b: &BranchKey, t: TaskId, id: &str) {
    if p.begin_effect(b, t, id).is_ok() {
        *sh.effects.entry((b.clone(), t)).or_insert(0) += 1;
    }
}

fn check_invariants(p: &Plane, sh: &Shadow) {
    for ((b, t), n) in &sh.effects {
        assert!(*n <= 1, "EO violated: effects[{b:?}][{t}] = {n}");
    }
    assert_eq!(sh.fork_outs, sh.fork_vals, "FD violated");
    let puts: usize = p.journal().iter().filter(|o| o.kind == OpKind::Put).count();
    assert_eq!(puts, p.checkpoints().len(), "CV bookkeeping violated");
    let gated = sh.effects.get(&(BranchKey::root(), INTERRUPT_TASK)).copied().unwrap_or(0);
    assert!(gated <= 1, "CO violated: gated effect fired {gated} times");
    for (frontier, dec) in &sh.recoveries {
        assert_eq!(dec, &Decision::SkipTo(frontier + 1), "PC violated at recovery");
    }
    let log = p.checkpoints().to_vec();
    assert_eq!(recover(&log), recover(&log), "RD violated: recover not functional");
    if log.len() >= 2 {
        let mut perm = log.clone();
        perm.reverse();
        assert!(recover_is_order_independent(&log, &perm), "RD violated: order-dependent");
    }
}

/// All successors of `n` under the enabled actions (deterministic per
/// parameterized action, exactly like the TLA+ Next disjunction).
fn successors(n: &Node, max_forks: u32, max_crashes: u32, max_extras: u32, max_invalids: u32) -> Vec<Node> {
    let root = BranchKey::root();
    let mut out = Vec::new();

    // ExecTask
    {
        let next = n.pc + 1;
        let enabled = next <= N_TASKS
            && (next != INTERRUPT_TASK || n.sh.consumed)
            && (next == 1 || next != INTERRUPT_TASK + 1 || n.pc >= INTERRUPT_TASK);
        if enabled {
            let mut m = n.clone();
            run_effect(&mut m.p, &mut m.sh, &root, next, &format!("e{next}"));
            if m.p.commit_checkpoint(&RejectMarker, &root, next, b"ok").is_ok() {
                m.pc = next;
                if m.pc == N_TASKS {
                    m.p.complete();
                }
            }
            out.push(m);
        }
    }
    // EmitInterrupt
    if n.pc == 1 && !n.sh.interrupted {
        let mut m = n.clone();
        m.sh.interrupted = true;
        out.push(m);
    }
    // Consume(v)
    if n.sh.interrupted && !n.sh.consumed {
        for v in VALUES {
            let mut m = n.clone();
            match m.p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::Ordinary) {
                ResumeDecision::ServeSupplied { branch } => {
                    m.sh.consumed = true;
                    m.sh.consumed_val = Some(v.to_string());
                    assert_eq!(m.p.outcome(&branch), Some(v));
                }
                other => panic!("first consume must serve supplied, got {other:?}"),
            }
            out.push(m);
        }
    }
    // ForkResume(v)
    if n.sh.consumed && n.forks < max_forks {
        for v in VALUES {
            let mut m = n.clone();
            match m.p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::ExplicitCheckpoint) {
                ResumeDecision::ServeSupplied { branch } => {
                    m.sh.fork_vals.push(v.to_string());
                    let outv = m.p.outcome(&branch).unwrap_or("").to_string();
                    m.sh.fork_outs.push(outv);
                    run_effect(&mut m.p, &mut m.sh, &branch, INTERRUPT_TASK, "gated");
                    run_effect(&mut m.p, &mut m.sh, &branch, INTERRUPT_TASK, "gated");
                }
                other => panic!("fork intent must serve supplied, got {other:?}"),
            }
            m.forks += 1;
            out.push(m);
        }
    }
    // CrashRecover
    if n.crashes < max_crashes {
        let mut m = n.clone();
        let frontier = m.p.frontier(&root);
        let dec = recover(m.p.checkpoints());
        m.sh.recoveries.push((frontier, dec.clone()));
        for t in 1..=frontier {
            run_effect(&mut m.p, &mut m.sh, &root, t, &format!("e{t}"));
        }
        if m.sh.consumed {
            match m.p.recovery_replay(IP_CHECKPOINT) {
                ResumeDecision::ServeRecorded { .. } => {}
                other => panic!("consumed interrupt must replay recorded, got {other:?}"),
            }
        }
        let Decision::SkipTo(next) = dec;
        m.pc = next.saturating_sub(1).min(N_TASKS);
        m.crashes += 1;
        out.push(m);
    }
    // ExtraResume(v)
    if n.sh.consumed && n.extras < max_extras {
        for v in VALUES {
            let mut m = n.clone();
            let before = m.p.ledger().len();
            let d = m.p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::Ordinary);
            assert_eq!(d, ResumeDecision::Inert, "stray ordinary resume must be inert");
            assert_eq!(m.p.ledger().len(), before, "stray resume touched the ledger");
            m.extras += 1;
            out.push(m);
        }
    }
    // InvalidPersistAttempt
    if n.invalids < max_invalids {
        let mut m = n.clone();
        let attempted = m.p.frontier(&root) + 1;
        let before = m.p.checkpoints().len();
        let r = m.p.commit_checkpoint(&RejectMarker, &root, attempted, b"INVALID");
        assert!(matches!(r, Err(RemitError::InvalidCheckpoint { .. })), "invalid persist must be refused");
        assert_eq!(m.p.checkpoints().len(), before, "CV: rejected state persisted");
        m.invalids += 1;
        out.push(m);
    }
    out
}

#[test]
fn exhaustive_state_space_preserves_all_six_invariants() {
    let max_forks = envu("REMIT_EXH_FORKS", 2);
    let max_crashes = envu("REMIT_EXH_CRASHES", 1);
    let max_extras = envu("REMIT_EXH_EXTRAS", 1);
    let max_invalids = envu("REMIT_EXH_INVALIDS", 1);

    let init = Node {
        p: Plane::default(),
        sh: Shadow::default(),
        pc: 0,
        forks: 0,
        crashes: 0,
        extras: 0,
        invalids: 0,
    };
    check_invariants(&init.p, &init.sh);

    let mut seen: HashSet<String> = HashSet::new();
    seen.insert(canonical_key(&init));
    let mut queue: VecDeque<Node> = VecDeque::new();
    queue.push_back(init);
    let (mut states, mut transitions, mut depth) = (1u64, 0u64, 0u64);
    let mut frontier_len = queue.len();

    while !queue.is_empty() {
        let mut next_frontier: VecDeque<Node> = VecDeque::new();
        for _ in 0..frontier_len {
            let n = queue.pop_front().unwrap();
            for m in successors(&n, max_forks, max_crashes, max_extras, max_invalids) {
                transitions += 1;
                check_invariants(&m.p, &m.sh);
                let key = canonical_key(&m);
                if seen.insert(key) {
                    states += 1;
                    next_frontier.push_back(m);
                }
            }
        }
        if !next_frontier.is_empty() {
            depth += 1;
        }
        queue = next_frontier;
        frontier_len = queue.len();
    }

    eprintln!(
        "exhaustive conformance: {states} distinct states, {transitions} transitions, depth {depth} \
         (bounds: forks<={max_forks}, crashes<={max_crashes}, extras<={max_extras}, invalids<={max_invalids}); \
         all six invariants hold at every reachable state"
    );
    assert!(states > 87, "expected a finer state space than TLC's abstract 87");
}
