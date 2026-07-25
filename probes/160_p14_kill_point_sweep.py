#!/usr/bin/env python3
"""
160_p14_kill_point_sweep.py  (campaign p14)

Systematic (exhaustive-at-the-interface) crash-point exploration, answering
the "your kill points are hand-picked" attack on probes 133/137: instead of
three chosen barriers, a counting saver SIGKILLs the run after EVERY
persistence operation (put / put_writes) the protocol performs, one kill
point per run, sweeping K = 1..N where N is measured by a dry run.  This is
lineage-driven-fault-injection-lite at the checkpointer interface -- the
layer the contract governs; power loss / torn writes remain the
crash-consistency literature's layer, per the paper's crash model.

Workload: the probe-133 two-task functional-API protocol (s1 effect, s2
effect, durability='sync', SqliteSaver), resumed in a FRESH process with a
stock saver after each kill.

Per kill point K: the op sequence prefix at kill, ledger at kill, resume
success, final ledger (duplicate-effect detection for s1 and s2).

Summary fields (stable):
  kill_points_total, points_unrecoverable (thesis predicts: none on
  LangGraph), points_with_duplicate_effect (thesis predicts: every point
  where a task's completion is durable but the run had not finished --
  the crash-path EO violation, now at every boundary, not three).

Usage:  .venv/bin/python3 probes/160_p14_kill_point_sweep.py
Modes (argv[1], internal): child | resume
"""
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_LANGGRAPH = "1.2.9"


def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)


_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"langgraph=={_lg} but the paper pin is {REQUIRED_LANGGRAPH}. "
          f"Interpreter: {sys.executable}. Launch with envs/langgraph-durable "
          f"or set PROBE_ALLOW_OFFPIN=1.")

from langgraph.checkpoint.sqlite import SqliteSaver     # noqa: E402
from langgraph.func import entrypoint, task             # noqa: E402


# ------------------------------------------------------------------ ledger
def ledger_init(path):
    c = sqlite3.connect(path, timeout=60)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    c.commit()
    c.close()


def ledger_write(path, name):
    c = sqlite3.connect(path, timeout=60)
    c.execute("INSERT INTO effects (task) VALUES (?)", (name,))
    c.commit()
    c.close()


def ledger_counts(path):
    c = sqlite3.connect(path, timeout=60)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects GROUP BY task").fetchall()
    c.close()
    return dict(rows)


def trace_append(path, op):
    with open(path, "a") as f:
        f.write(json.dumps({"op": op, "t": time.time()}) + "\n")


def trace_read(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l)["op"] for l in open(path) if l.strip()]


# ------------------------------------------------- counting / killing saver
def make_saver(d, kill_after):
    conn = sqlite3.connect(d["ckpt"], check_same_thread=False, timeout=60)

    class CountingSaver(SqliteSaver):
        _ops = 0
        _lock = threading.Lock()   # serializes persistence so the park
                                   # freezes the durable prefix at exactly K
                                   # completed ops (the flush is otherwise
                                   # multi-threaded and would keep landing)

        def put(self, config, checkpoint, metadata, new_versions):
            with CountingSaver._lock:
                r = super().put(config, checkpoint, metadata, new_versions)
                self._tick("put")
            return r

        def put_writes(self, config, writes, task_id, task_path=""):
            with CountingSaver._lock:
                r = super().put_writes(config, writes, task_id, task_path)
                self._tick("put_writes")
            return r

        def _tick(self, op):
            CountingSaver._ops += 1
            trace_append(d["trace"], op)
            if kill_after and CountingSaver._ops == kill_after:
                Path(d["ready"]).write_text(op)
                time.sleep(600)   # parked holding the lock; parent SIGKILLs

    return CountingSaver(conn)


def build_wf(d, kill_after):
    saver = make_saver(d, kill_after)
    ledger_init(d["ledger"])

    @task
    def s1(x: int) -> int:
        ledger_write(d["ledger"], "s1")
        return x + 1

    @task
    def s2(x: int) -> int:
        ledger_write(d["ledger"], "s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()

    return wf


def _invoke(wf):
    try:
        return wf.invoke(1, {"configurable": {"thread_id": "sweep"}},
                         durability="sync")
    except TypeError:
        return wf.invoke(1, {"configurable": {"thread_id": "sweep"}})


def child_main(d):
    wf = build_wf(d, d["kill_after"])
    r = _invoke(wf)
    print(json.dumps({"completed": True, "result": r}, default=str))


def resume_main(d):
    wf = build_wf(d, 0)                       # stock counting-only saver
    cfg = {"configurable": {"thread_id": "sweep"}}
    try:
        snap = wf.get_state(cfg)
        next_before = list(snap.next) if snap and snap.next else []
    except Exception as e:
        next_before = [f"get_state_error:{type(e).__name__}"]
    err, r = None, None
    try:
        r = _invoke(wf)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    print(json.dumps({"next_before_resume": next_before,
                      "resume_result": r, "resume_error": err}, default=str))


def _run(mode, env, timeout=120):
    p = subprocess.run([sys.executable, os.path.abspath(__file__), mode],
                       env=env, capture_output=True, text=True,
                       timeout=timeout)
    out = {}
    for line in p.stdout.splitlines():
        try:
            out = json.loads(line)
        except Exception:
            pass
    if p.stderr.strip():
        out["_stderr_tail"] = p.stderr.strip().splitlines()[-1]
    return out


def one_point(base, K, total_ops):
    d0 = tempfile.mkdtemp(prefix=f"p160_k{K}_", dir=base)
    d = {"ckpt": f"{d0}/ckpt.sqlite", "ledger": f"{d0}/ledger.sqlite",
         "trace": f"{d0}/trace.jsonl", "ready": f"{d0}/ready.flag",
         "kill_after": K}
    env = dict(os.environ, PROBE160_DIR=json.dumps(d))
    child = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "child"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 90
    completed_without_kill = False
    while not os.path.exists(d["ready"]):
        if child.poll() is not None:
            completed_without_kill = True
            break
        if time.time() > deadline:
            child.kill()
            raise SystemExit(f"K={K}: child neither parked nor finished")
        time.sleep(0.02)
    row = {"K": K}
    if completed_without_kill:
        row.update({"completed_without_kill": True,
                    "ops_trace": trace_read(d["trace"]),
                    "ledger_final": ledger_counts(d["ledger"])})
        return row
    op_at_kill = Path(d["ready"]).read_text()
    ops_at_kill = trace_read(d["trace"])        # snapshot BEFORE resume life
    ledger_at_kill = ledger_counts(d["ledger"])
    os.kill(child.pid, signal.SIGKILL)
    child.wait()
    res = _run("resume", env)
    ledger_final = ledger_counts(d["ledger"])
    run_complete_at_kill = len(ops_at_kill) >= total_ops
    row.update({
        "op_at_kill": op_at_kill,
        "ops_completed_at_kill": ops_at_kill,
        "durable_prefix_frozen_at_exactly_K": len(ops_at_kill) == K,
        "ledger_at_kill": ledger_at_kill,
        "next_before_resume": res.get("next_before_resume"),
        "run_complete_at_kill": run_complete_at_kill,
        "resume_error": res.get("resume_error"),
        "resume_result": res.get("resume_result"),
        "ledger_final": ledger_final,
        "dup_s1": ledger_final.get("s1", 0) > 1,
        "dup_s2": ledger_final.get("s2", 0) > 1,
        "any_duplicate": ledger_final.get("s1", 0) > 1
        or ledger_final.get("s2", 0) > 1,
        "recoverable": res.get("resume_error") is None,
    })
    return row


def parent_main():
    base = tempfile.mkdtemp(prefix="probe160_")
    # dry run: measure the protocol's persistence-op count
    d0 = tempfile.mkdtemp(prefix="p160_dry_", dir=base)
    d = {"ckpt": f"{d0}/ckpt.sqlite", "ledger": f"{d0}/ledger.sqlite",
         "trace": f"{d0}/trace.jsonl", "ready": f"{d0}/ready.flag",
         "kill_after": 0}
    env = dict(os.environ, PROBE160_DIR=json.dumps(d))
    dry = _run("child", env)
    ops = trace_read(d["trace"])
    total = len(ops)
    if total == 0:
        raise SystemExit("dry run recorded zero persistence ops -- "
                         "saver wrapper not engaged")

    rows = [one_point(base, K, total) for K in range(1, total + 1)]
    complete_pts = [r["K"] for r in rows if r.get("run_complete_at_kill")]
    dups = [r["K"] for r in rows
            if r.get("any_duplicate") and not r.get("run_complete_at_kill")]
    unrec = [r["K"] for r in rows if r.get("recoverable") is False]
    incomplete = [r["K"] for r in rows if r.get("completed_without_kill")]

    result = {
        "probe": "160_p14_kill_point_sweep",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": {
            "langgraph": version("langgraph"),
            "langgraph-checkpoint": version("langgraph-checkpoint"),
            "langgraph-checkpoint-sqlite":
                version("langgraph-checkpoint-sqlite"),
        },
        "protocol": "probe-133 two-task functional API, durability='sync'",
        "dry_run": {"result": dry, "ops_sequence": ops, "ops_total": total},
        "per_kill_point": rows,
        "stable": {
            "kill_points_total": total,
            "ops_sequence": ops,
            "points_with_duplicate_effect": dups,
            "points_run_complete_at_kill_excluded_from_dup_accounting":
                complete_pts,
            "points_unrecoverable": unrec,
            "points_completed_without_kill": incomplete,
            "all_points_recoverable": not unrec,
            "freeze_exact_at_every_point": all(
                r.get("durable_prefix_frozen_at_exactly_K") for r in rows
                if not r.get("completed_without_kill")),
            "note": "task execution on this protocol completes before the "
                    "sync flush finishes, so kill points vary the DURABLE "
                    "prefix; the contract's subject -- what the durable "
                    "state licenses on resume -- is exactly that prefix",
        },
    }
    print(json.dumps(result, indent=2, default=str))
    out = Path(os.environ.get(
        "PROBE160_OUT",
        Path(__file__).resolve().parents[1] / "results" / "parkkill"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "160_results.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")
    (out / "160_stable.json").write_text(json.dumps(
        {"probe": result["probe"], "host": result["host"],
         "utc": result["utc"], "pins": result["pins"],
         "stable": result["stable"]}, indent=2) + "\n")
    print(f"\nwrote {out}/160_results.json and 160_stable.json",
          file=sys.stderr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        parent_main()
    else:
        d = json.loads(os.environ["PROBE160_DIR"])
        (child_main if mode == "child" else resume_main)(d)
