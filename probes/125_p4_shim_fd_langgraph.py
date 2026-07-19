#!/usr/bin/env python3
"""
125_p4_shim_fd_langgraph.py
Repair demonstration, FD leg: probe 113/T2's fork protocol re-run through
harness/remit_shim.ForkKeyedSaver. Baseline on LangGraph 1.0.5-1.2.9: the
second resume value addressed to the same (thread, checkpoint) is silently
answered with the first branch's outcome (#6663; violated at every swept
version). Target through the shim: resume(True) -> 1, resume(False) -> 0,
and a repeated resume(True) still -> 1 (replay idempotence preserved).
"""
import json
import sys
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from remit_shim import ForkKeyedSaver  # noqa: E402

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

RESULTS = {"langgraph_version": version("langgraph")}


class S(TypedDict):
    value: int


def node(state: S):
    allow = interrupt("Allow to add?")
    if allow:
        return {"value": state["value"] + 1}
    return {"value": state["value"]}


try:
    app = (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=ForkKeyedSaver())
    )
    cfg = {"configurable": {"thread_id": "t-fd"}}
    app.invoke({"value": 0}, cfg)
    snap = app.get_state(cfg)
    ckpt_id = snap.config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": "t-fd", "checkpoint_id": ckpt_id}}

    r_true = app.invoke(Command(resume=True), fork_cfg)
    r_false = app.invoke(Command(resume=False), fork_cfg)
    r_true2 = app.invoke(Command(resume=True), fork_cfg)

    RESULTS.update({
        "resume_true_value": r_true.get("value"),
        "resume_false_value": r_false.get("value"),
        "resume_true_again_value": r_true2.get("value"),
        "violation_FD_through_shim": not (
            r_true.get("value") == 1 and r_false.get("value") == 0
        ),
        "replay_idempotence_same_value_holds": r_true2.get("value") == 1,
    })
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
