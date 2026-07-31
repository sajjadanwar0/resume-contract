"""Probe-165 protocol, replayed against the packaged cross-process gate.

These tests replicate probe 165 (the read-path repair of the cross-process
consume-once cell probe 159 measured) with the gate promoted into the
shipped shim: ``wrap(..., cross_process_gate=True)`` takes the
``(thread, checkpoint)`` claim in the saver's own store inside
``get_tuple``, and the core serves the winner and refuses the loser with
``RemitConsumeConflict`` before any node executes.

The cross-process race is exercised as a cross-*instance* race: two
independently constructed savers over two independent connections to one
shared database — exactly the surface the store-level compare-and-swap
guards (the SQL uniqueness constraint cannot see process boundaries, only
connections). The true two-OS-process differential, stock control included,
remains the paper artifact's probes 159 (arm D races this packaged shim)
and 165; this suite is the package's regression net, not the measurement.

Cells:
  G1   two instances, one parked interrupt: exactly one served, the loser
       raises RemitConsumeConflict inside get_tuple, ledger fires once
  G1b  late different-value resume after consumption: inert (arm E
       disposition), fires nothing, never served its own branch
  G2   sequential control: one gated saver, park -> resume; no
       false-positive on the legitimate first consumption
  G3   stray resume after completion: no pending __interrupt__, gate not
       engaged, stock silent-inert disposition preserved
  G4   fork intent under the gate: the FD repair (probe 134 cells) is
       untouched; fork-addressed deliveries do not take the claim
  G5   read intent: remit_inspect=True reads do not consume; a later
       ordinary resume still wins
  G6   default off: cross_process_gate absent => no claims table touched,
       stock-plus-shim behavior bit for bit
  G7   core verdict surface: consume_view / consume_claim_check bindings
  G9   fresh-database first-claim DDL race (Postgres catalog refusal
       42P07/23505) absorbed as table-exists; claim proceeds
  PG*  G1/G1b/G2 on PostgresSaver when REMIT_TEST_PG_DSN is set, plus
       the loud refusal of a non-autocommit connection

Requires ``langgraph`` and ``langgraph-checkpoint-sqlite`` (skipped
otherwise); the PG cells additionally require
``langgraph-checkpoint-postgres`` + ``psycopg`` and a reachable server.
"""

import os
import sqlite3
import tempfile
import uuid

import pytest

langgraph = pytest.importorskip("langgraph")
sqlite_mod = pytest.importorskip("langgraph.checkpoint.sqlite")

from typing import TypedDict  # noqa: E402

from langgraph.graph import END, START, StateGraph  # noqa: E402
from langgraph.types import Command, interrupt  # noqa: E402

import remit  # noqa: E402

SqliteSaver = sqlite_mod.SqliteSaver

PG_DSN = os.environ.get("REMIT_TEST_PG_DSN")


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


def sqlite_conn(path):
    return sqlite3.connect(path, timeout=60, check_same_thread=False)


def gated_sqlite(path, **kw):
    kw.setdefault("cross_process_gate", True)
    return remit.wrap(SqliteSaver, sqlite_conn(path), **kw)


def park(app, tag):
    cfg = {"configurable": {"thread_id": tag}}
    r0 = app.invoke({"value": 0}, cfg)
    assert "__interrupt__" in r0, "protocol must park at the gate"
    return cfg


def db_path():
    d = tempfile.mkdtemp(prefix="remit_gate_")
    return f"{d}/ckpt.sqlite"


def claims_rows(path):
    c = sqlite3.connect(path)
    try:
        return c.execute(
            f"SELECT thread, ckpt FROM {remit.CLAIMS_TABLE}"
        ).fetchall()
    except sqlite3.OperationalError:  # table never created
        return None
    finally:
        c.close()


# ---------------------------------------------------------------- G1: race


def test_g1_second_instance_loses_the_claim_and_is_refused_before_any_node():
    """G1 (probe 165 arm B/C shape): with the gate on, the first reader of
    the parked checkpoint takes the claim; the other instance's resume is
    refused inside ``get_tuple`` — RemitConsumeConflict, zero node
    executions — and the winner's resume fires the gated effect exactly
    once."""
    path = db_path()
    ledger = []
    saver_a = gated_sqlite(path)
    app_a = build(saver_a, ledger)
    cfg = park(app_a, "g1")
    assert ledger == [], "parking must not fire the gated effect"

    # A second, independent instance (fresh connection, fresh core) reads
    # the parked state first — the racer that wins the store-level CAS.
    saver_b = gated_sqlite(path)
    t = saver_b.get_tuple(cfg)
    assert t is not None

    # The loser — carrying a DIFFERENT value (arm C's shape): refused before
    # its node executes, never served the winner's branch.
    with pytest.raises(remit.RemitConsumeConflict):
        app_a.invoke(Command(resume=False), cfg)
    assert ledger == [], "the refused racer must not reach the node"

    # The winner is served: same claim key, idempotent for the holder.
    app_b = build(saver_b, ledger)
    r = app_b.invoke(Command(resume=True), cfg)
    assert r.get("value") == 1
    assert ledger == [1], f"gated effect must fire exactly once, got {ledger}"
    assert len(claims_rows(path) or []) == 1


def test_g1b_late_different_value_after_consumption_is_inert_not_served():
    """G1b: once the winner has CONSUMED the interrupt to completion, a late
    resume from another instance with a different value is the
    stray-after-completion case, not a race: the checkpoint carries no
    pending ``__interrupt__``, the gate does not engage, and the stock
    silent-inert disposition (probe 126 / arm E) applies — the late value
    fires nothing and is never served its own branch. (The concurrent
    different-value loser — refused loudly mid-race — is G1.)"""
    path = db_path()
    ledger = []
    saver_a = gated_sqlite(path)
    app_a = build(saver_a, ledger)
    cfg = park(app_a, "g1b")
    saver_b = gated_sqlite(path)
    app_b = build(saver_b, ledger)
    r = app_b.invoke(Command(resume=True), cfg)  # B claims inside get_tuple
    assert r.get("value") == 1 and ledger == [1]
    r2 = app_a.invoke(Command(resume=False), cfg)  # late, post-consumption
    assert ledger == [1], f"the late value must fire nothing, got {ledger}"
    assert r2.get("value") == 1, "the stray reports the consumed outcome"


# ---------------------------------------------------- G2: sequential control


def test_g2_sequential_single_resume_is_untouched():
    """G2 (arm A): the gate must not false-positive on the legitimate first
    consumption — park, one resume, completion, effect exactly once."""
    path = db_path()
    ledger = []
    saver = gated_sqlite(path)
    app = build(saver, ledger)
    cfg = park(app, "g2")
    r = app.invoke(Command(resume=True), cfg)
    assert r.get("value") == 1
    assert ledger == [1]


# ------------------------------------------------- G3: stray after completion


def test_g3_stray_after_completion_does_not_engage_the_gate():
    """G3 (arm E): the completed thread's checkpoint carries no pending
    ``__interrupt__``; the gate does not engage and the stock silent-inert
    disposition (probe 126) is preserved unchanged."""
    path = db_path()
    ledger = []
    saver = gated_sqlite(path)
    app = build(saver, ledger)
    cfg = park(app, "g3")
    app.invoke(Command(resume=True), cfg)
    before = list(ledger)
    n_claims = len(claims_rows(path) or [])
    r = app.invoke(Command(resume=True), cfg)  # stray
    assert ledger == before, f"stray resume re-fired the effect: {ledger}"
    assert r.get("value") == 1
    assert len(claims_rows(path) or []) == n_claims, "stray must claim nothing"


# --------------------------------------------------- G4: fork intent bypass


def test_g4_fork_intent_bypasses_the_consumption_gate():
    """G4: fork-addressed deliveries claim a fresh branch key through the FD
    machinery (probe 134 / 155 composition); the consumption gate stands
    aside, and the FD repair's [1, 0] trail is unchanged under the gate."""
    path = db_path()
    ledger = []
    saver = gated_sqlite(path)
    app = build(saver, ledger)
    cfg = {"configurable": {"thread_id": "g4"}}
    app.invoke({"value": 0}, cfg)
    # Fetch the fork address via an INSPECTION read: a bare read of the
    # parked state would take the consumption claim (the read-intent gap,
    # live — the very failure this test's first version tripped over via
    # ``get_state``); ``remit_inspect=True`` is the documented opt-out.
    t = saver.get_tuple(
        {"configurable": {"thread_id": "g4", "remit_inspect": True}}
    )
    ckpt = t.config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": "g4", "checkpoint_id": ckpt}}
    r1 = app.invoke(Command(resume=True), fork_cfg)
    r2 = app.invoke(Command(resume=False), fork_cfg)
    assert (r1.get("value"), r2.get("value")) == (1, 0)
    assert ledger == [1, 0]
    held = claims_rows(path)
    assert not held, f"fork-intent deliveries must take no consumption claim: {held}"


# ----------------------------------------------------- G5: read-intent bypass


def test_g5_inspection_read_does_not_consume():
    """G5: a ``remit_inspect=True`` read of the parked state takes no claim;
    the later ordinary resume still wins and completes."""
    path = db_path()
    ledger = []
    saver = gated_sqlite(path)
    app = build(saver, ledger)
    cfg = park(app, "g5")
    inspect_cfg = {"configurable": {"thread_id": "g5", "remit_inspect": True}}
    t = saver.get_tuple(inspect_cfg)
    assert t is not None
    assert not claims_rows(path), "inspection must not take the claim"
    r = app.invoke(Command(resume=True), cfg)
    assert r.get("value") == 1 and ledger == [1]


# --------------------------------------------------------- G6: default off


def test_g6_gate_defaults_off_and_touches_nothing():
    """G6: without ``cross_process_gate=True`` the wrap is the shipped
    v0.1.0 behavior — no claims table, no refusals."""
    path = db_path()
    ledger = []
    saver = remit.wrap(SqliteSaver, sqlite_conn(path))
    app = build(saver, ledger)
    cfg = park(app, "g6")
    other = remit.wrap(SqliteSaver, sqlite_conn(path))
    assert other.get_tuple(cfg) is not None
    r = app.invoke(Command(resume=True), cfg)
    assert r.get("value") == 1 and ledger == [1]
    assert claims_rows(path) is None, "default-off must create no claims table"


# ------------------------------------------------- G7: core verdict surface


def test_g7_core_verdicts_and_exception_surface():
    """G7: the decisions are the core's — ``consume_view`` mirrors probe
    165's rule and ``consume_claim_check`` raises the typed conflict from
    Rust for the loser."""
    assert remit.consume_view(True, True, False, False) == "attempt"
    assert remit.consume_view(True, True, True, False) == "pass"   # fork
    assert remit.consume_view(True, True, False, True) == "pass"   # inspect
    assert remit.consume_view(False, True, False, False) == "pass"  # no park
    assert remit.consume_view(True, False, False, False) == "pass"  # gate off
    remit.consume_claim_check(True, "t", "c")  # winner: returns
    with pytest.raises(remit.RemitConsumeConflict) as ei:
        remit.consume_claim_check(False, "t", "c")
    assert issubclass(ei.type, remit.RemitError)
    assert "already claimed" in str(ei.value)


def test_g8_gate_requires_a_connection_bearing_saver():
    """A gate-enabled wrap of a saver without ``.conn`` is refused loudly at
    construction, not silently degraded."""
    from langgraph.checkpoint.memory import InMemorySaver

    with pytest.raises(RuntimeError, match="cross_process_gate"):
        remit.wrap(InMemorySaver, cross_process_gate=True)


def test_g9_fresh_database_ddl_race_is_absorbed():
    """G9: two processes' FIRST claims on a fresh Postgres database race the
    ``CREATE TABLE IF NOT EXISTS`` itself; Postgres enforces catalog
    uniqueness before the IF NOT EXISTS check settles and refuses one
    creator with SQLSTATE 42P07/23505 — measured live at probe 159's PG
    rep 0 on a fresh database (v0.1.1's one non-typed loser). The shim
    absorbs that refusal as "the table exists" and proceeds to the claim;
    any other SQLSTATE still propagates."""

    class CatalogRace(Exception):
        def __init__(self, sqlstate):
            super().__init__("duplicate")
            self.sqlstate = sqlstate

    def make_conn(raise_state):
        class Cur:
            rowcount = 1

        class Conn:
            def __init__(self):
                self.calls = []

            def execute(self, sql, *a):
                self.calls.append(sql)
                if sql.lstrip().upper().startswith("CREATE TABLE") and raise_state:
                    raise CatalogRace(raise_state)
                return Cur()

        Conn.__module__ = "psycopg"  # route through the Postgres branch
        return Conn()

    class Saver:
        def __init__(self, conn):
            self.conn = conn

        def get_tuple(self, config):
            return None

    for state in ("42P07", "23505"):
        s = remit.wrap(Saver, make_conn(state), cross_process_gate=True)
        assert s._remit_take_claim("g9", "c9") is True
        assert any("INSERT INTO" in c for c in s.conn.calls), \
            "the claim INSERT must follow the absorbed DDL race"

    s = remit.wrap(Saver, make_conn("55P03"), cross_process_gate=True)
    with pytest.raises(CatalogRace):
        s._remit_take_claim("g9", "c9")


# ------------------------------------------------------------ PG variants


def _pg_saver(dsn, autocommit=True, **kw):
    pg_mod = pytest.importorskip("langgraph.checkpoint.postgres")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.Connection.connect(
        dsn, autocommit=autocommit, prepare_threshold=0, row_factory=dict_row
    )
    kw.setdefault("cross_process_gate", True)
    saver = remit.wrap(pg_mod.PostgresSaver, conn, **kw)
    saver.setup()  # Postgres does not create its schema lazily (probe 165)
    return saver


@pytest.mark.skipif(not PG_DSN, reason="REMIT_TEST_PG_DSN not set")
def test_pg_g1_race_and_g2_sequential():
    """PG G1+G2, mirroring the sqlite G1 staging exactly: racer B takes the
    claim via a bare ``get_tuple`` of the PARKED state, so A's resume is a
    concurrent loser (refused before any node) rather than a
    post-completion stray. (The first version of this test resumed B to
    completion first — the G1b staging — and correctly observed inertness
    instead of a conflict: the gate does not engage once no pending
    ``__interrupt__`` remains. That order lives in test_pg_g1b below.)"""
    tag = f"pg-{uuid.uuid4().hex[:6]}"
    ledger = []
    saver_a = _pg_saver(PG_DSN)
    app_a = build(saver_a, ledger)
    cfg = park(app_a, tag)
    assert ledger == [], "parking must not fire the gated effect"

    saver_b = _pg_saver(PG_DSN)
    t = saver_b.get_tuple(cfg)          # B wins the claim on the parked state
    assert t is not None

    with pytest.raises(remit.RemitConsumeConflict):
        app_a.invoke(Command(resume=True), cfg)
    assert ledger == [], "the refused racer must not reach the node"

    app_b = build(saver_b, ledger)
    r = app_b.invoke(Command(resume=True), cfg)
    assert r.get("value") == 1 and ledger == [1]

    seq_tag = f"pgseq-{uuid.uuid4().hex[:6]}"
    ledger2 = []
    saver_c = _pg_saver(PG_DSN)
    app_c = build(saver_c, ledger2)
    cfg2 = park(app_c, seq_tag)
    r2 = app_c.invoke(Command(resume=True), cfg2)
    assert r2.get("value") == 1 and ledger2 == [1]


@pytest.mark.skipif(not PG_DSN, reason="REMIT_TEST_PG_DSN not set")
def test_pg_g1b_late_resume_after_consumption_is_inert():
    """PG G1b: once the winner has consumed to completion, a late resume
    from the other instance is the stray case — gate not engaged, effect
    inert, stock disposition preserved (the behavior the first PG G1
    accidentally measured)."""
    tag = f"pgb-{uuid.uuid4().hex[:6]}"
    ledger = []
    saver_a = _pg_saver(PG_DSN)
    app_a = build(saver_a, ledger)
    cfg = park(app_a, tag)
    saver_b = _pg_saver(PG_DSN)
    app_b = build(saver_b, ledger)
    r = app_b.invoke(Command(resume=True), cfg)   # B claims inside get_tuple
    assert r.get("value") == 1 and ledger == [1]
    r2 = app_a.invoke(Command(resume=False), cfg)  # late, post-consumption
    assert ledger == [1], f"the late value must fire nothing, got {ledger}"
    assert r2.get("value") == 1


@pytest.mark.skipif(not PG_DSN, reason="REMIT_TEST_PG_DSN not set")
def test_pg_transactional_connection_is_refused_loudly():
    """A non-autocommit Postgres connection under the gate is a loud
    RuntimeError at first claim, never a silent entanglement of the claim
    with the caller's transaction. Exercised directly against the claim
    path: LangGraph's own setup()/put machinery independently requires
    autocommit, so routing through a graph would test their guard, not
    ours."""
    pg_mod = pytest.importorskip("langgraph.checkpoint.postgres")
    psycopg = pytest.importorskip("psycopg")
    from psycopg.rows import dict_row

    conn = psycopg.Connection.connect(
        PG_DSN, autocommit=False, prepare_threshold=0, row_factory=dict_row
    )
    saver = remit.wrap(pg_mod.PostgresSaver, conn, cross_process_gate=True)
    with pytest.raises(RuntimeError, match="autocommit"):
        saver._remit_take_claim(f"pgtx-{uuid.uuid4().hex[:6]}", "c1")
    conn.close()
