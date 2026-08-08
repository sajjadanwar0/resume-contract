import sqlite3
import tempfile

import pytest

langgraph = pytest.importorskip("langgraph")

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import remit

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

try:
    from importlib.metadata import version

    LG_VERSION = version("langgraph")
except Exception:
    LG_VERSION = "unknown"

SWEEP_LINES = ("1.0.", "1.1.", "1.2.")

class S(TypedDict):
    value: int

def build(saver, ledger):
    def node(state: S):
        allow = interrupt("Allow to add?")
        v = state["value"] + (1 if allow else 0)
        ledger.append(1 if allow else 0)
        return {"value": v}

    return (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=saver)
    )

def run_to_interrupt(app, tag):
    cfg = {"configurable": {"thread_id": tag}}
    r0 = app.invoke({"value": 0}, cfg)
    intr = r0.get("__interrupt__")
    intr_id = getattr(intr[0], "id", None) if intr else None
    ckpt = app.get_state(cfg).config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": tag, "checkpoint_id": ckpt}}
    return cfg, fork_cfg, intr_id

def fork_cells(saver, tag, use_map):
    ledger = []
    app = build(saver, ledger)
    cfg, fork_cfg, intr_id = run_to_interrupt(app, tag)
    if use_map and intr_id is None:
        pytest.skip("interrupt id unavailable; resume-map form untestable")

    def mk(v):
        return Command(resume={intr_id: v}) if use_map else Command(resume=v)

    r1 = app.invoke(mk(True), fork_cfg)
    r2 = app.invoke(mk(False), fork_cfg)
    return r1.get("value"), r2.get("value"), ledger, app, cfg, fork_cfg, mk

@pytest.mark.parametrize("use_map", [False, True], ids=["bare", "map"])
def test_t1_fork_serves_second_value_under_remit(use_map):
    """T1 / T1b: under the Rust-core shim, the second fork with a different
    value is served its own value — branch2 computes f(False) = 0."""
    saver = remit.wrap(InMemorySaver)
    b1, b2, ledger, *_ = fork_cells(saver, f"t1-{use_map}", use_map)
    assert b1 == 1, f"branch1 must compute f(True)=1, got {b1}"
    assert b2 == 0, f"FD: branch2 must compute f(False)=0, got {b2} (#6663 behavior)"
    assert ledger == [1, 0], f"per-branch effect trail must be [1, 0], got {ledger}"

def test_t2_same_value_refork_is_deterministic():
    """T2: re-answering the same value at the fork address recomputes f(v)
    — same outcome, fresh branch effect (per-branch EO, not global dedup)."""
    saver = remit.wrap(InMemorySaver)
    ledger = []
    app = build(saver, ledger)
    _, fork_cfg, _ = run_to_interrupt(app, "t2")
    r1 = app.invoke(Command(resume=True), fork_cfg)
    r2 = app.invoke(Command(resume=True), fork_cfg)
    assert r1.get("value") == r2.get("value") == 1
    assert ledger == [1, 1]

def test_t3_stray_resume_after_completion_is_inert():
    """T3: an ordinary-address resume after completion neither re-executes
    the node nor perturbs the final state (CO)."""
    saver = remit.wrap(InMemorySaver)
    ledger = []
    app = build(saver, ledger)
    cfg, _, _ = run_to_interrupt(app, "t3")
    r1 = app.invoke(Command(resume=True), cfg)
    assert r1.get("value") == 1
    before = list(ledger)
    r2 = app.invoke(Command(resume=True), cfg)
    assert ledger == before, f"stray resume re-fired the effect: {ledger}"
    assert r2.get("value") == 1

def test_cv_validator_rejection_is_loud_and_blocks_persistence():
    """CV: a schema validator raise surfaces as RemitValidityError before
    anything is persisted (the loud counterpart of the silent #6491 class)."""

    def validator(checkpoint):
        raise TypeError("None in List[str] violates the state schema")

    saver = remit.wrap(InMemorySaver, validator=validator)
    ledger = []
    app = build(saver, ledger)
    with pytest.raises(remit.RemitValidityError):
        app.invoke({"value": 0}, {"configurable": {"thread_id": "cv"}})
    assert ledger == []

def test_t0_stock_saver_exhibits_the_fork_violation():
    """T0 differential control: the *stock* saver serves branch2 the first
    branch's value (#6663). Asserted only on the release lines the paper's
    version sweep covered; on other lines the cell is recorded, not
    presumed."""
    if not LG_VERSION.startswith(SWEEP_LINES):
        pytest.skip(
            f"langgraph {LG_VERSION} outside the paper's sweep (1.0.x-1.2.x); "
            "stock behavior not presumed"
        )
    b1, b2, ledger, *_ = fork_cells(InMemorySaver(), "t0", use_map=False)
    assert b1 == 1
    assert b2 == 1, (
        f"expected the documented #6663 violation on langgraph {LG_VERSION}, "
        f"got branch2={b2}"
    )

def test_sqlite_backend_fork_repair():
    """Probe 134's original backend: SqliteSaver wrapped by the shim."""
    sqlite_mod = pytest.importorskip("langgraph.checkpoint.sqlite")
    d = tempfile.mkdtemp(prefix="remit_sqlite_")
    conn = sqlite3.connect(f"{d}/ckpt.sqlite", check_same_thread=False)
    saver = remit.wrap(sqlite_mod.SqliteSaver, conn)
    b1, b2, ledger, *_ = fork_cells(saver, "sqlite", use_map=False)
    assert (b1, b2) == (1, 0)
    assert ledger == [1, 0]

def test_core_journal_records_the_sequenced_ops():
    """RD substrate: the shim journals every put/put_writes submission in the
    core's per-thread sequencer; the journal is strictly ordered."""
    saver = remit.wrap(InMemorySaver)
    ledger = []
    app = build(saver, ledger)
    cfg, _, _ = run_to_interrupt(app, "journal")
    app.invoke(Command(resume=True), cfg)
    journal = saver.remit_core.journal("journal")
    assert journal, "expected sequenced operations in the core journal"
    seqs = [s for (s, _, _) in journal]
    assert seqs == sorted(seqs)
    kinds = {k for (_, k, _) in journal}
    assert "put" in kinds
