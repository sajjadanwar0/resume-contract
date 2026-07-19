#!/usr/bin/env python3
"""
147_p9_dbos_engine_cells.py
The semantic half of the deferred durable-execution head-to-head: the paper's
abstract workload (a non-idempotent effect gated on a human decision, with a
crash after a durable step) executed on DBOS Transact (SQLite system
database, no server), with the same external-ledger oracle as every other
probe.

Cells (matrix conventions apply: measure, don't presume):
  T-EO-crash  worker SIGKILLed while parked at the gate, after step s1's
              result is durably recorded; a fresh process recovers the
              pending workflow; decision sent; ledger must show s1 exactly
              once (exactly-once step across process death) -- and the
              workflow function's re-execution with recorded step results is
              the memoized-replay discipline PC names as conformant.
  T-FD-fork   DBOS.fork_workflow(id, start_step=<recv step>) -- the engine's
              *explicit, documented branch-creating address* (the contract's
              FI discriminator, Definition 2) -- then a different decision
              sent to the fork; the fork's outcome must be its own value,
              with one gated effect per branch (FD with per-branch EO).
  T-CO-stray  a duplicate decision sent to the completed original run;
              observed disposition recorded (loud error vs silent), ledger
              must be unchanged (consume-once).
Plus an interposition-relevant overhead bench: p50 wall time of the full
gate protocol (start->gate, answer->complete) on DBOS/SQLite vs stock
LangGraph SqliteSaver vs the REMIT-shimmed saver, N iterations each, one
durable store per system, distinct run ids.

Environment: dbos (pip), sqlite system DB (absolute sqlite://// URL);
langgraph pins as per the paper for the bench legs. Verdicts are
deterministic protocol properties; timings are environment-bound and
replicate relatively, not absolutely.

Usage:
  python3 147_p9_dbos_engine_cells.py            # driver: all cells + bench
  python3 147_p9_dbos_engine_cells.py worker ... # internal worker modes
Outputs results JSON to results/engines/147_results.json (raw + stable view).
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from statistics import median

# ---------------------------------------------------------------- ledger ---
def ledger(path, add=None):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT)")
    if add is not None:
        c.execute("INSERT INTO effects (tag) VALUES (?)", (add,))
    c.commit()
    rows = [r[0] for r in c.execute("SELECT tag FROM effects ORDER BY n")]
    c.close()
    return rows


# ------------------------------------------------------------ DBOS worker ---
# Invoked as: worker <sysdb_path> <ledger_path> <mode> <workflow_id>
#   mode=start    launch, start gate_wf under workflow_id, then serve forever
#   mode=recover  launch (recovery of pending workflows), then serve forever
# The worker prints READY once launched; the driver polls files/DB for state.

def worker_main(sysdb, ledger_path, mode, wf_id):
    from dbos import DBOS, DBOSConfig, SetWorkflowID

    global LEDGER
    LEDGER = ledger_path
    config: DBOSConfig = {
        "name": "probe147",
        "system_database_url": f"sqlite:///{sysdb}",  # sysdb is absolute -> 4 slashes total
        "run_admin_server": False,
        "log_level": "WARNING",
        # SQLite has no LISTEN/NOTIFY, so recv() is served by polling; the
        # default ~1 s interval dominates gate-answer latency. A short
        # interval gives the mechanism-fair number; both are reported.
        "notification_listener_polling_interval_sec": float(
            __import__("os").environ.get("P147_POLL_SEC", "0.05")),
    }
    DBOS(config=config)

    @DBOS.step()
    def s1() -> int:
        ledger(LEDGER, add="s1")
        return 1

    @DBOS.step()
    def gated(decision: bool) -> int:
        ledger(LEDGER, add=f"gated:{decision}")
        return 1 if decision else 0

    @DBOS.workflow()
    def gate_wf() -> dict:
        a = s1()
        decision = DBOS.recv(topic="decision", timeout_seconds=120)
        g = gated(bool(decision))
        return {"value": a + g}

    DBOS.launch()
    if mode == "start":
        with SetWorkflowID(wf_id):
            DBOS.start_workflow(gate_wf)
    print("READY", flush=True)
    while True:
        time.sleep(0.2)


# ---------------------------------------------------------------- driver ----
def spawn_worker(sysdb, ledger_path, mode, wf_id):
    p = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "worker", sysdb, ledger_path, mode, wf_id],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    # wait for READY
    t0 = time.time()
    while time.time() - t0 < 60:
        line = p.stdout.readline()
        if "READY" in line:
            return p
        if p.poll() is not None:
            raise RuntimeError(f"worker died during launch:\n{line}{p.stdout.read()}")
    raise RuntimeError("worker launch timeout")


def wait_for(pred, timeout=30, interval=0.1, what="condition"):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"timed out waiting for {what}")


def client(sysdb):
    """A NON-EXECUTING admin client: sends, retrieves, forks -- never runs
    workflow code (the executing runtimes are the worker subprocesses)."""
    from dbos import DBOSClient

    return DBOSClient(system_database_url=f"sqlite:///{sysdb}")


def step_field(step, *names):
    for n in names:
        if isinstance(step, dict) and step.get(n) is not None:
            return step.get(n)
        v = getattr(step, n, None)
        if v is not None:
            return v
    return None


def handle_id(h):
    v = getattr(h, "workflow_id", None)
    if v:
        return v
    for m in ("get_workflow_id",):
        f = getattr(h, m, None)
        if f:
            return f()
    return str(h)


def cell_eo_crash_and_fd_and_co(tmp):
    sysdb = f"{tmp}/sys.sqlite"
    lp = f"{tmp}/ledger.sqlite"
    wf_id = "run-eo-1"
    out = {}

    # -- run to the gate, s1 durable, then SIGKILL the worker ---------------
    w1 = spawn_worker(sysdb, lp, "start", wf_id)
    wait_for(lambda: "s1" in ledger(lp), what="s1 effect")
    time.sleep(0.5)  # let the recv checkpoint settle after the effect
    os.kill(w1.pid, signal.SIGKILL)
    w1.wait()
    out["ledger_at_kill"] = ledger(lp)

    # -- recover in a fresh process; observe whether recovery is automatic --
    w2 = spawn_worker(sysdb, lp, "recover", wf_id)
    DBOS = client(sysdb)
    h = DBOS.retrieve_workflow(wf_id)
    status0 = h.get_status().status
    out["status_after_recovery_launch"] = str(status0)

    recovery_path = "automatic"
    try:
        wait_for(lambda: h.get_status().status in ("PENDING", "RUNNING"), timeout=10,
                 what="workflow active after recovery launch")
    except TimeoutError:
        recovery_path = "explicit-resume"
        DBOS.resume_workflow(wf_id)
    out["recovery_path"] = recovery_path

    # -- answer the gate ----------------------------------------------------
    DBOS.send(wf_id, True, topic="decision")
    res = h.get_result()
    out["result_original"] = res
    led = ledger(lp)
    out["ledger_after_complete"] = led
    out["eo_crash_conform"] = (led.count("s1") == 1) and (led.count("gated:True") == 1)
    out["pc_memoized_replay"] = led.count("s1") == 1  # function replayed, step served recorded

    # -- FD via fork_workflow at the recv step ------------------------------
    steps = DBOS.list_workflow_steps(wf_id)
    out["steps"] = [
        {"function_id": step_field(s, "function_id"),
         "name": step_field(s, "function_name", "name")}
        for s in steps
    ]
    recv_fid = None
    for s in steps:
        nm = (step_field(s, "function_name", "name") or "").lower()
        if "recv" in nm:
            recv_fid = step_field(s, "function_id")
            break
    out["fork_start_step"] = recv_fid
    fork_conform = None
    if recv_fid is not None:
        fh = DBOS.fork_workflow(wf_id, recv_fid)
        fork_id = handle_id(fh)
        out["fork_id"] = str(fork_id)
        fhandle = DBOS.retrieve_workflow(fork_id)
        wait_for(lambda: fhandle.get_status().status in ("PENDING", "RUNNING", "ENQUEUED"),
                 timeout=15, what="fork active")
        time.sleep(0.5)
        DBOS.send(fork_id, False, topic="decision")
        fres = fhandle.get_result()
        out["result_fork"] = fres
        led2 = ledger(lp)
        out["ledger_after_fork"] = led2
        fork_conform = (
                fres.get("value") == 1                      # f(False): 1 + 0
                and led2.count("gated:False") == 1          # fork branch effect once, own value
                and led2.count("gated:True") == 1           # original branch untouched
                and led2.count("s1") == 1                   # prefix step NOT re-executed on fork
        )
    out["fd_fork_conform"] = fork_conform

    # -- CO: stray duplicate decision to the completed original -------------
    led_before = ledger(lp)
    stray = {"raised": None, "detail": None}
    try:
        DBOS.send(wf_id, True, topic="decision")
        stray["raised"] = False
    except Exception as e:  # loud rejection
        stray["raised"] = True
        stray["detail"] = f"{type(e).__name__}: {e}"[:200]
    time.sleep(1.0)
    led_after = ledger(lp)
    out["stray"] = stray
    out["co_stray_conform"] = led_after == led_before
    out["ledger_final"] = led_after

    os.kill(w2.pid, signal.SIGKILL)
    w2.wait()
    return out


# -------------------------------------------------------------- overhead ---
def bench_dbos(n):
    tmp = tempfile.mkdtemp(prefix="p147_bench_dbos_")
    sysdb = f"{tmp}/sys.sqlite"
    lp = f"{tmp}/ledger.sqlite"
    w = spawn_worker(sysdb, lp, "recover", "none")  # launch only; workflows started via client
    DBOS = client(sysdb)
    # client-side start: run workflows from the driver's own DBOS runtime
    initial, resume = [], []
    from dbos import SetWorkflowID
    # Register the same workflow shape in the client runtime:
    # simplest is to run the protocol via the worker's registration through
    # send/retrieve on ids started by a starter subprocess per iteration --
    # to keep parity and avoid double registration we time via starter procs.
    os.kill(w.pid, signal.SIGKILL)
    w.wait()
    for i in range(n):
        wf_id = f"bench-{i}"
        t0 = time.perf_counter()
        wi = spawn_worker(sysdb, lp, "start", wf_id)
        h = DBOS.retrieve_workflow(wf_id)
        wait_for(lambda: len([t for t in ledger(lp) if t == "s1"]) == i + 1,
                 interval=0.005, what="s1")
        t1 = time.perf_counter()
        DBOS.send(wf_id, True, topic="decision")
        # Resume latency = answer sent -> gated effect durable in the
        # external ledger (client-side result polling excluded; get_result
        # is awaited untimed afterwards for correctness).
        wait_for(lambda: len([t for t in ledger(lp) if t.startswith("gated:")]) == i + 1,
                 interval=0.005, what="gated effect")
        t2 = time.perf_counter()
        h.get_result()
        initial.append((t1 - t0) * 1000)
        resume.append((t2 - t1) * 1000)
        os.kill(wi.pid, signal.SIGKILL)
        wi.wait()
    return {"n": n, "initial_p50_ms": round(median(initial), 1),
            "resume_p50_ms": round(median(resume), 1),
            "note": "initial = spawn+launch+start->s1 durable; resume = send->gated effect durable (ledger-observed, client result-poll excluded)"}


def bench_langgraph(n, shim):
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt, Command
    from langgraph.checkpoint.sqlite import SqliteSaver

    tmp = tempfile.mkdtemp(prefix=f"p147_bench_lg_{'remit' if shim else 'stock'}_")
    conn = sqlite3.connect(f"{tmp}/ckpt.sqlite", check_same_thread=False)
    if shim:
        import remit
        saver = remit.wrap(SqliteSaver, conn)
    else:
        saver = SqliteSaver(conn)
    lp = f"{tmp}/ledger.sqlite"

    class S(TypedDict):
        value: int

    def node(state: S):
        allow = interrupt("Allow?")
        ledger(lp, add=f"gated:{allow}")
        return {"value": state["value"] + (1 if allow else 0)}

    app = (StateGraph(S).add_node("node", node).add_edge(START, "node")
           .add_edge("node", END).compile(checkpointer=saver))
    initial, resume = [], []
    for i in range(n):
        cfg = {"configurable": {"thread_id": f"b{i}"}}
        t0 = time.perf_counter()
        app.invoke({"value": 0}, cfg)
        t1 = time.perf_counter()
        app.invoke(Command(resume=True), cfg)
        t2 = time.perf_counter()
        initial.append((t1 - t0) * 1000)
        resume.append((t2 - t1) * 1000)
    return {"n": n, "initial_p50_ms": round(median(initial), 1),
            "resume_p50_ms": round(median(resume), 1)}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        worker_main(*sys.argv[2:])
        return

    n_bench = int(os.environ.get("P147_BENCH_N", "30"))
    tmp = tempfile.mkdtemp(prefix="p147_cells_")
    results = {"probe": 147, "engine": "dbos", "cells": None, "bench": {}}
    try:
        from importlib.metadata import version
        results["versions"] = {"dbos": version("dbos"), "python": sys.version.split()[0]}
        try:
            results["versions"]["langgraph"] = version("langgraph")
        except Exception:
            pass
    except Exception:
        pass

    results["cells"] = cell_eo_crash_and_fd_and_co(tmp)
    results["bench"]["dbos_sqlite_poll50ms"] = bench_dbos(n_bench)
    os.environ["P147_POLL_SEC"] = "1.0"
    results["bench"]["dbos_sqlite_poll1s_default"] = bench_dbos(max(10, n_bench // 3))
    os.environ["P147_POLL_SEC"] = "0.05"
    try:
        results["bench"]["langgraph_stock_sqlite"] = bench_langgraph(n_bench, shim=False)
        results["bench"]["langgraph_remit_sqlite"] = bench_langgraph(n_bench, shim=True)
    except Exception as e:
        results["bench"]["langgraph_error"] = f"{type(e).__name__}: {e}"[:200]

    c = results["cells"]
    results["stable"] = {
        "eo_crash": "conform" if c.get("eo_crash_conform") else "VIOLATION",
        "pc_memoized_replay": "conform" if c.get("pc_memoized_replay") else "VIOLATION",
        "fd_fork": ("conform" if c.get("fd_fork_conform")
                    else ("untestable" if c.get("fd_fork_conform") is None else "VIOLATION")),
        "co_stray": "conform" if c.get("co_stray_conform") else "VIOLATION",
        "stray_disposition": ("loud" if (c.get("stray") or {}).get("raised") else "silent-inert"),
        "recovery_path": c.get("recovery_path"),
        "bench_note": ("dbos initial includes worker spawn+launch; compare resume p50s and "
                       "treat absolute numbers as environment-bound"),
    }

    os.makedirs("results/engines", exist_ok=True)
    with open("results/engines/147_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(json.dumps(results["stable"], indent=2))
    print("bench:", json.dumps(results["bench"], indent=2))


if __name__ == "__main__":
    main()