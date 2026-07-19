#!/usr/bin/env python3
"""
122_p5_live_langgraph_anthropic_interrupt.py
Live ecological probe (requires ANTHROPIC_API_KEY): with a REAL model in the
loop, LangGraph interrupt->resume must not re-invoke completed tools, and a
stray second resume must stay inert (EO/CO under ecological conditions --
the cell the Threats section's abstraction caveat asks for).

Protocol: prebuilt ReAct agent, charge() tool with an effect counter,
interrupt_before=["tools"]; approve once (resume None), then inject a stray
second resume. Stable verdicts are the counters.
"""
import json
import os
import sys
import traceback
from importlib.metadata import version

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"skipped": "ANTHROPIC_API_KEY not set"}))
    sys.exit(0)

from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

RESULTS = {"langgraph_version": version("langgraph"),
           "model": os.environ.get("PROBE_MODEL", "claude-haiku-4-5")}
EFF = {"charge": 0}


@tool
def charge(amount_usd: int) -> str:
    """Charge the customer's card the given amount in USD."""
    EFF["charge"] += 1
    return f"receipt-{EFF['charge']:03d} for ${amount_usd}"

try:
    model = ChatAnthropic(model=os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),
                          temperature=0, max_tokens=512)
    agent = create_react_agent(model, [charge],
                               checkpointer=InMemorySaver(),
                               interrupt_before=["tools"])
    cfg = {"configurable": {"thread_id": "live-1"}}
    agent.invoke({"messages": [("user",
                  "Charge exactly 5 dollars once using the charge tool, "
                  "then say DONE.")]}, cfg)
    charges_at_interrupt = EFF["charge"]      # expected 0: paused pre-tool
    agent.invoke(None, cfg)                    # approval resume
    charges_after_approval = EFF["charge"]     # expected 1
    stray_error = None
    try:
        agent.invoke(None, cfg)                # stray second resume
    except Exception as e:
        stray_error = type(e).__name__
    RESULTS.update({
        "charges_at_interrupt": charges_at_interrupt,
        "charges_after_approval": charges_after_approval,
        "charges_after_stray_resume": EFF["charge"],
        "stray_resume_error": stray_error,
        "violation_EO_live_interrupt_resume": charges_after_approval != 1,
        "violation_CO_live_stray_resume_refired":
            EFF["charge"] > charges_after_approval,
    })
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
