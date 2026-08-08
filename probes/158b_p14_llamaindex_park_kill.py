#!/usr/bin/env python3
import asyncio
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

REQUIRED_WORKFLOWS = "2.22.2"

def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)

_wfv = version("llama-index-workflows")
if _wfv != REQUIRED_WORKFLOWS and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"llama-index-workflows=={_wfv} but the paper pin is "
          f"{REQUIRED_WORKFLOWS}. Interpreter: {sys.executable}. Launch with "
          f"envs/llamaindex, or set PROBE_ALLOW_OFFPIN=1.")

from workflows import Workflow, Context, step
from workflows.events import (
    StartEvent, StopEvent, InputRequiredEvent, HumanResponseEvent)

def ledger_init(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    c.commit()
    c.close()

def ledger_write(path, task):
    c = sqlite3.connect(path, timeout=30)
    c.execute("INSERT INTO effects (task) VALUES (?)", (task,))
    c.commit()
    c.close()

def ledger_counts(path):
    c = sqlite3.connect(path, timeout=30)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects GROUP BY task").fetchall()
    c.close()
    return dict(rows)

def make_wf(ledger_path):
    class WA(Workflow):
        @step
        async def pre(self, ctx: Context, ev: StartEvent) -> InputRequiredEvent:
            ledger_write(ledger_path, "pre")
            return InputRequiredEvent(prefix="approve?")

        @step
        async def post(self, ctx: Context, ev: HumanResponseEvent) -> StopEvent:
            ledger_write(ledger_path, f"post:{ev.response}")
            return StopEvent(result=f"answer={ev.response}")

    return WA(timeout=30)

async def _park(d):
    ledger_init(d["ledger"])
    wf = make_wf(d["ledger"])
    handler = wf.run()
    async for ev in handler.stream_events():
        if isinstance(ev, InputRequiredEvent):
            snap = handler.ctx.to_dict()
            Path(d["snap"]).write_text(json.dumps(snap))
            break
    Path(d["ready"]).write_text(json.dumps({"pid": os.getpid()}))
    await asyncio.sleep(600)

async def _resume(d, answer):
    ledger_init(d["ledger"])
    wf = make_wf(d["ledger"])
    snap = json.loads(Path(d["snap"]).read_text())
    ctx = Context.from_dict(wf, snap)
    handler = wf.run(ctx=ctx)
    handler.ctx.send_event(HumanResponseEvent(response=answer))
    res = await asyncio.wait_for(handler, timeout=25)
    print(json.dumps({"answer": answer, "result": str(res)}))

def _run_mode(mode, env, timeout=120):
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

def parent_main(out_dir):
    d0 = tempfile.mkdtemp(prefix="probe158b_")
    d = {"ledger": f"{d0}/ledger.sqlite", "snap": f"{d0}/ctx.json",
         "ready": f"{d0}/ready.flag"}
    env = dict(os.environ, PROBE158B_DIR=json.dumps(d))

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

    counts_at_kill = ledger_counts(d["ledger"])
    os.kill(victim.pid, signal.SIGKILL)
    victim.wait()
    exit_desc = ("SIGKILL" if victim.returncode == -signal.SIGKILL
                 else str(victim.returncode))

    yes = _run_mode("resume:YES", env)
    counts_after_yes = ledger_counts(d["ledger"])
    no = _run_mode("resume:NO", env)
    counts_after_no = ledger_counts(d["ledger"])

    result = {
        "probe": "158b_p14_llamaindex_park_kill",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": {"llama-index-workflows": _wfv},
        "crash_mechanism":
            "SIGKILL while the run is live-parked at InputRequiredEvent; the "
            "Context snapshot on disk is the durable artifact; "
            "barrier-synchronized, no timing window",
        "victim_exit": exit_desc,
        "ledger_at_kill": counts_at_kill,
        "resume_yes_life": yes,
        "ledger_after_yes": counts_after_yes,
        "resume_no_life": no,
        "ledger_after_no": counts_after_no,
        "stable": {
            "snapshot_survives_process_death":
                "answer=YES" in str(yes.get("result", "")),
            "eo_prefix_across_park_kill":
                counts_at_kill.get("pre", 0) == 1
                and counts_after_no.get("pre", 0) == 1,
            "post_per_branch_exactly_once":
                counts_after_no.get("post:YES", 0) == 1
                and counts_after_no.get("post:NO", 0) == 1,
            "fd_second_restore_own_answer":
                "answer=NO" in str(no.get("result", "")),
        },
    }
    stable_ok = all(v is True for v in result["stable"].values())
    result["stable"]["all_park_kill_cells_conformant"] = stable_ok

    print(json.dumps(result, indent=2, default=str))
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "158b_results.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n")
        (out / "158b_stable.json").write_text(
            json.dumps({"probe": result["probe"], "host": result["host"],
                        "utc": result["utc"], "pins": result["pins"],
                        "stable": result["stable"]}, indent=2) + "\n")
        print(f"\nwrote {out}/158b_results.json and 158b_stable.json",
              file=sys.stderr)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        default_out = Path(__file__).resolve().parents[1] / "results" / "parkkill"
        parent_main(os.environ.get("PROBE158B_OUT", str(default_out)))
    elif mode == "park":
        asyncio.run(_park(json.loads(os.environ["PROBE158B_DIR"])))
    elif mode.startswith("resume:"):
        asyncio.run(_resume(json.loads(os.environ["PROBE158B_DIR"]),
                            mode.split(":", 1)[1]))
    else:
        raise SystemExit(f"unknown mode {mode}")
