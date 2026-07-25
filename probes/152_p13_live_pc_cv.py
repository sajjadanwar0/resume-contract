#!/usr/bin/env python3
"""
152_p13_live_pc_cv.py
P13-live: PC and CV with a real model in the loop. Closes the remaining
live-cell asymmetry: live evidence covers EO (148 session restore), CO
(interrupt-approve + stray), and FD (131 live fork); PC and CV have no live
arm. Two legs, same keyed-harness conventions as probes 122/131/148 (skip
with a JSON marker if no key; only counters, checkpoint-id sequences, and
schema-validity observables are audited -- never model text).

Environment: envs/langgraph-live (langchain-anthropic + langgraph-prebuilt;
imports mirror probe 122, the committed pattern for this env).

Requires ANTHROPIC_API_KEY. Run on your host:
    export ANTHROPIC_API_KEY=sk-ant-...
    uv run --project envs/langgraph-live python probes/152_p13_live_pc_cv.py

Leg 1 -- live PC (state lineage across the gate). A prebuilt ReAct agent
(claude-haiku-4-5 by default; PROBE_MODEL overrides) with one side-effecting
tool, interrupt_before=["tools"]. At the interrupt, record the durable
lineage: the ordered checkpoint-id sequence from get_state_history and the
ledger count (must be 0). Approve-resume to completion. PC's observable: the
pre-gate lineage is a prefix of the post-completion lineage (same ids, same
order), pre-gate channel state was not rewritten, and the ledger moved
0 -> 1. Audited fields: id sequences, prefix boolean, ledger counts.

Leg 2 -- live CV (validity at the write, real-model state in the log). On
the same thread after completion, submit a schema-corrupted checkpoint
through the public saver API (a copy of the latest checkpoint with
channel_values replaced by a non-dict and the version map dropped). CV's
observable: is the invalid record rejected loudly (exception) or persisted
silently -- and if persisted, does a fresh get_state on the thread now serve
it? No verdict is asserted a priori; the JSON records exactly what happened
(the LLM-free expectation on 1.2.9 is silent persistence, the #6491 class).

Output: results/live/152_results.json shape, printed to stdout.
"""
import json
import os
import sys
from importlib.metadata import version

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(json.dumps({"skipped": "ANTHROPIC_API_KEY not set"}))
    sys.exit(0)

from langchain_anthropic import ChatAnthropic  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

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

    # Approve-resume: bare invoke(None, cfg) -- the static-breakpoint form.
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
    # Hex-max suffix on the latest id's time-ordered prefix: the corrupt id
    # sorts >= every real id on the thread, so the serving-side observable
    # is deterministic (see module docstring for the "beef" history).
    bad["id"] = good["id"][:-4] + "ffff"
    bad["channel_values"] = "CORRUPT-NOT-A-DICT"  # schema violation
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