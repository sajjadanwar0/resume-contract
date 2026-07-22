//! Remit: reference resume sequencer and append-only effect ledger for the
//! Resume Contract.
//!
//! Protocol-level specification: `formal/tla/ResumeContract.tla` (this crate's
//! intended external behavior is, verbatim, that module's reference
//! configuration `R0_reference.cfg`, which TLC verifies against all six
//! contract invariants). The Verus proof plan for the invariants below lives
//! in `VERIFICATION.md`; this file is the executable design skeleton, with
//! each invariant enforced by construction and exercised by a named test.
//!
//! Invariant map (contract property -> mechanism here):
//!   EO  effect exactly-once   -> `Ledger` uniqueness on (branch, task)
//!   CO  consume-once          -> EO at the gated task; stray resumes are
//!                                inert by construction (`resume_completed`)
//!   PC  prefix consistency    -> strict-next frontier advance in
//!                                `commit_checkpoint`
//!   FD  fork determinism      -> branches keyed by (checkpoint_id,
//!                                resume_index); the supplied value is the
//!                                branch's recorded outcome source
//!   CV  checkpoint validity   -> `CheckpointValidator` gate before any
//!                                durable append; rejected writes persist
//!                                nothing
//!   RD  recovery determinism  -> `recover` is a pure function of the
//!                                durable log (no ambient state)
//!
//! Integration target: a shim implementing LangGraph's
//! `BaseCheckpointSaver`, routing `put`/`put_writes` through
//! `commit_checkpoint` and tool-effect admission through `begin_effect`, so
//! unmodified graphs acquire the contract. The acceptance experiment is the
//! pilot probe suite (probes/113-115b) re-run through the shim with zero
//! violations.

use std::collections::{HashMap, HashSet};

pub type TaskId = u32;
pub type Seq = u64;

/// A branch of execution. The root branch is the primary run; every resume
/// addressed to an interrupt checkpoint with index `k` names branch
/// (checkpoint_id, k). Distinct resume indices are distinct branches even
/// when the supplied values coincide.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct BranchKey {
    pub checkpoint_id: String,
    pub resume_index: u32,
}

impl BranchKey {
    pub fn root() -> Self {
        BranchKey { checkpoint_id: String::from("root"), resume_index: 0 }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum RemitError {
    /// EO/CO: the (branch, task) effect already fired.
    DuplicateEffect { task: TaskId },
    /// PC: attempted to complete a task at or below the durable frontier
    /// (outside an explicit fork), or to skip ahead of it.
    PrefixViolation { frontier: TaskId, attempted: TaskId },
    /// CV: the state failed schema validation; nothing was persisted.
    InvalidCheckpoint { reason: String },
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EffectRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    pub effect_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CheckpointRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    /// Total order assigned by the sequencer: the durable log is totally
    /// ordered per instance, which is the substrate RD relies on (contrast
    /// LangGraph issue #8039, where `put_writes` and `put` race in a shared
    /// thread pool and recovery becomes host-dependent).
    pub seq: Seq,
}

/// CV gate. A production shim installs the framework's state schema here;
/// tests install a rejector for the schema-invalid marker.
pub trait CheckpointValidator {
    fn validate(&self, state: &[u8]) -> Result<(), String>;
}

pub struct AcceptAll;
impl CheckpointValidator for AcceptAll {
    fn validate(&self, _state: &[u8]) -> Result<(), String> {
        Ok(())
    }
}

/// The recovery decision. `SkipTo(t)` means: continue with task `t`; no task
/// below `t` re-executes and no effect below `t` re-fires.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Decision {
    SkipTo(TaskId),
}

/// RD: recovery is a pure function of the durable log. Two calls on equal
/// logs return equal decisions by construction (no `&mut`, no ambient
/// state, no randomness, no clock).
pub fn recover(log: &[CheckpointRecord]) -> Decision {
    let frontier = log
        .iter()
        .filter(|c| c.branch == BranchKey::root())
        .map(|c| c.task)
        .max()
        .unwrap_or(0);
    Decision::SkipTo(frontier + 1)
}

pub struct Remit<V: CheckpointValidator> {
    validator: V,
    ledger: Vec<EffectRecord>,
    fired: HashSet<(BranchKey, TaskId)>,
    ckpts: Vec<CheckpointRecord>,
    frontier: HashMap<BranchKey, TaskId>,
    branch_value: HashMap<BranchKey, String>,
    next_seq: Seq,
}

impl<V: CheckpointValidator> Remit<V> {
    pub fn new(validator: V) -> Self {
        Remit {
            validator,
            ledger: Vec::new(),
            fired: HashSet::new(),
            ckpts: Vec::new(),
            frontier: HashMap::new(),
            branch_value: HashMap::new(),
            next_seq: 0,
        }
    }

    /// EO/CO admission: a task's external effect may fire iff no effect
    /// record exists for (branch, task). On success the record is appended
    /// to the ledger atomically with admission.
    pub fn begin_effect(
        &mut self,
        branch: &BranchKey,
        task: TaskId,
        effect_id: &str,
    ) -> Result<(), RemitError> {
        let key = (branch.clone(), task);
        if self.fired.contains(&key) {
            return Err(RemitError::DuplicateEffect { task });
        }
        self.fired.insert(key);
        self.ledger.push(EffectRecord {
            branch: branch.clone(),
            task,
            effect_id: effect_id.to_string(),
        });
        Ok(())
    }

    /// PC + CV + sequencing: complete `task` on `branch`, persisting `state`.
    /// PC: `task` must be exactly frontier(branch)+1 -- the frontier is
    /// strictly monotone and completed work is never re-entered.
    /// CV: validation precedes any durable append; a rejected state persists
    /// nothing (contrast the silent persistence of schema-invalid state
    /// observed on LangGraph 1.2.9, issue #6491 class).
    pub fn commit_checkpoint(
        &mut self,
        branch: &BranchKey,
        task: TaskId,
        state: &[u8],
    ) -> Result<Seq, RemitError> {
        let f = *self.frontier.get(branch).unwrap_or(&0);
        if task != f + 1 {
            return Err(RemitError::PrefixViolation { frontier: f, attempted: task });
        }
        if let Err(reason) = self.validator.validate(state) {
            return Err(RemitError::InvalidCheckpoint { reason });
        }
        let seq = self.next_seq;
        self.next_seq += 1;
        self.ckpts.push(CheckpointRecord { branch: branch.clone(), task, seq });
        self.frontier.insert(branch.clone(), task);
        Ok(seq)
    }

    /// FD: a resume addressed to `checkpoint_id` with ordinal `resume_index`
    /// opens a distinct branch whose recorded value is exactly the supplied
    /// value. The second resume with a different value can never be answered
    /// with the first branch's outcome (contrast LangGraph issue #6663).
    pub fn fork(&mut self, checkpoint_id: &str, resume_index: u32, value: &str) -> BranchKey {
        let key = BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index,
        };
        self.branch_value.insert(key.clone(), value.to_string());
        key
    }

    /// The branch outcome source: the value the branch actually consumed.
    pub fn outcome(&self, branch: &BranchKey) -> Option<&str> {
        self.branch_value.get(branch).map(|s| s.as_str())
    }

    /// CO: a resume delivered to a completed run is inert -- it returns the
    /// continuation decision and, by construction, touches neither the
    /// ledger nor the frontier. (A re-fire attempt must go through
    /// `begin_effect`, where EO rejects it.)
    pub fn resume_completed(&self) -> Decision {
        recover(&self.ckpts)
    }

    pub fn ledger(&self) -> &[EffectRecord] {
        &self.ledger
    }

    pub fn checkpoints(&self) -> &[CheckpointRecord] {
        &self.ckpts
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct RejectNoneMarker;
    impl CheckpointValidator for RejectNoneMarker {
        fn validate(&self, state: &[u8]) -> Result<(), String> {
            if state == b"items=[None]" {
                Err(String::from("None in List[str] violates state schema"))
            } else {
                Ok(())
            }
        }
    }

    #[test]
    fn eo_duplicate_effect_rejected() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        assert!(r.begin_effect(&root, 1, "charge:1").is_ok());
        assert_eq!(
            r.begin_effect(&root, 1, "charge:1"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
        assert_eq!(r.ledger().len(), 1);
    }

    #[test]
    fn eo_crash_resume_exactly_once_counter_11_not_12() {
        // The CrewAI 1.15.2 receipt (probe 115, R2) and the TLC R1
        // counterexample share one arithmetic: s1 (+1) durable, crash in s2,
        // resume; exactly-once predicts 11, replay lands on 12. Under Remit
        // the replayed s1 effect is rejected and the counter is 11.
        let mut counter: u32 = 0;
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();

        r.begin_effect(&root, 1, "s1").unwrap();
        counter += 1; // s1 effect
        r.commit_checkpoint(&root, 1, b"counter=1").unwrap();
        // crash in s2; recover from the durable log:
        let d = recover(r.checkpoints());
        assert_eq!(d, Decision::SkipTo(2));
        // a faulty runtime replays s1; Remit rejects the duplicate effect:
        if r.begin_effect(&root, 1, "s1").is_ok() {
            counter += 1;
        }
        r.begin_effect(&root, 2, "s2").unwrap();
        counter += 10; // s2 effect
        r.commit_checkpoint(&root, 2, b"counter=11").unwrap();
        assert_eq!(counter, 11);
    }

    #[test]
    fn pc_prefix_regression_rejected() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        r.commit_checkpoint(&root, 1, b"ok").unwrap();
        r.commit_checkpoint(&root, 2, b"ok").unwrap();
        assert_eq!(
            r.commit_checkpoint(&root, 1, b"ok"),
            Err(RemitError::PrefixViolation { frontier: 2, attempted: 1 })
        );
        assert_eq!(r.checkpoints().len(), 2);
    }

    #[test]
    fn fd_second_resume_value_yields_distinct_branch_and_outcome() {
        let mut r = Remit::new(AcceptAll);
        let b1 = r.fork("ckpt-at-interrupt", 0, "va");
        let b2 = r.fork("ckpt-at-interrupt", 1, "vb");
        assert_ne!(b1, b2);
        assert_eq!(r.outcome(&b1), Some("va"));
        assert_eq!(r.outcome(&b2), Some("vb")); // never b1's outcome (#6663)
    }

    #[test]
    fn cv_invalid_state_rejected_and_nothing_persisted() {
        let mut r = Remit::new(RejectNoneMarker);
        let root = BranchKey::root();
        let err = r.commit_checkpoint(&root, 1, b"items=[None]");
        assert!(matches!(err, Err(RemitError::InvalidCheckpoint { .. })));
        assert_eq!(r.checkpoints().len(), 0); // loud and unpersisted (#6491)
    }

    #[test]
    fn co_stray_resume_on_completed_run_is_inert() {
        let mut r = Remit::new(AcceptAll);
        let root = BranchKey::root();
        r.begin_effect(&root, 1, "gated").unwrap();
        r.commit_checkpoint(&root, 1, b"done").unwrap();
        let before = r.ledger().len();
        let d = r.resume_completed(); // stray Command(resume=...) equivalent
        assert_eq!(d, Decision::SkipTo(2));
        assert_eq!(r.ledger().len(), before);
        assert_eq!(
            r.begin_effect(&root, 1, "gated"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
    }

    #[test]
    fn rd_recovery_is_a_pure_function_of_the_durable_log() {
        let root = BranchKey::root();
        let log = vec![
            CheckpointRecord { branch: root.clone(), task: 1, seq: 0 },
            CheckpointRecord { branch: root.clone(), task: 2, seq: 1 },
        ];
        assert_eq!(recover(&log), recover(&log));
        assert_eq!(recover(&log), Decision::SkipTo(3));
        assert_eq!(recover(&log[..1]), Decision::SkipTo(2));
    }
}
