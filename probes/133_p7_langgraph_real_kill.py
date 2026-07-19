#!/usr/bin/env python3
"""
133_p7_langgraph_real_kill.py
Answers the "your crashes are fake" reviewer attack head-on: the crash here
is a real, unhandled SIGKILL (kill -9) of the OS process executing the
workflow, not a raised exception. Determinism is preserved by a barrier, not
timing: the victim parks inside task s2 AFTER s1's result is durably
recorded, signals readiness via a flag file, and is killed while parked.
A FRESH process then resumes from the durable SQLite checkpoint.

Question: with s1's result durably recorded via put_writes, does resume in a
new process re-execute s1?  (The exception-based probes 118/126/130 say yes;
this probe tests whether a real process death changes the verdict.)

Oracles: (1) on-disk SQLite effect ledger written by the victim process and
read by the parent + resumer (cross-process, survives the kill); (2) the
checkpointer database itself, inspected at kill time.

Modes (argv[1]): parent (default) | child | resume
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version

WORKDIR = os.environ.get("PROBE133_DIR")


def ledger_conn(path):
    # Tasks run on worker threads; use thread-safe, per-call-friendly conns.
    c = sqlite3.connect(path, timeout=30, check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    c.commit()
    return c


def ledger_write(path, task_name):
    c = sqlite3.connect(path, timeout=30)
    c.execute("INSERT INTO effects (task) VALUES (?)", (task_name,))
    c.commit()
    c.close()


def build_wf(ckpt_path, ledger_path, ready_flag, crashed_flag):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.func import entrypoint, task

    conn = sqlite3.connect(ckpt_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    ledger_conn(ledger_path).close()  # ensure table exists

    @task
    def s1(x: int) -> int:
        ledger_write(ledger_path, "s1")
        return x + 1

    @task
    def s2(x: int) -> int:
        if not os.path.exists(crashed_flag):
            # First life: park here until SIGKILLed. s1's write is already
            # durable (functional API persists task results as they finish
            # under durability='sync').
            open(ready_flag, "w").write("parked")
            time.sleep(600)
        ledger_write(ledger_path, "s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()

    return wf


def child_main(d):
    wf = build_wf(d["ckpt"], d["ledger"], d["ready"], d["crashed"])
    wf.invoke(1, {"configurable": {"thread_id": "kill"}}, durability="sync")


def resume_main(d):
    wf = build_wf(d["ckpt"], d["ledger"], d["ready"], d["crashed"])
    r = wf.invoke(1, {"configurable": {"thread_id": "kill"}}, durability="sync")
    print(json.dumps({"resume_result": r}))


def count_writes(ckpt_path):
    c = sqlite3.connect(ckpt_path)
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")]
    out = {}
    for t in tables:
        if "write" in t:
            out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    c.close()
    return out


def parent_main():
    d0 = tempfile.mkdtemp(prefix="probe133_")
    d = {"ckpt": f"{d0}/ckpt.sqlite", "ledger": f"{d0}/ledger.sqlite",
         "ready": f"{d0}/ready.flag", "crashed": f"{d0}/crashed.flag"}
    env = dict(os.environ, PROBE133_DIR=json.dumps(d))
    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "child"],
                             env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    # Barrier: wait until the victim is parked inside s2 (=> s1 finished).
    deadline = time.time() + 120
    while not os.path.exists(d["ready"]):
        if time.time() > deadline:
            child.kill()
            raise SystemExit("victim never reached the barrier")
        if child.poll() is not None:
            raise SystemExit(f"victim exited early: {child.returncode}")
        time.sleep(0.05)

    led = ledger_conn(d["ledger"])
    s1_at_kill = led.execute(
        "SELECT COUNT(*) FROM effects WHERE task='s1'").fetchone()[0]
    writes_at_kill = count_writes(d["ckpt"])

    os.kill(child.pid, signal.SIGKILL)          # the real crash
    child.wait()
    exit_desc = ("SIGKILL" if child.returncode == -signal.SIGKILL
                 else str(child.returncode))

    open(d["crashed"], "w").write("1")          # second life: s2 completes
    res = subprocess.run([sys.executable, os.path.abspath(__file__), "resume"],
                         env=env, capture_output=True, text=True, timeout=180)
    resume_out = {}
    for line in res.stdout.splitlines():
        try:
            resume_out = json.loads(line)
        except Exception:
            pass

    s1_total = led.execute(
        "SELECT COUNT(*) FROM effects WHERE task='s1'").fetchone()[0]
    s2_total = led.execute(
        "SELECT COUNT(*) FROM effects WHERE task='s2'").fetchone()[0]
    print(json.dumps({
        "langgraph_version": version("langgraph"),
        "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
        "crash_mechanism": "os SIGKILL of victim process, barrier-synchronized",
        "victim_exit": exit_desc,
        "s1_ledger_rows_at_kill": s1_at_kill,
        "checkpoint_write_rows_at_kill": writes_at_kill,
        "resume_process": "fresh interpreter, same durable DB",
        "resume_result": resume_out.get("resume_result"),
        "resume_stderr_tail": res.stderr.strip().splitlines()[-1] if res.stderr.strip() else None,
        "s1_ledger_rows_total": s1_total,
        "s2_ledger_rows_total": s2_total,
        "violation_completed_durable_task_reexecuted_after_real_kill":
            s1_total > s1_at_kill,
    }, indent=2))


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        parent_main()
    else:
        d = json.loads(os.environ["PROBE133_DIR"])
        (child_main if mode == "child" else resume_main)(d)
