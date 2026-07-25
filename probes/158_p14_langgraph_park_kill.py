#!/usr/bin/env python3
"""
158_p14_langgraph_park_kill.py  (campaign p14: park-kill matrix)

Kill location no prior probe covers: SIGKILL of the process while the run is
PARKED AT THE INTERRUPT awaiting the human -- the canonical durable-HITL crash
(server dies overnight while an approval is pending).  Probe 133 kills DURING
execution (inside a task, after a durable write); this probe kills a process
whose invoke() has already RETURNED with the interrupt, the thread durably
parked.  The TLA+ module's CrashRecover carries the precondition ~waiting, so
this location is outside the model's transition relation; the harness must
therefore cover it empirically (reviewer item F9/M2).

Protocol (all cross-process, fresh interpreter per life):
  life 1 (mode=park):   invoke to the interrupt on SqliteSaver; write barrier;
                        sleep; parent SIGKILLs while parked.
  life 2 (mode=resume): fresh process; get_state (is the interrupt visible?);
                        invoke(Command(resume=True)).
  life 3 (mode=stray):  fresh process; Command(resume=False) at the ordinary
                        address of the now-completed thread (CO).

Oracles: on-disk SQLite effect ledger (survives the kill, read by every life
and by the parent) + the checkpointer database inspected at kill time.

Verdict fields (stable):
  interrupt_survives_process_death   park durability: pending interrupt visible
                                     to a fresh process before any resume
  eo_prefix_across_park_kill         pre-interrupt effect fired exactly once
                                     across the kill (ledger total == 1)
  gate_effect_exactly_once_supplied  gate fired once, with the resumed value
  pc_prefix_state_preserved          prefix-computed channel visible after
                                     resume (not rebuilt from initial values)
  co_stray_inert                     post-completion stray resume adds nothing

Usage:  .venv/bin/python3 probes/158_p14_langgraph_park_kill.py
Modes (argv[1]): parent (default) | park | resume | stray
"""
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
THREAD = "parkkill"
CFG = {"configurable": {"thread_id": THREAD}}


def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)


_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"langgraph=={_lg} but the paper pin is {REQUIRED_LANGGRAPH}. "
          f"Interpreter: {sys.executable}. Launch with the pinned env "
          f"(envs/langgraph-durable), or set PROBE_ALLOW_OFFPIN=1 for a "
          f"non-paper exploratory run.")

from typing import TypedDict                      # noqa: E402
from langgraph.graph import StateGraph, START, END  # noqa: E402
from langgraph.types import interrupt, Command      # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402


class S(TypedDict, total=False):
    x: int
    pre_out: str
    decision: bool


# ------------------------------------------------------------------ ledger
def ledger_init(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT, task TEXT)")
    c.commit()
    c.close()


def ledger_write(path, thread, task):
    c = sqlite3.connect(path, timeout=30)
    c.execute("INSERT INTO effects (thread, task) VALUES (?, ?)",
              (thread, task))
    c.commit()
    c.close()


def ledger_counts(path):
    c = sqlite3.connect(path, timeout=30)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects WHERE thread=? GROUP BY task",
        (THREAD,)).fetchall()
    c.close()
    return dict(rows)


def checkpoint_rows(ckpt_path):
    c = sqlite3.connect(ckpt_path, timeout=30)
    out = {}
    for (t,) in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        try:
            out[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = None
    c.close()
    return out


# ------------------------------------------------------------------ graph
def build(d):
    conn = sqlite3.connect(d["ckpt"], check_same_thread=False, timeout=30)
    saver = SqliteSaver(conn)
    ledger_init(d["ledger"])

    def pre(state: S):
        ledger_write(d["ledger"], THREAD, "pre")
        return {"pre_out": "pre:done"}

    def gate(state: S):
        v = interrupt({"q": "approve?"})
        ledger_write(d["ledger"], THREAD, f"gate:{v}")
        return {"decision": v}

    g = StateGraph(S)
    g.add_node("pre", pre)
    g.add_node("gate", gate)
    g.add_edge(START, "pre")
    g.add_edge("pre", "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=saver)


def _invoke(app, payload):
    """invoke with durability='sync' where supported; record which."""
    try:
        return app.invoke(payload, CFG, durability="sync"), "sync"
    except TypeError:
        return app.invoke(payload, CFG), "default"


# ------------------------------------------------------------------ lives
def park_main(d):
    app = build(d)
    res, dur = _invoke(app, {"x": 1})
    interrupted = "__interrupt__" in res
    Path(d["ready"]).write_text(json.dumps(
        {"pid": os.getpid(), "interrupted": interrupted, "durability": dur}))
    time.sleep(600)                      # parked; parent SIGKILLs here


def resume_main(d):
    app = build(d)
    snap = app.get_state(CFG)
    pending_next = list(snap.next) if snap and snap.next else []
    try:
        has_int = any(bool(getattr(t, "interrupts", ())) for t in snap.tasks)
    except Exception:
        has_int = None
    res, dur = _invoke(app, Command(resume=True))
    print(json.dumps({
        "state_next_before_resume": pending_next,
        "pending_interrupt_visible": has_int,
        "durability": dur,
        "resume_result": res,
    }, default=str))


def stray_main(d):
    app = build(d)
    err = None
    res = None
    try:
        res, _ = _invoke(app, Command(resume=False))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    print(json.dumps({"stray_result": res, "stray_error": err}, default=str))


def _run_mode(mode, env, timeout=180):
    p = subprocess.run([sys.executable, os.path.abspath(__file__), mode],
                       env=env, capture_output=True, text=True,
                       timeout=timeout)
    out = {}
    for line in p.stdout.splitlines():
        try:
            out = json.loads(line)
        except Exception:
            pass
    out["_stderr_tail"] = (p.stderr.strip().splitlines()[-1]
                           if p.stderr.strip() else None)
    return out


# ------------------------------------------------------------------ parent
def parent_main(out_dir):
    d0 = tempfile.mkdtemp(prefix="probe158_")
    d = {"ckpt": f"{d0}/ckpt.sqlite", "ledger": f"{d0}/ledger.sqlite",
         "ready": f"{d0}/ready.flag"}
    env = dict(os.environ, PROBE158_DIR=json.dumps(d))

    victim = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "park"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 120
    while not os.path.exists(d["ready"]):
        if time.time() > deadline:
            victim.kill()
            raise SystemExit("victim never reached the park barrier")
        if victim.poll() is not None:
            raise SystemExit(f"victim exited early: {victim.returncode}")
        time.sleep(0.05)

    park_info = json.loads(Path(d["ready"]).read_text())
    counts_at_kill = ledger_counts(d["ledger"])
    ckpt_at_kill = checkpoint_rows(d["ckpt"])

    os.kill(victim.pid, signal.SIGKILL)          # the crash: parked, not mid-task
    victim.wait()
    exit_desc = ("SIGKILL" if victim.returncode == -signal.SIGKILL
                 else str(victim.returncode))

    resume_out = _run_mode("resume", env)
    counts_after_resume = ledger_counts(d["ledger"])
    stray_out = _run_mode("stray", env)
    counts_after_stray = ledger_counts(d["ledger"])

    gate_true = counts_after_resume.get("gate:True", 0)
    gate_false = counts_after_resume.get("gate:False", 0)
    rr = resume_out.get("resume_result") or {}
    result = {
        "probe": "158_p14_langgraph_park_kill",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": {
            "langgraph": version("langgraph"),
            "langgraph-checkpoint": version("langgraph-checkpoint"),
            "langgraph-checkpoint-sqlite":
                version("langgraph-checkpoint-sqlite"),
        },
        "crash_mechanism":
            "SIGKILL of the process while the thread is parked at the "
            "interrupt (invoke returned; checkpoint durable); "
            "barrier-synchronized, no timing window",
        "victim_exit": exit_desc,
        "park_life": park_info,
        "ledger_at_kill": counts_at_kill,
        "checkpoint_rows_at_kill": ckpt_at_kill,
        "resume_life": resume_out,
        "ledger_after_resume": counts_after_resume,
        "stray_life": stray_out,
        "ledger_after_stray": counts_after_stray,
        "stable": {
            "interrupt_survives_process_death":
                resume_out.get("state_next_before_resume") == ["gate"]
                and resume_out.get("pending_interrupt_visible") in (True, None),
            "eo_prefix_across_park_kill":
                counts_at_kill.get("pre", 0) == 1
                and counts_after_stray.get("pre", 0) == 1,
            "gate_effect_exactly_once_supplied":
                gate_true == 1 and gate_false == 0,
            "pc_prefix_state_preserved":
                isinstance(rr, dict) and rr.get("pre_out") == "pre:done",
            "co_stray_inert":
                counts_after_stray == counts_after_resume
                and stray_out.get("stray_error") is None,
        },
    }
    stable_ok = all(v is True for v in result["stable"].values())
    result["stable"]["all_park_kill_cells_conformant"] = stable_ok

    print(json.dumps(result, indent=2, default=str))
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "158_results.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n")
        (out / "158_stable.json").write_text(
            json.dumps({"probe": result["probe"], "host": result["host"],
                        "utc": result["utc"], "pins": result["pins"],
                        "stable": result["stable"]}, indent=2) + "\n")
        print(f"\nwrote {out}/158_results.json and 158_stable.json",
              file=sys.stderr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        default_out = Path(__file__).resolve().parents[1] / "results" / "parkkill"
        out_dir = os.environ.get("PROBE158_OUT", str(default_out))
        parent_main(out_dir)
    else:
        d = json.loads(os.environ["PROBE158_DIR"])
        {"park": park_main, "resume": resume_main,
         "stray": stray_main}[mode](d)
