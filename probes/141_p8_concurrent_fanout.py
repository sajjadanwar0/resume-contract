#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from importlib.metadata import version
from typing import Annotated, TypedDict
import operator

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

def ledger(path, add=None):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, branch TEXT, val INTEGER)")
    if add is not None:
        c.execute("INSERT INTO effects (branch, val) VALUES (?, ?)", add)
    c.commit()
    rows = [(r[0], r[1]) for r in c.execute("SELECT branch, val FROM effects ORDER BY n")]
    c.close()
    return rows

class S(TypedDict):
    outs: Annotated[list, operator.add]

def build(ckpt_path, ledger_path, crash_flags):
    conn = sqlite3.connect(ckpt_path, check_same_thread=False)
    saver = SqliteSaver(conn)

    def mk(branch):
        def node(state: S):
            v = interrupt(f"approve {branch}?")
            val = 1 if v else 0
            ledger(ledger_path, add=(branch, val))
            if crash_flags.get(branch):
                crash_flags[branch] = False
                raise RuntimeError(f"injected crash in {branch}")
            return {"outs": [[branch, val]]}
        return node

    def join(state: S):
        return {}

    return (StateGraph(S)
            .add_node("p1", mk("p1")).add_node("p2", mk("p2"))
            .add_node("join", join)
            .add_edge(START, "p1").add_edge(START, "p2")
            .add_edge("p1", "join").add_edge("p2", "join")
            .add_edge("join", END)
            .compile(checkpointer=saver))

def intr_ids(result):
    ints = result.get("__interrupt__") or []
    return {getattr(i, "value", None): getattr(i, "id", None) for i in ints}

def cell_map_resume():
    d = tempfile.mkdtemp(prefix="probe141_a_")
    app = build(f"{d}/c.sqlite", f"{d}/l.sqlite", {})
    cfg = {"configurable": {"thread_id": "a"}}
    r0 = app.invoke({"outs": []}, cfg)
    ids = intr_ids(r0)
    surfaced = len(ids)
    resume_map = {i: (v == "approve p1?") for v, i in ids.items()}
    r1 = app.invoke(Command(resume=resume_map), cfg)
    rows = ledger(f"{d}/l.sqlite")
    stray = app.invoke(Command(resume={i: True for i in ids.values()}), cfg)
    rows_after_stray = ledger(f"{d}/l.sqlite")
    return {
        "interrupts_surfaced_together": surfaced,
        "outs_after_map_resume": sorted(r1.get("outs", []), key=str),
        "ledger_after_map_resume": rows,
        "each_branch_fired_once_with_own_value":
            sorted(rows) == [("p1", 1), ("p2", 0)],
        "stray_map_resume_inert":
            rows_after_stray == rows and sorted(stray.get("outs", [])) == sorted(r1.get("outs", []), key=str),
    }

def cell_crash_in_superstep():
    d = tempfile.mkdtemp(prefix="probe141_b_")
    flags = {"p2": True}
    app = build(f"{d}/c.sqlite", f"{d}/l.sqlite", flags)
    cfg = {"configurable": {"thread_id": "b"}}
    r0 = app.invoke({"outs": []}, cfg)
    ids = intr_ids(r0)
    resume_map = {i: True for i in ids.values()}
    try:
        app.invoke(Command(resume=resume_map), cfg)
        crashed = False
    except Exception:
        crashed = True
    rows_at_crash = ledger(f"{d}/l.sqlite")
    r2 = app.invoke(None, cfg)
    rows_final = ledger(f"{d}/l.sqlite")
    return {
        "crash_raised": crashed,
        "ledger_at_crash": rows_at_crash,
        "ledger_final": rows_final,
        "final_outs": sorted(r2.get("outs", []), key=str) if isinstance(r2, dict) else str(r2),
        "completed_parallel_branch_refired_on_retry":
            rows_final.count(("p1", 1)) > rows_at_crash.count(("p1", 1)),
        "p1_count_at_crash": rows_at_crash.count(("p1", 1)),
        "p1_count_final": rows_final.count(("p1", 1)),
        "p2_count_final": rows_final.count(("p2", 1)),
    }

def main():
    out = {
        "langgraph_version": version("langgraph"),
        "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
        "cell_parallel_map_resume": cell_map_resume(),
        "cell_crash_inside_parallel_superstep": cell_crash_in_superstep(),
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
