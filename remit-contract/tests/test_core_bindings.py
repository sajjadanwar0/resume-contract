"""Binding-level conformance tests for remit._core.

Each test names the contract property it exercises and, where applicable,
the paper receipt it mirrors (probe numbers refer to the paper artifact).
"""

import pytest

from remit import (
    Core,
    RemitDuplicateEffect,
    RemitOrderViolation,
    RemitPrefixViolation,
    RemitValidityError,
    fork_view,
    recover_from_log,
)


def test_eo_duplicate_effect_refused():
    c = Core()
    assert isinstance(c.begin_effect("t1", 1, "charge"), int)
    with pytest.raises(RemitDuplicateEffect):
        c.begin_effect("t1", 1, "charge")
    assert len(c.ledger("t1")) == 1


def test_eo_crash_resume_counter_is_11_not_12():
    """The CrewAI 1.15.2 receipt (probe 115 / TLC run R1) through the
    bindings: +1 durable, crash, replay, +10 — exactly-once predicts 11."""
    c = Core()
    counter = 0
    c.begin_effect("run", 1, "s1")
    counter += 1
    c.commit_checkpoint("run", 1, b"counter=1")
    # crash; recovery is a pure function of the durable log
    assert c.recover("run") == 2
    with pytest.raises(RemitDuplicateEffect):
        c.begin_effect("run", 1, "s1")  # replayed s1 refused
    c.begin_effect("run", 2, "s2")
    counter += 10
    c.commit_checkpoint("run", 2, b"counter=11")
    assert counter == 11


def test_pc_prefix_reentry_refused():
    c = Core()
    c.commit_checkpoint("t", 1, b"a")
    c.commit_checkpoint("t", 2, b"b")
    with pytest.raises(RemitPrefixViolation):
        c.commit_checkpoint("t", 1, b"a-again")
    with pytest.raises(RemitPrefixViolation):
        c.commit_checkpoint("t", 4, b"skip-ahead")
    assert c.frontier("t") == 2


def test_cv_invalid_state_is_loud_and_unpersisted():
    c = Core()
    with pytest.raises(RemitValidityError):
        c.commit_checkpoint("t", 1, b"items=[None]", False, "None in List[str]")
    assert c.frontier("t") == 0
    kinds = [k for (_, k, _) in c.journal("t")]
    assert kinds == ["validity_rejected"]


def test_cv_validity_gate_for_adapters():
    c = Core()
    seq = c.validity_gate("t", True, "")
    assert isinstance(seq, int)
    with pytest.raises(RemitValidityError):
        c.validity_gate("t", False, "TypeError: None is not str")


def test_fd_second_value_gets_distinct_branch_and_own_outcome():
    """#6663 through the bindings: two resumes, two values, two branches —
    the second branch's outcome is its own supplied value, never the
    first's."""
    c = Core()
    d1 = c.resolve_resume("run", "ckpt", "va", "explicit_checkpoint")
    d2 = c.resolve_resume("run", "ckpt", "vb", "explicit_checkpoint")
    assert d1["decision"] == d2["decision"] == "serve_supplied"
    b1, b2 = d1["branch"], d2["branch"]
    assert (b1["checkpoint_id"], b1["resume_index"]) != (b2["checkpoint_id"], b2["resume_index"])
    assert c.outcome("run", b1["checkpoint_id"], b1["resume_index"]) == "va"
    assert c.outcome("run", b2["checkpoint_id"], b2["resume_index"]) == "vb"
    assert c.recorded_resumes("run", "ckpt") == ["va", "vb"]


def test_co_stray_resume_is_inert_and_effect_refused():
    c = Core()
    first = c.resolve_resume("run", "gate", "yes", "ordinary")
    assert first["decision"] == "serve_supplied"
    c.begin_effect("run", 2, "gated")
    c.complete("run")
    stray = c.resolve_resume("run", "gate", "yes", "ordinary")
    assert stray["decision"] == "inert"
    with pytest.raises(RemitDuplicateEffect):
        c.begin_effect("run", 2, "gated")
    assert len(c.ledger("run")) == 1


def test_co_recovery_replay_serves_recorded():
    c = Core()
    c.resolve_resume("run", "gate", "approve", "ordinary")
    replay = c.recovery_replay("run", "gate")
    assert replay == {"decision": "serve_recorded", "value": "approve"}


def test_rd_recover_is_pure_and_order_independent():
    log = [(1, 0), (2, 1), (3, 2)]
    assert recover_from_log(log) == 4
    assert recover_from_log(list(reversed(log))) == 4
    assert recover_from_log([]) == 1


def test_rd_sequencer_journal_is_gap_free():
    c = Core()
    seqs = [c.sequence_op("t", "put_writes", str(i)) for i in range(10)]
    seqs.append(c.sequence_op("t", "put", "ckpt-1"))
    assert seqs == sorted(seqs)
    journal = c.journal("t")
    assert [s for (s, _, _) in journal] == list(range(11))


def test_rd_put_before_declared_writes_refused():
    c = Core()
    c.declare_writes("t", 1)
    with pytest.raises(RemitOrderViolation):
        c.commit_checkpoint("t", 1, b"s")
    c.sequence_op("t", "put_writes", "1")
    assert isinstance(c.commit_checkpoint("t", 1, b"s"), int)


def test_fi_fork_view_matches_probe_134_cells():
    assert fork_view(True, False, True) == "strip"   # T1 / T1b
    assert fork_view(False, True, True) == "strip"   # production fork flag
    assert fork_view(False, False, True) == "keep"   # T3 replay idempotence
    assert fork_view(True, False, False) == "keep"   # nothing recorded
    assert fork_view(False, False, False) == "keep"


def test_recovery_plan_skips_prefix_and_executes_frontier():
    c = Core()
    c.commit_checkpoint("t", 1, b"a")
    c.commit_checkpoint("t", 2, b"b")
    assert c.recovery_plan("t", 3) == [("skip", 1), ("skip", 2), ("execute", 3)]


def test_planes_are_isolated():
    c = Core()
    c.begin_effect("alpha", 1, "e")
    assert len(c.ledger("alpha")) == 1
    assert len(c.ledger("beta")) == 0


def test_snapshot_json_is_json():
    import json

    c = Core()
    c.begin_effect("t", 1, "e")
    snap = json.loads(c.snapshot_json())
    assert "t" in snap
