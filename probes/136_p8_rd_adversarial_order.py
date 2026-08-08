#!/usr/bin/env python3
import json
import sqlite3
import tempfile
from importlib.metadata import version

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.func import entrypoint, task

def ledger(path, add=None):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    if add is not None:
        c.execute("INSERT INTO effects (task) VALUES (?)", (add,))
    c.commit()
    rows = [r[0] for r in c.execute("SELECT task FROM effects ORDER BY n")]
    c.close()
    return rows

class DeferredWritesSaver(SqliteSaver):
    """Adversarial-but-legal scheduler realized at the saver interface:
    put_writes calls are buffered and only flushed when the NEXT put
    arrives -- the durable order a losing unbarriered submission produces.
    A crash while a buffer is pending leaves the divergent durable state:
    checkpoint advanced, task-result writes absent."""

    def __init__(self, conn):
        super().__init__(conn)
        self._pending = []

    def put_writes(self, config, writes, task_id, task_path=""):
        self._pending.append((config, writes, task_id, task_path))

    def put(self, config, checkpoint, metadata, new_versions):
        out = super().put(config, checkpoint, metadata, new_versions)
        for args in self._pending:
            super().put_writes(*args)
        self._pending = []
        return out

class Crash(Exception):
    pass

def run_cell(tag, adversarial):
    d = tempfile.mkdtemp(prefix=f"probe136_{tag}_")
    ckpt, led = f"{d}/ckpt.sqlite", f"{d}/ledger.sqlite"
    ledger(led)
    conn = sqlite3.connect(ckpt, check_same_thread=False)
    saver = (DeferredWritesSaver if adversarial else SqliteSaver)(conn)
    crashed = {"v": False}

    @task
    def s1(x: int) -> int:
        ledger(led, add="s1")
        return x + 1

    @task
    def s2(x: int) -> int:
        if not crashed["v"]:
            crashed["v"] = True
            raise Crash("injected in the unbarriered window")
        ledger(led, add="s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()

    cfg = {"configurable": {"thread_id": tag}}
    try:
        wf.invoke(1, cfg, durability="sync")
    except Exception:
        pass

    c = sqlite3.connect(ckpt)
    writes_at_crash = c.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    ckpts_at_crash = c.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    c.close()
    s1_at_crash = ledger(led).count("s1")

    result = wf.invoke(1, cfg, durability="sync")
    rows = ledger(led)
    return {
        "adversarial_order": adversarial,
        "writes_rows_at_crash": writes_at_crash,
        "checkpoint_rows_at_crash": ckpts_at_crash,
        "s1_effects_at_crash": s1_at_crash,
        "resume_result": result,
        "s1_effects_total": rows.count("s1"),
        "s2_effects_total": rows.count("s2"),
        "ledger": rows,
    }

def main():
    a = run_cell("normal", adversarial=False)
    b = run_cell("advers", adversarial=True)
    out = {
        "langgraph_version": version("langgraph"),
        "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
        "cell_A_normal_order": a,
        "cell_B_adversarial_order": b,
        "durable_orders_differ_at_crash":
            a["writes_rows_at_crash"] != b["writes_rows_at_crash"],
        "rd_holds_recovery_agrees":
            a["resume_result"] == b["resume_result"]
            and a["s1_effects_total"] == b["s1_effects_total"]
            and a["s2_effects_total"] == b["s2_effects_total"],
    }
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
