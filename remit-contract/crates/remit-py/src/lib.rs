//! PyO3 surface for `remit-core`, published as the extension module
//! `remit._core`.
//!
//! Design rule, stated once and enforced by review: **this file makes no
//! contract decision.** It translates Python types to `remit-core` types,
//! calls the core, and translates results and errors back. If a semantic
//! question ("is this a fork?", "may this effect fire?", "is this state
//! persistable?", "what does recovery do?") appears to be answered here,
//! that is a bug — the answer must come from `remit-core`, whose abstract
//! model is the Verus-verified one.

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use remit_core as core;

create_exception!(remit._core, RemitError, PyException, "Base class for Resume Contract violations refused by the core.");
create_exception!(remit._core, RemitDuplicateEffect, RemitError, "EO/CO: the (branch, task) effect already fired; the duplicate was refused.");
create_exception!(remit._core, RemitPrefixViolation, RemitError, "PC: attempted to re-enter or skip the durable prefix; the commit was refused.");
create_exception!(remit._core, RemitValidityError, RemitError, "CV: the state failed schema validation; nothing was persisted (loud, not silent).");
create_exception!(remit._core, RemitOrderViolation, RemitError, "RD: a checkpoint was submitted before its declared writes were sequenced (#8039 window); refused.");
create_exception!(remit._core, RemitConsumeConflict, RemitError, "CO (cross-process): the parked interrupt's consumption was already claimed by another process; this invocation was refused before any node executed.");

fn raise(e: core::RemitError) -> PyErr {
    let msg = e.to_string();
    match e {
        core::RemitError::DuplicateEffect { .. } => RemitDuplicateEffect::new_err(msg),
        core::RemitError::PrefixViolation { .. } => RemitPrefixViolation::new_err(msg),
        core::RemitError::InvalidCheckpoint { .. } => RemitValidityError::new_err(msg),
        core::RemitError::OrderViolation { .. } => RemitOrderViolation::new_err(msg),
    }
}

fn branch_dict<'py>(py: Python<'py>, b: &core::BranchKey) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("checkpoint_id", &b.checkpoint_id)?;
    d.set_item("resume_index", b.resume_index)?;
    Ok(d)
}

struct PyValidator {
    valid: bool,
    reason: String,
}
impl core::CheckpointValidator for PyValidator {
    fn validate(&self, _state: &[u8]) -> Result<(), String> {
        if self.valid {
            Ok(())
        } else {
            Err(self.reason.clone())
        }
    }
}

/// Thread-safe handle over the Rust core. One `Core` serves any number of
/// Python threads and any number of resume planes (keyed by thread id).
#[pyclass(name = "Core", module = "remit._core")]
struct PyCore {
    inner: core::RemitCore,
}

#[pymethods]
impl PyCore {
    #[new]
    fn new() -> Self {
        PyCore { inner: core::RemitCore::new() }
    }

    /// EO/CO admission. Returns the ledger sequence number on the first
    /// admission; raises `RemitDuplicateEffect` on any repeat for the same
    /// (branch, task) — the duplicate fires nothing and records nothing.
    #[pyo3(signature = (thread, task, effect_id, checkpoint_id="root", resume_index=0))]
    fn begin_effect(
        &self,
        thread: &str,
        task: u32,
        effect_id: &str,
        checkpoint_id: &str,
        resume_index: u32,
    ) -> PyResult<u64> {
        let b = core::BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index,
        };
        self.inner
            .with_plane(thread, |p| p.begin_effect(&b, task, effect_id))
            .map_err(raise)
    }

    /// PC + CV commit. `valid`/`reason` carry the framework-side validator's
    /// answer; the enforcement decision (invalid ⇒ loud error, nothing
    /// persisted; frontier advances by exactly one) is the core's.
    #[pyo3(signature = (thread, task, state, valid=true, reason="", checkpoint_id="root", resume_index=0))]
    #[allow(clippy::too_many_arguments)]
    fn commit_checkpoint(
        &self,
        thread: &str,
        task: u32,
        state: &[u8],
        valid: bool,
        reason: &str,
        checkpoint_id: &str,
        resume_index: u32,
    ) -> PyResult<u64> {
        let b = core::BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index,
        };
        let v = PyValidator { valid, reason: reason.to_string() };
        self.inner
            .with_plane(thread, |p| p.commit_checkpoint(&v, &b, task, state))
            .map_err(raise)
    }

    /// CV gate for adapters that cannot map framework checkpoints onto
    /// contract task numbers (the LangGraph shim's `put`). Raises
    /// `RemitValidityError` when `valid` is false, after journaling the loud
    /// rejection; returns the journal sequence number when valid.
    #[pyo3(signature = (thread, valid, reason=""))]
    fn validity_gate(&self, thread: &str, valid: bool, reason: &str) -> PyResult<u64> {
        self.inner
            .with_plane(thread, |p| p.validity_gate(valid, reason))
            .map_err(raise)
    }

    /// FD: open a fork branch for `checkpoint_id` with the supplied value;
    /// returns `{"checkpoint_id", "resume_index"}` of the fresh branch.
    fn fork<'py>(&self, py: Python<'py>, thread: &str, checkpoint_id: &str, value: &str) -> PyResult<Bound<'py, PyDict>> {
        let b = self.inner.with_plane(thread, |p| p.fork(checkpoint_id, value));
        branch_dict(py, &b)
    }

    /// The branch outcome source: the value the branch actually consumed.
    #[pyo3(signature = (thread, checkpoint_id, resume_index))]
    fn outcome(&self, thread: &str, checkpoint_id: &str, resume_index: u32) -> Option<String> {
        let b = core::BranchKey {
            checkpoint_id: checkpoint_id.to_string(),
            resume_index,
        };
        self.inner.with_plane(thread, |p| p.outcome(&b).map(|s| s.to_string()))
    }

    /// The interrupt lifecycle, decided in one place. `kind` is one of
    /// `"ordinary"`, `"explicit_checkpoint"`, `"fork_flag"`. Returns
    /// `{"decision": "serve_supplied"|"serve_recorded"|"inert", ...}`.
    #[pyo3(signature = (thread, checkpoint_id, supplied=None, kind="ordinary"))]
    fn resolve_resume<'py>(
        &self,
        py: Python<'py>,
        thread: &str,
        checkpoint_id: &str,
        supplied: Option<&str>,
        kind: &str,
    ) -> PyResult<Bound<'py, PyDict>> {
        let k = match kind {
            "ordinary" => core::AddressKind::Ordinary,
            "explicit_checkpoint" => core::AddressKind::ExplicitCheckpoint,
            "fork_flag" => core::AddressKind::ForkFlag,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown address kind {other:?}; expected ordinary | explicit_checkpoint | fork_flag"
                )))
            }
        };
        let d = self
            .inner
            .with_plane(thread, |p| p.resolve_resume(checkpoint_id, supplied, k));
        let out = PyDict::new(py);
        match d {
            core::ResumeDecision::ServeSupplied { branch } => {
                out.set_item("decision", "serve_supplied")?;
                out.set_item("branch", branch_dict(py, &branch)?)?;
            }
            core::ResumeDecision::ServeRecorded { value } => {
                out.set_item("decision", "serve_recorded")?;
                out.set_item("value", value)?;
            }
            core::ResumeDecision::Inert => {
                out.set_item("decision", "inert")?;
            }
        }
        Ok(out)
    }

    /// PC's memoized-replay clause: serve a consumed interrupt's recorded
    /// value during recovery re-traversal.
    fn recovery_replay<'py>(&self, py: Python<'py>, thread: &str, checkpoint_id: &str) -> PyResult<Bound<'py, PyDict>> {
        let d = self.inner.with_plane(thread, |p| p.recovery_replay(checkpoint_id));
        let out = PyDict::new(py);
        match d {
            core::ResumeDecision::ServeRecorded { value } => {
                out.set_item("decision", "serve_recorded")?;
                out.set_item("value", value)?;
            }
            _ => {
                out.set_item("decision", "inert")?;
            }
        }
        Ok(out)
    }

    /// RD sequencer: journal a persistence submission (`kind` is
    /// `"put_writes"` or `"put"`) and return its position in the plane's
    /// total order.
    fn sequence_op(&self, thread: &str, kind: &str, ref_id: &str) -> PyResult<u64> {
        let k = match kind {
            "put_writes" => core::OpKind::PutWrites,
            "put" => core::OpKind::Put,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown op kind {other:?}; expected put_writes | put"
                )))
            }
        };
        Ok(self.inner.with_plane(thread, |p| p.sequence_op(k, ref_id)))
    }

    /// Declare that superstep `s` will submit task-result writes; a
    /// checkpoint commit for `s` before those writes are sequenced is refused
    /// (`RemitOrderViolation`) instead of raced.
    fn declare_writes(&self, thread: &str, superstep: u32) {
        self.inner.with_plane(thread, |p| p.declare_writes(superstep));
    }

    /// Mark the run completed: subsequent ordinary-address resumes are inert
    /// (CO); fork-intent resumes still open branches (FD).
    fn complete(&self, thread: &str) {
        self.inner.with_plane(thread, |p| p.complete());
    }

    /// RD: the recovery decision, a pure function of the durable log.
    /// Returns the task index to continue from (`SkipTo`).
    fn recover(&self, thread: &str) -> u32 {
        self.inner.with_plane(thread, |p| {
            let core::Decision::SkipTo(t) = core::recover(p.checkpoints());
            t
        })
    }

    /// Per-task recovery plan over tasks `1..=total_tasks`:
    /// `[("skip"|"execute", task), ...]`.
    fn recovery_plan(&self, thread: &str, total_tasks: u32) -> Vec<(String, u32)> {
        self.inner.with_plane(thread, |p| {
            core::recovery_plan(p.checkpoints(), total_tasks)
                .into_iter()
                .map(|t| match t {
                    core::TaskPlan::Skip(t) => ("skip".to_string(), t),
                    core::TaskPlan::Execute(t) => ("execute".to_string(), t),
                })
                .collect()
        })
    }

    /// The append-only effect ledger, as `[(checkpoint_id, resume_index,
    /// task, effect_id, seq), ...]` — the paper's audit oracle.
    fn ledger(&self, thread: &str) -> Vec<(String, u32, u32, String, u64)> {
        self.inner.with_plane(thread, |p| {
            p.ledger()
                .iter()
                .map(|e| {
                    (
                        e.branch.checkpoint_id.clone(),
                        e.branch.resume_index,
                        e.task,
                        e.effect_id.clone(),
                        e.seq,
                    )
                })
                .collect()
        })
    }

    /// The sequencer journal, as `[(seq, kind, ref_id), ...]`.
    fn journal(&self, thread: &str) -> Vec<(u64, String, String)> {
        self.inner.with_plane(thread, |p| {
            p.journal()
                .iter()
                .map(|o| {
                    let k = match o.kind {
                        core::OpKind::PutWrites => "put_writes",
                        core::OpKind::Put => "put",
                        core::OpKind::ValidityRejected => "validity_rejected",
                        core::OpKind::EffectAdmitted => "effect_admitted",
                    };
                    (o.seq, k.to_string(), o.ref_id.clone())
                })
                .collect()
        })
    }

    /// Recorded resume values for an interrupt checkpoint, in ordinal order.
    fn recorded_resumes(&self, thread: &str, checkpoint_id: &str) -> Vec<String> {
        self.inner.with_plane(thread, |p| p.recorded_resumes(checkpoint_id))
    }

    /// Root-branch durable frontier for the plane.
    fn frontier(&self, thread: &str) -> u32 {
        self.inner.with_plane(thread, |p| p.frontier(&core::BranchKey::root()))
    }

    /// JSON snapshot of every plane, for audit and debugging.
    fn snapshot_json(&self) -> String {
        self.inner.snapshot_json()
    }
}

/// FI / probe-134 rule as a pure module-level function: given how the
/// invocation is addressed and whether the loaded checkpoint carries recorded
/// resume writes, return `"strip"` (fork intent: recorded resumes must not
/// shadow the supplied value) or `"keep"` (ordinary address: replay
/// idempotence and consume-once untouched).
#[pyfunction]
#[pyo3(signature = (explicit_checkpoint_address, fork_flag, has_recorded_resumes))]
fn fork_view(explicit_checkpoint_address: bool, fork_flag: bool, has_recorded_resumes: bool) -> &'static str {
    match core::fork_view(explicit_checkpoint_address, fork_flag, has_recorded_resumes) {
        core::ViewDecision::StripRecordedResumes => "strip",
        core::ViewDecision::KeepRecorded => "keep",
    }
}

/// CO (cross-process) / probe-165 rule as a pure module-level function:
/// given whether the loaded checkpoint carries a pending `__interrupt__`
/// write, whether the cross-process gate is enabled, and whether the
/// invocation carries fork or read (inspection) intent, return `"attempt"`
/// (take the `(thread, checkpoint)` claim in the shared store before any
/// node executes) or `"pass"` (the gate does not engage: fork intent claims
/// a fresh branch key instead, inspection must not consume, an
/// interrupt-free checkpoint has nothing to claim, and a disabled gate
/// preserves stock behavior).
#[pyfunction]
#[pyo3(signature = (has_pending_interrupt, gate_enabled, fork_intent, inspect_intent))]
fn consume_view(
    has_pending_interrupt: bool,
    gate_enabled: bool,
    fork_intent: bool,
    inspect_intent: bool,
) -> &'static str {
    match core::consume_view(has_pending_interrupt, gate_enabled, fork_intent, inspect_intent) {
        core::ConsumeDecision::AttemptClaim => "attempt",
        core::ConsumeDecision::Pass => "pass",
    }
}

/// The claim outcome, decided by the core: the compare-and-swap winner is
/// served (`Ok`); the loser is refused loudly with `RemitConsumeConflict`
/// *before any node executes*. The adapter reports `claim_won` and applies
/// this verdict mechanically — it holds no branch of its own on the
/// outcome.
#[pyfunction]
#[pyo3(signature = (claim_won, thread, checkpoint_id))]
fn consume_claim_check(claim_won: bool, thread: &str, checkpoint_id: &str) -> PyResult<()> {
    match core::consume_claim_verdict(claim_won) {
        core::ClaimVerdict::Serve => Ok(()),
        core::ClaimVerdict::Conflict => Err(RemitConsumeConflict::new_err(format!(
            "CO refused (cross-process consume-once): interrupt consumption for \
             thread={thread} checkpoint={checkpoint_id} is already claimed by another \
             process; this invocation was rejected before any node executed"
        ))),
    }
}

/// RD, standalone: recovery over an explicit log `[(task, seq), ...]` is a
/// pure, order-independent function; returns the `SkipTo` task.
#[pyfunction]
fn recover_from_log(log: Vec<(u32, u64)>) -> u32 {
    let recs: Vec<core::CheckpointRecord> = log
        .into_iter()
        .map(|(task, seq)| core::CheckpointRecord {
            branch: core::BranchKey::root(),
            task,
            seq,
        })
        .collect();
    let core::Decision::SkipTo(t) = core::recover(&recs);
    t
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCore>()?;
    m.add_function(wrap_pyfunction!(fork_view, m)?)?;
    m.add_function(wrap_pyfunction!(consume_view, m)?)?;
    m.add_function(wrap_pyfunction!(consume_claim_check, m)?)?;
    m.add_function(wrap_pyfunction!(recover_from_log, m)?)?;
    m.add("RemitError", m.py().get_type::<RemitError>())?;
    m.add("RemitDuplicateEffect", m.py().get_type::<RemitDuplicateEffect>())?;
    m.add("RemitPrefixViolation", m.py().get_type::<RemitPrefixViolation>())?;
    m.add("RemitValidityError", m.py().get_type::<RemitValidityError>())?;
    m.add("RemitOrderViolation", m.py().get_type::<RemitOrderViolation>())?;
    m.add("RemitConsumeConflict", m.py().get_type::<RemitConsumeConflict>())?;
    m.add("__core_language__", "rust")?;
    Ok(())
}
