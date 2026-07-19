#!/usr/bin/env python3
"""
139_p8_interposition_overhead.py
Answers "no performance evaluation" at the level this paper can honestly
claim: the runtime overhead of REMIT-style interposition at the
checkpointer interface, measured on the exact probe workflow. Three saver
configurations, identical protocol, N iterations each with fresh on-disk
SQLite files per iteration:

  stock        SqliteSaver, unmodified
  fork_intent  the read-path FD shim (probe 134): get_tuple override only
  validating   a CV gate: schema check on every checkpoint before commit

Protocol per iteration: initial invoke (runs to the interrupt) then
resume (fires the gated effect, completes). Reported per configuration
and phase: p50 / p95 / p99 / mean wall-clock (ms), plus relative overhead
vs stock. Scope stated in-paper: single host, microbenchmark of the
interposition point, not a throughput-under-concurrency study and not a
durable-execution-engine comparison.
"""
import json
import sqlite3
import statistics
import tempfile
import time
from importlib.metadata import version
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

N = 200
RESUME_CHANNEL = "__resume__"


class ForkIntentSqliteSaver(SqliteSaver):
    def get_tuple(self, config):
        t = super().get_tuple(config)
        if t is None:
            return t
        if config.get("configurable", {}).get("checkpoint_id") and t.pending_writes:
            t = t._replace(pending_writes=[w for w in t.pending_writes
                                           if w[1] != RESUME_CHANNEL])
        return t


class ValidatingSqliteSaver(SqliteSaver):
    """CV gate: structural validity check on every checkpoint commit."""
    def put(self, config, checkpoint, metadata, new_versions):
        if not isinstance(checkpoint, dict) or "id" not in checkpoint \
                or "channel_values" not in checkpoint \
                or not isinstance(checkpoint.get("channel_values"), dict):
            raise ValueError("invalid checkpoint rejected by CV gate")
        return super().put(config, checkpoint, metadata, new_versions)


class S(TypedDict):
    value: int


def build(saver):
    def node(state: S):
        allow = interrupt("ok?")
        return {"value": state["value"] + (1 if allow else 0)}
    return (StateGraph(S).add_node("node", node)
            .add_edge(START, "node").add_edge("node", END)
            .compile(checkpointer=saver))


def pct(xs, q):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, int(round(q / 100 * (len(xs) - 1)))))
    return xs[k]


def bench(saver_cls, label):
    first, second = [], []
    for i in range(N):
        d = tempfile.mkdtemp(prefix=f"probe139_{label}_")
        conn = sqlite3.connect(f"{d}/ckpt.sqlite", check_same_thread=False)
        app = build(saver_cls(conn))
        cfg = {"configurable": {"thread_id": f"t{i}"}}
        t0 = time.perf_counter()
        app.invoke({"value": 0}, cfg)
        t1 = time.perf_counter()
        app.invoke(Command(resume=True), cfg)
        t2 = time.perf_counter()
        first.append((t1 - t0) * 1000)
        second.append((t2 - t1) * 1000)
        conn.close()
    def stats(xs):
        return {"p50_ms": round(pct(xs, 50), 3), "p95_ms": round(pct(xs, 95), 3),
                "p99_ms": round(pct(xs, 99), 3), "mean_ms": round(statistics.mean(xs), 3)}
    return {"initial_invoke": stats(first), "resume": stats(second)}


def main():
    out = {"langgraph_version": version("langgraph"),
           "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
           "iterations_per_config": N, "configs": {}}
    for cls, label in [(SqliteSaver, "stock"),
                       (ForkIntentSqliteSaver, "fork_intent"),
                       (ValidatingSqliteSaver, "validating")]:
        out["configs"][label] = bench(cls, label)
    base = out["configs"]["stock"]
    for label in ("fork_intent", "validating"):
        for phase in ("initial_invoke", "resume"):
            b, x = base[phase]["p50_ms"], out["configs"][label][phase]["p50_ms"]
            out["configs"][label][phase]["p50_overhead_vs_stock_pct"] = \
                round((x - b) / b * 100, 2) if b else None
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
