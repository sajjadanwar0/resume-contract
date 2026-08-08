
#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Mutex;

pub type TaskId = u32;
pub type Seq = u64;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct BranchKey {
    pub checkpoint_id: String,
    pub resume_index: u32,
}

impl BranchKey {
    pub fn root() -> Self {
        BranchKey {
            checkpoint_id: String::from("root"),
            resume_index: 0,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum RemitError {
    DuplicateEffect { task: TaskId },
    PrefixViolation { frontier: TaskId, attempted: TaskId },
    InvalidCheckpoint { reason: String },
    OrderViolation { superstep: TaskId },
}

impl std::fmt::Display for RemitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RemitError::DuplicateEffect { task } => {
                write!(f, "EO/CO violation refused: effect for task {task} already fired on this branch")
            }
            RemitError::PrefixViolation { frontier, attempted } => write!(
                f,
                "PC violation refused: frontier is {frontier}, attempted commit of task {attempted}"
            ),
            RemitError::InvalidCheckpoint { reason } => {
                write!(f, "CV violation refused (nothing persisted): {reason}")
            }
            RemitError::OrderViolation { superstep } => write!(
                f,
                "RD ordering refused: checkpoint for superstep {superstep} submitted before its declared writes were sequenced"
            ),
        }
    }
}

impl std::error::Error for RemitError {}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct EffectRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    pub effect_id: String,
    pub seq: Seq,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckpointRecord {
    pub branch: BranchKey,
    pub task: TaskId,
    pub seq: Seq,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpRecord {
    pub seq: Seq,
    pub kind: OpKind,
    pub ref_id: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum OpKind {
    PutWrites,
    Put,
    ValidityRejected,
    EffectAdmitted,
}

pub trait CheckpointValidator: Send {
    fn validate(&self, state: &[u8]) -> Result<(), String>;
}

pub struct AcceptAll;
impl CheckpointValidator for AcceptAll {
    fn validate(&self, _state: &[u8]) -> Result<(), String> {
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Decision {
    SkipTo(TaskId),
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskPlan {
    Skip(TaskId),
    Execute(TaskId),
}

pub fn recover(log: &[CheckpointRecord]) -> Decision {
    let frontier = log
        .iter()
        .filter(|c| c.branch == BranchKey::root())
        .map(|c| c.task)
        .max()
        .unwrap_or(0);
    Decision::SkipTo(frontier + 1)
}

pub fn recover_is_order_independent(log: &[CheckpointRecord], permuted: &[CheckpointRecord]) -> bool {
    let mut a: Vec<_> = log.to_vec();
    let mut b: Vec<_> = permuted.to_vec();
    let key = |c: &CheckpointRecord| (c.branch.clone(), c.task, c.seq);
    a.sort_by_key(key);
    b.sort_by_key(key);
    a == b && recover(log) == recover(permuted)
}

pub fn recovery_plan(log: &[CheckpointRecord], total_tasks: TaskId) -> Vec<TaskPlan> {
    let Decision::SkipTo(next) = recover(log);
    (1..=total_tasks)
        .map(|t| if t < next { TaskPlan::Skip(t) } else { TaskPlan::Execute(t) })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AddressKind {
    Ordinary,
    ExplicitCheckpoint,
    ForkFlag,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResumeDecision {
    ServeSupplied { branch: BranchKey },
    ServeRecorded { value: String },
    Inert,
}

pub fn fork_view(explicit_checkpoint_address: bool, fork_flag: bool, has_recorded_resumes: bool) -> ViewDecision {
    if (explicit_checkpoint_address || fork_flag) && has_recorded_resumes {
        ViewDecision::StripRecordedResumes
    } else {
        ViewDecision::KeepRecorded
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ViewDecision {
    StripRecordedResumes,
    KeepRecorded,
}

pub fn consume_view(
    has_pending_interrupt: bool,
    gate_enabled: bool,
    fork_intent: bool,
    inspect_intent: bool,
) -> ConsumeDecision {
    if has_pending_interrupt && gate_enabled && !fork_intent && !inspect_intent {
        ConsumeDecision::AttemptClaim
    } else {
        ConsumeDecision::Pass
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ConsumeDecision {
    AttemptClaim,
    Pass,
}

pub fn consume_claim_verdict(claim_won: bool) -> ClaimVerdict {
    if claim_won {
        ClaimVerdict::Serve
    } else {
        ClaimVerdict::Conflict
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ClaimVerdict {
    Serve,
    Conflict,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
struct InterruptState {
    recorded: Vec<String>,
    consumed: bool,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Plane {
    ledger: Vec<EffectRecord>,
    fired: HashSet<(BranchKey, TaskId)>,
    ckpts: Vec<CheckpointRecord>,
    frontier: HashMap<BranchKey, TaskId>,
    branch_value: HashMap<BranchKey, String>,
    interrupts: BTreeMap<String, InterruptState>,
    journal: Vec<OpRecord>,
    pending_writes: HashSet<TaskId>,
    completed: bool,
    next_seq: Seq,
}

impl Plane {
    pub fn new() -> Self {
        Plane::default()
    }

    fn bump(&mut self) -> Seq {
        let s = self.next_seq;
        self.next_seq += 1;
        s
    }

    pub fn begin_effect(
        &mut self,
        branch: &BranchKey,
        task: TaskId,
        effect_id: &str,
    ) -> Result<Seq, RemitError> {
        let key = (branch.clone(), task);
        if self.fired.contains(&key) {
            return Err(RemitError::DuplicateEffect { task });
        }
        let seq = self.bump();
        self.fired.insert(key);
        self.ledger.push(EffectRecord {
            branch: branch.clone(),
            task,
            effect_id: effect_id.to_string(),
            seq,
        });
        self.journal.push(OpRecord {
            seq,
            kind: OpKind::EffectAdmitted,
            ref_id: format!("{}:{}", task, effect_id),
        });
        Ok(seq)
    }

    pub fn commit_checkpoint<V: CheckpointValidator + ?Sized>(
        &mut self,
        validator: &V,
        branch: &BranchKey,
        task: TaskId,
        state: &[u8],
    ) -> Result<Seq, RemitError> {
        let f = *self.frontier.get(branch).unwrap_or(&0);
        if task != f + 1 {
            return Err(RemitError::PrefixViolation { frontier: f, attempted: task });
        }
        if let Err(reason) = validator.validate(state) {
            let seq = self.bump();
            self.journal.push(OpRecord {
                seq,
                kind: OpKind::ValidityRejected,
                ref_id: reason.clone(),
            });
            return Err(RemitError::InvalidCheckpoint { reason });
        }
        if self.pending_writes.contains(&task) {
            return Err(RemitError::OrderViolation { superstep: task });
        }
        let seq = self.bump();
        self.ckpts.push(CheckpointRecord { branch: branch.clone(), task, seq });
        self.frontier.insert(branch.clone(), task);
        self.journal.push(OpRecord { seq, kind: OpKind::Put, ref_id: task.to_string() });
        Ok(seq)
    }

    pub fn validity_gate(&mut self, valid: bool, reason: &str) -> Result<Seq, RemitError> {
        if !valid {
            let seq = self.bump();
            self.journal.push(OpRecord {
                seq,
                kind: OpKind::ValidityRejected,
                ref_id: reason.to_string(),
            });
            return Err(RemitError::InvalidCheckpoint { reason: reason.to_string() });
        }
        Ok(self.bump())
    }

    pub fn sequence_op(&mut self, kind: OpKind, ref_id: &str) -> Seq {
        let seq = self.bump();
        if kind == OpKind::PutWrites {
            if let Ok(t) = ref_id.parse::<TaskId>() {
                self.pending_writes.remove(&t);
            }
        }
        self.journal.push(OpRecord { seq, kind, ref_id: ref_id.to_string() });
        seq
    }

    pub fn declare_writes(&mut self, superstep: TaskId) {
        self.pending_writes.insert(superstep);
    }

    pub fn fork(&mut self, checkpoint_id: &str, value: &str) -> BranchKey {
        let ist = self.interrupts.entry(checkpoint_id.to_string()).or_default();
        let ordinal = ist.recorded.len() as u32;
        ist.recorded.push(value.to_string());
        ist.consumed = true;
        let key = BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index: ordinal,
        };
        self.branch_value.insert(key.clone(), value.to_string());
        key
    }

    pub fn outcome(&self, branch: &BranchKey) -> Option<&str> {
        self.branch_value.get(branch).map(|s| s.as_str())
    }

    pub fn resolve_resume(
        &mut self,
        checkpoint_id: &str,
        supplied: Option<&str>,
        kind: AddressKind,
    ) -> ResumeDecision {
        match kind {
            AddressKind::ExplicitCheckpoint | AddressKind::ForkFlag => match supplied {
                Some(v) => ResumeDecision::ServeSupplied {
                    branch: self.fork(checkpoint_id, v),
                },
                None => self.replay_or_inert(checkpoint_id),
            },
            AddressKind::Ordinary => {
                let (consumed, completed) = {
                    let ist = self.interrupts.entry(checkpoint_id.to_string()).or_default();
                    (ist.consumed, self.completed)
                };
                if completed || consumed {
                    ResumeDecision::Inert
                } else {
                    match supplied {
                        Some(v) => {
                            let ist = self.interrupts.get_mut(checkpoint_id).expect("just inserted");
                            ist.consumed = true;
                            ist.recorded.push(v.to_string());
                            let key = BranchKey {
                                checkpoint_id: checkpoint_id.to_string(),
                                resume_index: 0,
                            };
                            self.branch_value.insert(key.clone(), v.to_string());
                            ResumeDecision::ServeSupplied { branch: key }
                        }
                        None => ResumeDecision::Inert,
                    }
                }
            }
        }
    }

    pub fn recovery_replay(&mut self, checkpoint_id: &str) -> ResumeDecision {
        self.replay_or_inert(checkpoint_id)
    }

    fn replay_or_inert(&mut self, checkpoint_id: &str) -> ResumeDecision {
        let ist = self.interrupts.entry(checkpoint_id.to_string()).or_default();
        match ist.recorded.first() {
            Some(v) => ResumeDecision::ServeRecorded { value: v.clone() },
            None => ResumeDecision::Inert,
        }
    }

    pub fn complete(&mut self) {
        self.completed = true;
    }

    pub fn resume_completed(&self) -> Decision {
        recover(&self.ckpts)
    }

    pub fn ledger(&self) -> &[EffectRecord] {
        &self.ledger
    }

    pub fn checkpoints(&self) -> &[CheckpointRecord] {
        &self.ckpts
    }

    pub fn journal(&self) -> &[OpRecord] {
        &self.journal
    }

    pub fn frontier(&self, branch: &BranchKey) -> TaskId {
        *self.frontier.get(branch).unwrap_or(&0)
    }

    pub fn recorded_resumes(&self, checkpoint_id: &str) -> Vec<String> {
        self.interrupts
            .get(checkpoint_id)
            .map(|i| i.recorded.clone())
            .unwrap_or_default()
    }
}

pub struct RemitCore {
    planes: Mutex<HashMap<String, Plane>>,
}

impl Default for RemitCore {
    fn default() -> Self {
        Self::new()
    }
}

impl RemitCore {
    pub fn new() -> Self {
        RemitCore {
            planes: Mutex::new(HashMap::new()),
        }
    }

    pub fn with_plane<R>(&self, thread: &str, f: impl FnOnce(&mut Plane) -> R) -> R {
        let mut g = self.planes.lock().expect("remit core lock poisoned");
        let plane = g.entry(thread.to_string()).or_default();
        f(plane)
    }

    pub fn snapshot_json(&self) -> String {
        let g = self.planes.lock().expect("remit core lock poisoned");
        serde_json::to_string_pretty(&*g).unwrap_or_else(|e| format!("{{\"error\":\"{e}\"}}"))
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
        let mut p = Plane::new();
        let root = BranchKey::root();
        assert!(p.begin_effect(&root, 1, "charge:1").is_ok());
        assert_eq!(
            p.begin_effect(&root, 1, "charge:1"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
        assert_eq!(p.ledger().len(), 1);
    }

    #[test]
    fn eo_crash_resume_exactly_once_counter_11_not_12() {
        let mut counter: u32 = 0;
        let mut p = Plane::new();
        let root = BranchKey::root();
        p.begin_effect(&root, 1, "s1").unwrap();
        counter += 1;
        p.commit_checkpoint(&AcceptAll, &root, 1, b"counter=1").unwrap();
        let d = recover(p.checkpoints());
        assert_eq!(d, Decision::SkipTo(2));
        if p.begin_effect(&root, 1, "s1").is_ok() {
            counter += 1;
        }
        p.begin_effect(&root, 2, "s2").unwrap();
        counter += 10;
        p.commit_checkpoint(&AcceptAll, &root, 2, b"counter=11").unwrap();
        assert_eq!(counter, 11);
    }

    #[test]
    fn pc_prefix_regression_rejected() {
        let mut p = Plane::new();
        let root = BranchKey::root();
        p.commit_checkpoint(&AcceptAll, &root, 1, b"ok").unwrap();
        p.commit_checkpoint(&AcceptAll, &root, 2, b"ok").unwrap();
        assert_eq!(
            p.commit_checkpoint(&AcceptAll, &root, 1, b"ok"),
            Err(RemitError::PrefixViolation { frontier: 2, attempted: 1 })
        );
        assert_eq!(p.checkpoints().len(), 2);
    }

    #[test]
    fn fd_second_resume_value_yields_distinct_branch_and_outcome() {
        let mut p = Plane::new();
        let b1 = p.fork("ckpt-at-interrupt", "va");
        let b2 = p.fork("ckpt-at-interrupt", "vb");
        assert_ne!(b1, b2);
        assert_eq!(p.outcome(&b1), Some("va"));
        assert_eq!(p.outcome(&b2), Some("vb"));
    }

    #[test]
    fn fd_resolve_resume_serves_supplied_under_fork_intent() {
        let mut p = Plane::new();
        let d1 = p.resolve_resume("c1", Some("True"), AddressKind::ExplicitCheckpoint);
        let d2 = p.resolve_resume("c1", Some("False"), AddressKind::ExplicitCheckpoint);
        let (b1, b2) = match (d1, d2) {
            (ResumeDecision::ServeSupplied { branch: a }, ResumeDecision::ServeSupplied { branch: b }) => (a, b),
            other => panic!("expected two ServeSupplied, got {other:?}"),
        };
        assert_ne!(b1, b2);
        assert_eq!(p.outcome(&b1), Some("True"));
        assert_eq!(p.outcome(&b2), Some("False"));
    }

    #[test]
    fn cv_invalid_state_rejected_and_nothing_persisted() {
        let mut p = Plane::new();
        let root = BranchKey::root();
        let err = p.commit_checkpoint(&RejectNoneMarker, &root, 1, b"items=[None]");
        assert!(matches!(err, Err(RemitError::InvalidCheckpoint { .. })));
        assert_eq!(p.checkpoints().len(), 0);
    }

    #[test]
    fn co_stray_resume_on_completed_run_is_inert() {
        let mut p = Plane::new();
        let root = BranchKey::root();
        p.resolve_resume("gate", Some("yes"), AddressKind::Ordinary);
        p.begin_effect(&root, 1, "gated").unwrap();
        p.commit_checkpoint(&AcceptAll, &root, 1, b"done").unwrap();
        p.complete();
        let before = p.ledger().len();
        assert_eq!(
            p.resolve_resume("gate", Some("yes"), AddressKind::Ordinary),
            ResumeDecision::Inert
        );
        assert_eq!(p.ledger().len(), before);
        assert_eq!(
            p.begin_effect(&root, 1, "gated"),
            Err(RemitError::DuplicateEffect { task: 1 })
        );
    }

    #[test]
    fn rd_recovery_is_a_pure_order_independent_function() {
        let root = BranchKey::root();
        let log = vec![
            CheckpointRecord { branch: root.clone(), task: 1, seq: 0 },
            CheckpointRecord { branch: root.clone(), task: 2, seq: 1 },
        ];
        let permuted = vec![log[1].clone(), log[0].clone()];
        assert_eq!(recover(&log), recover(&log));
        assert_eq!(recover(&log), Decision::SkipTo(3));
        assert!(recover_is_order_independent(&log, &permuted));
    }

    #[test]
    fn rd_sequencer_refuses_put_before_declared_writes() {
        let mut p = Plane::new();
        let root = BranchKey::root();
        p.declare_writes(1);
        assert_eq!(
            p.commit_checkpoint(&AcceptAll, &root, 1, b"s"),
            Err(RemitError::OrderViolation { superstep: 1 })
        );
        p.sequence_op(OpKind::PutWrites, "1");
        assert!(p.commit_checkpoint(&AcceptAll, &root, 1, b"s").is_ok());
    }

    #[test]
    fn fi_fork_view_matches_probe_134() {
        use ViewDecision::*;
        assert_eq!(fork_view(true, false, true), StripRecordedResumes);
        assert_eq!(fork_view(false, true, true), StripRecordedResumes);
        assert_eq!(fork_view(false, false, true), KeepRecorded);
        assert_eq!(fork_view(true, false, false), KeepRecorded);
        assert_eq!(fork_view(false, false, false), KeepRecorded);
    }

    #[test]
    fn co_consume_view_matches_probe_165() {
        use ConsumeDecision::*;
        assert_eq!(consume_view(true, true, false, false), AttemptClaim);
        assert_eq!(consume_view(true, true, true, false), Pass);
        assert_eq!(consume_view(true, true, false, true), Pass);
        assert_eq!(consume_view(false, true, false, false), Pass);
        assert_eq!(consume_view(true, false, false, false), Pass);
    }

    #[test]
    fn co_claim_verdict_is_the_cas_outcome() {
        assert_eq!(consume_claim_verdict(true), ClaimVerdict::Serve);
        assert_eq!(consume_claim_verdict(false), ClaimVerdict::Conflict);
    }
}
