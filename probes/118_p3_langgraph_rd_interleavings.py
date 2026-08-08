import copy
import json
import traceback
from importlib.metadata import version
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task

RESULTS = {"langgraph_version": version("langgraph")}

class DroppingSaver(InMemorySaver):
    def __init__(self):
        super().__init__()
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

def _arm_drops(saver, order: str):
    """Calibration insight: the first put is the input checkpoint; s1's
    result lands via put_writes, then the superstep checkpoint via put. Arm
    drops AFTER the ops that precede the crash step so only the contested
    pair is affected."""
    if order == "W":
        saver.drop_puts_after = 1
    elif order == "C":
        saver.drop_writes_after = 0

def _construct_crashed_state(order: str, thread_id: str):
    """Reflexive-arm helper: run to the crash under the given drop schedule;
    return the saver holding the constructed durable log plus observables.
    (The original two-order arm below is untouched and does not use this.)"""
    eff = {"s1": 0, "s2": 0}
    saver = DroppingSaver()
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": thread_id}}
    _arm_drops(saver, order)

    crashed = None

    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__
    return saver, cfg, {
        "crashed": crashed,
        "persistence_log": list(saver.log),
        "s1_execs_at_crash": eff["s1"],
    }

def _recover_once(saver, cfg):
    """One recovery against the given saver: fresh workflow, fresh effect
    counters, crash disarmed. Returns the recovery decision observables."""
    eff = {"s1": 0, "s2": 0}
    crash = {"armed": False}
    wf = build(saver, eff, crash)
    result = resume_error = None

    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"
    return {
        "s1_execs": eff["s1"],
        "s2_execs": eff["s2"],
        "result": result,
        "resume_error": resume_error,
    }

def scenario(order: str):
    """order='W': writes durable, checkpoint dropped.
       order='C': checkpoint durable, writes dropped."""
    eff = {"s1": 0, "s2": 0}
    saver = DroppingSaver()
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": f"t-{order}"}}

    _arm_drops(saver, order)
    crashed = None

    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
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

def scenario_same_log(order: str):
    """Reflexive RD: recover twice from one constructed durable log."""
    saver, cfg, cons = _construct_crashed_state(order, f"r-{order}")

    decisions = []
    strategy = "deepcopy"

    try:
        copies = []
        for _ in range(2):
            s_k = copy.deepcopy(saver)
            s_k.drop_puts_after = None
            s_k.drop_writes_after = None
            s_k.log = []
            copies.append(s_k)
        for s_k in copies:
            decisions.append(_recover_once(s_k, cfg))
        log_evidence = {"identical_durable_log": "shared deep copies of one saver"}
    except Exception:
        strategy = "double_construction"
        decisions = []
        op_logs = []

        for k in range(2):
            s_k, cfg_k, cons_k = _construct_crashed_state(order, f"r{k}-{order}")
            s_k.drop_puts_after = None
            s_k.drop_writes_after = None
            op_logs.append(cons_k["persistence_log"])
            decisions.append(_recover_once(s_k, cfg_k))
        log_evidence = {
            "construction_op_logs_identical": op_logs[0] == op_logs[1],
            "note": "logs identical up to fresh checkpoint ids/timestamps",
        }

    return {
        "order": order,
        "strategy": strategy,
        "construction": cons,
        "log_evidence": log_evidence,
        "decisions": decisions,
        "identical": decisions[0] == decisions[1],
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

    RESULTS["violation_EO_crash_resume_reexecutes_durable_task"] = (
            n["s1_execs_total"] > 1
    )

    refl = {order: scenario_same_log(order) for order in ("W", "C", "N")}
    RESULTS["rd_reflexive_same_log"] = refl
    RESULTS["rd_reflexive_same_log_identical_all"] = all(
        r["identical"] for r in refl.values()
    )
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
