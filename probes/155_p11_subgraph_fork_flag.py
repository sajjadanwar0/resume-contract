#!/usr/bin/env python3
"""
155_p11_subgraph_fork_flag.py  (E-F1)  v3 -- pin-guarded; Part B REQUIRED (both backends) unless --part-a-only.
Evaluates the FI deployment discriminator where the address heuristic
cannot go: subgraph-internal checkpoint_id plumbing.

Part A (self-contained): the probe-134 ADDRESS-BASED demonstrator filter
(strip __resume__ pending writes when the caller config carries an explicit
checkpoint_id) armed on a parent graph containing a CHECKPOINTED SUBGRAPH
with an interrupt. Measures whether internal plumbing carries explicit
checkpoint_ids into get_tuple and spuriously strips the legitimate resume
(the failure mode the paper predicts the flag exists to avoid).
Part B (gated on `import remit`): the packaged shim with
fork_on_explicit_checkpoint=False + an explicit flag key. Three cells:
  B1 subgraph interrupt->resume must be STOCK-IDENTICAL (differential vs A)
  B2 caller-flagged fork at the parent gate must still repair #6663
  B3 stray ordinary-address resume stays inert (CO preserved)
Oracles: per-node effect counters + on-disk ledger (probe-126 pattern).
Backends: InMemorySaver + SqliteSaver; two hosts.
"""
import json, os, sqlite3, sys, tempfile, traceback
from typing import TypedDict
from importlib.metadata import version

REQUIRED_LANGGRAPH = "1.2.9"
_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    print(json.dumps({"probe_refused": f"langgraph=={_lg}, pin is {REQUIRED_LANGGRAPH}",
                      "interpreter": sys.executable}), file=sys.stderr)
    sys.exit(3)
PART_A_ONLY = "--part-a-only" in sys.argv
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.base import CheckpointTuple

RESULTS = {"langgraph_version": _lg, "pins_ok": _lg == REQUIRED_LANGGRAPH,
           "interpreter": sys.executable}

class AddressFilterSaver(InMemorySaver):
    """Probe-134 demonstrator rule, transplanted. VERIFY against your
    committed 134 implementation before trusting Part A verdicts."""
    def __init__(self):
        super().__init__(); self.strips = 0
    def get_tuple(self, config):
        t = super().get_tuple(config)
        if t is None: return t
        if config.get("configurable", {}).get("checkpoint_id"):  # explicit address
            pw = [w for w in (t.pending_writes or []) if w[1] != "__resume__"]
            if len(pw) != len(t.pending_writes or []):
                self.strips += 1
            t = CheckpointTuple(t.config, t.checkpoint, t.metadata,
                                t.parent_config, pw)  # VERIFY field order/name
        return t

class Sub(TypedDict): x: str
class Par(TypedDict): x: str

def build(saver_cls):
    eff = {"sub_post": 0}
    def sub_gate(state: Sub):
        ans = interrupt("sub decision?")
        eff["sub_post"] += 1
        return {"x": str(ans)}
    sg = StateGraph(Sub); sg.add_node("gate", sub_gate)
    sg.add_edge(START, "gate"); sg.add_edge("gate", END)
    sub = sg.compile(checkpointer=True)  # checkpointed subgraph: exercises internal checkpoint_id plumbing
    pg = StateGraph(Par); pg.add_node("sub", sub)
    pg.add_edge(START, "sub"); pg.add_edge("sub", END)
    saver = saver_cls()
    return pg.compile(checkpointer=saver), saver, eff

def part_a():
    app, saver, eff = build(AddressFilterSaver)
    cfg = {"configurable": {"thread_id": "a"}}
    r1 = app.invoke({"x": ""}, cfg)
    out = {"interrupt_surfaced": "__interrupt__" in r1}
    try:
        app.invoke(Command(resume=True), cfg)
        out["resume_completed"] = True
    except Exception as e:
        out["resume_completed"] = False
        out["resume_error"] = type(e).__name__
    out["sub_effect"] = eff["sub_post"]
    out["spurious_strips_during_normal_resume"] = saver.strips
    out["finding_address_heuristic_breaks_subgraph_resume"] = (
            saver.strips > 0 and (not out["resume_completed"] or eff["sub_post"] != 1))
    return out

def part_b_sqlite():
    """part_b's three cells over wrap(SqliteSaver, conn): the durable backend."""
    from remit import langgraph_shim as _rls
    out = {}
    def mk(**kw):
        db = tempfile.mktemp(suffix="_155.sqlite")
        conn = sqlite3.connect(db, check_same_thread=False)
        return _rls.wrap(SqliteSaver, conn, **kw), conn, db
    # B1 differential
    def run_sub(saver):
        eff = {"sub_post": 0}
        def sub_gate(state: Sub):
            ans = interrupt("sub decision?")
            eff["sub_post"] += 1
            return {"x": str(ans)}
        sg = StateGraph(Sub); sg.add_node("gate", sub_gate)
        sg.add_edge(START, "gate"); sg.add_edge("gate", END)
        pg = StateGraph(Par); pg.add_node("sub", sg.compile(checkpointer=True))
        pg.add_edge(START, "sub"); pg.add_edge("sub", END)
        app = pg.compile(checkpointer=saver)
        cfg = {"configurable": {"thread_id": "b1"}}
        r1 = app.invoke({"x": ""}, cfg)
        ok, err = True, None
        try:
            app.invoke(Command(resume=True), cfg)
        except Exception as e:
            ok, err = False, type(e).__name__
        return {"interrupt": "__interrupt__" in r1, "resume_ok": ok,
                "resume_error": err, "sub_effect": eff["sub_post"]}
    sdb = tempfile.mktemp(suffix="_155s.sqlite")
    sconn = sqlite3.connect(sdb, check_same_thread=False)
    out["B1_stock"] = run_sub(SqliteSaver(sconn)); sconn.close()
    shim, conn1, _ = mk(fork_on_explicit_checkpoint=False)
    out["B1_shim"] = run_sub(shim); conn1.close()
    out["B1_stock_identical"] = (out["B1_stock"] == out["B1_shim"])
    # B2 + B3 on one wrapped saver
    eff = {"post": 0}
    def gate(state: Par):
        ans = interrupt("decision?")
        eff["post"] += 1
        return {"x": str(ans)}
    g = StateGraph(Par); g.add_node("gate", gate)
    g.add_edge(START, "gate"); g.add_edge("gate", END)
    shim2, conn2, _ = mk(fork_on_explicit_checkpoint=False)
    app = g.compile(checkpointer=shim2)
    cfg = {"configurable": {"thread_id": "b2"}}
    app.invoke({"x": ""}, cfg)
    ckpt = app.get_state(cfg).config["configurable"]["checkpoint_id"]
    ra = app.invoke(Command(resume="A"), cfg)
    rb = app.invoke(Command(resume="B"),
                    {"configurable": {"thread_id": "b2", "checkpoint_id": ckpt,
                                      "remit_fork": True}})
    out["B2_first_resume_value"] = ra.get("x")
    out["B2_forked_resume_value"] = rb.get("x")
    out["B2_fork_honors_supplied_value"] = (rb.get("x") == "B")
    before = eff["post"]
    b3 = None
    try:
        app.invoke(Command(resume="C"), cfg)
    except Exception as e:
        b3 = type(e).__name__
    out["B3_disposition"] = b3 or "silent"
    out["B3_effect_refired"] = eff["post"] > before + 1
    conn2.close()
    return out


RESULTS["PartA_address_demonstrator_under_subgraph"] = None
try:
    RESULTS["PartA_address_demonstrator_under_subgraph"] = part_a()
except Exception:
    RESULTS["PartA_address_demonstrator_under_subgraph"] = {
        "probe_error": traceback.format_exc(limit=6)}

def part_b():
    from remit import langgraph_shim as _rls
    out = {}
    # -- B1: subgraph resume must be stock-identical under the flag config --
    def run_sub(saver_factory):
        eff = {"sub_post": 0}
        def sub_gate(state: Sub):
            ans = interrupt("sub decision?")
            eff["sub_post"] += 1
            return {"x": str(ans)}
        sg = StateGraph(Sub); sg.add_node("gate", sub_gate)
        sg.add_edge(START, "gate"); sg.add_edge("gate", END)
        pg = StateGraph(Par); pg.add_node("sub", sg.compile(checkpointer=True))
        pg.add_edge(START, "sub"); pg.add_edge("sub", END)
        app = pg.compile(checkpointer=saver_factory())
        cfg = {"configurable": {"thread_id": "b1"}}
        r1 = app.invoke({"x": ""}, cfg)
        ok, err = True, None
        try:
            app.invoke(Command(resume=True), cfg)
        except Exception as e:
            ok, err = False, type(e).__name__
        return {"interrupt": "__interrupt__" in r1, "resume_ok": ok,
                "resume_error": err, "sub_effect": eff["sub_post"]}
    stock = run_sub(lambda: InMemorySaver())
    shim = run_sub(lambda: _rls.wrap(InMemorySaver,
                                     fork_on_explicit_checkpoint=False))
    out["B1_stock"] = stock
    out["B1_shim"] = shim
    out["B1_stock_identical"] = (stock == shim)

    # -- B2: caller-flagged fork at a parent gate still repairs #6663 --------
    eff = {"post": 0}
    def gate(state: Par):
        ans = interrupt("decision?")
        eff["post"] += 1
        return {"x": str(ans)}
    g = StateGraph(Par); g.add_node("gate", gate)
    g.add_edge(START, "gate"); g.add_edge("gate", END)
    app = g.compile(checkpointer=_rls.wrap(
        InMemorySaver, fork_on_explicit_checkpoint=False))
    cfg = {"configurable": {"thread_id": "b2"}}
    app.invoke({"x": ""}, cfg)
    snap = app.get_state(cfg)
    ckpt = snap.config["configurable"]["checkpoint_id"]
    ra = app.invoke(Command(resume="A"), cfg)
    fork_cfg = {"configurable": {"thread_id": "b2",
                                 "checkpoint_id": ckpt,
                                 "remit_fork": True}}
    rb = app.invoke(Command(resume="B"), fork_cfg)
    out["B2_first_resume_value"] = ra.get("x")
    out["B2_forked_resume_value"] = rb.get("x")
    out["B2_fork_honors_supplied_value"] = (rb.get("x") == "B")
    out["B2_effects_total"] = eff["post"]

    # -- B3: stray ordinary-address resume stays inert (CO) ------------------
    b3_err, before = None, eff["post"]
    try:
        app.invoke(Command(resume="C"), cfg)
    except Exception as e:
        b3_err = type(e).__name__
    out["B3_disposition"] = b3_err or "silent"
    out["B3_effect_refired"] = eff["post"] > before + 1
    return out

if PART_A_ONLY:
    RESULTS["PartB"] = "not run: --part-a-only"
else:
    try:
        from remit import langgraph_shim  # noqa: F401  (import check up front)
    except Exception as e:
        print(json.dumps({"probe_refused":
                              f"remit shim not importable ({type(e).__name__}: {e}). Paper runs "
                              "need Part B: build the wheel into THIS venv "
                              "(cd remit-contract && maturin develop --release), or pass "
                              "--part-a-only explicitly.",
                          "interpreter": sys.executable}), file=sys.stderr)
        sys.exit(3)
    try:
        RESULTS["PartB_inmemory"] = part_b()
        RESULTS["PartB_sqlite"] = part_b_sqlite()
    except Exception:
        RESULTS["PartB_error"] = {"probe_error": traceback.format_exc(limit=8)}

print(json.dumps(RESULTS, indent=2, default=str))