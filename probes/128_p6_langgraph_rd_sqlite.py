#!/usr/bin/env python3
"""
128_p6_langgraph_rd_sqlite.py
P6 - RD (recovery determinism) on the DURABLE backend: probe 118's
exhaustive two-interleaving construction ported from InMemorySaver to
SqliteSaver. The #8039 hazard is that a task's put_writes and the superstep
put are submitted without an ordering barrier, so a crash between them
leaves one of two legal durable states. We construct both states on the
real SQLite backend and compare recovery.

  Order W: writes durable, superstep checkpoint dropped
  Order C: checkpoint durable, writes dropped
  Order N: control, nothing dropped

Verdict: RD requires identical recovery decisions (s1 re-execution counts)
from identical crash points regardless of which of the two ops landed. If
W and C resume to different s1 counts, the recovery decision is a function
of persistence-op order -- the divergence #8039 predicts, demonstrated
deterministically on the durable saver without racing the pool.
"""
import json
import os
import sqlite3
import tempfile
import traceback
from importlib.metadata import version

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.func import entrypoint, task

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
}
WORKDIR = tempfile.mkdtemp(prefix="probe128_")


class DroppingSqliteSaver(SqliteSaver):
    """SqliteSaver that can drop designated persistence ops, constructing the
    durable state a crash between unordered ops would leave behind."""

    def __init__(self, conn):
        super().__init__(conn)
        self.log = []
        self.drop_puts_after = None
        self.drop_writes_after = None
        self._put_count = 0
        self._writes_count = 0

    def put(self, config, checkpoint, metadata, new_versions):
        self._put_count += 1
        self.log.append(("put", self._put_count))
        if self.drop_puts_after is not None and self._put_count > self.drop_puts_after:
            return config
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        self._writes_count += 1
        self.log.append(("put_writes", self._writes_count, [w[0] for w in writes]))
        if (
            self.drop_writes_after is not None
            and self._writes_count > self.drop_writes_after
        ):
            return
        return super().put_writes(config, writes, task_id, task_path)


class Crash(RuntimeError):
    pass


def build(saver, eff, crash_in_s2):
    @task
    def s1(x: int) -> int:
        eff["s1"] += 1
        return x + 1

    @task
    def s2(x: int) -> int:
        if crash_in_s2["armed"]:
            crash_in_s2["armed"] = False
            raise Crash("simulated process death")
        eff["s2"] += 1
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        a = s1(x).result()
        b = s2(a).result()
        return b

    return wf


def scenario(order: str):
    eff = {"s1": 0, "s2": 0}
    conn = sqlite3.connect(
        os.path.join(WORKDIR, f"rd_{order}.sqlite"), check_same_thread=False
    )
    saver = DroppingSqliteSaver(conn)
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": f"t-{order}"}}

    if order == "W":
        saver.drop_puts_after = 1  # keep input ckpt; drop superstep put
    elif order == "C":
        saver.drop_writes_after = 0  # drop s1's put_writes; keep puts

    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__

    s1_at_crash = eff["s1"]
    persistence_log = list(saver.log)

    result = resume_error = None
    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"

    return {
        "order": order,
        "crashed_as": crashed,
        "s1_execs_at_crash": s1_at_crash,
        "s1_execs_after_resume": eff["s1"],
        "s2_execs_after_resume": eff["s2"],
        "result": result,
        "resume_error": resume_error,
        "persistence_log": persistence_log,
    }


def main():
    for order in ("N", "W", "C"):
        try:
            RESULTS[f"order_{order}"] = scenario(order)
        except Exception:
            RESULTS[f"order_{order}"] = {"probe_error": traceback.format_exc()}
    try:
        w = RESULTS["order_W"]["s1_execs_after_resume"]
        c = RESULTS["order_C"]["s1_execs_after_resume"]
        RESULTS["verdict"] = {
            "backend": "SqliteSaver",
            "recovery_identical_across_orders": w == c,
            "violation_rd": w != c,
            "note": "RD at checkpointer-API granularity on the durable saver; "
            "the executor pool itself is not raced.",
        }
    except Exception:
        RESULTS["verdict"] = {"probe_error": traceback.format_exc()}
    print(json.dumps(RESULTS, indent=2, default=str))


if __name__ == "__main__":
    main()
