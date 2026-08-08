#!/usr/bin/env python3
import json
import os
import sqlite3
import tempfile
from importlib.metadata import version
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

RESUME_CHANNEL = "__resume__"

class ForkIntentSqliteSaver(SqliteSaver):
    """Read-path shim implementing the contract's FI obligation: a subclass
    of the stock saver overriding ONLY get_tuple.

    Fork intent discriminator: an explicit checkpoint_id in the caller's
    config (the branch-creating time-travel address). On that address,
    recorded resume writes are invisible to task preparation, so the
    invocation's own resume value is served. Ordinary-address calls are
    byte-identical to the stock saver. Scope: single-graph demonstrator;
    a production version would key on an explicit fork flag to cover
    subgraph-internal checkpoint_id plumbing.
    """

    def get_tuple(self, config):
        t = super().get_tuple(config)
        if t is None:
            return t
        if config.get("configurable", {}).get("checkpoint_id") and t.pending_writes:
            filtered = [w for w in t.pending_writes if w[1] != RESUME_CHANNEL]
            t = t._replace(pending_writes=filtered)
        return t

def ledger(path, add=None):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, v INTEGER)")
    if add is not None:
        c.execute("INSERT INTO effects (v) VALUES (?)", (add,))
    c.commit()
    rows = [r[0] for r in c.execute("SELECT v FROM effects ORDER BY n")]
    c.close()
    return rows

class S(TypedDict):
    value: int

def build(saver, ledger_path):
    def node(state: S):
        allow = interrupt("Allow to add?")
        v = state["value"] + (1 if allow else 0)
        ledger(ledger_path, add=1 if allow else 0)
        return {"value": v}

    return (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=saver)
    )

def fresh(tag, shim):
    d = tempfile.mkdtemp(prefix=f"probe134_{tag}_")
    conn = sqlite3.connect(f"{d}/ckpt.sqlite", check_same_thread=False)
    saver = (ForkIntentSqliteSaver if shim else SqliteSaver)(conn)
    lp = f"{d}/ledger.sqlite"
    return build(saver, lp), lp

def fork_cell(tag, shim, use_map, second_value):
    app, lp = fresh(tag, shim)
    cfg = {"configurable": {"thread_id": tag}}
    r0 = app.invoke({"value": 0}, cfg)
    intr = r0.get("__interrupt__")
    intr_id = getattr(intr[0], "id", None) if intr else None
    ckpt = app.get_state(cfg).config["configurable"]["checkpoint_id"]
    fork_cfg = {"configurable": {"thread_id": tag, "checkpoint_id": ckpt}}

    def mk(v):
        return Command(resume={intr_id: v}) if use_map else Command(resume=v)

    r1 = app.invoke(mk(True), fork_cfg)
    r2 = app.invoke(mk(second_value), fork_cfg)
    return {
        "shim": shim, "form": "map" if use_map else "bare",
        "branch1_value": r1.get("value"), "branch2_value": r2.get("value"),
        "expected_branch2": 1 if second_value else 0,
        "fd_holds": r2.get("value") == (1 if second_value else 0)
        and r1.get("value") == 1,
        "ledger": ledger(lp),
    }

def co_cell():
    app, lp = fresh("co", shim=True)
    cfg = {"configurable": {"thread_id": "co"}}
    app.invoke({"value": 0}, cfg)
    app.invoke(Command(resume=True), cfg)
    r = app.invoke(Command(resume=False), cfg)
    return {
        "final_value_after_stray": r.get("value"),
        "ledger": ledger(lp),
        "co_holds_stray_inert": r.get("value") == 1 and ledger(lp) == [1],
    }

def main():
    out = {
        "langgraph_version": version("langgraph"),
        "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
        "t0_control_no_shim_bare": fork_cell("t0", shim=False, use_map=False, second_value=False),
        "t1_shim_bare_fork": fork_cell("t1", shim=True, use_map=False, second_value=False),
        "t1b_shim_map_fork": fork_cell("t1b", shim=True, use_map=True, second_value=False),
        "t2_shim_same_value_refork": fork_cell("t2", shim=True, use_map=False, second_value=True),
        "t3_shim_co_stray": co_cell(),
    }
    out["repair_summary"] = {
        "control_violates": not out["t0_control_no_shim_bare"]["fd_holds"],
        "fd_repaired_bare": out["t1_shim_bare_fork"]["fd_holds"],
        "fd_repaired_map": out["t1b_shim_map_fork"]["fd_holds"],
        "same_value_deterministic": out["t2_shim_same_value_refork"]["fd_holds"],
        "co_preserved": out["t3_shim_co_stray"]["co_holds_stray_inert"],
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
