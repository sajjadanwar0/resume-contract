"""Remit reference shim for LangGraph's checkpointer interface (CV leg).

Executable Python specification of remit::commit_checkpoint's CV gate
(crates/remit/src/lib.rs): validation precedes any durable append; a write
that would persist schema-invalid state raises loudly and persists nothing.
The Rust crate is the verification target; this shim is the drop-in
demonstration that the contract is enforceable at the BaseCheckpointSaver
narrow waist without modifying graphs.
"""
from langgraph.checkpoint.memory import InMemorySaver


class InvalidCheckpointError(ValueError):
    """CV: rejected durable write (nothing was persisted)."""


class ValidatingSaver(InMemorySaver):
    """InMemorySaver wrapper enforcing checkpoint validity (CV).

    validator(channel_values: dict) -> None, raising ValueError on
    schema-invalid state. The gate runs BEFORE delegation, so a rejected
    checkpoint leaves the durable log untouched and readable.
    """

    def __init__(self, validator):
        super().__init__()
        self._validate = validator

    def put(self, config, checkpoint, metadata, new_versions):
        try:
            self._validate(checkpoint.get("channel_values", {}))
        except ValueError as e:
            raise InvalidCheckpointError(
                f"CV: refusing to persist schema-invalid state: {e}"
            ) from e
        return super().put(config, checkpoint, metadata, new_versions)


class ForkKeyedSaver(InMemorySaver):
    """Remit FD leg: resume writes keyed by arrival ordinal, latest served.

    Executable specification of the LGF_ForkKeyed repair (formal/tla/
    LangGraphFork.tla). Root cause located in source: InMemorySaver.
    put_writes maps the RESUME channel through WRITES_IDX_MAP to a FIXED
    inner index, and its dedup guard (`inner_key in outer_writes_: continue`)
    drops any later write to that slot -- so a second Command(resume) to the
    same checkpoint never persists and task preparation consults the first
    (#6663, reproduced at langgraph 1.0.5/1.1.0/1.1.3/1.1.10/1.2.9). This
    shim overrides put_writes to store each RESUME write under a DISTINCT
    inner key (arrival ordinal), and get_tuple to expose only the latest, so
    each invocation's supplied value is the value its branch consumes:
    fork determinism per invocation, replay idempotence preserved for
    same-value re-invocation.
    """

    RESUME_CHANNEL = "__resume__"

    def __init__(self):
        super().__init__()
        self._resume_ordinal = {}

    def put_writes(self, config, writes, task_id, task_path=""):
        cfg = config["configurable"]
        outer = (cfg["thread_id"], cfg.get("checkpoint_ns", ""),
                 cfg["checkpoint_id"])
        resume_writes = [(c, v) for (c, v) in writes if c == self.RESUME_CHANNEL]
        other = [(c, v) for (c, v) in writes if c != self.RESUME_CHANNEL]
        if other:
            super().put_writes(config, other, task_id, task_path)
        for c, v in resume_writes:
            n = self._resume_ordinal.get(outer, 0)
            self._resume_ordinal[outer] = n + 1
            # distinct negative-free synthetic task id keeps inner keys unique
            self.writes.setdefault(outer, {})
            inner_key = (f"{task_id}#resume{n}", 0)
            self.writes[outer][inner_key] = (
                task_id, c, self.serde.dumps_typed(v), task_path)

    def get_tuple(self, config):
        tup = super().get_tuple(config)
        if tup is None or not tup.pending_writes:
            return tup
        resumes = [w for w in tup.pending_writes if w[1] == self.RESUME_CHANNEL]
        if len(resumes) <= 1:
            return tup
        keep = resumes[-1]
        filtered = [w for w in tup.pending_writes
                    if w[1] != self.RESUME_CHANNEL or w is keep]
        return tup._replace(pending_writes=filtered)
