#!/usr/bin/env python3
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version

PORT = 8139

def oracle_main(db_path):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("CREATE TABLE IF NOT EXISTS charges (n INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT)")
    conn.commit()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            ln = int(self.headers.get("Content-Length", 0))
            tag = self.rfile.read(ln).decode() or "effect"
            conn.execute("INSERT INTO charges (tag) VALUES (?)", (tag,))
            conn.commit()
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def do_GET(self):
            n = conn.execute("SELECT COUNT(*) FROM charges").fetchone()[0]
            self.send_response(200); self.end_headers()
            self.wfile.write(str(n).encode())

    HTTPServer(("127.0.0.1", PORT), H).serve_forever()

def post_effect(tag):
    urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{PORT}/effect",
                               data=tag.encode(), method="POST"), timeout=10).read()

def get_count():
    return int(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/count",
                                      timeout=10).read())

def build_wf(d):
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.func import entrypoint, task
    conn = sqlite3.connect(d["ckpt"], check_same_thread=False)
    saver = SqliteSaver(conn)

    @task
    def s1(x: int) -> int:
        post_effect("s1")
        return x + 1

    @task
    def s2(x: int) -> int:
        if not os.path.exists(d["crashed"]):
            open(d["ready"], "w").write("parked")
            time.sleep(600)
        post_effect("s2")
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()
    return wf

def parent_main():
    d0 = tempfile.mkdtemp(prefix="probe142_")
    d = {"ckpt": f"{d0}/ckpt.sqlite", "oracle_db": f"{d0}/oracle.sqlite",
         "ready": f"{d0}/ready.flag", "crashed": f"{d0}/crashed.flag"}
    env = dict(os.environ, PROBE142_DIR=json.dumps(d))
    oracle = subprocess.Popen([sys.executable, os.path.abspath(__file__), "oracle"],
                              env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            get_count(); break
        except Exception:
            time.sleep(0.05)
    child = subprocess.Popen([sys.executable, os.path.abspath(__file__), "child"],
                             env=env, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
    deadline = time.time() + 120
    while not os.path.exists(d["ready"]):
        if time.time() > deadline or child.poll() is not None:
            child.kill(); oracle.kill()
            raise SystemExit(f"victim never parked (exit={child.returncode})")
        time.sleep(0.05)
    count_at_kill = get_count()
    os.kill(child.pid, signal.SIGKILL); child.wait()
    open(d["crashed"], "w").write("1")
    res = subprocess.run([sys.executable, os.path.abspath(__file__), "resume"],
                         env=env, capture_output=True, text=True, timeout=180)
    resume_result = None
    for line in reversed(res.stdout.strip().splitlines()):
        try:
            resume_result = json.loads(line)["resume_result"]; break
        except Exception:
            continue
    total = get_count()
    oracle.kill(); oracle.wait()
    oc = sqlite3.connect(d["oracle_db"])
    tags = [r[0] for r in oc.execute("SELECT tag FROM charges ORDER BY n")]
    oc.close()
    print(json.dumps({
        "langgraph_version": version("langgraph"),
        "oracle": "separate OS process, own SQLite, HTTP interface",
        "victim_exit": "SIGKILL" if child.returncode == -signal.SIGKILL else str(child.returncode),
        "oracle_count_at_kill": count_at_kill,
        "resume_result": resume_result,
        "oracle_count_total": total,
        "oracle_tags": tags,
        "violation_confirmed_by_independent_state_holder":
            tags.count("s1") == 2 and count_at_kill == 1,
    }, indent=2))

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode == "parent":
        parent_main()
    elif mode == "oracle":
        oracle_main(json.loads(os.environ["PROBE142_DIR"])["oracle_db"])
    else:
        d = json.loads(os.environ["PROBE142_DIR"])
        wf = build_wf(d)
        r = wf.invoke(1, {"configurable": {"thread_id": "xo"}}, durability="sync")
        print(json.dumps({"resume_result": r}))
