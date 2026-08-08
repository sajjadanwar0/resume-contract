import json
import os
import sqlite3
import tempfile
import traceback
from typing import TypedDict, List
from importlib.metadata import version
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.func import entrypoint, task
from pydantic import BaseModel

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_version": version("langgraph-checkpoint"),
    "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
}

WORKDIR = tempfile.mkdtemp(prefix="probe126_")

class DurableLedger:
    """On-disk effect ledger, independent of the checkpointer DB and of
    process memory. Survives everything short of filesystem loss; the
    process-local counter is the first oracle, this is the second."""

    def __init__(self, path):
        self.path = path
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE IF NOT EXISTS effects (name TEXT)")
        con.commit()
        con.close()

    def fire(self, name):
        con = sqlite3.connect(self.path)
        con.execute("INSERT INTO effects (name) VALUES (?)", (name,))
        con.commit()
        con.close()

    def count(self, name):
        con = sqlite3.connect(self.path)
        n = con.execute(
            "SELECT COUNT(*) FROM effects WHERE name=?", (name,)
        ).fetchone()[0]
        con.close()
        return n

def fresh_saver(tag):
    db = os.path.join(WORKDIR, f"ckpt_{tag}.sqlite")
    conn = sqlite3.connect(db, check_same_thread=False)
    return SqliteSaver(conn), db

def get_interrupt_checkpoint_id(app, config):
    snap = app.get_state(config)
    return snap.config["configurable"]["checkpoint_id"], snap

def t1_fork_sqlite():
    ledger = DurableLedger(os.path.join(WORKDIR, "ledger_t1.sqlite"))

    class S(TypedDict):
        value: int

    def node(state: S):
        allow = interrupt("Allow to add?")
        ledger.fire("branch_decided")
        if allow:
            return {"value": state["value"] + 1}
        return {"value": state["value"]}

    saver, db = fresh_saver("t1")
    app = (
        StateGraph(S)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile(checkpointer=saver)
    )
    cfg = {"configurable": {"thread_id": "t1"}}
    app.invoke({"value": 0}, cfg)
    ckpt_id, _ = get_interrupt_checkpoint_id(app, cfg)
    fork_cfg = {"configurable": {"thread_id": "t1", "checkpoint_id": ckpt_id}}

    r_true = app.invoke(Command(resume=True), fork_cfg)
    r_false = app.invoke(Command(resume=False), fork_cfg)

    violation = not (r_true.get("value") == 1 and r_false.get("value") == 0)
    return {
        "backend": "SqliteSaver",
        "resume_true_value": r_true.get("value"),
        "resume_false_value": r_false.get("value"),
        "violation_second_resume_ignored": violation,
        "ledger_branch_decisions": ledger.count("branch_decided"),
    }

def t2_cv_sqlite():
    class S(BaseModel):
        items: List[str] = []

    def bad_node(state: S):
        return {"items": state.items + [None]}

    saver, db = fresh_saver("t2")
    app = (
        StateGraph(S)
        .add_node("bad", bad_node)
        .add_edge(START, "bad")
        .add_edge("bad", END)
        .compile(checkpointer=saver)
    )
    cfg = {"configurable": {"thread_id": "t2"}}
    invoke_error = history_error = None
    persisted_invalid = False
    n_records_with_invalid = 0
    try:
        app.invoke(S(), cfg)
    except Exception as e:
        invoke_error = f"{type(e).__name__}: {e}"
    try:
        for snap in app.get_state_history(cfg):
            vals = snap.values
            items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
            if items and None in items:
                persisted_invalid = True
                n_records_with_invalid += 1
    except Exception as e:
        history_error = f"{type(e).__name__}: {e}"

    con = sqlite3.connect(db)
    raw_rows = con.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    con.close()

    return {
        "backend": "SqliteSaver",
        "invoke_error": invoke_error,
        "history_read_error": history_error,
        "schema_invalid_value_persisted": persisted_invalid,
        "checkpoint_records_containing_invalid": n_records_with_invalid,
        "raw_checkpoint_rows_in_db": raw_rows,
        "violation_silent_invalid_persistence": (
            persisted_invalid and invoke_error is None and history_error is None
        ),
    }

def t3_co_sqlite():
    ledger = DurableLedger(os.path.join(WORKDIR, "ledger_t3.sqlite"))
    eff = {"post": 0}

    class S(TypedDict):
        val: str

    def node(state: S):
        ans = interrupt("q")
        eff["post"] += 1
        ledger.fire("post")
        return {"val": str(ans)}

    saver, db = fresh_saver("t3")
    app = (
        StateGraph(S)
        .add_node("n", node)
        .add_edge(START, "n")
        .add_edge("n", END)
        .compile(checkpointer=saver)
    )
    cfg = {"configurable": {"thread_id": "t3"}}
    app.invoke({"val": ""}, cfg)
    app.invoke(Command(resume="yes"), cfg)
    stray_error = None
    try:
        app.invoke(Command(resume="stray"), cfg)
    except Exception as e:
        stray_error = f"{type(e).__name__}: {e}"
    agree = eff["post"] == ledger.count("post")
    return {
        "backend": "SqliteSaver",
        "post_effect_counter": eff["post"],
        "post_effect_ledger": ledger.count("post"),
        "oracles_agree": agree,
        "stray_resume_error": stray_error,
        "violation_stray_resume_refired_effect": eff["post"] != 1,
    }

class Crash(RuntimeError):
    pass

def t4_eo_crash_sqlite():
    ledger = DurableLedger(os.path.join(WORKDIR, "ledger_t4.sqlite"))
    eff = {"s1": 0, "s2": 0}
    crash = {"armed": True}
    saver, db = fresh_saver("t4")

    @task
    def s1(x: int) -> int:
        eff["s1"] += 1
        ledger.fire("s1")
        return x + 1

    @task
    def s2(x: int) -> int:
        if crash["armed"]:
            crash["armed"] = False
            raise Crash("simulated process death")
        eff["s2"] += 1
        ledger.fire("s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        a = s1(x).result()
        b = s2(a).result()
        return b

    cfg = {"configurable": {"thread_id": "t4"}}
    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__
    s1_at_crash = eff["s1"]
    con = sqlite3.connect(db)
    writes_rows = con.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    con.close()
    result = resume_error = None

    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"
    agree = (eff["s1"] == ledger.count("s1")) and (eff["s2"] == ledger.count("s2"))

    return {
        "backend": "SqliteSaver",
        "crashed_as": crashed,
        "s1_execs_at_crash": s1_at_crash,
        "durable_writes_rows_at_crash": writes_rows,
        "s1_execs_after_resume": eff["s1"],
        "s1_ledger_after_resume": ledger.count("s1"),
        "s2_execs_after_resume": eff["s2"],
        "oracles_agree": agree,
        "result": result,
        "resume_error": resume_error,
        "violation_completed_task_reexecuted_on_crash_resume": eff["s1"] > 1,
    }

def main():
    for name, fn in [
        ("t1_fd_fork_sqlite", t1_fork_sqlite),
        ("t2_cv_invalid_persist_sqlite", t2_cv_sqlite),
        ("t3_co_stray_resume_sqlite", t3_co_sqlite),
        ("t4_eo_crash_split_sqlite", t4_eo_crash_sqlite),
    ]:
        try:
            RESULTS[name] = fn()
        except Exception:
            RESULTS[name] = {"probe_error": traceback.format_exc()}
    print(json.dumps(RESULTS, indent=2, default=str))

if __name__ == "__main__":
    main()
