#!/usr/bin/env python3
"""
118_p3_langgraph_rd_interleavings.py
RD (recovery determinism) probe for LangGraph, deterministic by construction.

Mechanism under test (issue #8039): in synchronous durability mode a task's
result persistence (put_writes) and the superstep checkpoint (put) are
submitted without an ordering constraint, so a crash between them leaves one
of two legal durable states. Rather than racing a kill (host-dependent), this
probe CONSTRUCTS both durable states explicitly with a dropping checkpointer
-- exhaustive exploration of the two interleavings -- then resumes each and
counts task re-executions.

  Order W (writes landed, checkpoint lost): drop the superstep `put`
  Order C (checkpoint landed, writes lost): drop the task `put_writes`

Verdict: RD requires the recovery decision (skip vs re-execute task 1, hence
its external effect count) to be a function of the crash point alone. If the
two orders yield different s1 execution counts on resume, recovery is a
function of scheduler order -- an RD violation, reproduced without timing.
"""
import json
import traceback
from importlib.metadata import version
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task
from langgraph.errors import GraphInterrupt  # noqa: F401 (import check)

RESULTS = {"langgraph_version": version("langgraph")}


class DroppingSaver(InMemorySaver):
    """InMemorySaver that can drop designated persistence ops, constructing
    the durable state a crash between unordered ops would leave behind."""

    def __init__(self):
        super().__init__()
        self.log = []
        self.drop_puts_after = None      # drop every `put` once armed
        self.drop_writes_after = None    # drop every `put_writes` once armed
        self._put_count = 0
        self._writes_count = 0

    def put(self, config, checkpoint, metadata, new_versions):
        self._put_count += 1
        self.log.append(("put", self._put_count))
        if self.drop_puts_after is not None and self._put_count > self.drop_puts_after:
            return config  # durable layer never received it
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        self._writes_count += 1
        self.log.append(("put_writes", self._writes_count, [w[0] for w in writes]))
        if (self.drop_writes_after is not None
                and self._writes_count > self.drop_writes_after):
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
    """order='W': writes durable, checkpoint dropped.
       order='C': checkpoint durable, writes dropped."""
    eff = {"s1": 0, "s2": 0}
    saver = DroppingSaver()
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": f"t-{order}"}}

    # Calibration insight: the first put is the input checkpoint; s1's result
    # lands via put_writes, then the superstep checkpoint via put. Arm drops
    # AFTER the ops that precede the crash step so only the contested pair is
    # affected.
    if order == "W":
        saver.drop_puts_after = 1      # keep input ckpt; drop superstep put
    elif order == "C":
        saver.drop_writes_after = 0    # drop s1's put_writes; keep puts
    # order == "N": control -- nothing dropped; full durable state at crash

    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:  # crash surfaced wrapped
        crashed = type(e).__name__

    persistence_log = list(saver.log)
    s1_at_crash = eff["s1"]

    resume_error = None
    result = None
    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"

    return {
        "crashed": crashed,
        "persistence_log": persistence_log,
        "s1_execs_at_crash": s1_at_crash,
        "s1_execs_total": eff["s1"],
        "s2_execs_total": eff["s2"],
        "result": result,
        "resume_error": resume_error,
    }


try:
    w = scenario("W")
    c = scenario("C")
    n = scenario("N")
    RESULTS["order_W_writes_durable"] = w
    RESULTS["order_C_checkpoint_durable"] = c
    RESULTS["order_N_control_nothing_dropped"] = n
    RESULTS["s1_execs_order_W"] = w["s1_execs_total"]
    RESULTS["s1_execs_order_C"] = c["s1_execs_total"]
    RESULTS["s1_execs_order_N"] = n["s1_execs_total"]
    RESULTS["violation_RD_recovery_depends_on_persistence_order"] = (
        w["s1_execs_total"] != c["s1_execs_total"]
        or (w["result"] != c["result"])
    )
    # EO across crash-resume: in the CONTROL run every persistence op landed,
    # so s1's __return__ is durable at the crash. Re-execution of s1 on resume
    # is a duplicate external effect despite durable task results -- the
    # functional API memoizes across interrupts (#6792 fixed) but not across
    # crash-resume.
    RESULTS["violation_EO_crash_resume_reexecutes_durable_task"] = (
        n["s1_execs_total"] > 1
    )
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
