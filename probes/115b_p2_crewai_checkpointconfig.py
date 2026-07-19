#!/usr/bin/env python3
"""
115b_p2_crewai_checkpointconfig.py
Tests CrewAI's written resume contract for the Checkpointing feature
(docs.crewai.com/en/concepts/checkpointing: restore "resume[s] without
re-running completed work" / "skips completed tasks").

Two phases, one JSON verdict:

  Phase 1 (documented configuration): a Flow configured exactly as the
  feature's Flow example -- on_events=["method_execution_finished"] --
  completes a method; count checkpoint files written. Verdict key:
  violation_silent_noop_documented_event (files == 0 with no error).

  Phase 2 (wildcard control + restore): on_events=["*"]; s1 completes
  (state counter 1 persisted), s2 crashes; Flow.from_checkpoint(latest)
  + kickoff with the crash disarmed. Verdict keys: s1_execs_total (2 =
  completed work re-ran), counter_after_resume vs
  expected_counter_if_docs_hold=11, and
  violation_doc_vs_behavior_completed_work_rerun. Note the oracle split:
  the replay is visible in the effect counters and may be invisible in
  flow state (state can be rebuilt from initial values).

Deterministic; no LLM calls; the only "crash" is a raised exception.
"""
import json
import os
import shutil
import traceback
from pathlib import Path

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from importlib.metadata import version
from pydantic import BaseModel
from crewai import CheckpointConfig
from crewai.flow.flow import Flow, listen, start

OUT = {"crewai_version": version("crewai")}
EFF = {"s1": 0, "s2": 0}
FAIL = {"armed": False}


class St(BaseModel):
    id: str = ""
    counter: int = 0


class F(Flow[St]):
    @start()
    def s1(self):
        EFF["s1"] += 1
        self.state.counter += 1

    @listen(s1)
    def s2(self):
        if FAIL["armed"]:
            FAIL["armed"] = False
            raise RuntimeError("simulated crash in s2")
        EFF["s2"] += 1
        self.state.counter += 10


def fresh_dir(p: Path) -> Path:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


try:
    # ---------------- Phase 1: the documented Flow example configuration ---
    d1 = fresh_dir(Path("/tmp/rc_115b_documented"))
    EFF["s1"] = EFF["s2"] = 0
    FAIL["armed"] = False
    f_doc = F(checkpoint=CheckpointConfig(
        location=str(d1),
        on_events=["method_execution_finished"],
    ))
    f_doc.kickoff()
    n_doc = len(list(d1.glob("**/*.json")))
    OUT["checkpoints_written_documented_event"] = n_doc
    OUT["silent_noop_documented_event"] = (n_doc == 0)
    OUT["violation_silent_noop_documented_event"] = (n_doc == 0)

    # ---------------- Phase 2: wildcard control, crash, from_checkpoint ----
    d2 = fresh_dir(Path("/tmp/rc_115b_wildcard"))
    EFF["s1"] = EFF["s2"] = 0
    FAIL["armed"] = True
    f1 = F(checkpoint=CheckpointConfig(location=str(d2), on_events=["*"]))
    try:
        f1.kickoff()
        OUT["first_run"] = "completed (unexpected)"
    except Exception as e:
        OUT["first_run"] = f"crashed as planned: {type(e).__name__}"
    OUT["counter_at_crash"] = f1.state.counter

    cps = sorted(d2.glob("**/*.json"), key=lambda p: p.stat().st_mtime)
    OUT["n_checkpoints"] = len(cps)
    if not cps:
        raise RuntimeError("wildcard control wrote no checkpoints")

    f2 = F.from_checkpoint(CheckpointConfig(restore_from=str(cps[-1])))
    resume_error = None
    try:
        f2.kickoff()
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"
    OUT["resume_error"] = resume_error
    OUT["counter_after_resume"] = f2.state.counter
    OUT["s1_execs_total"] = EFF["s1"]
    OUT["s2_execs_total"] = EFF["s2"]
    OUT["expected_counter_if_docs_hold"] = 11
    OUT["violation_doc_vs_behavior_completed_work_rerun"] = EFF["s1"] > 1
except Exception:
    OUT["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(OUT, indent=2, default=str))
