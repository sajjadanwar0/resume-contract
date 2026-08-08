#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from importlib.metadata import version
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

RESUME_CHANNEL = "__resume__"

class ForkIntentSqliteSaver(SqliteSaver):
    def get_tuple(self, config):
        t = super().get_tuple(config)
        if t is None:
            return t
        if config.get("configurable", {}).get("checkpoint_id") and t.pending_writes:
            t = t._replace(pending_writes=[w for w in t.pending_writes
                                           if w[1] != RESUME_CHANNEL])
        return t

def ledger(path, add=None):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, gate TEXT, val INTEGER)")
    if add is not None:
        c.execute("INSERT INTO effects (gate, val) VALUES (?, ?)", add)
    c.commit()
    rows = [(r[0], r[1]) for r in c.execute("SELECT gate, val FROM effects ORDER BY n")]
    c.close()
    return rows

class S(TypedDict):
    v1: int
    v2: int

def build(ckpt_path, ledger_path, shim=False):
    conn = sqlite3.connect(ckpt_path, check_same_thread=False)
    saver = (ForkIntentSqliteSaver if shim else SqliteSaver)(conn)

    def g1(state: S):
        a = interrupt("approve step 1?")
        val = 1 if a else 0
        ledger(ledger_path, add=("e1", val))
        return {"v1": val}

    def g2(state: S):
        b = interrupt("approve step 2?")
        val = 1 if b else 0
        ledger(ledger_path, add=("e2", val))
        return {"v2": val}

    return (StateGraph(S)
            .add_node("g1", g1).add_node("g2", g2)
            .add_edge(START, "g1").add_edge("g1", "g2").add_edge("g2", END)
            .compile(checkpointer=saver))

def run_thread(tag, shim_for_fork):
    d = tempfile.mkdtemp(prefix=f"probe138_{tag}_")
    ckpt, led = f"{d}/ckpt.sqlite", f"{d}/ledger.sqlite"
    ledger(led)
    cfg = {"configurable": {"thread_id": tag}}

    app1 = build(ckpt, led)
    app1.invoke({"v1": -1, "v2": -1}, cfg)
    app1.invoke(Command(resume=True), cfg)
    ckpt_g2 = app1.get_state(cfg).config["configurable"]["checkpoint_id"]
    e1_before_restart = [r for r in ledger(led) if r[0] == "e1"]

    app2 = build(ckpt, led, shim=shim_for_fork)
    done = app2.invoke(Command(resume=True), cfg)
    rows = ledger(led)
    e1_total = [r for r in rows if r[0] == "e1"]
    e2_total = [r for r in rows if r[0] == "e2"]

    stray = app2.invoke(Command(resume=False), cfg)
    rows_after_stray = ledger(led)

    fork_cfg = {"configurable": {"thread_id": tag, "checkpoint_id": ckpt_g2}}
    forked = app2.invoke(Command(resume=False), fork_cfg)
    rows_after_fork = ledger(led)
    e2_after_fork = [r for r in rows_after_fork if r[0] == "e2"]

    return {
        "shim": shim_for_fork,
        "e1_before_restart": e1_before_restart,
        "completed_state": {"v1": done.get("v1"), "v2": done.get("v2")},
        "e1_total_after_completion": e1_total,
        "e2_total_after_completion": e2_total,
        "eo_across_restart_between_gates": len(e1_total) == 1,
        "stray_state": {"v1": stray.get("v1"), "v2": stray.get("v2")},
        "co_stray_inert_after_completion":
            stray.get("v2") == done.get("v2")
            and len(rows_after_stray) == len(rows),
        "fork_state": {"v1": forked.get("v1"), "v2": forked.get("v2")},
        "e2_after_fork": e2_after_fork,
        "fd_gate2_second_decision_honored":
            forked.get("v2") == 0 and e2_after_fork[-1] == ("e2", 0),
    }

def main():
    control = run_thread("ctrl", shim_for_fork=False)
    repaired = run_thread("shim", shim_for_fork=True)
    out = {
        "langgraph_version": version("langgraph"),
        "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
        "control": control,
        "with_fork_intent_shim": repaired,
        "summary": {
            "eo_across_restart_both_cells":
                control["eo_across_restart_between_gates"]
                and repaired["eo_across_restart_between_gates"],
            "co_stray_inert_both_cells":
                control["co_stray_inert_after_completion"]
                and repaired["co_stray_inert_after_completion"],
            "fd_gate2_control_violates":
                not control["fd_gate2_second_decision_honored"],
            "fd_gate2_shim_repairs":
                repaired["fd_gate2_second_decision_honored"],
        },
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
