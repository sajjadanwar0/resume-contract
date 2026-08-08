#!/usr/bin/env python3
import asyncio
import json
import traceback
from typing import TypedDict, List, Annotated
import operator
from pydantic import BaseModel

from importlib.metadata import version
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task

RESULTS = {"langgraph_version": version("langgraph")}

def get_interrupt_checkpoint_id(app, config):
    snap = app.get_state(config)
    return snap.config["configurable"]["checkpoint_id"], snap

def t1_resume_from_checkpoint_id():
    eff = {"a": 0, "b_post": 0}

    class S(TypedDict):
        val: str

    def node_a(state: S):
        eff["a"] += 1
        return {"val": "A"}

    def node_b(state: S):
        ans = interrupt("need input")
        eff["b_post"] += 1
        return {"val": ans}

    g = StateGraph(S)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    app = g.compile(checkpointer=InMemorySaver())

    cfg = {"configurable": {"thread_id": "t1"}}
    r1 = app.invoke({"val": ""}, cfg)
    assert "__interrupt__" in r1, "expected interrupt"
    assert eff["a"] == 1

    ckpt_id, _ = get_interrupt_checkpoint_id(app, cfg)
    resume_cfg = {"configurable": {"thread_id": "t1", "checkpoint_id": ckpt_id}}
    r2 = app.invoke(Command(resume="human-answer"), resume_cfg)

    violation = eff["a"] != 1

    return {
        "a_execs": eff["a"],
        "b_post_execs": eff["b_post"],
        "final": r2,
        "violation_resume_became_replay": violation,
    }

def t1b_resume_thread_only():
    eff = {"a": 0, "b_post": 0}

    class S(TypedDict):
        val: str

    def node_a(state: S):
        eff["a"] += 1
        return {"val": "A"}

    def node_b(state: S):
        ans = interrupt("need input")
        eff["b_post"] += 1
        return {"val": ans}

    g = StateGraph(S)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    app = g.compile(checkpointer=InMemorySaver())

    cfg = {"configurable": {"thread_id": "t1b"}}
    app.invoke({"val": ""}, cfg)
    r2 = app.invoke(Command(resume="ok"), cfg)

    return {
        "a_execs": eff["a"],
        "b_post_execs": eff["b_post"],
        "final": r2,
        "violation_resume_became_replay": eff["a"] != 1,
    }

def t2_fork_on_different_resume_values():
    class S(TypedDict):
        value: int

    def node(state: S):
        allow = interrupt("Allow to add?")
        if allow:
            return {"value": state["value"] + 1}
        return {"value": state["value"]}

    app = (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=InMemorySaver())
    )

    cfg = {"configurable": {"thread_id": "t2"}}
    app.invoke({"value": 0}, cfg)
    ckpt_id, _ = get_interrupt_checkpoint_id(app, cfg)
    fork_cfg = {"configurable": {"thread_id": "t2", "checkpoint_id": ckpt_id}}

    r_true = app.invoke(Command(resume=True), fork_cfg)
    r_false = app.invoke(Command(resume=False), fork_cfg)
    violation = not (r_true.get("value") == 1 and r_false.get("value") == 0)

    return {
        "resume_true_value": r_true.get("value"),
        "resume_false_value": r_false.get("value"),
        "violation_second_resume_ignored": violation,
    }

def t3_functional_api_sync():
    eff = {"s1": 0, "s3": 0}
    ckpt = InMemorySaver()

    @task
    def step_1(q: str) -> str:
        eff["s1"] += 1
        return f"{q} bar"

    @task
    def human_feedback(q: str) -> str:
        fb = interrupt(f"Please provide feedback: {q}")
        return f"{q} {fb}"

    @task
    def step_3(q: str) -> str:
        eff["s3"] += 1
        return f"{q} qux"

    @entrypoint(checkpointer=ckpt)
    def wf(q: str) -> str:
        r1 = step_1(q).result()
        r2 = human_feedback(r1).result()
        r3 = step_3(r2).result()
        return r3

    cfg = {"configurable": {"thread_id": "t3s"}}
    r1 = wf.invoke("foo", cfg)
    n_interrupts_first = len(r1.get("__interrupt__", [])) if isinstance(r1, dict) else 0
    r2 = wf.invoke(Command(resume="HUMAN"), cfg)

    return {
        "s1_execs": eff["s1"],
        "s3_execs": eff["s3"],
        "interrupts_on_first_run": n_interrupts_first,
        "final": r2,
        "violation_completed_task_reexecuted": eff["s1"] != 1,
    }

async def _t3_async():
    eff = {"s1": 0, "s3": 0}
    ckpt = InMemorySaver()

    @task
    async def step_1(q: str) -> str:
        eff["s1"] += 1
        return f"{q} bar"

    @task
    async def human_feedback(q: str) -> str:
        fb = interrupt(f"Please provide feedback: {q}")
        return f"{q} {fb}"

    @task
    async def step_3(q: str) -> str:
        eff["s3"] += 1
        return f"{q} qux"

    @entrypoint(checkpointer=ckpt)
    async def wf(q: str) -> str:
        r1 = await step_1(q)
        r2 = await human_feedback(r1)
        r3 = await step_3(r2)
        return r3

    cfg = {"configurable": {"thread_id": "t3a"}}
    r1 = await wf.ainvoke("foo", cfg)
    interrupts_seen = []
    if isinstance(r1, dict) and "__interrupt__" in r1:
        interrupts_seen = list(r1["__interrupt__"])
    r2 = await wf.ainvoke(Command(resume="HUMAN"), cfg)
    dup_interrupts = None
    if isinstance(r2, dict) and "__interrupt__" in r2:
        dup_interrupts = len(r2["__interrupt__"])
    return {
        "s1_execs": eff["s1"],
        "s3_execs": eff["s3"],
        "interrupts_on_first_run": len(interrupts_seen),
        "interrupts_on_resume": dup_interrupts,
        "final": r2,
        "violation_completed_task_reexecuted": eff["s1"] != 1,
    }

def t3_functional_api_async():
    return asyncio.run(_t3_async())

def t4_interrupt_consume_once():
    eff = {"b_post": 0}

    class S(TypedDict):
        log: Annotated[List[str], operator.add]

    def node_a(state: S):
        return {"log": ["a"]}

    def node_b(state: S):
        ans = interrupt("approve?")
        eff["b_post"] += 1
        return {"log": [f"b:{ans}"]}

    g = StateGraph(S)
    g.add_node("a", node_a)
    g.add_node("b", node_b)
    g.add_edge(START, "a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    app = g.compile(checkpointer=InMemorySaver())

    cfg = {"configurable": {"thread_id": "t4"}}
    app.invoke({"log": []}, cfg)
    app.invoke(Command(resume="YES"), cfg)
    post_first = eff["b_post"]

    try:
        r3 = app.invoke(Command(resume="YES-REPLAY"), cfg)
        replay_error = None
    except Exception as e:
        r3 = None
        replay_error = f"{type(e).__name__}: {e}"

    return {
        "b_post_after_first_resume": post_first,
        "b_post_after_replay_attempt": eff["b_post"],
        "replay_result": r3,
        "replay_raised": replay_error,
        "violation_effect_refired": eff["b_post"] > 1,
    }

def t5_checkpoint_validity():

    class S(BaseModel):
        items: List[str] = []

    def bad_node(state: S) -> S:
        state.items.append(None)
        return state

    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t5"}}

    invoke_error = None

    try:
        app.invoke(S(items=["ok"]), cfg)
    except Exception as e:
        invoke_error = f"{type(e).__name__}"

    history_error = None
    n_ckpts = None
    invalid_persisted = None

    try:
        hist = list(app.get_state_history(cfg))
        n_ckpts = len(hist)
        for snap in hist:
            vals = snap.values
            items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
            if items and None in items:
                invalid_persisted = True
        if invalid_persisted is None:
            invalid_persisted = False
    except Exception as e:
        history_error = f"{type(e).__name__}: {e}"

    return {
        "invoke_error": invoke_error,
        "n_checkpoints": n_ckpts,
        "invalid_state_persisted": invalid_persisted,
        "history_error": history_error,
        "violation_corrupt_checkpoint": bool(history_error) or bool(invalid_persisted),
    }

TESTS = {
    "T1_resume_from_checkpoint_id_#7361": t1_resume_from_checkpoint_id,
    "T1b_resume_thread_only_control": t1b_resume_thread_only,
    "T2_fork_on_resume_values_#6663": t2_fork_on_different_resume_values,
    "T3_functional_sync_#6792": t3_functional_api_sync,
    "T3a_functional_async_#6792": t3_functional_api_async,
    "T4_interrupt_consume_once": t4_interrupt_consume_once,
    "T5_checkpoint_validity_#6491": t5_checkpoint_validity,
}

for name, fn in TESTS.items():
    try:
        RESULTS[name] = fn()
    except Exception:
        RESULTS[name] = {"probe_error": traceback.format_exc(limit=3)}

print(json.dumps(RESULTS, indent=2, default=str))
