from langgraph.checkpoint.memory import InMemorySaver

class InvalidCheckpointError(ValueError):
    """CV: rejected durable write (nothing was persisted)."""

class ValidatingSaver(InMemorySaver):

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
