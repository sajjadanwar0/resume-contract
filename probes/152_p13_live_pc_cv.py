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

LEDGER = []

def set_discount(percent: int) -> str:
    """Apply a discount of the given percent to the customer's order."""
    LEDGER.append(percent)
    return f"discount of {percent}% applied"

MODEL = os.environ.get("PROBE_MODEL", "claude-haiku-4-5")
RESULTS = {
    "langgraph_version": version("langgraph"),
    "langchain_anthropic_version": version("langchain-anthropic"),
    "model": MODEL,
}

def lineage(agent, cfg):
    """Ordered (oldest-first) checkpoint-id sequence for the thread."""
    hist = list(agent.get_state_history(cfg))
    hist.reverse()
    return [s.config["configurable"]["checkpoint_id"] for s in hist]

def leg_pc():
    saver = InMemorySaver()
    model = ChatAnthropic(model=MODEL, temperature=0)
    agent = create_react_agent(
        model, [set_discount], interrupt_before=["tools"], checkpointer=saver
    )
    cfg = {"configurable": {"thread_id": "live-pc"}}

    agent.invoke(
        {"messages": [("user", "Apply a 10% discount to my order. Use the tool.")]},
        cfg,
    )
    ids_at_gate = lineage(agent, cfg)
    gate_state = agent.get_state(cfg)
    msgs_at_gate = len(gate_state.values.get("messages", []))
    ledger_at_gate = len(LEDGER)

    agent.invoke(None, cfg)
    ids_done = lineage(agent, cfg)
    ledger_done = len(LEDGER)

    prefix_stable = ids_done[: len(ids_at_gate)] == ids_at_gate
    done_state = agent.get_state(cfg)
    msgs_done = len(done_state.values.get("messages", []))

    out = {
        "checkpoints_at_gate": len(ids_at_gate),
        "checkpoints_done": len(ids_done),
        "pre_gate_lineage_is_prefix": prefix_stable,
        "pre_gate_messages_not_rewritten": msgs_done >= msgs_at_gate,
        "ledger_at_gate": ledger_at_gate,
        "ledger_done": ledger_done,
        "pc_observable_holds": (
                prefix_stable and ledger_at_gate == 0 and ledger_done == 1
        ),
    }
    return out, saver, agent, cfg

def leg_cv(saver, agent, cfg):
    base_cfg = {"configurable": {"thread_id": "live-pc", "checkpoint_ns": ""}}
    tup = saver.get_tuple(base_cfg)
    good = tup.checkpoint
    bad = dict(good)
    bad["id"] = good["id"][:-4] + "ffff"
    bad["channel_values"] = "CORRUPT-NOT-A-DICT"
    bad.pop("channel_versions", None)

    write_error = None
    try:
        saver.put(
            base_cfg,
            bad,
            {"source": "probe152", "step": -1, "parents": {}},
            {},
        )
    except Exception as e:
        write_error = f"{type(e).__name__}: {e}"

    served_error = None
    serves_corrupt = None
    try:
        latest = saver.get_tuple(base_cfg)
        serves_corrupt = latest.checkpoint.get("id") == bad["id"] if latest else None
    except Exception as e:
        served_error = f"{type(e).__name__}: {e}"

    state_read_error = None
    try:
        agent.get_state(cfg)
    except Exception as e:
        state_read_error = f"{type(e).__name__}: {e}"

    return {
        "latest_id_suffix": good["id"][-4:],
        "corrupt_id_sorts_latest": bad["id"] >= good["id"],
        "invalid_write_rejected_loudly": write_error is not None,
        "invalid_write_error": write_error,
        "corrupt_record_served_as_latest": serves_corrupt,
        "get_tuple_error_after_corrupt_write": served_error,
        "get_state_error_after_corrupt_write": state_read_error,
    }

try:
    pc, saver, agent, cfg = leg_pc()
    RESULTS["live_pc"] = pc
    RESULTS["live_cv"] = leg_cv(saver, agent, cfg)
except Exception:
    import traceback

    RESULTS["probe_error"] = traceback.format_exc(limit=8)

print(json.dumps(RESULTS, indent=2, default=str))
