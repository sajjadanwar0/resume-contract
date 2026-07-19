//! Model-conformance harness.
//!
//! This file is `formal/tla/ResumeContract.tla`'s transition relation
//! transliterated into an executable randomized explorer: the action
//! alphabet is exactly the module's — ExecTask, EmitInterrupt, Consume(v),
//! ForkResume(v), CrashRecover, ExtraResume(v) — plus the CV probe
//! InvalidPersistAttempt, and after **every** action the six contract
//! invariants (EO, FD, CV, CO, PC, RD) are re-checked against the core's
//! state, mirroring how TLC checks the invariants at every reachable state
//! of the reference configuration R0. Actions are gated by the module's
//! enabling conditions; a disabled action is a no-op, exactly as TLC only
//! explores enabled transitions.
//!
//! The harness carries its own xorshift64* PRNG so it has zero external
//! dependencies and is bit-for-bit reproducible: every case's seed derives
//! from REMIT_MODEL_SEED (default 0x5EED_2026) plus the case index, and a
//! failing case prints its seed and full action trace before re-raising.
//! Scale with REMIT_MODEL_CASES (default 512 sequences of up to 48 actions).
//!
//! What this does and does not establish: executable conformance of the
//! production core to the model's transition relation under seeded
//! randomized deep exploration. It is not a proof and is not claimed as
//! one; the proofs live in the Verus files, and this harness is the bridge
//! evidence between them and the shipped core.

use remit_core::*;

const IP_CHECKPOINT: &str = "interrupt-ckpt";
const N_TASKS: TaskId = 3;
const INTERRUPT_TASK: TaskId = 2;

struct XorShift64Star(u64);
impl XorShift64Star {
    fn new(seed: u64) -> Self {
        XorShift64Star(seed.max(1))
    }
    fn next(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
    fn value(&mut self) -> &'static str {
        if self.next() & 1 == 0 { "va" } else { "vb" }
    }
}

fn envu64(var: &str, default: u64) -> u64 {
    std::env::var(var).ok().and_then(|v| v.parse().ok()).unwrap_or(default)
}

#[derive(Clone, Debug)]
enum Action {
    ExecTask,
    EmitInterrupt,
    Consume(&'static str),
    ForkResume(&'static str),
    CrashRecover,
    ExtraResume(&'static str),
    InvalidPersistAttempt,
}

struct RejectMarker;
impl CheckpointValidator for RejectMarker {
    fn validate(&self, state: &[u8]) -> Result<(), String> {
        if state == b"INVALID" {
            Err("schema-invalid state".into())
        } else {
            Ok(())
        }
    }
}

/// Shadow model: the TLA+ variables, maintained independently of the core so
/// the invariants are judged against the spec's own bookkeeping, not the
/// implementation's.
#[derive(Default)]
struct Shadow {
    effects: std::collections::HashMap<(BranchKey, TaskId), u32>,
    interrupted: bool,
    consumed: bool,
    fork_vals: Vec<String>,
    fork_outs: Vec<String>,
    recoveries: Vec<(TaskId, Decision)>,
}

fn run_effect(p: &mut Plane, sh: &mut Shadow, b: &BranchKey, t: TaskId, id: &str) {
    // The runtime under the contract only fires the effect if admission
    // succeeds; the shadow counts what actually fired.
    if p.begin_effect(b, t, id).is_ok() {
        *sh.effects.entry((b.clone(), t)).or_insert(0) += 1;
    }
}

fn check_invariants(p: &Plane, sh: &Shadow) {
    // EO: \A branch, t : effects[branch][t] <= 1
    for ((b, t), n) in &sh.effects {
        assert!(*n <= 1, "EO violated: effects[{b:?}][{t}] = {n}");
    }
    // FD: \A k : forkOuts[k] = forkVals[k]
    assert_eq!(
        sh.fork_outs, sh.fork_vals,
        "FD violated: served outcomes differ from supplied values"
    );
    // CV: rejected states never reach the durable log — every journal Put
    // has a checkpoint record and vice versa, regardless of how many
    // rejections were attempted.
    let puts: usize = p.journal().iter().filter(|o| o.kind == OpKind::Put).count();
    assert_eq!(
        puts,
        p.checkpoints().len(),
        "CV bookkeeping violated: journal Puts ({puts}) != checkpoint log ({})",
        p.checkpoints().len()
    );
    // CO: the gated task's effect count is <= 1 on the root branch even
    // across stray/extra resumes.
    let gated = sh
        .effects
        .get(&(BranchKey::root(), INTERRUPT_TASK))
        .copied()
        .unwrap_or(0);
    assert!(gated <= 1, "CO violated: gated effect fired {gated} times on root");
    // PC: recovery continues from the frontier — every recorded recovery
    // decision equals SkipTo(frontier_at_recovery + 1).
    for (frontier, dec) in &sh.recoveries {
        assert_eq!(dec, &Decision::SkipTo(frontier + 1), "PC violated at recovery");
    }
    // RD: equal durable logs -> equal decisions; functional purity and
    // order-independence on the current log.
    let log = p.checkpoints().to_vec();
    assert_eq!(recover(&log), recover(&log), "RD violated: recover not functional");
    if log.len() >= 2 {
        let mut perm = log.clone();
        perm.reverse();
        assert!(
            recover_is_order_independent(&log, &perm),
            "RD violated: recovery depends on log order"
        );
    }
}

fn run_case(seed: u64, max_actions: usize, trace: &mut Vec<Action>) {
    let mut rng = XorShift64Star::new(seed);
    let mut p = Plane::new();
    let mut sh = Shadow::default();
    let root = BranchKey::root();
    let mut pc: TaskId = 0; // completed prefix over tasks 1..=N; interrupt point after task 1

    let n_actions = 1 + (rng.below(max_actions as u64) as usize);
    for _ in 0..n_actions {
        let a = match rng.below(7) {
            0 => Action::ExecTask,
            1 => Action::EmitInterrupt,
            2 => Action::Consume(rng.value()),
            3 => Action::ForkResume(rng.value()),
            4 => Action::CrashRecover,
            5 => Action::ExtraResume(rng.value()),
            _ => Action::InvalidPersistAttempt,
        };
        trace.push(a.clone());
        match a {
            Action::ExecTask => {
                let next = pc + 1;
                let enabled = next <= N_TASKS
                    && (next != INTERRUPT_TASK || sh.consumed)
                    && (next == 1 || next != INTERRUPT_TASK + 1 || pc >= INTERRUPT_TASK);
                if enabled {
                    run_effect(&mut p, &mut sh, &root, next, &format!("e{next}"));
                    if p
                        .commit_checkpoint(&RejectMarker, &root, next, b"ok")
                        .is_ok()
                    {
                        pc = next;
                        if pc == N_TASKS {
                            p.complete();
                        }
                    }
                }
            }
            Action::EmitInterrupt => {
                if pc == 1 && !sh.interrupted {
                    sh.interrupted = true;
                }
            }
            Action::Consume(v) => {
                if sh.interrupted && !sh.consumed {
                    match p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::Ordinary) {
                        ResumeDecision::ServeSupplied { branch } => {
                            sh.consumed = true;
                            assert_eq!(p.outcome(&branch), Some(v), "first consume must serve the supplied value");
                        }
                        other => panic!("first consume must serve supplied, got {other:?}"),
                    }
                }
            }
            Action::ForkResume(v) => {
                if sh.consumed {
                    match p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::ExplicitCheckpoint) {
                        ResumeDecision::ServeSupplied { branch } => {
                            sh.fork_vals.push(v.to_string());
                            let out = p.outcome(&branch).unwrap_or("").to_string();
                            sh.fork_outs.push(out);
                            // per-branch EO: the fork's gated effect fires
                            // once on its branch; a duplicate is refused.
                            run_effect(&mut p, &mut sh, &branch, INTERRUPT_TASK, "gated");
                            run_effect(&mut p, &mut sh, &branch, INTERRUPT_TASK, "gated");
                        }
                        other => panic!("fork intent must serve supplied, got {other:?}"),
                    }
                }
            }
            Action::CrashRecover => {
                let frontier = p.frontier(&root);
                let dec = recover(p.checkpoints());
                sh.recoveries.push((frontier, dec.clone()));
                // Replay of the prefix under memoization: every re-attempted
                // prefix effect must be refused by the ledger (the shadow
                // counter catches any re-fire as an EO violation).
                for t in 1..=frontier {
                    run_effect(&mut p, &mut sh, &root, t, &format!("e{t}"));
                }
                if sh.consumed {
                    match p.recovery_replay(IP_CHECKPOINT) {
                        ResumeDecision::ServeRecorded { .. } => {}
                        other => panic!("consumed interrupt must replay recorded, got {other:?}"),
                    }
                }
                let Decision::SkipTo(next) = dec;
                pc = next.saturating_sub(1).min(N_TASKS);
            }
            Action::ExtraResume(v) => {
                if sh.consumed {
                    let before = p.ledger().len();
                    let d = p.resolve_resume(IP_CHECKPOINT, Some(v), AddressKind::Ordinary);
                    assert_eq!(d, ResumeDecision::Inert, "stray ordinary resume after consumption must be inert");
                    assert_eq!(p.ledger().len(), before, "stray resume must not touch the ledger");
                }
            }
            Action::InvalidPersistAttempt => {
                let attempted = p.frontier(&root) + 1;
                let before = p.checkpoints().len();
                let r = p.commit_checkpoint(&RejectMarker, &root, attempted, b"INVALID");
                assert!(
                    matches!(r, Err(RemitError::InvalidCheckpoint { .. })),
                    "invalid persist must be refused loudly, got {r:?}"
                );
                assert_eq!(p.checkpoints().len(), before, "CV: rejected state must persist nothing");
            }
        }
        check_invariants(&p, &sh);
    }
}

#[test]
fn transition_relation_preserves_all_six_invariants() {
    let cases = envu64("REMIT_MODEL_CASES", 512);
    let base_seed = envu64("REMIT_MODEL_SEED", 0x5EED_2026);
    for i in 0..cases {
        let seed = base_seed.wrapping_add(i).wrapping_mul(0x9E37_79B9_7F4A_7C15);
        let mut trace: Vec<Action> = Vec::new();
        let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            run_case(seed, 48, &mut trace)
        }));
        if let Err(e) = r {
            eprintln!(
                "model-conformance failure: case={i} seed={seed:#x} (REMIT_MODEL_SEED base {base_seed:#x})\ntrace={trace:?}"
            );
            std::panic::resume_unwind(e);
        }
    }
}
