#!/usr/bin/env python3
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

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver

try:
    from remit import langgraph_shim as _rls
    HAVE_REMIT = True
except Exception as _e:
    HAVE_REMIT = False
    _REMIT_ERR = f"{type(_e).__name__}: {_e}"

HOSTNAME = socket.gethostname()
POLL_S = 0.002
IDLE_S = 0.05
ROUND_TIMEOUT_S = 180
TABLES = ("xh174_rounds", "xh174_ready", "xh174_effects", "xh174_results")

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
            t_invoke_ms DOUBLE PRECISION, arrive_at TIMESTAMPTZ,
            PRIMARY KEY (round, host))""")

def _ledger_write(dsn, rnd, host, thread, task):
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("INSERT INTO xh174_effects (round, host, thread, task) "
                  "VALUES (%s, %s, %s, %s)", (rnd, host, thread, task))

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
    saver.setup()
    return saver, conn.close

def _build_graph(dsn, saver, rnd, host_tag, gate_ms, thread):
    flag = {"fired": False}

    class S(TypedDict, total=False):
        pre_out: str
        decision: bool

    def pre(state):
        return {"pre_out": "done"}

    def gate(state):
        decision = interrupt({"q": "approve?"})
        if gate_ms:
            time.sleep(gate_ms / 1000.0)
        _ledger_write(dsn, rnd, host_tag, thread, "gate")
        flag["fired"] = True
        return {"decision": bool(decision)}

    g = StateGraph(S)
    g.add_node("pre", pre)
    g.add_node("gate", gate)
    g.add_edge(START, "pre")
    g.add_edge("pre", "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=saver), flag

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
    rnd = int(time.time()) * 100
    coord = _coord(a.dsn)
    try:
        for arm in arms:
            if arm == "gate" and not HAVE_REMIT:
                _fail(f"gate arm needs remit-contract: {_REMIT_ERR}")
            fires, losers, others, hostpairs = [], [], [], []
            offsets, fired_by = [], []
            for rep in range(reps):
                rnd += 1
                thread = f"xh174-{uuid.uuid4().hex[:8]}"
                saver, close = _make_saver(a.dsn, gated=False)
                try:
                    graph, _ = _build_graph(a.dsn, saver, rnd,
                                            "coordinator", a.gate_ms,
                                            thread)
                    graph.invoke({}, {"configurable":
                                      {"thread_id": thread}})
                finally:
                    close()
                coord.execute("INSERT INTO xh174_rounds "
                              "(round, thread, arm, gate_ms) "
                              "VALUES (%s, %s, %s, %s)",
                              (rnd, thread, arm, a.gate_ms))
                t0 = time.time()
                while True:
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
                while True:
                    done = coord.execute(
                        "SELECT host, outcome, error, arrive_at "
                        "FROM xh174_results WHERE round=%s",
                        (rnd,)).fetchall()
                    if len(done) >= 2:
                        break
                    if time.time() - t0 > ROUND_TIMEOUT_S:
                        _fail(f"round {rnd}: racers never finished")
                    time.sleep(POLL_S)
                n = coord.execute(
                    "SELECT COUNT(*) FROM xh174_effects "
                    "WHERE round=%s AND task='gate'", (rnd,)).fetchone()[0]
                fires.append(n)
                fired_by.append(sorted(r[0] for r in coord.execute(
                    "SELECT host FROM xh174_effects "
                    "WHERE round=%s AND task='gate'", (rnd,)).fetchall()))
                arr = [r[3] for r in done if r[3] is not None]
                offsets.append(round(abs((arr[0] - arr[1])
                                         .total_seconds()) * 1000.0, 1)
                               if len(arr) == 2 else None)
                losers.extend(e for _, o, e, _a in done
                              if o == "refused" and e)
                others.extend(e for _, o, e, _a in done
                              if o == "errored" and e)
                coord.execute("UPDATE xh174_rounds SET done=TRUE "
                              "WHERE round=%s", (rnd,))
            dist = {}
            for f in fires:
                dist[str(f)] = dist.get(str(f), 0) + 1
            same_host = any(len(set(p)) < 2 for p in hostpairs)
            real = [o for o in offsets if o is not None]
            cell = {"arm": arm, "gate_ms": a.gate_ms, "reps": reps,
                    "fires": fires, "distribution": dist,
                    "fired_by": fired_by,
                    "arrival_offsets_ms": offsets,
                    "max_offset_ms": (max(real) if real else None),
                    "raced": (bool(real)
                              and max(real) < a.gate_ms),
                    "loser_errors": sorted(set(losers)),
                    "racer_errors": sorted(set(others)),
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
                            "max_offset_ms", "raced", "loser_errors",
                            "racer_errors", "cross_host_attested")}
                          for c in cells]}
    p = outdir / "174_crosshost_matrix.json"
    p.write_text(json.dumps(receipt, indent=2))
    print(json.dumps({"receipt": str(p)}))

def role_race(a):
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    log = []
    coord = _coord(a.dsn)
    print(json.dumps({"racer": a.host_tag, "hostname": HOSTNAME,
                      "note": "looping; kill after serve prints its "
                              "receipt"}))

    def _reconnect():
        nonlocal coord
        try:
            coord.close()
        except Exception:
            pass
        time.sleep(1.0)
        coord = _coord(a.dsn)

    try:
        while True:
            try:
                row = coord.execute(
                    "SELECT r.round, r.thread, r.arm, r.gate_ms "
                    "FROM xh174_rounds r WHERE r.done=FALSE AND NOT EXISTS "
                    "(SELECT 1 FROM xh174_ready y WHERE y.round=r.round "
                    " AND y.host=%s) ORDER BY r.round LIMIT 1",
                    (a.host_tag,)).fetchone()
            except psycopg.errors.UndefinedTable:
                time.sleep(0.5)
                continue
            except psycopg.OperationalError:
                _reconnect()
                continue
            if row is None:
                time.sleep(IDLE_S)
                continue
            rnd, thread, arm, gate_ms = row
            close = None
            try:
                saver, close = _make_saver(a.dsn, gated=(arm == "gate"))
                graph, flag = _build_graph(a.dsn, saver, rnd, a.host_tag,
                                           gate_ms, thread)
            except Exception as e:
                if close:
                    close()
                log.append({"round": rnd, "arm": arm,
                            "outcome": "build_error",
                            "error": type(e).__name__, "t_invoke_ms": 0})
                time.sleep(0.5)
                continue
            try:
                coord.execute(
                    "INSERT INTO xh174_ready (round, host, hostname) "
                    "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (rnd, a.host_tag, HOSTNAME))
            except psycopg.errors.UndefinedTable:
                close(); time.sleep(0.5)
                continue
            except psycopg.OperationalError:
                close(); _reconnect()
                continue
            aborted = False
            while True:
                try:
                    rel = coord.execute(
                        "SELECT released FROM xh174_rounds "
                        "WHERE round=%s", (rnd,)).fetchone()
                except psycopg.errors.UndefinedTable:
                    aborted = True
                    break
                except psycopg.OperationalError:
                    _reconnect()
                    continue
                if rel is None:
                    aborted = True
                    break
                if rel[0]:
                    break
                time.sleep(POLL_S)
            if aborted:
                close(); time.sleep(0.5)
                continue
            arrive_at = None
            try:
                arrive_at = coord.execute("SELECT now()").fetchone()[0]
            except (psycopg.errors.UndefinedTable,
                    psycopg.OperationalError):
                pass
            outcome, err, t0 = "inert", "", time.time()
            try:
                graph.invoke(Command(resume=True),
                             {"configurable": {"thread_id": thread}})
                if flag["fired"]:
                    outcome = "fired"
            except Exception as e:
                err = type(e).__name__
                outcome = ("refused" if "RemitConsume" in err
                           else "errored")
            finally:
                close()
            dt = (time.time() - t0) * 1000.0
            try:
                coord.execute(
                    "INSERT INTO xh174_results "
                    "(round, host, outcome, error, t_invoke_ms, arrive_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (rnd, a.host_tag, outcome, err, dt, arrive_at))
            except (psycopg.errors.UndefinedTable,
                    psycopg.OperationalError):
                pass
            log.append({"round": rnd, "arm": arm, "outcome": outcome,
                        "error": err, "t_invoke_ms": round(dt, 1)})
            (outdir / f"174_crosshost_racer_{a.host_tag}.json").write_text(
                json.dumps({"probe": 174, "role": "race",
                            "host_tag": a.host_tag, "hostname": HOSTNAME,
                            "log": log}, indent=2))
    finally:
        try:
            coord.close()
        except Exception:
            pass

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
