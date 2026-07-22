#!/usr/bin/env python3
"""
150_p11_langgraph_cv_downstream.py
CV downstream-consequence probe (E-L2): bounds what "silent" means for the
#6491-class validity violation on LangGraph 1.2.9.

Section 6.1 of the paper establishes the violation itself: a node output that
breaks the pydantic schema (None appended to items: List[str]) is persisted
with no error at invoke time and no error at history-read time, on all three
backends. Reviewers asked the follow-on question this probe answers
deterministically: WHAT HAPPENS NEXT. Four protocols, all LLM-free,
timing-free, exception-crash-free (no crash is needed -- the hazard is the
write itself):

  P1  same-run downstream consumer (InMemorySaver):
      graph = bad -> consume, consume computes sum(len(s) for s in items).
      Measures: where the failure finally surfaces (exception type; whether
      the raising frame is USER code), how many checkpoints already contain
      the corrupt value at that moment (displacement distance), and that the
      corrupting write itself signaled nothing.

  P2  read-back typing (InMemorySaver, bad-only graph):
      get_state / get_state_history succeed?; runtime type of the corrupt
      element; and pydantic's own verdict on the returned values
      (S.model_validate raises?) -- i.e., the framework hands back state its
      own declared schema rejects.

  P3  propagation past a tolerant reader (InMemorySaver):
      graph = bad -> tolerant -> effect, where tolerant reads defensively
      and effect appends "done" (list-replace update). Measures whether the
      corrupt value is copied forward into checkpoints WRITTEN AFTER later
      nodes ran -- corruption as a fixed point, not a transient.

  P4  fresh-process deserialization (SqliteSaver):
      child interpreter 1 performs the corrupting run against a SQLite db
      and exits; child interpreter 2 opens the db cold and repeats P2's
      read-back + model_validate. This is the boundary the original #6491
      report broke at (history read after persistence); the probe records
      whether the 1.2.9 silence survives a full process + deser boundary.

Verdict fields are stable-view booleans/strings; counters are process-local
dicts per the suite convention. Postgres replication of P4 follows the
probe-130 pattern and is deferred to that campaign.

Environment: pinned per Sec. 5.4 -- langgraph 1.2.9,
langgraph-checkpoint 4.1.1, langgraph-checkpoint-sqlite 3.1.0.
"""
import json
import os
import subprocess
import sys
import tempfile
import traceback
from importlib.metadata import version
from typing import List

from pydantic import BaseModel, ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver

RESULTS = {
    "langgraph_version": version("langgraph"),
    "langgraph_checkpoint_version": version("langgraph-checkpoint"),
    "langgraph_checkpoint_sqlite_version": version("langgraph-checkpoint-sqlite"),
    "python": sys.version.split()[0],
}

THIS_FILE = os.path.abspath(__file__)


class S(BaseModel):
    items: List[str] = []


def bad_node(state: S) -> S:
    state.items.append(None)  # schema violation, identical to probe 113 T5
    return state


def history_none_profile(app, cfg):
    """App-level history scan; on failure RECORD the error (that is data)."""
    try:
        snaps = list(app.get_state_history(cfg))  # newest first
    except Exception as e:
        return {"history_read_error": f"{type(e).__name__}",
                "history_read_error_detail": str(e)[:200]}
    snaps.reverse()
    per_ckpt = []
    for snap in snaps:
        vals = snap.values
        items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
        per_ckpt.append(bool(items) and None in items)
    return {
        "n_checkpoints": len(per_ckpt),
        "none_per_checkpoint_oldest_first": per_ckpt,
        "first_index_with_none": per_ckpt.index(True) if True in per_ckpt else None,
        "n_checkpoints_containing_none": sum(per_ckpt),
    }


def raw_saver_profile(saver, cfg):
    """Saver-level scan, bypassing _prepare_state_snapshot: what is ON DISK
    even when the app-level readers refuse to construct a snapshot."""
    try:
        tuples = list(saver.list(cfg))
    except Exception as e:
        return {"raw_list_error": f"{type(e).__name__}: {e}"}
    per = []
    for t in tuples:
        cv = (t.checkpoint or {}).get("channel_values", {})
        items = cv.get("items")
        per.append(bool(items) and None in items)
    per.reverse()  # oldest first
    return {"n_checkpoints_raw": len(per),
            "none_per_checkpoint_raw_oldest_first": per,
            "n_raw_checkpoints_containing_none": sum(per)}


# ------------------------------------------------------------------ P1
def p1_same_run_consumer():
    eff = {"consume_entered": 0, "consume_completed": 0}

    def consume(state: S):
        eff["consume_entered"] += 1
        total = sum(len(s) for s in state.items)  # natural crash on None
        eff["consume_completed"] += 1
        return {"items": state.items + [f"len={total}"]}

    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_node("consume", consume)
    g.add_edge(START, "bad")
    g.add_edge("bad", "consume")
    g.add_edge("consume", END)
    saver = InMemorySaver()
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "p1"}}

    err_type, err_in_user_frame, err_at_input_mapping = None, None, None
    try:
        app.invoke(S(items=["ok"]), cfg)
    except Exception as e:
        err_type = type(e).__name__
        tb = traceback.format_exc()
        err_in_user_frame = (THIS_FILE in tb or "150_p11" in tb) and eff["consume_entered"] > 0
        err_at_input_mapping = "_proc_input" in tb or "proc.mapper" in tb

    gs_err = None
    try:
        app.get_state(cfg)
    except Exception as e:
        gs_err = type(e).__name__

    prof = history_none_profile(app, cfg)
    raw = raw_saver_profile(saver, cfg)
    return {
        "corrupting_write_raised": False,  # invoke reached the next superstep
        "invoke_error_type": err_type,
        "consume_entered": eff["consume_entered"],
        "consume_completed": eff["consume_completed"],
        "failure_raised_in_user_consumer_frame": err_in_user_frame,
        "failure_raised_at_framework_input_mapping": err_at_input_mapping,
        "get_state_error_after_failure": gs_err,
        "history_profile": prof,
        "raw_saver_profile": raw,
        "finding_failure_deferred_one_superstep_framework_frame": bool(
            err_type and not eff["consume_entered"] and err_at_input_mapping),
        "finding_corrupt_record_durable_before_failure": (
                raw.get("n_raw_checkpoints_containing_none", 0) >= 1),
        "finding_6491_unreadable_thread_resurfaces_with_pending_task": bool(
            prof.get("history_read_error")),
    }


# ------------------------------------------------------------------ P2
def p2_readback_typing():
    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "p2"}}

    invoke_err = None
    try:
        app.invoke(S(items=["ok"]), cfg)
    except Exception as e:
        invoke_err = type(e).__name__

    out = {"invoke_error": invoke_err}
    try:
        snap = app.get_state(cfg)
        vals = snap.values
        items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
        out["get_state_succeeds"] = True
        out["returned_items"] = items
        out["corrupt_element_runtime_type"] = type(items[-1]).__name__ if items else None
        try:
            S.model_validate(vals if isinstance(vals, dict) else vals)
            out["schema_model_validate_raises"] = False
        except ValidationError:
            out["schema_model_validate_raises"] = True
    except Exception as e:
        out["get_state_succeeds"] = False
        out["get_state_error"] = f"{type(e).__name__}: {e}"
    out["history_profile"] = history_none_profile(app, cfg)
    out["finding_framework_returns_state_its_own_schema_rejects"] = (
            out.get("get_state_succeeds") and out.get("schema_model_validate_raises") is True
    )
    return out


# ------------------------------------------------------------------ P3
def p3_second_run_on_corrupt_thread():
    """On the TERMINAL-silent path (bad-only graph), start a second run on the
    same thread with fresh input: does the corrupt channel value poison the
    new run, or does full-channel overwrite escape it? Either way, measure."""
    g = StateGraph(S)
    g.add_node("bad", bad_node)
    g.add_edge(START, "bad")
    g.add_edge("bad", END)
    saver = InMemorySaver()
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "p3"}}

    first_err = None
    try:
        app.invoke(S(items=["ok"]), cfg)
    except Exception as e:
        first_err = type(e).__name__

    # Second run uses a DIFFERENT graph (no corrupting node) on the SAME
    # saver + thread, so any None in its output is carry-over, not re-minting.
    def clean_node(state: S):
        return {"items": state.items + ["clean-done"]}

    g2 = StateGraph(S)
    g2.add_node("clean", clean_node)
    g2.add_edge(START, "clean")
    g2.add_edge("clean", END)
    app2 = g2.compile(checkpointer=saver)

    second_err, second_items = None, None
    try:
        app2.invoke(S(items=["fresh"]), cfg)
        vals = app2.get_state(cfg).values
        second_items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
    except Exception as e:
        second_err = type(e).__name__

    raw = raw_saver_profile(saver, cfg)
    return {
        "first_run_error": first_err,
        "second_run_error": second_err,
        "second_run_final_items": second_items,
        "raw_saver_profile": raw,
        "finding_corrupt_history_retained_alongside_new_run": (
                raw.get("n_raw_checkpoints_containing_none", 0) >= 1),
        "finding_second_run_escapes_corruption_via_input_overwrite": (
                second_err is None and second_items is not None
                and None not in second_items),
        "finding_corruption_carries_into_second_run": (
                second_items is not None and None in second_items),
    }


# ------------------------------------------------------------------ P4
CHILD_WRITE = r"""
import json, sys, sqlite3
from typing import List
from pydantic import BaseModel
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

class S(BaseModel):
    items: List[str] = []

def bad_node(state: S) -> S:
    state.items.append(None)
    return state

db = sys.argv[1]
conn = sqlite3.connect(db, check_same_thread=False)
saver = SqliteSaver(conn)
g = StateGraph(S)
g.add_node("bad", bad_node)
g.add_edge(START, "bad")
g.add_edge("bad", END)
app = g.compile(checkpointer=saver)
cfg = {"configurable": {"thread_id": "p4"}}
err = None
try:
    app.invoke(S(items=["ok"]), cfg)
except Exception as e:
    err = type(e).__name__
conn.commit(); conn.close()
print(json.dumps({"writer_invoke_error": err}))
"""

CHILD_READ = r"""
import json, sys, sqlite3
from typing import List
from pydantic import BaseModel, ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

class S(BaseModel):
    items: List[str] = []

def bad_node(state: S) -> S:
    state.items.append(None)
    return state

db = sys.argv[1]
conn = sqlite3.connect(db, check_same_thread=False)
saver = SqliteSaver(conn)
g = StateGraph(S)
g.add_node("bad", bad_node)
g.add_edge(START, "bad")
g.add_edge("bad", END)
app = g.compile(checkpointer=saver)
cfg = {"configurable": {"thread_id": "p4"}}
out = {}
try:
    snap = app.get_state(cfg)
    vals = snap.values
    items = vals.get("items") if isinstance(vals, dict) else getattr(vals, "items", None)
    out["fresh_process_get_state_succeeds"] = True
    out["items"] = items
    out["corrupt_element_runtime_type"] = type(items[-1]).__name__ if items else None
    try:
        S.model_validate(vals)
        out["schema_model_validate_raises"] = False
    except ValidationError:
        out["schema_model_validate_raises"] = True
except Exception as e:
    out["fresh_process_get_state_succeeds"] = False
    out["error"] = f"{type(e).__name__}: {e}"
try:
    hist = list(app.get_state_history(cfg))
    out["fresh_process_history_read_succeeds"] = True
    out["n_checkpoints"] = len(hist)
    out["none_in_any_checkpoint"] = any(
        (v.values.get("items") if isinstance(v.values, dict)
         else getattr(v.values, "items", None)) and
        None in (v.values.get("items") if isinstance(v.values, dict)
                 else getattr(v.values, "items"))
        for v in hist)
except Exception as e:
    out["fresh_process_history_read_succeeds"] = False
    out["history_error"] = f"{type(e).__name__}: {e}"
print(json.dumps(out))
"""


def p4_fresh_process_deser():
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "p4_ckpt.sqlite")
        w = subprocess.run([sys.executable, "-c", CHILD_WRITE, db],
                           capture_output=True, text=True, timeout=120)
        r = subprocess.run([sys.executable, "-c", CHILD_READ, db],
                           capture_output=True, text=True, timeout=120)
    out = {"writer_rc": w.returncode, "reader_rc": r.returncode}
    try:
        out.update(json.loads(w.stdout.strip().splitlines()[-1]))
    except Exception:
        out["writer_raw"] = (w.stdout + w.stderr)[-800:]
    try:
        out.update(json.loads(r.stdout.strip().splitlines()[-1]))
    except Exception:
        out["reader_raw"] = (r.stdout + r.stderr)[-800:]
    out["finding_silence_survives_process_and_deser_boundary"] = bool(
        out.get("fresh_process_get_state_succeeds")
        and out.get("fresh_process_history_read_succeeds")
        and out.get("none_in_any_checkpoint")
    )
    return out


PROTOCOLS = {
    "P1_same_run_downstream_consumer": p1_same_run_consumer,
    "P2_readback_typing_and_model_validate": p2_readback_typing,
    "P3_second_run_on_corrupt_thread": p3_second_run_on_corrupt_thread,
    "P4_fresh_process_deserialization_sqlite": p4_fresh_process_deser,
}

for name, fn in PROTOCOLS.items():
    try:
        RESULTS[name] = fn()
    except Exception:
        RESULTS[name] = {"probe_error": traceback.format_exc(limit=8)}

print(json.dumps(RESULTS, indent=2, default=str))