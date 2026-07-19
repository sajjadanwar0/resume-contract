#!/usr/bin/env python3
"""
115_p2_conformance_crewai.py
Resume-contract conformance probe: CrewAI Flows (@persist / SQLiteFlowPersistence).

CrewAI has no interrupt/approval primitive; its resume model is
"restore persisted state, re-run the flow methods". This probe measures what
that means for the contract:

  R1  restore-after-success : kickoff(id=...) on a COMPLETED flow ->
        do s1/s2 re-execute (EO)? does restored state compound (PC)?
  R2  crash-resume          : s2 raises on first run; re-kickoff with same id ->
        does completed s1 re-execute (EO)? final counter value vs expected?
No LLM calls involved; steps are pure-Python with effect counters.
"""
import json
import os
import traceback

os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
os.environ["OTEL_SDK_DISABLED"] = "true"

from importlib.metadata import version
from pydantic import BaseModel
from crewai.flow.flow import Flow, listen, start
from crewai.flow.persistence import persist

RESULTS = {"crewai_version": version("crewai")}
EFF = {"s1": 0, "s2": 0}
FAIL_FLAG = {"armed": False, "fired": False}


class St(BaseModel):
    id: str = ""
    counter: int = 0


@persist()
class F(Flow[St]):
    @start()
    def s1(self):
        EFF["s1"] += 1
        self.state.counter += 1

    @listen(s1)
    def s2(self):
        if FAIL_FLAG["armed"] and not FAIL_FLAG["fired"]:
            FAIL_FLAG["fired"] = True
            raise RuntimeError("simulated crash in s2")
        EFF["s2"] += 1
        self.state.counter += 10


def r1_restore_after_success():
    EFF["s1"] = EFF["s2"] = 0
    f1 = F()
    f1.kickoff()
    fid = f1.state.id
    c_after_first = f1.state.counter
    s1_first, s2_first = EFF["s1"], EFF["s2"]

    f2 = F()
    f2.kickoff(inputs={"id": fid})
    return {
        "flow_id": fid,
        "counter_after_first_run": c_after_first,
        "counter_after_restore_run": f2.state.counter,
        "s1_execs_total": EFF["s1"],
        "s2_execs_total": EFF["s2"],
        "violation_EO_methods_replayed": EFF["s1"] > s1_first or EFF["s2"] > s2_first,
        "violation_PC_state_compounded": f2.state.counter != c_after_first,
    }


def r2_crash_resume():
    EFF["s1"] = EFF["s2"] = 0
    FAIL_FLAG["armed"] = True
    FAIL_FLAG["fired"] = False

    f1 = F()
    crash = None
    try:
        f1.kickoff()
    except Exception as e:
        crash = f"{type(e).__name__}: {e}"
    fid = f1.state.id
    c_after_crash = f1.state.counter
    s1_before_resume = EFF["s1"]

    FAIL_FLAG["armed"] = False  # "bug fixed", now resume
    f2 = F()
    resume_error = None
    try:
        f2.kickoff(inputs={"id": fid})
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"

    return {
        "flow_id": fid,
        "crash": crash,
        "counter_at_crash": c_after_crash,
        "counter_after_resume": f2.state.counter,
        "s1_execs_total": EFF["s1"],
        "s2_execs_total": EFF["s2"],
        "resume_error": resume_error,
        "violation_EO_completed_step_reexecuted": EFF["s1"] > s1_before_resume,
        "note_expected_counter_if_exactly_once": 11,
    }


for name, fn in {"R1_restore_after_success": r1_restore_after_success,
                 "R2_crash_resume": r2_crash_resume}.items():
    try:
        RESULTS[name] = fn()
    except Exception:
        RESULTS[name] = {"probe_error": traceback.format_exc(limit=4)}

print(json.dumps(RESULTS, indent=2, default=str))
