#!/usr/bin/env python3
import argparse
from typing import TypedDict
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_LANGGRAPH = "1.2.9"
LEDGER_SCHEMA = "CREATE TABLE IF NOT EXISTS fx (id INTEGER PRIMARY KEY, tag TEXT, ts REAL)"

def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)

def _pins():
    pins = {}
    for pkg in ("langgraph", "langgraph-checkpoint",
                "langgraph-checkpoint-sqlite", "langgraph-checkpoint-postgres"):
        try:
            pins[pkg] = version(pkg)
        except Exception:
            pins[pkg] = None
    if pins.get("langgraph") != REQUIRED_LANGGRAPH:
        _fail(f"langgraph {pins.get('langgraph')} != pinned {REQUIRED_LANGGRAPH}")
    return pins

def ledger_append(ledger_path, tag):
    """The effect IS the ledger append: one autocommitted INSERT, durable
    before the node returns.  Sec. 5.1's ordering argument, and probe 163's
    mechanical check of it, carry over unchanged."""
    con = sqlite3.connect(ledger_path, timeout=30)
    con.execute(LEDGER_SCHEMA)
    con.execute("INSERT INTO fx (tag, ts) VALUES (?, ?)", (tag, time.time()))
    con.commit()
    con.close()

def ledger_counts(ledger_path):
    con = sqlite3.connect(ledger_path, timeout=30)
    con.execute(LEDGER_SCHEMA)
    rows = con.execute("SELECT tag, COUNT(*) FROM fx GROUP BY tag").fetchall()
    con.close()
    return {t: c for t, c in rows}

def build_graph(saver, ledger_path, gate_ms=0):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt

    class S(TypedDict, total=False):
        x: int
        decision: bool

    def pre(state: S) -> S:
        ledger_append(ledger_path, "pre")
        return {"x": 1}

    def gate(state: S) -> S:
        d = interrupt({"approve?": True})
        if gate_ms:
            time.sleep(gate_ms / 1000.0)
        ledger_append(ledger_path, f"gate:{d}")
        return {"decision": d}

    g = StateGraph(S)
    g.add_node("pre", pre)
    g.add_node("gate", gate)
    g.add_edge(START, "pre")
    g.add_edge("pre", "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=saver)

def open_saver(backend, db, dsn, setup=False):
    """Open a saver. `setup` runs the Postgres schema migration and is done
    ONCE by the park child. Racers must not call it: k concurrent
    CREATE/ALTER statements can raise "tuple concurrently updated", which
    would surface as racer errors indistinguishable from the race under
    study. The schema exists before any racer starts -- the same discipline
    probe 159 uses."""
    if backend == "sqlite":
        from langgraph.checkpoint.sqlite import SqliteSaver
        cm = SqliteSaver.from_conn_string(db)
        return cm.__enter__(), cm
    from langgraph.checkpoint.postgres import PostgresSaver
    cm = PostgresSaver.from_conn_string(dsn)
    saver = cm.__enter__()
    if setup:
        saver.setup()
    return saver, cm

def child_park(backend, db, dsn, ledger, thread, gate_ms=0):
    saver, cm = open_saver(backend, db, dsn, setup=True)
    try:
        app = build_graph(saver, ledger, gate_ms)
        cfg = {"configurable": {"thread_id": thread}}
        out = app.invoke({}, cfg)
        print(json.dumps({"interrupted": "__interrupt__" in out}))
    finally:
        cm.__exit__(None, None, None)

def child_racer(backend, db, dsn, ledger, thread, barrier, jitter_ms, seed,
                ready=None, gate_ms=0):
    """Announce readiness, spin on the barrier file, then optionally jitter,
    then resume.  The jitter is applied AFTER the barrier so every racer
    starts from the same instant and the offset is the only difference
    between arms.

    The readiness file matters: an earlier revision slept a fixed 0.35 s
    before dropping the barrier, which at k=2 occasionally released the
    barrier before the second racer reached the spin loop -- producing a
    single fire and a spurious P3 gate failure.  The parent now waits for
    every racer to announce."""
    from langgraph.types import Command
    saver, cm = open_saver(backend, db, dsn, setup=False)
    err = None
    try:
        app = build_graph(saver, ledger, gate_ms)
        cfg = {"configurable": {"thread_id": thread}}
        if ready:
            Path(ready).touch()
        while not Path(barrier).exists():
            time.sleep(0.0005)
        if jitter_ms:
            random.seed(seed)
            time.sleep(random.uniform(0, jitter_ms) / 1000.0)
        app.invoke(Command(resume=True), cfg)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    finally:
        cm.__exit__(None, None, None)
    print(json.dumps({"error": err}))

def run_cell(backend, dsn, k, jitter_ms, reps, py, gate_ms=0):
    fires, errors, per_rep = [], 0, []
    for rep in range(reps):
        tmp = tempfile.mkdtemp(prefix=f"p168_{backend}_k{k}_j{jitter_ms}_")
        db = os.path.join(tmp, "ckpt.sqlite")
        ledger = os.path.join(tmp, "ledger.sqlite")
        barrier = os.path.join(tmp, "GO")
        thread = f"t168_{backend}_{k}_{jitter_ms}_{rep}_{os.getpid()}"

        r = subprocess.run([py, __file__, "park", backend, db, dsn or "",
                            ledger, thread, str(gate_ms)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"park child failed (backend={backend}, rc={r.returncode}).\n"
                f"--- child stderr ---\n{r.stderr.strip()[:2000]}\n"
                f"--- child stdout ---\n{r.stdout.strip()[:500]}")

        procs = [subprocess.Popen(
            [py, __file__, "racer", backend, db, dsn or "", ledger, thread,
             barrier, str(jitter_ms), str(rep * 1000 + i),
             os.path.join(tmp, f"ready.{i}"), str(gate_ms)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(k)]

        deadline = time.time() + 60
        while time.time() < deadline:
            if all(Path(os.path.join(tmp, f"ready.{i}")).exists()
                   for i in range(k)):
                break
            time.sleep(0.002)
        else:
            raise RuntimeError(f"racers did not all become ready (k={k})")
        Path(barrier).touch()
        for p in procs:
            p.wait(timeout=120)

        counts = ledger_counts(ledger)
        gate_fires = sum(v for t, v in counts.items() if t.startswith("gate:"))
        rep_errors = sum(1 for p in procs if p.returncode != 0)
        fires.append(gate_fires)
        errors += rep_errors
        per_rep.append({"rep": rep, "gate_fires": gate_fires,
                        "pre_fires": counts.get("pre", 0),
                        "racer_errors": rep_errors})

    dist = {}
    for f in fires:
        dist[str(f)] = dist.get(str(f), 0) + 1
    return {"k": k, "jitter_ms": jitter_ms, "backend": backend, "reps": reps,
            "gate_duration_ms": gate_ms,
            "gate_fire_distribution": dist,
            "max_fires": max(fires), "min_fires": min(fires),
            "mean_fires": round(sum(fires) / len(fires), 3),
            "reps_with_duplicate": sum(1 for f in fires if f > 1),
            "racer_errors_total": errors, "per_rep": per_rep}

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("park", "racer"):
        mode = sys.argv[1]
        if mode == "park":
            child_park(sys.argv[2], sys.argv[3], sys.argv[4] or None,
                       sys.argv[5], sys.argv[6],
                       int(sys.argv[7]) if len(sys.argv) > 7 else 0)
        else:
            child_racer(sys.argv[2], sys.argv[3], sys.argv[4] or None,
                        sys.argv[5], sys.argv[6], sys.argv[7],
                        int(sys.argv[8]), int(sys.argv[9]),
                        sys.argv[10] if len(sys.argv) > 10 else None,
                        int(sys.argv[11]) if len(sys.argv) > 11 else 0)
        return

    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--pg-dsn", default=None)
    ap.add_argument("--ks", default=None,
                    help="comma-separated racer counts, e.g. 8,16")
    ap.add_argument("--jitters", default=None,
                    help="comma-separated jitters in ms, e.g. 0,25")
    ap.add_argument("--gate-duration-ms", type=int, default=0,
                    help="work the gated node performs before its effect; "
                         "sweep jitter past this to measure the window")
    ap.add_argument("--out", default="results/multiproc")
    args = ap.parse_args()

    pins = _pins()
    if args.pg_dsn:
        try:
            import psycopg
            with psycopg.connect(args.pg_dsn, connect_timeout=5) as c:
                c.execute("SELECT 1")
        except Exception as e:
            _fail(f"--pg-dsn preflight failed: {type(e).__name__}: {e}. "
                  f"Check, in order: the database exists (createdb "
                  f"resume_contract); the DSN carries credentials your "
                  f"pg_hba.conf accepts; the same DSN form probes 159/165 "
                  f"used already works.")
    ks = ([int(x) for x in args.ks.split(",")] if args.ks
          else [2, 3] if args.smoke else [2, 3, 4, 8, 16])
    jitters = ([int(x) for x in args.jitters.split(",")] if args.jitters
               else [0, 5] if args.smoke else [0, 1, 5, 25])
    reps = 3 if args.smoke else args.reps
    backends = ["sqlite"] + (["postgres"] if args.pg_dsn else [])

    cells = []
    for backend in backends:
        for k in ks:
            for j in jitters:
                cell = run_cell(backend, args.pg_dsn, k, j, reps,
                                sys.executable, args.gate_duration_ms)
                cells.append(cell)
                print(f"  {backend:9s} k={k:<3d} jitter={j:>3d}ms  "
                      f"fires={cell['gate_fire_distribution']}  "
                      f"dup_reps={cell['reps_with_duplicate']}/{reps}",
                      file=sys.stderr)

    by = {(c["backend"], c["k"], c["jitter_ms"]): c for c in cells}
    zero_jitter = [c for c in cells if c["jitter_ms"] == 0]
    p1 = None
    if zero_jitter:
        big = [c for c in zero_jitter if c["k"] > 2]
        p1 = bool(big) and max(c["max_fires"] for c in big) > 2
        p1_detail = {str(c["k"]): c["max_fires"] for c in zero_jitter}
    BAND = 0.10
    p2_by_k, saturation = {}, {}
    for backend in backends:
        for k in ks:
            js = sorted(jitters)
            ms = [by[(backend, k, j)]["mean_fires"] for j in js
                  if (backend, k, j) in by]
            if len(ms) >= 2 and ms[0] > 0:
                rel = (ms[-1] - ms[0]) / ms[0]
                p2_by_k[f"{backend}.k{k}"] = (
                    "declining" if rel < -BAND
                    else "rising" if rel > BAND
                    else "flat")
            for j in jitters:
                c = by.get((backend, k, j))
                if c:
                    saturation[f"{backend}.k{k}.j{j}"] = round(
                        c["mean_fires"] / k, 3)
    p2 = (all(v == "declining" for v in p2_by_k.values())
          if p2_by_k else None)
    flat_or_rising = [k for k, v in p2_by_k.items() if v != "declining"]
    window_lower_bound = max(jitters) if flat_or_rising else None
    saturating = [c["jitter_ms"] for c in cells
                  if c["k"] > 1 and c["mean_fires"] / c["k"] >= 0.95]
    window_edge_ms = max(saturating) if saturating else None

    gate159 = by.get((backends[0], 2, 0))
    p3 = (bool(gate159["reps_with_duplicate"] == gate159["reps"])
          if gate159 else None)

    stable = {
        "P1_duplicates_scale_beyond_two": p1,
        "P1_max_fires_by_k_at_zero_jitter": (
            {str(c["k"]): c["max_fires"] for c in zero_jitter} if zero_jitter else None),
        "P2_jitter_reduces_duplicates": p2,
        "P2_response_by_k": p2_by_k,
        "window_at_least_ms": window_lower_bound,
        "window_edge_ms": window_edge_ms,
        "saturation_mean_fires_over_k": saturation,
        "gate_duration_ms": args.gate_duration_ms,
        "widest_jitter_still_duplicating_ms": max(
            [c["jitter_ms"] for c in cells if c["reps_with_duplicate"] > 0]
            or [-1]),
        "P3_reproduces_probe159_k2_j0": p3,
        "max_fires_observed": max(c["max_fires"] for c in cells),
        "any_racer_errors": any(c["racer_errors_total"] for c in cells),
    }

    out = {"probe": "168_p16_multiracer_sweep",
           "host": os.uname().nodename,
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "pins": pins, "reps": reps, "ks": ks, "jitters_ms": jitters,
           "backends": backends, "gate_duration_ms": args.gate_duration_ms,
           "stable": stable, "cells": cells}

    Path(args.out).mkdir(parents=True, exist_ok=True)
    raw = Path(args.out) / "168_results.json"
    stb = Path(args.out) / "168_stable.json"
    raw.write_text(json.dumps(out, indent=1))
    stb.write_text(json.dumps({k: out[k] for k in
                               ("probe", "host", "utc", "pins", "stable")},
                              indent=1))
    print(json.dumps(stable, indent=1))
    if p3 is None:
        print("NOTE: k=2/jitter=0 not in this run, so the probe-159 "
              "consistency gate was not evaluated. Targeted sweeps should be "
              "read alongside a full run that does include it.", file=sys.stderr)
    elif p3 is False:
        print("GATE FAILED: k=2 jitter=0 did not reproduce probe 159. "
              "No other cell in this run is interpretable.", file=sys.stderr)
        sys.exit(4)

if __name__ == "__main__":
    main()
