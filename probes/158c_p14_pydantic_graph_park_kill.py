#!/usr/bin/env python3
"""
158c_p14_pydantic_graph_park_kill.py  (campaign p14: park-kill matrix)

pydantic-graph arm of the park-kill location.  Probe 119 established that a
crash INSIDE a node leaves FileStatePersistence unrestorable
(GraphRuntimeError at iter_from_persistence).  This probe kills at the OTHER
location: the run is stepped with graph.iter to the clean point BETWEEN nodes
-- NodeA completed and snapshotted, NodeB created but not started -- the
process parks there and is SIGKILLed.  A fresh interpreter then attempts
iter_from_persistence.

Question (discovery cell, either answer is a paper datum): is the
unrecoverability of 119 specific to mid-node crash state, or does the plane
fail to restore even a clean between-node snapshot after process death?

Oracle: on-disk SQLite effect ledger written by the node bodies.

Usage:  .venv/bin/python3 probes/158c_p14_pydantic_graph_park_kill.py
Modes (argv[1]): parent (default) | park | resume
"""
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
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_PG = "1.107.1"


def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)


_pgv = version("pydantic-graph")
if _pgv != REQUIRED_PG and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"pydantic-graph=={_pgv} but the paper pin is {REQUIRED_PG}. "
          f"Interpreter: {sys.executable}. Launch with envs/pydantic-graph, "
          f"or set PROBE_ALLOW_OFFPIN=1.")

from pydantic_graph import BaseNode, End, Graph, GraphRunContext   # noqa: E402
from pydantic_graph.persistence.file import FileStatePersistence   # noqa: E402
from pydantic import BaseModel                                     # noqa: E402

LEDGER = {"path": None}


def ledger_init(path):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    c.commit()
    c.close()


def ledger_write(task):
    c = sqlite3.connect(LEDGER["path"], timeout=30)
    c.execute("INSERT INTO effects (task) VALUES (?)", (task,))
    c.commit()
    c.close()


def ledger_counts(path):
    c = sqlite3.connect(path, timeout=30)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects GROUP BY task").fetchall()
    c.close()
    return dict(rows)


class StateX(BaseModel):
    total: int = 0


@dataclass
class NodeA(BaseNode[StateX]):
    async def run(self, ctx: GraphRunContext[StateX]) -> "NodeB":
        ledger_write("a")
        ctx.state.total += 1
        return NodeB()


@dataclass
class NodeB(BaseNode[StateX, None, int]):
    async def run(self, ctx: GraphRunContext[StateX]) -> End[int]:
        ledger_write("b")
        ctx.state.total += 10
        return End(ctx.state.total)


graph = Graph(nodes=(NodeA, NodeB))


# ------------------------------------------------------------------ lives
async def _park(d):
    LEDGER["path"] = d["ledger"]
    ledger_init(d["ledger"])
    persistence = FileStatePersistence(Path(d["snap"]))
    async with graph.iter(NodeA(), state=StateX(),
                          persistence=persistence) as run:
        node = await run.next()          # NodeA executed; NodeB created
        Path(d["ready"]).write_text(json.dumps(
            {"pid": os.getpid(), "parked_before": type(node).__name__}))
        await asyncio.sleep(600)         # parked between nodes; SIGKILL here


async def _resume(d):
    LEDGER["path"] = d["ledger"]
    ledger_init(d["ledger"])
    outcome, err = None, None
    try:
        async with graph.iter_from_persistence(
                FileStatePersistence(Path(d["snap"]))) as run:
            node = await run.next()
            while not isinstance(node, End):
                node = await run.next()
            outcome = node.data
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    print(json.dumps({"outcome": outcome, "resume_error": err}))


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


# ------------------------------------------------------------------ parent
def parent_main(out_dir):
    d0 = tempfile.mkdtemp(prefix="probe158c_")
    d = {"ledger": f"{d0}/ledger.sqlite", "snap": f"{d0}/state.json",
         "ready": f"{d0}/ready.flag"}
    env = dict(os.environ, PROBE158C_DIR=json.dumps(d))

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
    snapshot_at_kill = None
    try:
        snapshot_at_kill = json.loads(Path(d["snap"]).read_text())
    except Exception as e:
        snapshot_at_kill = f"unreadable: {type(e).__name__}"

    os.kill(victim.pid, signal.SIGKILL)
    victim.wait()
    exit_desc = ("SIGKILL" if victim.returncode == -signal.SIGKILL
                 else str(victim.returncode))

    resume_out = _run_mode("resume", env)
    counts_after = ledger_counts(d["ledger"])

    restored = resume_out.get("resume_error") is None
    result = {
        "probe": "158c_p14_pydantic_graph_park_kill",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pins": {"pydantic-graph": _pgv},
        "crash_mechanism":
            "SIGKILL while parked BETWEEN nodes under graph.iter (NodeA "
            "snapshotted, NodeB created, not started); barrier-synchronized",
        "victim_exit": exit_desc,
        "park_life": park_info,
        "ledger_at_kill": counts_at_kill,
        "snapshot_kinds_at_kill":
            [s.get("status") or s.get("kind") for s in snapshot_at_kill]
            if isinstance(snapshot_at_kill, list) else snapshot_at_kill,
        "resume_life": resume_out,
        "ledger_after_resume": counts_after,
        "stable": {
            "between_node_snapshot_restorable_after_kill": restored,
            "resume_error": resume_out.get("resume_error"),
            "outcome": resume_out.get("outcome"),
            "eo_prefix_across_park_kill":
                counts_at_kill.get("a", 0) == 1
                and counts_after.get("a", 0) == 1,
            "b_execs_total": counts_after.get("b", 0),
            "expected_outcome_if_exactly_once": 11,
        },
    }
    print(json.dumps(result, indent=2, default=str))
    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "158c_results.json").write_text(
            json.dumps(result, indent=2, default=str) + "\n")
        (out / "158c_stable.json").write_text(
            json.dumps({"probe": result["probe"], "host": result["host"],
                        "utc": result["utc"], "pins": result["pins"],
                        "stable": result["stable"]}, indent=2) + "\n")
        print(f"\nwrote {out}/158c_results.json and 158c_stable.json",
              file=sys.stderr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        default_out = Path(__file__).resolve().parents[1] / "results" / "parkkill"
        parent_main(os.environ.get("PROBE158C_OUT", str(default_out)))
    else:
        d = json.loads(os.environ["PROBE158C_DIR"])
        asyncio.run({"park": _park, "resume": _resume}[mode](d))
