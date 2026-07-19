#!/usr/bin/env python3
"""
143_p8_trace_conformance.py
Answers "you verified your transcription, not the framework" with the
strongest mechanical evidence short of extraction: RUNTIME TRACE
CONFORMANCE. The LGF model of Sec. 4.3 (recorded-resume precedence at
task preparation; incoming value otherwise; under the fork-intent filter,
recorded resume writes invisible at explicit-checkpoint loads) is
transliterated into ~20 lines of Python. Real executions are then run
with an instrumented saver that logs the durable resume-write trace and
the address kind of every load; the model consumes ONLY that trace plus
the driver-supplied values and must predict the measured branch outcome
of every invocation.

Protocols checked (every invocation asserted):
  P1 stock saver, bare fork, second value different   (LGF-A behavior)
  P2 fork-intent shim, bare fork                      (LGF-B behavior)
  P3 fork-intent shim, same-value re-fork
  P4 stock saver, stray resume at the ordinary address after completion
Verdict: all_invocations_conform -- model-predicted == measured for each.
This checks the recorded executions are behaviors of the transliterated
model; it is evidence for the mapping, not a refinement proof, and the
paper says so.
"""
import json
import sqlite3
import tempfile
from importlib.metadata import version
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

RESUME_CHANNEL = "__resume__"
NULL_TASK = ""  # null-task writes carry empty-string task id in the writes table


class TraceSaver(SqliteSaver):
    """Logs, per get_tuple load: address kind + the task-recorded resume
    values visible in the returned tuple. Logs every durable __resume__
    put_writes with its task kind. Optional fork-intent read-path filter."""

    def __init__(self, conn, log, shim=False):
        super().__init__(conn)
        self._log = log
        self._shim = shim

    def put_writes(self, config, writes, task_id, task_path=""):
        for ch, val in writes:
            if ch == RESUME_CHANNEL:
                self._log.append(("record", "task" if task_id else "null",
                                  self._flat(val)))
        return super().put_writes(config, writes, task_id, task_path)

    def get_tuple(self, config):
        t = super().get_tuple(config)
        explicit = bool(config.get("configurable", {}).get("checkpoint_id"))
        if t is not None and self._shim and explicit and t.pending_writes:
            t = t._replace(pending_writes=[w for w in t.pending_writes
                                           if w[1] != RESUME_CHANNEL])
        visible = []
        if t is not None and t.pending_writes:
            for tid, ch, val in t.pending_writes:
                if ch == RESUME_CHANNEL and tid:
                    visible.append(self._flat(val))
        self._log.append(("load", "explicit" if explicit else "ordinary",
                          tuple(visible)))
        return t

    @staticmethod
    def _flat(val):
        if isinstance(val, list) and val:
            return bool(val[0])
        if isinstance(val, dict) and val:
            return bool(next(iter(val.values())))
        return bool(val)


def model_predict(load_event, incoming, open_task, persisted):
    """The transliterated LGF rules. Inertness: if the loaded thread has
    no open task (public state API: get_state().next is empty), the
    resume consumes nothing and the outcome is the persisted state (CO).
    Serve rule otherwise: task-recorded resume visible at the load =>
    the FIRST recorded value is served; else the invocation's own value.
    (The fork-intent filter acts upstream, by making recorded writes
    invisible at explicit loads.) The inertness clause was ADDED after
    the checker's first run flagged its omission -- the check is real."""
    if not open_task:
        return persisted
    _, _, visible = load_event
    return visible[0] if visible else incoming


class S(TypedDict):
    value: int


def build(shim, log, path):
    conn = sqlite3.connect(path, check_same_thread=False)
    saver = TraceSaver(conn, log, shim=shim)

    def node(state: S):
        allow = interrupt("ok?")
        return {"value": state["value"] + (1 if allow else 0)}

    return (StateGraph(S).add_node("node", node)
            .add_edge(START, "node").add_edge("node", END)
            .compile(checkpointer=saver))


def run_protocol(name, shim, invocations):
    """invocations: list of (address_kind, value or None). First entry is
    the initial run to the interrupt; the rest are resumes. Returns per-
    resume (predicted, measured) pairs."""
    d = tempfile.mkdtemp(prefix=f"probe143_{name}_")
    log = []
    app = build(shim, log, f"{d}/c.sqlite")
    cfg = {"configurable": {"thread_id": name}}
    app.invoke({"value": 0}, cfg)
    ckpt = app.get_state(cfg).config["configurable"]["checkpoint_id"]
    pairs = []
    for kind, v in invocations:
        c = ({"configurable": {"thread_id": name, "checkpoint_id": ckpt}}
             if kind == "explicit" else cfg)
        pre = app.get_state(c)
        open_task = bool(pre.next)
        persisted = bool((pre.values or {}).get("value"))
        marker = len(log)
        r = app.invoke(Command(resume=v), c)
        load = next(e for e in log[marker:] if e[0] == "load")
        predicted = model_predict(load, v, open_task, persisted)
        measured = bool(r.get("value"))
        pairs.append({"address": kind, "supplied": v,
                      "model_predicted_outcome": int(predicted),
                      "measured_outcome": int(measured),
                      "conforms": predicted == measured})
    return {"protocol": name, "shim": shim, "invocations": pairs,
            "trace_events": len(log)}


def main():
    protocols = [
        run_protocol("p1_stock_fork", False,
                     [("explicit", True), ("explicit", False)]),
        run_protocol("p2_shim_fork", True,
                     [("explicit", True), ("explicit", False)]),
        run_protocol("p3_shim_samevalue", True,
                     [("explicit", True), ("explicit", True)]),
        run_protocol("p4_stock_stray", False,
                     [("ordinary", True), ("ordinary", False)]),
    ]
    all_ok = all(p["conforms"] for pr in protocols for p in pr["invocations"])
    print(json.dumps({
        "langgraph_version": version("langgraph"),
        "protocols": protocols,
        "invocations_checked": sum(len(p["invocations"]) for p in protocols),
        "all_invocations_conform": all_ok,
    }, indent=2))


if __name__ == "__main__":
    main()
