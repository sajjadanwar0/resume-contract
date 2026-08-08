#!/usr/bin/env python3
import json
import os
import sys
from importlib.metadata import version

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"skipped": "ANTHROPIC_API_KEY not set"}))
    sys.exit(0)

from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

LEDGER = []

def set_discount(percent: int) -> str:
    """Apply a discount of the given percent to the customer's order."""
    LEDGER.append(percent)
    return f"discount of {percent}% applied"

model = ChatAnthropic(
    model=os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),
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

agent.invoke(
    {"messages": [("user", "Apply a 10% discount to my order. Use the tool.")]},
    cfg,
)
out["ledger_at_interrupt"] = list(LEDGER)
snap = agent.get_state(cfg)
ckpt_id = snap.config["configurable"]["checkpoint_id"]
out["interrupted_next"] = list(snap.next)
fork_cfg = {"configurable": {"thread_id": "live-fd", "checkpoint_id": ckpt_id}}

r1 = agent.invoke(None, fork_cfg)
out["ledger_after_resume1"] = list(LEDGER)
out["resume1_last_msg"] = r1["messages"][-1].content[:200]

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

out["violation_second_decision_ignored"] = 25 not in LEDGER
print(json.dumps(out, indent=2, default=str))
