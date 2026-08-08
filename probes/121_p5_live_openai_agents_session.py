import json
import os
import sys
import tempfile
import traceback
from importlib.metadata import version
from agents import Agent, Runner, SQLiteSession, function_tool, ModelSettings

if not os.environ.get("OPENAI_API_KEY"):
    print(json.dumps({"skipped": "OPENAI_API_KEY not set"}))
    sys.exit(0)

RESULTS = {"openai_agents_version": version("openai-agents"),
           "model": os.environ.get("PROBE_MODEL", "sdk-default")}
EFF = {"charge": 0}

@function_tool
def charge(amount_usd: int) -> str:
    """Charge the customer's card the given amount in USD."""
    EFF["charge"] += 1
    return f"receipt-{EFF['charge']:03d} for ${amount_usd}"

try:
    db = os.path.join(tempfile.mkdtemp(prefix="rc121_"), "s.db")
    agent = Agent(
        name="clerk",
        instructions=("Use the charge tool exactly when instructed. "
                      "Never charge unless the user explicitly asks."),
        tools=[charge],
        model=os.environ.get("PROBE_MODEL"),
        model_settings=ModelSettings(temperature=0),
    )
    s1 = SQLiteSession("thread-1", db)
    r1 = Runner.run_sync(agent, "Charge exactly 5 dollars once, then say DONE.",
                         session=s1)
    charges_after_run1 = EFF["charge"]

    s2 = SQLiteSession("thread-1", db)
    r2 = Runner.run_sync(agent,
                         "Do not call any tools. Summarize what was charged.",
                         session=s2)
    RESULTS.update({
        "charges_after_run1": charges_after_run1,
        "charges_after_restore_run": EFF["charge"],
        "violation_EO_session_restore_reinvoked_tool":
            EFF["charge"] > charges_after_run1,
        "run1_ok": bool(r1.final_output),
        "run2_ok": bool(r2.final_output),
    })
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
