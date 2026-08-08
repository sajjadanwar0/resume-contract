#!/usr/bin/env python3
import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
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

from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

KILL_POINTS = ("pre_insert", "post_insert", "post_persist", "none")
S2_RESULT_CHANNEL = "s2_out"

class S(TypedDict, total=False):
    x: int
    s1_out: str
    s2_out: str

def ledger_init(path):
    c = sqlite3.connect(path, timeout=60)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    c.commit()
    c.close()

def ledger_write(path, task):
    c = sqlite3.connect(path, timeout=60)
    c.execute("INSERT INTO effects (task) VALUES (?)", (task,))
    c.commit()
    c.close()

def ledger_counts(path):
    c = sqlite3.connect(path, timeout=60)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects GROUP BY task").fetchall()
    c.close()
    return dict(rows)

def _die():
    os.kill(os.getpid(), signal.SIGKILL)

def make_saver_cls(kill_point):
    """SqliteSaver that SIGKILLs the process right after the checkpoint put
    that follows s2's task-result put_writes has returned (both durable)."""
    class KillingSaver(SqliteSaver):
        _s2_written = False

        def put_writes(self, config, writes, task_id, *a, **kw):
            r = super().put_writes(config, writes, task_id, *a, **kw)
            if any(str(w[0]) == S2_RESULT_CHANNEL for w in (writes or ())):
                type(self)._s2_written = True
            return r

        def put(self, config, checkpoint, metadata, new_versions):
            r = super().put(config, checkpoint, metadata, new_versions)
            if kill_point == "post_persist" and type(self)._s2_written:
                _die()
            return r
    return KillingSaver

def build(ctx, kill_point):
    conn = sqlite3.connect(ctx["db"], check_same_thread=False, timeout=60)
    saver = make_saver_cls(kill_point)(conn)

    def s1(state: S):
        ledger_write(ctx["ledger"], "s1")
        return {"s1_out": "done"}

    def s2(state: S):
        if kill_point == "pre_insert":
            _die()
        ledger_write(ctx["ledger"], "s2")
        if kill_point == "post_insert":
            _die()
        return {"s2_out": "done"}

    g = StateGraph(S)
    g.add_node("s1", s1)
    g.add_node("s2", s2)
    g.add_edge(START, "s1")
    g.add_edge("s1", "s2")
    g.add_edge("s2", END)
    return g.compile(checkpointer=saver)

def _cfg(thread):
    return {"configurable": {"thread_id": thread}}

def _invoke(app, payload, thread):
    try:
        return app.invoke(payload, _cfg(thread), durability="sync")
    except TypeError:
        return app.invoke(payload, _cfg(thread))

def victim_main(ctx):
    app = build(ctx, ctx["kill"])
    res = _invoke(app, {"x": 1}, ctx["thread"])
    print(json.dumps({"completed": True, "result": res}, default=str))

def resume_main(ctx):
    app = build(ctx, "none")
    err, err_type, res = None, None, None
    try:
        res = _invoke(app, None, ctx["thread"])
    except Exception as e:
        err, err_type = f"{type(e).__name__}: {e}"[:300], type(e).__name__
    print(json.dumps({"result": res, "error": err, "error_type": err_type},
                     default=str))

def _spawn(mode, ctx):
    env = dict(os.environ, PROBE163_CTX=json.dumps(ctx))
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), mode], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def _wait(proc, timeout):
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
    parsed = {}
    for line in (out or "").splitlines():
        try:
            parsed = json.loads(line)
        except Exception:
            pass
    if err and err.strip():
        parsed["_stderr_tail"] = err.strip().splitlines()[-1][:300]
    parsed["returncode"] = proc.returncode
    return parsed

EXPECTED = {
    "pre_insert":   (0, 1, -signal.SIGKILL),
    "post_insert":  (1, 2, -signal.SIGKILL),
    "post_persist": (1, 1, -signal.SIGKILL),
    "none":         (None, 1, 0),
}

def run_arm(base, kill, rep):
    d0 = tempfile.mkdtemp(prefix=f"p163_{kill}_{rep}_", dir=base)
    ctx = {"db": f"{d0}/ckpt.sqlite", "ledger": f"{d0}/ledger.sqlite",
           "thread": "t", "kill": kill}
    ledger_init(ctx["ledger"])
    v = _wait(_spawn("victim", ctx), 120)
    post_crash = ledger_counts(ctx["ledger"])
    r = None
    if kill != "none":
        r = _wait(_spawn("resume", ctx), 120)
    post_resume = ledger_counts(ctx["ledger"])
    exp_crash, exp_final, exp_rc = EXPECTED[kill]
    return {
        "kill_point": kill, "rep": rep,
        "victim": v,
        "post_crash_counts": post_crash,
        "resume": r,
        "post_resume_counts": post_resume,
        "as_expected": (
            (exp_crash is None or post_crash.get("s2", 0) == exp_crash)
            and post_resume.get("s2", 0) == exp_final
            and post_resume.get("s1", 0) == 1
            and v.get("returncode") == exp_rc),
    }

def parent_main(args):
    base = tempfile.mkdtemp(prefix="probe163_")
    arms = {k: [run_arm(base, k, i) for i in range(args.reps)]
            for k in KILL_POINTS}
    matrix = {
        k: {"post_crash_s2": sorted({r["post_crash_counts"].get("s2", 0)
                                     for r in v}),
            "post_resume_s2": sorted({r["post_resume_counts"].get("s2", 0)
                                      for r in v}),
            "s1_always_one": all(r["post_resume_counts"].get("s1", 0) == 1
                                 for r in v),
            "all_as_expected": all(r["as_expected"] for r in v)}
        for k, v in arms.items()}
    result = {
        "probe": "163_p15_oracle_atomicity",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": {
            "langgraph": version("langgraph"),
            "langgraph-checkpoint": version("langgraph-checkpoint"),
            "langgraph-checkpoint-sqlite":
                version("langgraph-checkpoint-sqlite"),
        },
        "reps_per_arm": args.reps,
        "kill_matrix": matrix,
        "arms": arms,
        "stable": {
            "overcount_impossible_no_record_without_effect":
                all(matrix[k]["all_as_expected"] for k in KILL_POINTS),
            "loss_window_resolves_as_duplicate_not_undercount":
                matrix["post_insert"]["post_resume_s2"] == [2],
            "complete_boundary_never_reexecutes":
                matrix["post_persist"]["post_resume_s2"] == [1],
            "prefix_task_stable_at_one":
                all(matrix[k]["s1_always_one"] for k in KILL_POINTS),
        },
    }
    print(json.dumps(result, indent=2, default=str))
    out = Path(os.environ.get(
        "PROBE163_OUT",
        Path(__file__).resolve().parents[1] / "results" / "oracle"))
    out.mkdir(parents=True, exist_ok=True)
    (out / "163_results.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")
    (out / "163_stable.json").write_text(json.dumps(
        {"probe": result["probe"], "host": result["host"],
         "utc": result["utc"], "pins": result["pins"],
         "kill_matrix": result["kill_matrix"],
         "stable": result["stable"]}, indent=2) + "\n")
    print(f"\nwrote {out}/163_results.json and 163_stable.json",
          file=sys.stderr)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode in ("victim", "resume"):
        ctx = json.loads(os.environ["PROBE163_CTX"])
        {"victim": victim_main, "resume": resume_main}[mode](ctx)
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--reps", type=int, default=3)
        ap.add_argument("--smoke", action="store_true")
        args = ap.parse_args()
        if args.smoke:
            args.reps = 1
        parent_main(args)
