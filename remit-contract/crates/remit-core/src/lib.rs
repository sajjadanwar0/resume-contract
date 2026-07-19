//! # remit-core
//!
//! Production core of REMIT — the reference resume sequencer and append-only
//! effect ledger for the **Resume Contract** (PC / EO / FD / CV / CO / RD),
//! introduced in *"Resume Means Resume: A Conformance Contract for
//! Checkpoint, Interrupt, and Resume Semantics in LLM-Agent Frameworks"*.
//!
//! ## Position in the verification chain
//!
//! This crate is the production twin of the machine-checked artifacts:
//!
//! | Layer | Artifact | Status |
//! |-------|----------|--------|
//! | Protocol spec | `formal/tla/ResumeContract.tla`, config `R0` | TLC: all six invariants, no error |
//! | Abstract model proof | `crates/remit/proof/remit_verus.rs` | Verus: 11 verified, 0 errors |
//! | CV/RD lemma set | `crates/remit/proof/remit_verus_cv_rd.rs` | Verus: 4 verified, 0 errors |
//! | **Production core** | **this crate** | mirrors the verified model item-for-item; conformance exercised by a property-test harness that transliterates the TLA+ transition relation (`tests/model_conformance.rs`) and by a concurrent stress suite (`tests/concurrency.rs`) |
//! | Language surface | `remit-py` (PyO3) | thin bindings; every semantic decision is a call into this crate |
//! | Framework adapter | `python/remit/langgraph_shim.py` | decision-free veneer: strips/keeps, raises/delegates, exactly as this crate instructs |
//!
//! The correspondence between each verified Verus lemma and the function here
//! that realizes it is tabulated in `VERIFICATION.md` at the repository root.
//! No mechanized refinement proof between the Verus model and this crate is
//! claimed; what is claimed, and checkable, is (i) an item-for-item structural
//! mirror, (ii) executable conformance of this crate to the model's transition
//! relation under property testing, and (iii) that the Python surface above it
//! contains no contract decision of its own.
//!
//! ## Invariant map (contract property → mechanism here)
//!
//! * **EO** (effect exactly-once) → [`Plane::begin_effect`]: ledger uniqueness
//!   on `(branch, task)`; a duplicate admission is refused and nothing is
//!   appended. (Verus: `lemma_begin_effect_admits_once`, `lemma_eo_no_duplicate`.)
//! * **CO** (consume-once) → [`Plane::resolve_resume`]: a resume without fork
//!   intent addressed to a consumed interrupt or a completed run is
//!   [`ResumeDecision::Inert`]; a re-fire attempt still has to pass
//!   [`Plane::begin_effect`], where EO refuses it.
//! * **PC** (prefix continuation) → [`Plane::commit_checkpoint`]: the durable
//!   frontier advances by exactly one and never re-enters the prefix.
//!   (Verus: `lemma_pc_strict_monotone`, `lemma_pc_no_prefix_reentry`.)
//! * **FD** (fork determinism) → [`Plane::fork`] / [`Plane::resolve_resume`]:
//!   branches keyed by `(checkpoint_id, resume_ordinal)`; the supplied value is
//!   the branch's recorded outcome source, so a second, different answer can
//!   never be served the first branch's outcome (contrast LangGraph #6663).
//!   (Verus: `lemma_fd_ordinal_injective`,
//!   `lemma_fd_distinct_values_served_distinctly`.)
//! * **CV** (checkpoint validity) → [`Plane::commit_checkpoint`] /
//!   [`Plane::validity_gate`]: validation precedes any durable append; a
//!   rejected state persists nothing and the rejection is loud (contrast the
//!   silent persistence of schema-invalid state, LangGraph #6491 class).
//!   (Verus: `lemma_cv_init`, `lemma_cv_gate_preserves`.)
//! * **RD** (recovery determinism) → [`recover`] / [`recovery_plan`]: recovery
//!   is a pure function of the durable log — no `&mut`, no ambient state, no
//!   randomness, no clock — and is invariant under reordering of
//!   same-superstep write sets. (Verus: `lemma_rd_functional`,
//!   `lemma_rd_order_independent`.) The per-thread [`Plane::sequence_op`]
//!   journal totally orders persistence submissions, the substrate whose
//!   absence produces LangGraph #8039.
//! * **FI** (fork-intent expressibility) → [`fork_view`]: the wire-level
//!   discriminator of the contract's Definition 2, exposed as a pure decision
//!   function so that adapters carry no discrimination logic of their own.

#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::Mutex;

pub type TaskId = u32;
pub type Seq = u64;

/// A branch of execution. The root branch is the primary run; every resume
/// carrying fork intent addressed to interrupt checkpoint `c` with ordinal `k`
/// names branch `(c, k)`. Distinct resume ordinals are distinct branches even
/// when the supplied values coincide — this is exactly the keying the Verus
/// lemma `lemma_fd_ordinal_injective` proves injective.
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

/// Contract-level failures. Every variant is loud by construction: the
/// operation that raises it has persisted nothing.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum RemitError {
    /// EO/CO: the `(branch, task)` effect already fired.
    DuplicateEffect { task: TaskId },
    /// PC: attempted to complete a task at or below the durable frontier
    /// (outside an explicit fork), or to skip ahead of it.
    PrefixViolation { frontier: TaskId, attempted: TaskId },
    /// CV: the state failed schema validation; nothing was persisted.
    InvalidCheckpoint { reason: String },
    /// Sequencer: a checkpoint commit was submitted for a superstep whose
    /// task-result writes were declared but not yet sequenced (the #8039
    /// hazard window, refused rather than raced).
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
    /// Total order assigned by the sequencer. The durable log is totally
    /// ordered per plane, which is the substrate RD relies on (contrast
    /// LangGraph #8039, where `put_writes` and `put` race in a shared thread
    /// pool and durable order becomes schedule-dependent).
    pub seq: Seq,
}

/// One journaled persistence submission. The journal is the sequencer's
/// receipt: recovery is a pure function of it, and its total order is
/// assigned under the plane's lock at submission time.
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

/// CV gate. A production adapter installs the framework's state schema here;
/// tests install a rejector for the schema-invalid marker. The *decision* —
/// invalid implies loud error and nothing persisted — lives in
/// [`Plane::commit_checkpoint`] and [`Plane::validity_gate`], not in the
/// validator: the validator only answers, the core enforces.
pub trait CheckpointValidator: Send {
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
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Decision {
    SkipTo(TaskId),
}

/// Per-task recovery plan derived from the durable log and the effect ledger.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskPlan {
    /// Task completed and its effect is on the ledger: serve from record.
    Skip(TaskId),
    /// Task is at or beyond the frontier: execute.
    Execute(TaskId),
}

/// **RD (functional):** recovery is a pure function of the durable log. Two
/// calls on equal logs return equal decisions by construction — no `&mut`,
/// no ambient state, no randomness, no clock. This is the executable twin of
/// the Verus lemma `lemma_rd_functional`.
pub fn recover(log: &[CheckpointRecord]) -> Decision {
    let frontier = log
        .iter()
        .filter(|c| c.branch == BranchKey::root())
        .map(|c| c.task)
        .max()
        .unwrap_or(0);
    Decision::SkipTo(frontier + 1)
}

/// **RD (order-independent):** the decision is invariant under any
/// permutation of the log — the executable twin of
/// `lemma_rd_order_independent`. Exposed so harnesses can check it directly
/// against shuffled same-content logs (the construction of probes 118/128).
pub fn recover_is_order_independent(log: &[CheckpointRecord], permuted: &[CheckpointRecord]) -> bool {
    let mut a: Vec<_> = log.to_vec();
    let mut b: Vec<_> = permuted.to_vec();
    let key = |c: &CheckpointRecord| (c.branch.clone(), c.task, c.seq);
    a.sort_by_key(key);
    b.sort_by_key(key);
    a == b && recover(log) == recover(permuted)
}

/// Per-task plan: tasks strictly below `SkipTo` are served from the durable
/// record (memoized replay conforms to PC provided every prefix effect is
/// served from the ledger — which [`Plane::begin_effect`] enforces); the
/// frontier task executes.
pub fn recovery_plan(log: &[CheckpointRecord], total_tasks: TaskId) -> Vec<TaskPlan> {
    let Decision::SkipTo(next) = recover(log);
    (1..=total_tasks)
        .map(|t| if t < next { TaskPlan::Skip(t) } else { TaskPlan::Execute(t) })
        .collect()
}

/// How a resume is addressed on the wire — the contract's Definition 2
/// discriminators, plus the ordinary (retry / first-consumption) address.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum AddressKind {
    /// No discriminator: first consumption, transport retry, or stray.
    Ordinary,
    /// The framework's documented branch-creating address (Definition 2,
    /// clause 3) — for LangGraph, an explicit `checkpoint_id` in the config.
    ExplicitCheckpoint,
    /// An explicit fork flag on the wire (Definition 2, clause 2) — the
    /// production discriminator, safe under subgraph-internal checkpoint-id
    /// plumbing.
    ForkFlag,
}

/// The core's answer to "which value does this invocation get?" — every
/// adapter maps these three variants mechanically and adds nothing.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResumeDecision {
    /// Fork intent present: serve the invocation's own value on a fresh
    /// branch keyed `(checkpoint_id, resume_ordinal)` (FD).
    ServeSupplied { branch: BranchKey },
    /// Recovery replay of an already-consumed interrupt: serve the recorded
    /// value, memoized (PC's memoized-replay clause; EO preserved because
    /// effects are served from the ledger).
    ServeRecorded { value: String },
    /// CO: a discriminator-free delivery to a consumed interrupt or a
    /// completed run is inert with respect to effects.
    Inert,
}

/// **FI / probe-134 rule as a pure function.** Given how the invocation is
/// addressed and whether the loaded checkpoint carries recorded resume
/// writes, decide what view of those writes task preparation may see:
///
/// * fork intent (explicit-checkpoint address or fork flag) **and** recorded
///   writes present → [`ViewDecision::StripRecordedResumes`] — the recorded
///   value must not shadow the newly supplied one (this is the read-path
///   repair of LangGraph #6663, probe 134);
/// * otherwise → [`ViewDecision::KeepRecorded`] — ordinary-address replay
///   idempotence and consume-once are untouched (probe 134 cells T2/T3).
///
/// The LangGraph adapter calls this function and does exactly what it says;
/// the adapter itself contains no branch on contract semantics.
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

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
struct InterruptState {
    /// Every recorded resume value, in ordinal order. Both values of a fork
    /// are durably recorded — recording is not the defect; consulting the
    /// wrong one is (the source finding of the paper's Section 4.3).
    recorded: Vec<String>,
    consumed: bool,
}

/// One resume plane: the state the contract's Definition 1 quantifies over,
/// for a single thread/run. [`RemitCore`] multiplexes planes by thread id and
/// serializes access.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct Plane {
    ledger: Vec<EffectRecord>,
    fired: HashSet<(BranchKey, TaskId)>,
    ckpts: Vec<CheckpointRecord>,
    frontier: HashMap<BranchKey, TaskId>,
    branch_value: HashMap<BranchKey, String>,
    interrupts: BTreeMap<String, InterruptState>,
    journal: Vec<OpRecord>,
    /// Supersteps with declared-but-unsequenced task-result writes; a `Put`
    /// for such a superstep is refused (the #8039 barrier).
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

    /// **EO/CO admission.** A task's external effect may fire iff no effect
    /// record exists for `(branch, task)`. On success the record is appended
    /// to the ledger atomically with admission; on refusal nothing changes.
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

    /// **PC + CV + sequencing.** Complete `task` on `branch`, persisting
    /// `state`. PC: `task` must be exactly `frontier(branch) + 1` — the
    /// frontier is strictly monotone and completed work is never re-entered.
    /// CV: validation precedes any durable append; a rejected state persists
    /// nothing and the rejection is loud.
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

    /// **CV gate for adapters** that cannot map framework checkpoints onto
    /// contract task numbers (the LangGraph shim's `put`): the framework-side
    /// validator has already answered; the core enforces. `valid == false`
    /// journals a loud rejection and returns the error the adapter must
    /// raise; nothing is marked durable.
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

    /// **Sequencer.** Assign the next position in the plane's total order to
    /// a persistence submission and journal it. `PutWrites` for superstep `s`
    /// clears the barrier a later `Put` for `s` would otherwise trip.
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

    /// Declare that superstep `s` will submit task-result writes; a
    /// checkpoint commit for `s` submitted before those writes are sequenced
    /// is refused ([`RemitError::OrderViolation`]) instead of raced — the
    /// #8039 window closed by construction rather than by luck.
    pub fn declare_writes(&mut self, superstep: TaskId) {
        self.pending_writes.insert(superstep);
    }

    /// **FD.** A resume carrying fork intent addressed to `checkpoint_id`
    /// opens a distinct branch keyed by the next resume ordinal, whose
    /// recorded value is exactly the supplied value. The second resume with a
    /// different value can never be answered with the first branch's outcome.
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

    /// The branch outcome source: the value the branch actually consumed.
    /// `outcome(fork(c, v)) == v` — the executable face of
    /// `lemma_fd_distinct_values_served_distinctly`.
    pub fn outcome(&self, branch: &BranchKey) -> Option<&str> {
        self.branch_value.get(branch).map(|s| s.as_str())
    }

    /// **The interrupt lifecycle, decided in one place.** See
    /// [`ResumeDecision`]; adapters map the three variants mechanically.
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
                // Fork intent with nothing supplied degenerates to replay.
                None => self.replay_or_inert(checkpoint_id),
            },
            AddressKind::Ordinary => {
                let (consumed, completed) = {
                    let ist = self.interrupts.entry(checkpoint_id.to_string()).or_default();
                    (ist.consumed, self.completed)
                };
                if completed || consumed {
                    // CO: discriminator-free re-delivery is inert w.r.t.
                    // effects. (Recovery replay goes through
                    // `recovery_replay`, not here.)
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

    /// **PC's memoized-replay clause.** During recovery re-traversal, an
    /// already-consumed interrupt is served its recorded value from the
    /// durable record — never re-asked, never re-fired (the effect admission
    /// still goes through [`Plane::begin_effect`], where EO refuses
    /// duplicates).
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

    /// Mark the run completed; subsequent [`AddressKind::Ordinary`] resumes
    /// are inert (CO), while fork-intent resumes still open branches (FD).
    pub fn complete(&mut self) {
        self.completed = true;
    }

    /// **CO on the completed run:** a stray resume returns the continuation
    /// decision and, by construction, touches neither the ledger nor the
    /// frontier.
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

/// Thread-safe multiplexer of [`Plane`]s, keyed by thread id — the object the
/// PyO3 surface exposes. Every operation takes the core lock, so concurrent
/// submissions from framework executor pools acquire a total order at the
/// core boundary; the concurrency suite (`tests/concurrency.rs`) hammers this
/// from many OS threads and re-checks every invariant afterward.
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
        // The CrewAI 1.15.2 receipt (probe 115, TLC run R1) in one test:
        // s1 (+1) durable, crash in s2, resume; exactly-once predicts 11,
        // replay lands on 12. Under the core the replayed s1 effect is
        // refused and the counter is 11.
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
        assert_eq!(p.outcome(&b2), Some("vb")); // never b1's outcome (#6663)
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
        assert_eq!(p.checkpoints().len(), 0); // loud and unpersisted (#6491)
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
        assert_eq!(fork_view(true, false, true), StripRecordedResumes); // T1/T1b
        assert_eq!(fork_view(false, true, true), StripRecordedResumes); // prod flag
        assert_eq!(fork_view(false, false, true), KeepRecorded); // replay / T3
        assert_eq!(fork_view(true, false, false), KeepRecorded); // nothing to strip
        assert_eq!(fork_view(false, false, false), KeepRecorded);
    }
}
