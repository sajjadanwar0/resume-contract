#!/usr/bin/env python3
import argparse
from typing import TypedDict
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_LANGGRAPH = "1.2.9"

PREDICTIONS = {
    "P-A_three_resumes_abc":        {"served": ["a", "a", "a"]},
    "P-B_fork_after_stray":         {"served": ["a", None, "a"],
                                     "stray_effect_delta": 0},
    "P-C_interleaved_bare_and_map": {"served": ["a", "a", "a"]},
    "P-D_two_checkpoints_independent": {"served": ["a", "b", "a", "b"]},
}

def _fail(msg):
    print(json.dumps({"probe_refused": msg}), file=sys.stderr)
    sys.exit(3)

def _pins(backend="memory"):
    pins = {}
    for pkg in ("langgraph", "langgraph-checkpoint",
                "langgraph-checkpoint-sqlite"):
        try:
            pins[pkg] = version(pkg)
        except Exception:
            pins[pkg] = None
    if pins.get("langgraph") != REQUIRED_LANGGRAPH:
        _fail(f"langgraph {pins.get('langgraph')} != pinned {REQUIRED_LANGGRAPH}")
    if backend == "sqlite":
        try:
            import langgraph.checkpoint.sqlite
        except ModuleNotFoundError:
            _fail("--backend sqlite needs langgraph-checkpoint-sqlite, which "
                  "envs/langgraph does not carry. Run under the durable env: "
                  "uv run --project envs/langgraph-durable python "
                  "probes/171_p16_lgf_outofsample.py --backend sqlite")
    return pins

def open_saver(backend, db):
    if backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver
        return InMemorySaver(), None
    from langgraph.checkpoint.sqlite import SqliteSaver
    cm = SqliteSaver.from_conn_string(db)
    return cm.__enter__(), cm

def build(saver, effects, n_gates=1):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt

    class S(TypedDict, total=False):
        seen: list

    def make(idx):
        def gate(state: S) -> S:
            v = interrupt({"which": idx})
            effects.append((idx, v))
            return {"seen": (state.get("seen") or []) + [v]}
        return gate

    g = StateGraph(S)
    prev = START
    for i in range(n_gates):
        g.add_node(f"gate{i}", make(i))
        g.add_edge(prev, f"gate{i}")
        prev = f"gate{i}"
    g.add_edge(prev, END)
    return g.compile(checkpointer=saver)

def interrupt_ckpt(app, cfg):
    """checkpoint_id of the tuple carrying the pending interrupt."""
    t = app.get_state(cfg)
    return t.config["configurable"]["checkpoint_id"]

def run_protocol(name, backend, tmp):
    from langgraph.types import Command
    db = os.path.join(tmp, f"{name}.sqlite")
    saver, cm = open_saver(backend, db)
    effects, served = [], []
    extra = {}
    try:
        thread = f"t171_{name}"
        cfg = {"configurable": {"thread_id": thread}}
        n_gates = 2 if name.startswith("P-D") else 1
        app = build(saver, effects, n_gates)
        app.invoke({}, cfg)
        ck = interrupt_ckpt(app, cfg)
        at = lambda c: {"configurable": {"thread_id": thread,
                                         "checkpoint_id": c}}

        if name.startswith("P-A"):
            for v in ("a", "b", "c"):
                app.invoke(Command(resume=v), at(ck))
                served.append(effects[-1][1] if effects else None)

        elif name.startswith("P-B"):
            app.invoke(Command(resume="a"), at(ck))
            served.append(effects[-1][1])
            before = len(effects)
            app.invoke(Command(resume="stray"), cfg)
            served.append(effects[-1][1] if len(effects) > before else None)
            extra["stray_effect_delta"] = len(effects) - before
            app.invoke(Command(resume="b"), at(ck))
            served.append(effects[-1][1])

        elif name.startswith("P-C"):
            app.invoke(Command(resume="a"), at(ck))
            served.append(effects[-1][1])
            app.invoke(Command(resume={ck: "b"}), at(ck))
            served.append(effects[-1][1])
            app.invoke(Command(resume="c"), at(ck))
            served.append(effects[-1][1])

        elif name.startswith("P-D"):
            app.invoke(Command(resume="a"), at(ck))
            served.append(effects[-1][1])
            ck2 = interrupt_ckpt(app, cfg)
            app.invoke(Command(resume="b"), at(ck2))
            served.append(effects[-1][1])
            app.invoke(Command(resume="x"), at(ck))
            served.append(effects[-1][1])
            app.invoke(Command(resume="y"), at(ck2))
            served.append(effects[-1][1])
    except Exception as e:
        return {"protocol": name, "error": f"{type(e).__name__}: {e}",
                "served": served, **extra}
    finally:
        if cm:
            cm.__exit__(None, None, None)
    return {"protocol": name, "served": served, "error": None, **extra}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="memory",
                    choices=["memory", "sqlite"])
    ap.add_argument("--out", default="results/matrix")
    a = ap.parse_args()
    pins = _pins(a.backend)

    tmp = tempfile.mkdtemp(prefix="p171_")
    results, verdicts = {}, {}
    for name, pred in PREDICTIONS.items():
        obs = run_protocol(name, a.backend, tmp)
        results[name] = obs
        if obs.get("error"):
            verdicts[name] = "ERROR"
        else:
            ok = all(obs.get(k) == v for k, v in pred.items())
            verdicts[name] = "CONFIRMED" if ok else "MISPREDICTED"
        print(f"  {name:34s} {verdicts[name]:13s} "
              f"predicted {pred}  observed "
              f"{ {k: obs.get(k) for k in pred} }")

    mis = [k for k, v in verdicts.items() if v == "MISPREDICTED"]
    err = [k for k, v in verdicts.items() if v == "ERROR"]
    stable = {
        "predictions_registered_in_source": list(PREDICTIONS),
        "confirmed": [k for k, v in verdicts.items() if v == "CONFIRMED"],
        "mispredicted": mis,
        "errored": err,
        "model_out_of_sample_confirmed": (not mis and not err),
        "negative_control_PD_confirmed":
            verdicts.get("P-D_two_checkpoints_independent") == "CONFIRMED",
    }
    out = {"probe": "171_p16_lgf_outofsample",
           "host": os.uname().nodename,
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "backend": a.backend, "pins": pins,
           "predictions": PREDICTIONS, "stable": stable, "results": results}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, "171_results.json").write_text(json.dumps(out, indent=1))
    Path(a.out, "171_stable.json").write_text(json.dumps(
        {k: out[k] for k in ("probe", "host", "utc", "backend", "pins",
                             "stable")}, indent=1))
    print(f"\n{json.dumps(stable, indent=1)}")

    if not stable["negative_control_PD_confirmed"]:
        print("\nP-D is the negative control. With it unconfirmed, the other "
              "three carry no weight: a degenerate model that always serves "
              "the first value would satisfy them too.", file=sys.stderr)
    if mis:
        print(f"\nMODEL DEFECT: {mis}. LangGraphFork.tla mispredicts a "
              "protocol it was not fitted to. Report this in Sec. 4.3; do "
              "not re-anchor the prediction.", file=sys.stderr)
        return 4
    return 0

if __name__ == "__main__":
    sys.exit(main())
