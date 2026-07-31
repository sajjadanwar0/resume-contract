#!/usr/bin/env python3
"""
174_p17_crosshost_race.py  (campaign p17)

Cross-HOST evidence for the one residual probes 159/165/168 name and
Sec. 9 states plainly: "racers distributed across hosts are unmeasured."
Two OS processes on TWO machines share one networked PostgreSQL server
(checkpointer schema + coordination tables + effect ledger, all in
Postgres, so no filesystem is shared). Arms:

  stock  -- both racers resume the same parked interrupt through the
            stock PostgresSaver.  Discovery cell at gate duration D:
            with D >> release skew, does every racer that arrives
            within the window consume, as it does within one host?
  gate   -- same race through the packaged REMIT shim with
            cross_process_gate=True on BOTH hosts (probe 165's read-path
            claim, v0.1.2 packaging, now across a network boundary).

Registered predictions (the probe-171 discipline: committed before the
first cross-host run; the run may falsify them):

  P1 stock, D=2000 ms : gated effect fires 2 in every repetition
                        (barrier-release skew over LAN/WAN is tens to a
                        few hundred ms; the window is D).
  P2 gate,  D=2000 ms : distribution {1:reps}; the loser refused with
                        RemitConsumeConflict before any node executes.
  P3 stock, D=0       : NO prediction registered -- exploratory: the
                        outcome depends on network RTT vs. the bare
                        node's execution time; reported as an observed
                        frequency only.

Scope, stated the way Sec. 9 states scope: this measures cross-host
DISTRIBUTION of the racers over one networked store. It does not
measure partitions, packet loss, or multi-store topologies; those
remain named exclusions.

Saver construction and shim wrap are copied VERBATIM from probe 159's
make_saver() at the pins (verified against the committed file,
2026-07-31): psycopg Connection.connect(dsn, autocommit=True,
prepare_threshold=0, row_factory=dict_row); _rls.wrap(PostgresSaver,
conn, cross_process_gate=True) for the gate arm; saver.setup() at every
construction (the probe-165 UndefinedTable finding). This file is
py_compile-checked, not yet executed against a live server: run the
single-host --smoke pre-flight before the cross-host campaign.

Topology / usage (three shells; A = the host nearer the DB; interpreter
is the durable env's, per the pin guard):

  PY=envs/langgraph-durable/.venv/bin/python3

  # once (host A): create coordination tables + checkpointer schema
  $PY probes/174_p17_crosshost_race.py init  --dsn "$PG_DSN"

  # between attempts (host A): drop xh174_* coordination/ledger tables
  $PY probes/174_p17_crosshost_race.py reset --dsn "$PG_DSN"

  # shell 1, host A (coordinator: parks runs, releases barrier, judges)
  $PY probes/174_p17_crosshost_race.py serve --dsn "$PG_DSN" \
      --reps 10 --arms stock,gate --gate-ms 2000

  # shell 2, host A  AND  shell 3, host B (ONE racer per host; racers
  # loop forever -- Ctrl-C them after serve prints its receipt; the
  # racer receipt is rewritten after every round, so Ctrl-C loses
  # nothing)
  $PY probes/174_p17_crosshost_race.py race  --dsn "$PG_DSN" --host-tag hostA
  $PY probes/174_p17_crosshost_race.py race  --dsn "$PG_DSN" --host-tag hostB

Pre-flight (single host, two local racers with distinct tags, remote or
local DSN): serve --smoke.  The coordinator records the two racers'
hostnames per round; a same-hostname round sets cross_host_attested
false in the receipt, so a pre-flight can never be silently promoted to
a cross-host cell.

Oracle: the effect IS the ledger append -- one autocommitted INSERT
into xh174_effects inside the gated node, after the instrumented
sleep, durable before the node returns (the probe-163 ordering
discipline, transplanted to Postgres). The coordinator parks each run
through a STOCK saver in every arm: the gate under test acts on the
racers' resume-path read, and the parked-thread inspection hazard the
paper names (Sec. 7.2) is exactly why the parking process must not
carry the gate.

Receipts: results/multiproc/174_crosshost_matrix.json (coordinator) +
results/multiproc/174_crosshost_racer_<tag>.json (each racer, local to
that host -- scp host B's back). Stable fields: per-cell fire
distribution, loser exception types, cross_host_attested.
"""
import argparse
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_LANGGRAPH = "1.2.9"


def _fail(msg):
    print(json.dumps({"probe_refused": msg,
                      "interpreter": sys.executable}), file=sys.stderr)
    sys.exit(3)


_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"langgraph=={_lg} but the paper pin is {REQUIRED_LANGGRAPH}. "
          f"Launch with envs/langgraph-durable or set PROBE_ALLOW_OFFPIN=1.")

import psycopg                                          # noqa: E402
from psycopg import Connection                          # noqa: E402
from psycopg.rows import dict_row                       # noqa: E402
from typing import TypedDict                            # noqa: E402
from langgraph.graph import StateGraph, START, END      # noqa: E402
from langgraph.types import interrupt, Command          # noqa: E402
from langgraph.checkpoint.postgres import PostgresSaver # noqa: E402

try:
    from remit import langgraph_shim as _rls
    HAVE_REMIT = True
except Exception as _e:                                 # pragma: no cover
    HAVE_REMIT = False
    _REMIT_ERR = f"{type(_e).__name__}: {_e}"

HOSTNAME = socket.gethostname()
POLL_S = 0.002          # barrier poll on a PERSISTENT connection
IDLE_S = 0.05           # racer idle poll between rounds
ROUND_TIMEOUT_S = 180
TABLES = ("xh174_rounds", "xh174_ready", "xh174_effects", "xh174_results")


# ------------------------------------------------------------ pg helpers
def _coord(dsn):
    """One persistent autocommit connection for coordination polling."""
    return psycopg.connect(dsn, autocommit=True)


def _init_tables(dsn):
    with _coord(dsn) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS xh174_rounds (
            round BIGINT PRIMARY KEY, thread TEXT, arm TEXT, gate_ms INT,
            released BOOL DEFAULT FALSE, done BOOL DEFAULT FALSE)""")
        c.execute("""CREATE TABLE IF NOT EXISTS xh174_ready (
            round BIGINT, host TEXT, hostname TEXT,
            PRIMARY KEY (round, host))""")
        c.execute("""CREATE TABLE IF NOT EXISTS xh174_effects (
            n SERIAL PRIMARY KEY, round BIGINT, host TEXT,
            thread TEXT, task TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS xh174_results (
            round BIGINT, host TEXT, outcome TEXT, error TEXT,
            t_fire_ms DOUBLE PRECISION, PRIMARY KEY (round, host))""")


def _ledger_write(dsn, rnd, host, thread, task):
    # The effect: one autocommitted INSERT on its own short-lived
    # connection, durable before the node returns (probe-163 ordering).
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO xh174_effects (round, host, thread, task) "
                  "VALUES (%s, %s, %s, %s)", (rnd, host, thread, task))


# --------------------------------------------------- saver (159-verbatim)
def _make_saver(dsn, gated):
    """(saver, closer) -- construction copied from probe 159 make_saver()."""
    conn = Connection.connect(dsn, autocommit=True,
                              prepare_threshold=0, row_factory=dict_row)
    if gated:
        if not HAVE_REMIT:
            _fail(f"gate arm needs remit-contract: {_REMIT_ERR}")
        saver = _rls.wrap(PostgresSaver, conn, cross_process_gate=True)
    else:
        saver = PostgresSaver(conn)
    saver.setup()    # Postgres does not create the checkpointer schema
                     # lazily (probe 165 finding); idempotent thereafter
    return saver, conn.close


def _build_graph(dsn, saver, rnd, host_tag, gate_ms, thread):
    class S(TypedDict, total=False):
        pre_out: str
        decision: bool

    def pre(state):
        return {"pre_out": "done"}

    def gate(state):
        decision = interrupt({"q": "approve?"})
        if gate_ms:
            time.sleep(gate_ms / 1000.0)   # instrumented duration D: the
                                           # model-call / payment stand-in
                                           # (probe 168's dose axis)
        _ledger_write(dsn, rnd, host_tag, thread, "gate")
        return {"decision": bool(decision)}

    g = StateGraph(S)
    g.add_node("pre", pre)
    g.add_node("gate", gate)
    g.add_edge(START, "pre")
    g.add_edge("pre", "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=saver)


# ---------------------------------------------------------------- roles
def role_init(a):
    _init_tables(a.dsn)
    saver, close = _make_saver(a.dsn, gated=False)
    close()
    print(json.dumps({"init": "ok", "host": HOSTNAME, "langgraph": _lg}))


def role_reset(a):
    with _coord(a.dsn) as c:
        for t in TABLES:
            c.execute(f"DROP TABLE IF EXISTS {t}")
    print(json.dumps({"reset": "ok", "dropped": list(TABLES),
                      "note": "checkpointer tables untouched; stale "
                              "parked threads are inert (unique ids)"}))


def role_serve(a):
    _init_tables(a.dsn)
    arms = a.arms.split(",")
    reps = 2 if a.smoke else a.reps
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    cells = []
    rnd = int(time.time()) * 100        # monotonic across attempts
    coord = _coord(a.dsn)
    try:
        for arm in arms:
            if arm == "gate" and not HAVE_REMIT:
                _fail(f"gate arm needs remit-contract: {_REMIT_ERR}")
            fires, losers, hostpairs = [], [], []
            for rep in range(reps):
                rnd += 1
                thread = f"xh174-{uuid.uuid4().hex[:8]}"
                # Park through a STOCK saver in every arm (see docstring).
                saver, close = _make_saver(a.dsn, gated=False)
                try:
                    graph = _build_graph(a.dsn, saver, rnd, "coordinator",
                                         a.gate_ms, thread)
                    graph.invoke({}, {"configurable":
                                      {"thread_id": thread}})
                finally:
                    close()
                coord.execute("INSERT INTO xh174_rounds "
                              "(round, thread, arm, gate_ms) "
                              "VALUES (%s, %s, %s, %s)",
                              (rnd, thread, arm, a.gate_ms))
                t0 = time.time()
                while True:                      # wait for two hosts
                    rows = coord.execute(
                        "SELECT host, hostname FROM xh174_ready "
                        "WHERE round=%s", (rnd,)).fetchall()
                    if len(rows) >= 2:
                        break
                    if time.time() - t0 > ROUND_TIMEOUT_S:
                        _fail(f"round {rnd}: racers never arrived")
                    time.sleep(POLL_S)
                hostpairs.append(sorted(r[1] for r in rows))
                coord.execute("UPDATE xh174_rounds SET released=TRUE "
                              "WHERE round=%s", (rnd,))
                t0 = time.time()
                while True:                      # wait for two verdicts
                    done = coord.execute(
                        "SELECT host, outcome, error FROM xh174_results "
                        "WHERE round=%s", (rnd,)).fetchall()
                    if len(done) >= 2:
                        break
                    if time.time() - t0 > ROUND_TIMEOUT_S:
                        _fail(f"round {rnd}: racers never finished")
                    time.sleep(POLL_S)
                n = coord.execute(
                    "SELECT COUNT(*) FROM xh174_effects "
                    "WHERE round=%s AND task='gate'", (rnd,)).fetchone()[0]
                fires.append(n)
                losers.extend(e for _, o, e in done
                              if o == "refused" and e)
                coord.execute("UPDATE xh174_rounds SET done=TRUE "
                              "WHERE round=%s", (rnd,))
            dist = {}
            for f in fires:
                dist[str(f)] = dist.get(str(f), 0) + 1
            same_host = any(len(set(p)) < 2 for p in hostpairs)
            cell = {"arm": arm, "gate_ms": a.gate_ms, "reps": reps,
                    "fires": fires, "distribution": dist,
                    "loser_errors": sorted(set(losers)),
                    "cross_host_attested": not same_host,
                    "host_pairs": hostpairs}
            cells.append(cell)
            print(json.dumps(cell))
    finally:
        coord.close()
    try:
        _remit_v = version("remit-contract")
    except Exception:
        _remit_v = None
    receipt = {"probe": 174, "role": "serve", "host": HOSTNAME,
               "ts": datetime.now(timezone.utc).isoformat(),
               "langgraph": _lg, "remit_contract": _remit_v,
               "cells": cells,
               "stable": [{k: c[k] for k in
                           ("arm", "gate_ms", "distribution",
                            "loser_errors", "cross_host_attested")}
                          for c in cells]}
    p = outdir / "174_crosshost_matrix.json"
    p.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"receipt": str(p)}))


def role_race(a):
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    log = []
    coord = _coord(a.dsn)
    print(json.dumps({"racer": a.host_tag, "hostname": HOSTNAME,
                      "note": "looping; Ctrl-C after serve prints its "
                              "receipt"}))
    try:
        while True:
            row = coord.execute(
                "SELECT r.round, r.thread, r.arm, r.gate_ms "
                "FROM xh174_rounds r WHERE r.done=FALSE AND NOT EXISTS "
                "(SELECT 1 FROM xh174_ready y WHERE y.round=r.round "
                " AND y.host=%s) ORDER BY r.round LIMIT 1",
                (a.host_tag,)).fetchone()
            if row is None:
                time.sleep(IDLE_S)
                continue
            rnd, thread, arm, gate_ms = row
            coord.execute(
                "INSERT INTO xh174_ready (round, host, hostname) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (rnd, a.host_tag, HOSTNAME))
            while True:                          # spin on the barrier
                rel = coord.execute(
                    "SELECT released FROM xh174_rounds WHERE round=%s",
                    (rnd,)).fetchone()[0]
                if rel:
                    break
                time.sleep(POLL_S)
            outcome, err, t0 = "fired", "", time.time()
            saver = close = None
            try:
                saver, close = _make_saver(a.dsn, gated=(arm == "gate"))
                graph = _build_graph(a.dsn, saver, rnd, a.host_tag,
                                     gate_ms, thread)
                graph.invoke(Command(resume=True),
                             {"configurable": {"thread_id": thread}})
            except Exception as e:               # loser path under gate
                outcome = "refused"
                err = type(e).__name__
            finally:
                if close:
                    close()
            dt = (time.time() - t0) * 1000.0
            coord.execute(
                "INSERT INTO xh174_results "
                "(round, host, outcome, error, t_fire_ms) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (rnd, a.host_tag, outcome, err, dt))
            log.append({"round": rnd, "arm": arm, "outcome": outcome,
                        "error": err, "t_fire_ms": round(dt, 1)})
            (outdir / f"174_crosshost_racer_{a.host_tag}.json").write_text(
                json.dumps({"probe": 174, "role": "race",
                            "host_tag": a.host_tag, "hostname": HOSTNAME,
                            "log": log}, indent=2))
    finally:
        coord.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("role", choices=["init", "reset", "serve", "race"])
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--arms", default="stock,gate")
    ap.add_argument("--gate-ms", type=int, default=2000)
    ap.add_argument("--host-tag", default=HOSTNAME)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--out", default="results/multiproc")
    a = ap.parse_args()
    {"init": role_init, "reset": role_reset,
     "serve": role_serve, "race": role_race}[a.role](a)


if __name__ == "__main__":
    main()
