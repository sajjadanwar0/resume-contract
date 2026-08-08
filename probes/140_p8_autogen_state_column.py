#!/usr/bin/env python3
import asyncio
import json
import re
import sqlite3
import tempfile
from importlib.metadata import version

from autogen_agentchat.agents import AssistantAgent
from autogen_core import FunctionCall
from autogen_core.models import CreateResult, ModelInfo
from autogen_ext.models.replay import ReplayChatCompletionClient

def ledger(path, add=None):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT)")
    if add is not None:
        c.execute("INSERT INTO effects (tag) VALUES (?)", (add,))
    c.commit()
    rows = [r[0] for r in c.execute("SELECT tag FROM effects ORDER BY n")]
    c.close()
    return rows

def make_agent(ledger_path, script):
    async def charge(amount: int) -> str:
        """Apply a non-idempotent charge."""
        ledger(ledger_path, add=f"charge:{amount}")
        return f"charged {amount}"

    client = ReplayChatCompletionClient(
        script,
        model_info=ModelInfo(vision=False, function_calling=True,
                             json_output=False, family="unknown",
                             structured_output=False))
    return AssistantAgent("worker", model_client=client, tools=[charge],
                          reflect_on_tool_use=False)

def tool_call_script():
    call = CreateResult(
        finish_reason="function_calls",
        content=[FunctionCall(id="c1", name="charge", arguments=json.dumps({"amount": 10}))],
        usage={"prompt_tokens": 0, "completion_tokens": 0},
        cached=False,
    )
    return [call, "done", "acknowledged", "acknowledged-2"]

async def cell_all():
    d = tempfile.mkdtemp(prefix="probe140_")
    lp = f"{d}/ledger.sqlite"
    ledger(lp)

    a1 = make_agent(lp, tool_call_script())
    r1 = await a1.run(task="charge the customer 10")
    after_run1 = ledger(lp)
    state = await a1.save_state()

    a2 = make_agent(lp, ["acknowledged"])
    await a2.load_state(state)
    await a2.run(task="thanks, summarize")
    after_restore_followup = ledger(lp)

    a3 = make_agent(lp, ["acknowledged"])
    await a3.load_state(state)
    await a3.run(task="thanks, summarize")
    after_double_restore = ledger(lp)

    bad = json.loads(json.dumps(state))
    tamper_note = None
    try:
        msgs = bad.get("llm_context", {}).get("messages")
        if isinstance(msgs, list) and msgs:
            msgs[0]["type"] = "NoSuchMessageType"
            tamper_note = "llm_context.messages[0].type -> NoSuchMessageType"
        else:
            for k in list(bad.keys()):
                if isinstance(bad[k], dict):
                    bad[k]["type"] = "NoSuchType"
                    tamper_note = f"{k}.type -> NoSuchType"
                    break
    except Exception as e:
        tamper_note = f"tamper-construction failed: {e!r}"
    a4 = make_agent(lp, ["acknowledged"])
    try:
        await a4.load_state(bad)
        cv = {"loud_rejection": False, "error": None}
        try:
            await a4.run(task="continue")
            cv["silent_acceptance_and_run"] = True
        except Exception as e:
            cv["silent_acceptance_and_run"] = False
            cv["late_error"] = repr(e)[:160]
    except Exception as e:
        cv = {"loud_rejection": True, "error": repr(e)[:160]}

    return {
        "autogen_agentchat_version": version("autogen-agentchat"),
        "documented_plane": "AssistantAgent.save_state/load_state",
        "ledger_after_run1": after_run1,
        "final_text_run1": str(r1.messages[-1].content)[:80],
        "ledger_after_restore_followup": after_restore_followup,
        "restore_EO_completed_tool_not_refired":
            after_restore_followup == after_run1,
        "ledger_after_double_restore": after_double_restore,
        "double_restore_added_effects":
            len(after_double_restore) - len(after_restore_followup),
        "tamper": tamper_note,
        "cv_on_tampered_state": cv,
    }

def main():
    print(json.dumps(asyncio.run(cell_all()), indent=2))

if __name__ == "__main__":
    main()
