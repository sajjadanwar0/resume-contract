import json
import threading
import traceback
from importlib.metadata import version
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.func import entrypoint, task

RESULTS = {"langgraph_version": version("langgraph")}

class OrderRecordingSaver(InMemorySaver):
    """Records the interleaving of put vs put_writes as submitted."""
    def __init__(self):
        super().__init__()
        self.order = []
        self._lock = threading.Lock()

    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            self.order.append("put")
        return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        with self._lock:
            self.order.append(("put_writes", [w[0] for w in writes]))
        return super().put_writes(config, writes, task_id, task_path)

def run_and_record(durability):
    eff = {"s1": 0, "s2": 0}
    saver = OrderRecordingSaver()

    @task
    def s1(x: int) -> int:
        eff["s1"] += 1
        return x + 1

    @task
    def s2(x: int) -> int:
        eff["s2"] += 1
        return x + 10

    @entrypoint(checkpointer=saver)
    def wf(x: int) -> int:
        return s2(s1(x).result()).result()

    cfg = {"configurable": {"thread_id": f"t-{durability}"}}
    out = wf.invoke(1, cfg, durability=durability)
    return {"durability": durability, "result": out,
            "submission_order": saver.order,
            "s1_execs": eff["s1"], "s2_execs": eff["s2"]}

try:
    for mode in ["sync", "async", "exit"]:
        try:
            RESULTS[f"observed_{mode}"] = run_and_record(mode)
        except Exception as e:
            RESULTS[f"observed_{mode}"] = {"error": f"{type(e).__name__}: {e}"}

    sync = RESULTS.get("observed_sync", {})
    order = sync.get("submission_order", [])
    RESULTS["sync_order_summary"] = order
    RESULTS["finding_puts_and_writes_both_present"] = (
        any(o == "put" for o in order)
        and any(isinstance(o, list) or (isinstance(o, tuple) and o[0] == "put_writes")
                for o in order)
    )
    RESULTS["note"] = (
        "RD at executor layer requires forcing thread-pool submission order; "
        "InMemorySaver serializes puts, so the #8039 race is only observable "
        "with the SQLite/Postgres savers' real thread pool. This probe records "
        "the submission SEQUENCE as evidence the operations are adjacent and "
        "unbarriered; the forced-order replay is scheduled for the persistent "
        "savers where the pool is real."
    )
except Exception:
    RESULTS["probe_error"] = traceback.format_exc(limit=6)

print(json.dumps(RESULTS, indent=2, default=str))
