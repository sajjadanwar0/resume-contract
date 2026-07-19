#!/usr/bin/env python3
"""
131_p5_live_fd_violation.py
P5-live, FAILURE direction: does the #6663 fork violation reproduce with a
real model in the loop? This closes the live-cell asymmetry the paper
records (live evidence currently covers only conformant paths).

Requires ANTHROPIC_API_KEY. Run on your host:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 probes/131_p5_live_fd_violation.py

Design (mirrors probe 121's agent but drives the FD protocol of probe 113):
  1. A prebuilt ReAct agent (claude-haiku-4-5) with one side-effecting tool
     `set_discount(percent)` that writes to an effect ledger.
  2. interrupt_before=["tools"]: the run halts at the interrupt checkpoint
     before the tool fires.
  3. Record checkpoint_id at the interrupt. Resume #1 addressed to
     (thread_id, checkpoint_id) approving the call -> tool fires, ledger+1.
  4. Resume #2 addressed to the SAME (thread_id, checkpoint_id) with a
     DIFFERENT decision (edited tool args / rejection). Contract FD: this
     is a fork; the second branch outcome must reflect the second decision.
  5. Verdict fields (only counters/decisions are stable under model
     nondeterminism): ledger count per branch, whether branch 2's outcome
     tracked the second decision or re-served branch 1's.

Expected on 1.2.9 given the LLM-free result: branch 2 re-serves branch 1's
outcome (violation live). If the model path routes differently, the JSON
records exactly what happened; no verdict is asserted a priori.
"""
import json
import os
import sys
from importlib.metadata import version

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"skipped": "ANTHROPIC_API_KEY not set"}))
    sys.exit(0)

from langchain.chat_models import init_chat_model  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

LEDGER = []


def set_discount(percent: int) -> str:
    """Apply a discount of the given percent to the customer's order."""
    LEDGER.append(percent)
    return f"discount of {percent}% applied"


model = init_chat_model(
    "anthropic:" + os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),
    temperature=0)
saver = InMemorySaver()
agent = create_react_agent(
    model, [set_discount], interrupt_before=["tools"], checkpointer=saver
)

cfg = {"configurable": {"thread_id": "live-fd"}}
out = {
    "langgraph_version": version("langgraph"),
    "model": os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),
}

# 1. Drive to the interrupt (model should call set_discount(10)).
agent.invoke(
    {"messages": [("user", "Apply a 10% discount to my order. Use the tool.")]},
    cfg,
)
out["ledger_at_interrupt"] = list(LEDGER)
snap = agent.get_state(cfg)
ckpt_id = snap.config["configurable"]["checkpoint_id"]
out["interrupted_next"] = list(snap.next)
fork_cfg = {"configurable": {"thread_id": "live-fd", "checkpoint_id": ckpt_id}}

# 2. Resume #1: approve as-is.
r1 = agent.invoke(None, fork_cfg)
out["ledger_after_resume1"] = list(LEDGER)
out["resume1_last_msg"] = r1["messages"][-1].content[:200]

# 3. Resume #2 addressed to the SAME checkpoint: different decision.
#    Contract reading: fork -> the 25% decision must govern this branch.
try:
    r2 = agent.invoke(
        Command(resume={"action": "edit", "args": {"percent": 25}}), fork_cfg
    )
    out["resume2_mode"] = "command_edit"
except Exception as e:
    out["resume2_command_error"] = f"{type(e).__name__}: {e}"
    r2 = agent.invoke(None, fork_cfg)
    out["resume2_mode"] = "bare_reinvoke"
out["ledger_after_resume2"] = list(LEDGER)
out["resume2_last_msg"] = r2["messages"][-1].content[:200]

# Stable verdict: did the second branch produce a 25 effect, or re-serve 10?
out["violation_second_decision_ignored"] = 25 not in LEDGER
print(json.dumps(out, indent=2, default=str))
