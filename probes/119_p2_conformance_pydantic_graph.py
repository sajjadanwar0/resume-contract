#!/usr/bin/env python3
"""
119_p2_conformance_pydantic_graph.py
Resume-contract conformance probe: pydantic-graph 1.x (FileStatePersistence +
Graph.iter_from_persistence). Note for the matrix: pydantic-graph 2.x removes
this persistence machinery entirely (deprecation notice names it slated for
removal), so the cell is pinned <2 -- a framework deleting its resume plane
between majors is itself a fragmentation datum.

Tests (deterministic, LLM-free):
  G1  crash-resume PC/EO : node A (external effect) completes and snapshots;
      node B crashes; resume via iter_from_persistence with the crash
      disarmed -> does A re-execute?
  G2  completed-run resume (CO analog): iter_from_persistence on a run whose
      persistence already reached End -> inert, error, or re-execution?
  G3  checkpoint validity (CV): truncate the persistence JSON mid-file ->
      does load fail loudly or accept silently?
"""
import asyncio
import json
import traceback
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from pydantic_graph import BaseNode, End, Graph, GraphRunContext
from pydantic_graph.persistence.file import FileStatePersistence

RESULTS = {"pydantic_graph_version": version("pydantic-graph")}
EFF = {"a": 0, "b": 0}
FAIL = {"armed": True}


@dataclass
class StateX:
    total: int = 0


@dataclass
class NodeA(BaseNode[StateX]):
    async def run(self, ctx: GraphRunContext[StateX]) -> "NodeB":
        EFF["a"] += 1
        ctx.state.total += 1
        return NodeB()


@dataclass
class NodeB(BaseNode[StateX, None, int]):
    async def run(self, ctx: GraphRunContext[StateX]) -> End[int]:
        if FAIL["armed"]:
            FAIL["armed"] = False
            raise RuntimeError("simulated crash in NodeB")
        EFF["b"] += 1
        ctx.state.total += 10
        return End(ctx.state.total)


graph = Graph(nodes=(NodeA, NodeB))


async def g1_crash_resume(path: Path):
    EFF["a"] = EFF["b"] = 0
    FAIL["armed"] = True
    persistence = FileStatePersistence(path)
    crash = None
    try:
        await graph.run(NodeA(), state=StateX(), persistence=persistence)
    except Exception as e:
        crash = type(e).__name__
    a_at_crash = EFF["a"]

    resume_error = None
    outcome = None
    try:
        async with graph.iter_from_persistence(
            FileStatePersistence(path)
        ) as run:
            node = await run.next()
            while not isinstance(node, End):
                node = await run.next()
            outcome = node.data
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"

    return {
        "crashed": crash,
        "a_execs_at_crash": a_at_crash,
        "a_execs_total": EFF["a"],
        "b_execs_total": EFF["b"],
        "outcome": outcome,
        "resume_error": resume_error,
        "expected_outcome_if_exactly_once": 11,
        "violation_PC_EO_completed_node_reexecuted": EFF["a"] > a_at_crash,
    }


async def g2_completed_run_resume(path: Path):
    EFF["a"] = EFF["b"] = 0
    FAIL["armed"] = False
    persistence = FileStatePersistence(path)
    await graph.run(NodeA(), state=StateX(), persistence=persistence)
    a_done, b_done = EFF["a"], EFF["b"]
    err = None
    extra = None
    try:
        async with graph.iter_from_persistence(
            FileStatePersistence(path)
        ) as run:
            extra = await run.next()
    except Exception as e:
        err = f"{type(e).__name__}"
    return {
        "a_execs_after_stray_resume": EFF["a"],
        "b_execs_after_stray_resume": EFF["b"],
        "stray_resume_error": err,
        "stray_next_result": str(type(extra).__name__) if extra else None,
        "violation_CO_effect_refired": EFF["a"] > a_done or EFF["b"] > b_done,
    }


async def g3_corrupt_snapshot(path: Path):
    EFF["a"] = EFF["b"] = 0
    FAIL["armed"] = True
    persistence = FileStatePersistence(path)
    try:
        await graph.run(NodeA(), state=StateX(), persistence=persistence)
    except Exception:
        pass
    raw = path.read_text()
    path.write_text(raw[: len(raw) // 2])  # structural corruption
    err = None
    try:
        async with graph.iter_from_persistence(
            FileStatePersistence(path)
        ) as run:
            await run.next()
    except Exception as e:
        err = f"{type(e).__name__}"
    return {
        "corrupt_load_error": err,
        "violation_CV_silent_acceptance": err is None,
    }


async def main():
    base = Path("/tmp/rc_119")
    base.mkdir(exist_ok=True)
    for name, fn, fname in [
        ("G1_crash_resume", g1_crash_resume, "g1.json"),
        ("G2_completed_run_resume", g2_completed_run_resume, "g2.json"),
        ("G3_corrupt_snapshot", g3_corrupt_snapshot, "g3.json"),
    ]:
        p = base / fname
        if p.exists():
            p.unlink()
        try:
            RESULTS[name] = await fn(p)
        except Exception:
            RESULTS[name] = {"probe_error": traceback.format_exc(limit=5)}
    print(json.dumps(RESULTS, indent=2, default=str))


asyncio.run(main())
