#!/usr/bin/env python3
"""
123_p4_shim_cv_langgraph.py
Repair demonstration: probe 113/T5's checkpoint-validity protocol re-run
through the Remit CV shim (harness/remit_shim.py). Baseline behavior on
LangGraph 1.2.9 (probe 113): schema-invalid state (None appended to a
pydantic List[str]) persists silently across checkpoints with no error at
write or read time. Target through the shim: the write is rejected loudly,
nothing invalid is persisted, and the durable history stays readable.
"""
import json
import sys
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
from remit_shim import ValidatingSaver, InvalidCheckpointError  # noqa: E402

from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END

RESULTS = {"langgraph_version": version("langgraph")}


class S(BaseModel):
    items: List[str] = []


def validator(channel_values: dict) -> None:
    items = channel_values.get("items")
    if items is not None and any(not isinstance(x, str) for x in items):
        raise ValueError("items must be List[str]")


def bad_node(state: S) -> S:
    state.items.append(None)  # schema violation, exactly as probe 113/T5
    return state


try:
    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)
    app = g.compile(checkpointer=ValidatingSaver(validator))
    cfg = {"configurable": {"thread_id": "t-shim"}}

    write_rejected = None
    try:
        app.invoke(S(items=["ok"]), cfg)
        write_rejected = False
    except InvalidCheckpointError:
        write_rejected = True

    hist_error = None
    invalid_persisted = None
    n_ckpts = None
    try:
        hist = list(app.get_state_history(cfg))
        n_ckpts = len(hist)
        invalid_persisted = any(
            (v := (s.values.get("items") if isinstance(s.values, dict)
                   else getattr(s.values, "items", None)))
            and any(not isinstance(x, str) for x in v)
            for s in hist
        )
    except Exception as e:
        hist_error = f"{type(e).__name__}: {e}"

    RESULTS.update({
        "write_rejected_loudly": write_rejected,
        "n_checkpoints": n_ckpts,
        "invalid_state_persisted": bool(invalid_persisted),
        "history_error": hist_error,
        "violation_CV_through_shim": (not write_rejected)
                                     or bool(invalid_persisted)
                                     or bool(hist_error),
    })
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
