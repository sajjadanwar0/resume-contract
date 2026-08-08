import json
import os
import sys
import traceback
from importlib.metadata import version
from langchain_anthropic import ChatAnthropic
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"skipped": "ANTHROPIC_API_KEY not set"}))
    sys.exit(0)

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
    charges_at_interrupt = EFF["charge"]
    agent.invoke(None, cfg)
    charges_after_approval = EFF["charge"]
    stray_error = None

    try:
        agent.invoke(None, cfg)
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
