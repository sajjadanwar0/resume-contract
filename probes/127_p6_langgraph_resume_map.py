#!/usr/bin/env python3
"""
127_p6_langgraph_resume_map.py
P6 - Alternative-invocation control for #6663: LangGraph's documented
re-answer pattern is a resume MAP keyed by interrupt id,
Command(resume={interrupt_id: value}). A sharp reviewer asks whether that
form escapes the dedup guard that produces the fork violation for the bare
Command(resume=value) form. This probe answers it on both backends.

  T1  interrupt; resume map {id: True}; then second invocation with resume
      map {id: False} addressed to the SAME (thread_id, checkpoint_id).
  T2  same, bare-value control (the pilot's original protocol) for
      side-by-side comparison in one output.

Backends: InMemorySaver and SqliteSaver.
"""
import json
import os
import sqlite3
import tempfile
import traceback
from typing import TypedDict
from importlib.metadata import version

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_version": version("langgraph-checkpoint"),
}
WORKDIR = tempfile.mkdtemp(prefix="probe127_")


def build(saver):
    class S(TypedDict):
        value: int

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


def extract_interrupt_id(r):
    intr = r.get("__interrupt__")
    if not intr:
        return None, "no __interrupt__ in result"
    item = intr[0]
    for attr in ("id", "interrupt_id"):
        v = getattr(item, attr, None)
        if v:
            return v, None
    # namespace-based fallback (older API shapes)
    ns = getattr(item, "ns", None)
    if ns:
        return ns[0] if isinstance(ns, (list, tuple)) else ns, None
    return None, f"interrupt object has no id field: {item!r}"


def scenario(saver, tag, use_map):
    app = build(saver)
    cfg = {"configurable": {"thread_id": tag}}
    r1 = app.invoke({"value": 0}, cfg)
    intr_id, id_err = extract_interrupt_id(r1)
    snap = app.get_state(cfg)
    ckpt_id = snap.config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": tag, "checkpoint_id": ckpt_id}}

    def mk(v):
        return Command(resume={intr_id: v}) if use_map else Command(resume=v)

    err = None
    r_true = r_false = None
    try:
        r_true = app.invoke(mk(True), fork_cfg)
        r_false = app.invoke(mk(False), fork_cfg)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    tv = r_true.get("value") if isinstance(r_true, dict) else None
    fv = r_false.get("value") if isinstance(r_false, dict) else None
    return {
        "form": "resume_map" if use_map else "bare_value",
        "interrupt_id": str(intr_id),
        "interrupt_id_error": id_err,
        "resume_true_value": tv,
        "resume_false_value": fv,
        "error": err,
        "violation_second_resume_ignored": not (tv == 1 and fv == 0),
    }


def main():
    for backend, mk_saver in [
        ("InMemorySaver", lambda t: InMemorySaver()),
        (
            "SqliteSaver",
            lambda t: SqliteSaver(
                sqlite3.connect(
                    os.path.join(WORKDIR, f"{t}.sqlite"), check_same_thread=False
                )
            ),
        ),
    ]:
        for use_map in (True, False):
            key = f"{backend}_{'map' if use_map else 'bare'}"
            try:
                RESULTS[key] = scenario(
                    mk_saver(key), f"t-{key}", use_map
                ) | {"backend": backend}
            except Exception:
                RESULTS[key] = {"probe_error": traceback.format_exc()}
    print(json.dumps(RESULTS, indent=2, default=str))


if __name__ == "__main__":
    main()
