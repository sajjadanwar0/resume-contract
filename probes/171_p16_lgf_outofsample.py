#!/usr/bin/env python3
"""
171_p16_lgf_outofsample.py  (campaign p16)

The cheapest available upgrade to LangGraphFork.tla's grounding.

Sec. 4.3 concedes the mapping from source to model is "expert-established,
not tool-certified." Probe 143 partially answers this by transliterating
the serve/record/filter/inertness rules and reproducing the measured
branch outcome of every recorded invocation, 8/8. But those eight
protocols are the protocols the model was BUILT from. Reproducing them is
consistency with the training set, not validation: a model fitted to
eight observations that reproduces those eight observations has been
checked for arithmetic, not for content.

This probe closes that gap without a refinement proof. Four protocols the
model has never seen are chosen, the model's prediction for each is
REGISTERED IN THIS FILE BEFORE THE RUN (below, as PREDICTIONS), and then
the protocol is measured. A prediction that fails is a defect in the
model and must be reported as one -- the model would then be an
abstraction that fits the observed cases and mispredicts elsewhere, which
is materially weaker than Sec. 4.3 currently claims.

THE MODEL. LangGraphFork.tla's serve rule, at specification granularity:
a resume value delivered to (thread, checkpoint) is recorded as a pending
write; an invocation addressed to a checkpoint already carrying a
recorded resume replays the recorded write rather than recording a new
one. The binding site is task preparation, and the precedence is: the
task's own previously recorded resume first; the null-task resume only
via get_null_resume when the task has none; resume-map values appended
after the recorded list.

THE PREDICTIONS. Each follows from that rule alone. None was derived by
running the protocol first.

  P-A  three_resumes_abc
       Three invocations to one interrupt checkpoint carrying a, b, c.
       The first records; the second and third replay the record.
       PREDICT served == [a, a, a].
       The paper only ever measured TWO invocations. If the third
       diverges -- if, say, the record is consumed and the third
       invocation records afresh -- the serve rule is wrong about
       record persistence.

  P-B  fork_after_stray
       Invoke with a, then a post-completion stray, then invoke with b
       at the same checkpoint. The stray is inert (it addresses a
       completed run, not the interrupt) and does not disturb the
       record, so the third invocation still replays a.
       PREDICT served == [a, None, a], stray_effect_delta == 0.
       This tests that inertness and the serve rule compose, which no
       measured protocol exercises.

  P-C  interleaved_bare_and_map
       Invoke bare with a, then via the resume-map form with b, then
       bare with c. The paper measures each FORM separately and finds
       the violation identical; it never interleaves them. Under the
       precedence rule the recorded value wins regardless of the form
       the later invocation uses.
       PREDICT served == [a, a, a].

  P-D  two_checkpoints_independent
       Two interrupts on distinct checkpoints, resumed a then b, then
       re-invoked at each. Records are per-checkpoint, so neither
       replays the other's value.
       PREDICT served == [a, b, a, b].
       This is the negative control: a model that simply returned the
       first value ever seen would also predict [a,a,a] for P-A and P-C
       and would FAIL here. Without P-D the other three are consistent
       with a degenerate model, so P-D is what gives them content.

FALSIFICATION. Any single mismatch is a model defect. The correct
response is to report it in Sec. 4.3, not to re-anchor the prediction
after the fact; this file is committed before the run precisely so that
the registration is auditable in git history.

Usage:
  .venv/bin/python3 probes/171_p16_lgf_outofsample.py
  .venv/bin/python3 probes/171_p16_lgf_outofsample.py --backend sqlite
"""
import argparse
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
    # Refuse before the first protocol rather than tracebacking inside it:
    # envs/langgraph carries no SQLite saver, envs/langgraph-durable does,
    # and a half-run sweep is worse than no run.
    if backend == "sqlite":
        try:
            import langgraph.checkpoint.sqlite  # noqa: F401
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
    from typing import TypedDict

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
            app.invoke(Command(resume="stray"), cfg)      # completed run
            served.append(effects[-1][1] if len(effects) > before else None)
            extra["stray_effect_delta"] = len(effects) - before
            app.invoke(Command(resume="b"), at(ck))
            served.append(effects[-1][1])

        elif name.startswith("P-C"):
            app.invoke(Command(resume="a"), at(ck))
            served.append(effects[-1][1])
            app.invoke(Command(resume={ck: "b"}), at(ck))  # map form
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
    except Exception as e:                                  # noqa: BLE001
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