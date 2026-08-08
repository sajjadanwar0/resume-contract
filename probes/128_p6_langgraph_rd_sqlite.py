#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import traceback
from importlib.metadata import version

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.func import entrypoint, task

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
}
WORKDIR = tempfile.mkdtemp(prefix="probe128_")

class DroppingSqliteSaver(SqliteSaver):
    """SqliteSaver that can drop designated persistence ops, constructing the
    durable state a crash between unordered ops would leave behind."""

    def __init__(self, conn):
        super().__init__(conn)
        self.log = []
        self.drop_puts_after = None
        self.drop_writes_after = None
        self._put_count = 0
        self._writes_count = 0

    def put(self, config, checkpoint, metadata, new_versions):
        self._put_count += 1
        self.log.append(("put", self._put_count))
        if self.drop_puts_after is not None and self._put_count > self.drop_puts_after:
            return config
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        self._writes_count += 1
        self.log.append(("put_writes", self._writes_count, [w[0] for w in writes]))
        if (
                self.drop_writes_after is not None
                and self._writes_count > self.drop_writes_after
        ):
            return
        return super().put_writes(config, writes, task_id, task_path)

class Crash(RuntimeError):
    pass

def build(saver, eff, crash_in_s2):
    @task
    def s1(x: int) -> int:
        eff["s1"] += 1
        return x + 1

    @task
    def s2(x: int) -> int:
        if crash_in_s2["armed"]:
            crash_in_s2["armed"] = False
            raise Crash("simulated process death")
        eff["s2"] += 1
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        a = s1(x).result()
        b = s2(a).result()
        return b

    return wf

def scenario(order: str):
    eff = {"s1": 0, "s2": 0}
    conn = sqlite3.connect(
        os.path.join(WORKDIR, f"rd_{order}.sqlite"), check_same_thread=False
    )
    saver = DroppingSqliteSaver(conn)
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": f"t-{order}"}}

    if order == "W":
        saver.drop_puts_after = 1
    elif order == "C":
        saver.drop_writes_after = 0

    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__

    s1_at_crash = eff["s1"]
    persistence_log = list(saver.log)

    result = resume_error = None
    try:
        result = wf.invoke(1, cfg, durability="sync")
    except Exception as e:
        resume_error = f"{type(e).__name__}: {e}"

    return {
        "order": order,
        "crashed_as": crashed,
        "s1_execs_at_crash": s1_at_crash,
        "s1_execs_after_resume": eff["s1"],
        "s2_execs_after_resume": eff["s2"],
        "result": result,
        "resume_error": resume_error,
        "persistence_log": persistence_log,
    }

def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def scenario_same_log(order: str):
    """Reflexive RD: construct one crashed durable file, recover twice from
    byte-identical copies of it."""
    db = os.path.join(WORKDIR, f"refl_{order}.sqlite")
    conn = sqlite3.connect(db, check_same_thread=False)
    saver = DroppingSqliteSaver(conn)
    eff = {"s1": 0, "s2": 0}
    crash = {"armed": True}
    wf = build(saver, eff, crash)
    cfg = {"configurable": {"thread_id": f"t-{order}"}}

    if order == "W":
        saver.drop_puts_after = 1
    elif order == "C":
        saver.drop_writes_after = 0

    crashed = None
    try:
        wf.invoke(1, cfg, durability="sync")
    except Crash:
        crashed = "Crash"
    except Exception as e:
        crashed = type(e).__name__

    construction = {
        "crashed_as": crashed,
        "s1_execs_at_crash": eff["s1"],
        "persistence_log": list(saver.log),
    }

    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    conn.commit()
    conn.close()
    for side in ("-wal", "-shm"):
        try:
            os.remove(db + side)
        except FileNotFoundError:
            pass

    src_sha = _sha256(db)
    decisions = []
    copy_shas = []
    for k in (0, 1):
        db_k = os.path.join(WORKDIR, f"refl_{order}_copy{k}.sqlite")
        shutil.copyfile(db, db_k)
        copy_shas.append(_sha256(db_k))
        conn_k = sqlite3.connect(db_k, check_same_thread=False)
        saver_k = SqliteSaver(conn_k)
        eff_k = {"s1": 0, "s2": 0}
        crash_k = {"armed": False}
        wf_k = build(saver_k, eff_k, crash_k)
        result = resume_error = None
        try:
            result = wf_k.invoke(1, cfg, durability="sync")
        except Exception as e:
            resume_error = f"{type(e).__name__}: {e}"
        conn_k.close()
        decisions.append(
            {
                "s1_execs": eff_k["s1"],
                "s2_execs": eff_k["s2"],
                "result": result,
                "resume_error": resume_error,
            }
        )

    return {
        "order": order,
        "construction": construction,
        "durable_log_sha256": src_sha,
        "copies_byte_identical": copy_shas[0] == copy_shas[1] == src_sha,
        "decisions": decisions,
        "identical": decisions[0] == decisions[1],
    }

def main():
    for order in ("N", "W", "C"):
        try:
            RESULTS[f"order_{order}"] = scenario(order)
        except Exception:
            RESULTS[f"order_{order}"] = {"probe_error": traceback.format_exc()}
    try:
        w = RESULTS["order_W"]["s1_execs_after_resume"]
        c = RESULTS["order_C"]["s1_execs_after_resume"]
        RESULTS["verdict"] = {
            "backend": "SqliteSaver",
            "recovery_identical_across_orders": w == c,
            "violation_rd": w != c,
            "note": "RD at checkpointer-API granularity on the durable saver; "
                    "the executor pool itself is not raced.",
        }
    except Exception:
        RESULTS["verdict"] = {"probe_error": traceback.format_exc()}

    try:
        refl = {order: scenario_same_log(order) for order in ("N", "W", "C")}
        RESULTS["rd_reflexive_same_log"] = refl
        RESULTS["rd_reflexive_same_log_identical_all"] = all(
            r["identical"] and r["copies_byte_identical"] for r in refl.values()
        )
    except Exception:
        RESULTS["rd_reflexive_same_log"] = {"probe_error": traceback.format_exc()}
    print(json.dumps(RESULTS, indent=2, default=str))

if __name__ == "__main__":
    main()
