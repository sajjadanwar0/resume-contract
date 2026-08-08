#!/usr/bin/env python3
import json
import os
import traceback
from typing import TypedDict, List
from importlib.metadata import version

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.func import entrypoint, task
from pydantic import BaseModel
import psycopg

DSN = os.environ.get("PROBE_PG_DSN", "postgresql://postgres:pg@localhost:5432/postgres")

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_postgres_version": version("langgraph-checkpoint-postgres"),
    "dsn_host": DSN.split("@")[-1],
}

def fresh_saver(tag):
    dbname = f"probe130_{tag}"
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute(f"DROP DATABASE IF EXISTS {dbname}")
    admin.execute(f"CREATE DATABASE {dbname}")
    admin.close()
    base = DSN.rsplit("/", 1)[0]
    conn = psycopg.connect(f"{base}/{dbname}", autocommit=True)
    saver = PostgresSaver(conn)
    saver.setup()
    return saver, conn

class S(TypedDict):
    value: int

def fork_graph(saver):
    def node(state: S):
        allow = interrupt("Allow to add?")
        if allow:
            return {"value": state["value"] + 1}
        return {"value": state["value"]}

    return (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=saver)
    )

def t1_fork(use_map):
    saver, conn = fresh_saver("t1m" if use_map else "t1")
    app = fork_graph(saver)
    tag = "t1m" if use_map else "t1"
    cfg = {"configurable": {"thread_id": tag}}
    r1 = app.invoke({"value": 0}, cfg)
    intr = r1.get("__interrupt__")
    intr_id = getattr(intr[0], "id", None) if intr else None
    snap = app.get_state(cfg)
    ckpt_id = snap.config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": tag, "checkpoint_id": ckpt_id}}

    def mk(v):
        return Command(resume={intr_id: v}) if use_map else Command(resume=v)

    r_true = app.invoke(mk(True), fork_cfg)
    r_false = app.invoke(mk(False), fork_cfg)
    resume_rows = conn.execute(
        "SELECT task_id, idx FROM checkpoint_writes WHERE channel='__resume__'"
    ).fetchall()
    return {
        "backend": "PostgresSaver",
        "form": "resume_map" if use_map else "bare_value",
        "resume_true_value": r_true.get("value"),
        "resume_false_value": r_false.get("value"),
        "resume_writes_rows": len(resume_rows),
        "violation_second_resume_ignored": not (
            r_true.get("value") == 1 and r_false.get("value") == 0
        ),
    }

def t2_cv():
    class P(BaseModel):
        items: List[str] = []

    def bad_node(state: P):
        return {"items": state.items + [None]}

    saver, conn = fresh_saver("t2")
    app = (
        StateGraph(P)
        .add_node("bad", bad_node)
        .add_edge(START, "bad")
        .add_edge("bad", END)
        .compile(checkpointer=saver)
    )
    cfg = {"configurable": {"thread_id": "t2"}}
    invoke_error = history_error = None
    persisted_invalid = False
    try:
        app.invoke(P(), cfg)
    except Exception as e:
        invoke_error = f"{type(e).__name__}: {e}"
    try:
        for snap in app.get_state_history(cfg):
            vals = snap.values
            items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
            if items and None in items:
                persisted_invalid = True
    except Exception as e:
        history_error = f"{type(e).__name__}: {e}"
    return {
        "backend": "PostgresSaver",
        "invoke_error": invoke_error,
        "history_read_error": history_error,
        "schema_invalid_value_persisted": persisted_invalid,
        "violation_silent_invalid_persistence": (
            persisted_invalid and invoke_error is None and history_error is None
        ),
    }

def t3_co():
    eff = {"post": 0}

    class T(TypedDict):
        val: str

    def node(state: T):
        ans = interrupt("q")
        eff["post"] += 1
        return {"val": str(ans)}

    saver, conn = fresh_saver("t3")
    app = (
        StateGraph(T)
        .add_node("n", node)
        .add_edge(START, "n")
        .add_edge("n", END)
        .compile(checkpointer=saver)
    )
    cfg = {"configurable": {"thread_id": "t3"}}
    app.invoke({"val": ""}, cfg)
    app.invoke(Command(resume="yes"), cfg)
    app.invoke(Command(resume="stray"), cfg)
    return {
        "backend": "PostgresSaver",
        "post_effect_counter": eff["post"],
        "violation_stray_resume_refired_effect": eff["post"] != 1,
    }

class Crash(RuntimeError):
    pass

def t4_eo_crash():
    eff = {"s1": 0, "s2": 0}
    crash = {"armed": True}
    saver, conn = fresh_saver("t4")

    @task
    def s1(x: int) -> int:
        eff["s1"] += 1
        return x + 1

    @task
    def s2(x: int) -> int:
        if crash["armed"]:
            crash["armed"] = False
            raise Crash("simulated process death")
        eff["s2"] += 1
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        a = s1(x).result()
        b = s2(a).result()
        return b

    cfg = {"configurable": {"thread_id": "t4"}}
    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__
    s1_at_crash = eff["s1"]
    writes_rows = conn.execute("SELECT COUNT(*) FROM checkpoint_writes").fetchone()[0]
    result = resume_error = None
    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"
    return {
        "backend": "PostgresSaver",
        "crashed_as": crashed,
        "s1_execs_at_crash": s1_at_crash,
        "durable_writes_rows_at_crash": writes_rows,
        "s1_execs_after_resume": eff["s1"],
        "s2_execs_after_resume": eff["s2"],
        "result": result,
        "resume_error": resume_error,
        "violation_completed_task_reexecuted_on_crash_resume": eff["s1"] > 1,
    }

def main():
    for name, fn in [
        ("t1_fd_fork_bare", lambda: t1_fork(False)),
        ("t1b_fd_fork_resume_map", lambda: t1_fork(True)),
        ("t2_cv_invalid_persist", t2_cv),
        ("t3_co_stray_resume", t3_co),
        ("t4_eo_crash_split", t4_eo_crash),
    ]:
        try:
            RESULTS[name] = fn()
        except Exception:
            RESULTS[name] = {"probe_error": traceback.format_exc()}
    print(json.dumps(RESULTS, indent=2, default=str))

if __name__ == "__main__":
    main()
