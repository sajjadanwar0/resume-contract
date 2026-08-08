#!/usr/bin/env python3
import argparse
import asyncio
from dataclasses import dataclass
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
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

REQUIRED = {"crewai": "1.15.2", "pydantic-graph": "1.107.1"}

def _pin(dist):
    try:
        v = version(dist)
    except PackageNotFoundError:
        return None, f"{dist} not installed in {sys.executable}"
    if v != REQUIRED[dist] and not os.environ.get("PROBE_ALLOW_OFFPIN"):
        return None, (f"{dist}=={v} but the paper pin is {REQUIRED[dist]} "
                      f"(PROBE_ALLOW_OFFPIN=1 to override)")
    return v, None

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

def _crewai_flow(ctx, kill_in_s2):
    """Build the @persist Flow class inside the child so the decorator's
    SQLiteFlowPersistence binds to this run's isolated db path."""
    from pydantic import BaseModel
    from crewai.flow.flow import Flow, listen, start
    from crewai.flow.persistence import persist
    from crewai.flow.persistence import SQLiteFlowPersistence

    class St(BaseModel):
        id: str = ""
        counter: int = 0

    @persist(persistence=SQLiteFlowPersistence(db_path=ctx["flowdb"]))
    class F(Flow[St]):
        @start()
        def s1(self):
            ledger_write(ctx["ledger"], "s1")
            self.state.counter += 1
            Path(ctx["idfile"]).write_text(self.state.id)

        @listen(s1)
        def s2(self):
            if kill_in_s2:
                _die()
            ledger_write(ctx["ledger"], "s2")
            self.state.counter += 10

    return F

def cw_victim_main(ctx):
    F = _crewai_flow(ctx, kill_in_s2=True)
    F().kickoff()
    print(json.dumps({"unexpected": "victim survived"}))

def cw_resume_main(ctx):
    F = _crewai_flow(ctx, kill_in_s2=False)
    fid = Path(ctx["idfile"]).read_text().strip()
    err, err_type = None, None
    try:
        F().kickoff(inputs={"id": fid})
    except Exception as e:
        err, err_type = f"{type(e).__name__}: {e}"[:300], type(e).__name__
    print(json.dumps({"restored_id": fid, "error": err,
                      "error_type": err_type}))

def _pg_graph(ctx, kill_in_b):
    from pydantic_graph import BaseNode, End, Graph, GraphRunContext

    @dataclass
    class StateX:
        total: int = 0

    @dataclass
    class NodeB(BaseNode[StateX, None, int]):
        async def run(self, ctx_: GraphRunContext[StateX]) -> End[int]:
            ledger_write(ctx["ledger"], "b")
            if kill_in_b:
                _die()
            ctx_.state.total += 10
            return End(ctx_.state.total)

    @dataclass
    class NodeA(BaseNode[StateX]):
        async def run(self, ctx_: GraphRunContext[StateX]) -> NodeB:
            ledger_write(ctx["ledger"], "a")
            ctx_.state.total += 1
            return NodeB()

    return Graph(nodes=[NodeA, NodeB]), StateX, NodeA, End

def pg_victim_main(ctx):
    from pydantic_graph.persistence.file import FileStatePersistence
    graph, StateX, NodeA, End = _pg_graph(ctx, kill_in_b=True)

    async def go():
        p = FileStatePersistence(Path(ctx["snap"]))
        async with graph.iter(NodeA(), state=StateX(),
                              persistence=p) as run:
            node = await run.next()
            node = await run.next()
        print(json.dumps({"unexpected": "victim survived", "node": str(node)}))

    asyncio.run(go())

def pg_resume_main(ctx):
    from pydantic_graph.persistence.file import FileStatePersistence
    graph, StateX, NodeA, End = _pg_graph(ctx, kill_in_b=False)

    async def go():
        outcome, err, err_type = None, None, None
        try:
            async with graph.iter_from_persistence(
                    FileStatePersistence(Path(ctx["snap"]))) as run:
                node = await run.next()
                while not isinstance(node, End):
                    node = await run.next()
                outcome = node.data
        except Exception as e:
            err = f"{type(e).__name__}: {e}"[:300]
            err_type = type(e).__name__
        print(json.dumps({"outcome": outcome, "error": err,
                          "error_type": err_type}))

    asyncio.run(go())

def _spawn(mode, ctx):
    env = dict(os.environ, PROBE164_CTX=json.dumps(ctx))
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

def run_crewai_rep(base, rep):
    d0 = tempfile.mkdtemp(prefix=f"p164_cw_{rep}_", dir=base)
    ctx = {"ledger": f"{d0}/ledger.sqlite", "flowdb": f"{d0}/flows.sqlite",
           "idfile": f"{d0}/flow_id.txt"}
    ledger_init(ctx["ledger"])
    v = _wait(_spawn("cw_victim", ctx), 180)
    post_crash = ledger_counts(ctx["ledger"])
    r = _wait(_spawn("cw_resume", ctx), 180)
    final = ledger_counts(ctx["ledger"])
    return {
        "rep": rep, "victim": v, "post_crash_counts": post_crash,
        "resume": r, "final_counts": final,
        "victim_sigkilled": v.get("returncode") == -signal.SIGKILL,
        "s1_duplicated": final.get("s1", 0) == 2,
        "as_expected": (v.get("returncode") == -signal.SIGKILL
                        and post_crash.get("s1", 0) == 1
                        and post_crash.get("s2", 0) == 0
                        and final.get("s1", 0) == 2
                        and final.get("s2", 0) == 1
                        and r.get("error") is None),
    }

def run_pg_rep(base, rep):
    d0 = tempfile.mkdtemp(prefix=f"p164_pg_{rep}_", dir=base)
    ctx = {"ledger": f"{d0}/ledger.sqlite", "snap": f"{d0}/snap.json"}
    ledger_init(ctx["ledger"])
    v = _wait(_spawn("pg_victim", ctx), 120)
    post_crash = ledger_counts(ctx["ledger"])
    r = _wait(_spawn("pg_resume", ctx), 120)
    final = ledger_counts(ctx["ledger"])
    return {
        "rep": rep, "victim": v, "post_crash_counts": post_crash,
        "resume": r, "final_counts": final,
        "victim_sigkilled": v.get("returncode") == -signal.SIGKILL,
        "resume_error_type": r.get("error_type"),
        "as_expected": (v.get("returncode") == -signal.SIGKILL
                        and post_crash.get("b", 0) == 1
                        and r.get("error_type") == "GraphRuntimeError"
                        and final.get("b", 0) == 1),
    }

def parent_main(args):
    base = tempfile.mkdtemp(prefix="probe164_")
    result = {
        "probe": "164_p15_sigkill_crewai_pydantic",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "reps": args.reps,
        "halves": {},
    }
    stable = {}
    if args.only in ("both", "crewai"):
        v, refusal = _pin("crewai")
        if refusal:
            result["halves"]["crewai"] = {"probe_refused": refusal}
        else:
            reps = [run_crewai_rep(base, i) for i in range(args.reps)]
            result["halves"]["crewai"] = {"pin": v, "reps": reps}
            stable["crewai_victim_sigkilled_every_rep"] = all(
                r["victim_sigkilled"] for r in reps)
            stable["crewai_completed_s1_reexecuted_under_sigkill"] = all(
                r["s1_duplicated"] for r in reps)
            stable["crewai_matrix_verdict_replicated"] = all(
                r["as_expected"] for r in reps)
    if args.only in ("both", "pg"):
        v, refusal = _pin("pydantic-graph")
        if refusal:
            result["halves"]["pydantic_graph"] = {"probe_refused": refusal}
        else:
            reps = [run_pg_rep(base, i) for i in range(args.reps)]
            result["halves"]["pydantic_graph"] = {"pin": v, "reps": reps}
            stable["pg_victim_sigkilled_every_rep"] = all(
                r["victim_sigkilled"] for r in reps)
            stable["pg_midnode_resume_error_types"] = sorted(
                {str(r["resume_error_type"]) for r in reps})
            stable["pg_matrix_verdict_replicated"] = all(
                r["as_expected"] for r in reps)
    result["stable"] = stable
    out = Path(os.environ.get(
        "PROBE164_OUT",
        Path(__file__).resolve().parents[1] / "results" / "sigkill"))
    out.mkdir(parents=True, exist_ok=True)
    prev_path = out / "164_results.json"
    if prev_path.exists():
        try:
            prev = json.loads(prev_path.read_text())
            merged_halves = dict(prev.get("halves", {}))
            merged_halves.update(result["halves"])
            result["halves"] = merged_halves
            merged_stable = dict(prev.get("stable", {}))
            merged_stable.update(stable)
            result["stable"] = merged_stable
            stable = merged_stable
            result["merged_from_previous_receipt"] = True
        except Exception:
            pass
    print(json.dumps(result, indent=2, default=str))
    (out / "164_results.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")
    (out / "164_stable.json").write_text(json.dumps(
        {"probe": result["probe"], "host": result["host"],
         "utc": result["utc"],
         "pins": {k: v.get("pin") for k, v in result["halves"].items()
                  if isinstance(v, dict)},
         "stable": stable}, indent=2) + "\n")
    print(f"\nwrote {out}/164_results.json and 164_stable.json",
          file=sys.stderr)

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode in ("cw_victim", "cw_resume", "pg_victim", "pg_resume"):
        ctx = json.loads(os.environ["PROBE164_CTX"])
        {"cw_victim": cw_victim_main, "cw_resume": cw_resume_main,
         "pg_victim": pg_victim_main, "pg_resume": pg_resume_main}[mode](ctx)
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--reps", type=int, default=3)
        ap.add_argument("--smoke", action="store_true")
        ap.add_argument("--only", choices=("both", "crewai", "pg"),
                        default="both")
        args = ap.parse_args()
        if args.smoke:
            args.reps = 1
        parent_main(args)
