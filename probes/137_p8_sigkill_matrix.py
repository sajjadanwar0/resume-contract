#!/usr/bin/env python3
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version

REPS = 3

def ledger(path, add=None):
    c = sqlite3.connect(path, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS effects (n INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT)")
    if add is not None:
        c.execute("INSERT INTO effects (task) VALUES (?)", (add,))
    c.commit()
    rows = [r[0] for r in c.execute("SELECT task FROM effects ORDER BY n")]
    c.close()
    return rows

def park(ready_flag):
    open(ready_flag, "w").write("parked")
    time.sleep(600)

def build(d, for_resume):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.func import entrypoint, task

    conn = sqlite3.connect(d["ckpt"], check_same_thread=False)

    class SeamSaver(SqliteSaver):
        """Kill point B: park at the put that follows the first real
        task-result put_writes -- inside the persistence seam."""
        def __init__(self, c):
            super().__init__(c)
            self._armed = False

        def put_writes(self, config, writes, task_id, task_path=""):
            out = super().put_writes(config, writes, task_id, task_path)
            self._armed = True
            return out

        def put(self, config, checkpoint, metadata, new_versions):
            if self._armed and not for_resume:
                park(d["ready"])
            return super().put(config, checkpoint, metadata, new_versions)

    point = d["point"]
    saver = SeamSaver(conn) if (point == "B" and not for_resume) else SqliteSaver(conn)

    @task
    def s1(x: int) -> int:
        ledger(d["ledger"], add="s1")
        if point == "C" and not for_resume and not os.path.exists(d["crashed"]):
            park(d["ready"])
        return x + 1

    @task
    def s2(x: int) -> int:
        if point == "A" and not for_resume and not os.path.exists(d["crashed"]):
            park(d["ready"])
        ledger(d["ledger"], add="s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()

    return wf

def db_shape(ckpt):
    c = sqlite3.connect(ckpt)
    w = c.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
    k = c.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    c.close()
    return {"writes_rows": w, "checkpoint_rows": k}

def one_rep(point):
    d0 = tempfile.mkdtemp(prefix=f"probe137_{point}_")
    d = {"point": point, "ckpt": f"{d0}/ckpt.sqlite",
         "ledger": f"{d0}/ledger.sqlite", "ready": f"{d0}/ready.flag",
         "crashed": f"{d0}/crashed.flag"}
    ledger(d["ledger"])
    env = dict(os.environ, PROBE137_DIR=json.dumps(d))
    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "child"],
                             env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    deadline = time.time() + 120
    while not os.path.exists(d["ready"]):
        if time.time() > deadline or child.poll() is not None:
            child.kill()
            return {"error": f"victim never parked (exit={child.returncode})"}
        time.sleep(0.05)
    shape_at_kill = db_shape(d["ckpt"])
    s1_at_kill = ledger(d["ledger"]).count("s1")
    os.kill(child.pid, signal.SIGKILL)
    child.wait()
    open(d["crashed"], "w").write("1")
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "resume"],
                       env=env, capture_output=True, text=True, timeout=180)
    resume_result = None
    for line in reversed(r.stdout.strip().splitlines()):
        try:
            resume_result = json.loads(line)["resume_result"]
            break
        except Exception:
            continue
    rows = ledger(d["ledger"])
    return {
        "victim_exit": "SIGKILL" if child.returncode == -signal.SIGKILL else str(child.returncode),
        "db_at_kill": shape_at_kill,
        "s1_effects_at_kill": s1_at_kill,
        "resume_result": resume_result,
        "s1_effects_total": rows.count("s1"),
        "s2_effects_total": rows.count("s2"),
    }

def main():
    out = {"langgraph_version": version("langgraph"),
           "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
           "reps_per_point": REPS, "points": {}}
    for point in ("A", "B", "C"):
        reps = [one_rep(point) for _ in range(REPS)]
        stable = all(r == reps[0] for r in reps[1:]) and "error" not in reps[0]
        out["points"][point] = {"reps": reps, "stable_across_reps": stable}
    out["all_points_stable"] = all(v["stable_across_reps"] for v in out["points"].values())
    out["completed_durable_task_reexecutes_at_A"] = (
            out["points"]["A"]["reps"][0].get("s1_effects_total") == 2)
    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        main()
    else:
        d = json.loads(os.environ["PROBE137_DIR"])
        wf = build(d, for_resume=(mode == "resume"))
        res = wf.invoke(1, {"configurable": {"thread_id": "km"}}, durability="sync")
        print(json.dumps({"resume_result": res}))
