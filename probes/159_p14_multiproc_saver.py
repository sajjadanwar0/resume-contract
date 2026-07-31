#!/usr/bin/env python3
"""
159_p14_multiproc_saver.py  (campaign p14)

Multi-PROCESS evidence for the cell probe 157 cannot reach: 157's own
diagnosis is that its concurrency is GIL-bound (threads in one interpreter),
while the deployment the live-Postgres cells gesture at is multiple worker
processes sharing one durable saver.  Three arms, all with separate OS
processes over one on-disk checkpointer database:

  A. race_same   -- one thread parked at the interrupt; TWO processes issue
                    Command(resume=True) simultaneously (spin-barrier start).
                    Discovery cell: does the gated effect fire once or twice
                    under a genuine cross-process duplicate-delivery race?
  B. race_diff   -- same race, values True vs False: which value(s) fire,
                    and is either racer served the other's branch?
  C. contention_kill -- k worker processes, distinct threads, ONE shared db;
                    the victim worker is SIGKILLed while parked at its
                    interrupt; survivors resume concurrently; a fresh process
                    recovers the victim's thread.  Per-thread EO/CO under
                    cross-process contention + park-kill recovery (158's cell
                    replicated under load).
  D. race_same under the packaged REMIT shim (one shim instance PER PROCESS
                    over the same db) -- does per-process interposition
                    change or break the race behavior?  (Scope probe: the
                    shim's sequencer is per-process by construction.
                    RETAINED as the ungated differential now that the shim
                    ships an opt-in gate: default wrap must still race.)
  E. race_same under the shim's CROSS-PROCESS GATE (v0.1.1+,
                    wrap(..., cross_process_gate=True)): probe 165's
                    read-path claim, promoted into the package.  Expected:
                    exactly one racer fires; the loser raises
                    RemitConsumeConflict inside get_tuple, before any node.
  F. race_diff under the gate: the loser with the other value is refused,
                    not served the winner's branch.

Oracle: on-disk SQLite effect ledger, rows tagged by thread, written by the
node bodies, read by the parent (cross-process, kill-surviving).

Backends: SQLite (default); --pg-dsn adds the same arms on PostgresSaver
(unique thread ids per rep; saver.setup() once).

Usage:
  .venv/bin/python3 probes/159_p14_multiproc_saver.py            # reps=10 k=4
  .venv/bin/python3 probes/159_p14_multiproc_saver.py --smoke    # reps=3 k=2
  .venv/bin/python3 probes/159_p14_multiproc_saver.py --pg-dsn "postgresql://..."
Child modes (argv[1], internal): park | racer | worker | recover
"""
import argparse
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path

REQUIRED_LANGGRAPH = "1.2.9"


def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}),
          file=sys.stderr)
    sys.exit(3)


_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"langgraph=={_lg} but the paper pin is {REQUIRED_LANGGRAPH}. "
          f"Interpreter: {sys.executable}. Launch with envs/langgraph-durable "
          f"or set PROBE_ALLOW_OFFPIN=1.")

from typing import TypedDict                          # noqa: E402
from langgraph.graph import StateGraph, START, END    # noqa: E402
from langgraph.types import interrupt, Command        # noqa: E402
from langgraph.checkpoint.sqlite import SqliteSaver   # noqa: E402

try:
    from remit import langgraph_shim as _rls
    HAVE_REMIT = True
except Exception as _e:                               # pragma: no cover
    HAVE_REMIT = False
    _REMIT_ERR = f"{type(_e).__name__}: {_e}"


class S(TypedDict, total=False):
    x: int
    pre_out: str
    decision: bool


RUN_NONCE = uuid.uuid4().hex[:6]    # isolates thread ids across invocations
                                    # that share one Postgres database (the
                                    # probe-165 convention; SQLite never
                                    # collides because every rep gets a fresh
                                    # tmpdir, Postgres reuses one database)


# ------------------------------------------------------------------ ledger
def ledger_init(path):
    c = sqlite3.connect(path, timeout=60)
    c.execute("CREATE TABLE IF NOT EXISTS effects "
              "(n INTEGER PRIMARY KEY AUTOINCREMENT, thread TEXT, task TEXT)")
    c.commit()
    c.close()


def ledger_write(path, thread, task):
    c = sqlite3.connect(path, timeout=60)
    c.execute("INSERT INTO effects (thread, task) VALUES (?, ?)",
              (thread, task))
    c.commit()
    c.close()


def ledger_thread(path, thread):
    c = sqlite3.connect(path, timeout=60)
    rows = c.execute(
        "SELECT task, COUNT(*) FROM effects WHERE thread=? GROUP BY task",
        (thread,)).fetchall()
    c.close()
    return dict(rows)


# ------------------------------------------------------------------ savers
def make_saver(ctx):
    """Return (saver, closer) for this process, per ctx backend/shim."""
    if ctx.get("pg_dsn"):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
        conn = Connection.connect(ctx["pg_dsn"], autocommit=True,
                                  prepare_threshold=0, row_factory=dict_row)
        saver = (_rls.wrap(PostgresSaver, conn,
                           cross_process_gate=bool(ctx.get("shim_gate")))
                 if ctx.get("shim") else PostgresSaver(conn))
        saver.setup()    # Postgres does not create the checkpointer schema
                         # lazily (probe 165 finding, 2026-07-27); without
                         # this, every child dies UndefinedTable on a fresh
                         # database -- reproduced by this probe 2026-07-31
                         # as "contention workers never all parked"
        return saver, conn.close
    conn = sqlite3.connect(ctx["db"], check_same_thread=False, timeout=60)
    saver = (_rls.wrap(SqliteSaver, conn,
                       cross_process_gate=bool(ctx.get("shim_gate")))
             if ctx.get("shim") else SqliteSaver(conn))
    return saver, conn.close


def build(ctx, thread):
    saver, closer = make_saver(ctx)
    ledger_init(ctx["ledger"])

    def pre(state: S):
        ledger_write(ctx["ledger"], thread, "pre")
        return {"pre_out": "pre:done"}

    def gate(state: S):
        v = interrupt({"q": "approve?"})
        ledger_write(ctx["ledger"], thread, f"gate:{v}")
        return {"decision": v}

    g = StateGraph(S)
    g.add_node("pre", pre)
    g.add_node("gate", gate)
    g.add_edge(START, "pre")
    g.add_edge("pre", "gate")
    g.add_edge("gate", END)
    return g.compile(checkpointer=saver), closer


def _cfg(thread):
    return {"configurable": {"thread_id": thread}}


def _invoke(app, payload, thread):
    try:
        return app.invoke(payload, _cfg(thread), durability="sync")
    except TypeError:
        return app.invoke(payload, _cfg(thread))


# ------------------------------------------------------------ child modes
def park_main(ctx):
    app, closer = build(ctx, ctx["thread"])
    res = _invoke(app, {"x": 1}, ctx["thread"])
    closer()
    print(json.dumps({"interrupted": "__interrupt__" in res}))


def racer_main(ctx):
    app, closer = build(ctx, ctx["thread"])
    Path(ctx["armed"]).write_text("1")
    while not os.path.exists(ctx["start"]):
        time.sleep(0.0005)
    t0 = time.perf_counter()
    err, res = None, None
    try:
        res = _invoke(app, Command(resume=ctx["value"]), ctx["thread"])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    dt = (time.perf_counter() - t0) * 1000
    closer()
    print(json.dumps({"idx": ctx["idx"], "value": ctx["value"],
                      "result": res, "error": err,
                      "dt_ms": round(dt, 1)}, default=str))


def worker_main(ctx):
    thread = f"{ctx['nonce']}-w{ctx['idx']}"
    app, closer = build(ctx, thread)
    res = _invoke(app, {"x": 1}, thread)
    Path(ctx["ready"]).write_text("1")
    if ctx["victim"]:
        time.sleep(600)                      # parked; parent SIGKILLs
    while not os.path.exists(ctx["go"]):
        time.sleep(0.002)
    out = None
    err = None
    try:
        out = _invoke(app, Command(resume=True), thread)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    closer()
    print(json.dumps({"idx": ctx["idx"], "interrupted":
                      "__interrupt__" in res, "result": out, "error": err},
                     default=str))


def recover_main(ctx):
    app, closer = build(ctx, ctx["thread"])
    err, res = None, None
    try:
        res = _invoke(app, Command(resume=True), ctx["thread"])
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    closer()
    print(json.dumps({"result": res, "error": err}, default=str))


def _spawn(mode, ctx, capture=True):
    env = dict(os.environ, PROBE159_CTX=json.dumps(ctx))
    return subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), mode], env=env,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL, text=True)


def _last_json(proc, timeout):
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        return {"error": "timeout", "_stderr_tail": (err or "").strip()[-200:]}
    parsed = {}
    for line in (out or "").splitlines():
        try:
            parsed = json.loads(line)
        except Exception:
            pass
    if err and err.strip():
        parsed["_stderr_tail"] = err.strip().splitlines()[-1]
    return parsed


# ------------------------------------------------------------ parent arms
def run_race(base, tag, value_a, value_b, reps, shim, pg_dsn,
             shim_gate=False):
    reps_out = []
    for i in range(reps):
        d0 = tempfile.mkdtemp(prefix=f"p159_{tag}_{i}_", dir=base)
        thread = f"{RUN_NONCE}-{tag}{i}"
        ctx0 = {"db": f"{d0}/ckpt.sqlite", "ledger": f"{base}/ledger.sqlite",
                "thread": thread, "shim": shim, "shim_gate": shim_gate,
                "pg_dsn": pg_dsn}
        pk = _spawn("park", ctx0)
        pk_out = _last_json(pk, 60)
        racers, flags = [], []
        for idx, val in ((0, value_a), (1, value_b)):
            ctx = dict(ctx0, idx=idx, value=val,
                       armed=f"{d0}/armed{idx}", start=f"{d0}/start")
            flags.append(ctx["armed"])
            racers.append(_spawn("racer", ctx))
        deadline = time.time() + 60
        while not all(os.path.exists(f) for f in flags):
            if time.time() > deadline:
                tails = {}
                for j, r in enumerate(racers):
                    r.kill()
                    try:
                        _o, e = r.communicate(timeout=5)
                        tails[j] = (e or "").strip().splitlines()[-3:]
                    except Exception:
                        tails[j] = ["<no stderr captured>"]
                raise SystemExit(f"{tag} rep {i}: racers never armed; "
                                 f"stderr tails: {json.dumps(tails)}")
            time.sleep(0.002)
        Path(f"{d0}/start").write_text("1")
        r_out = [_last_json(r, 120) for r in racers]
        led = ledger_thread(ctx0["ledger"], thread)
        gate_rows = {k: v for k, v in led.items() if k.startswith("gate:")}
        reps_out.append({
            "rep": i, "park": pk_out, "racers": r_out,
            "ledger": led, "gate_fires_total": sum(gate_rows.values()),
            "gate_values_fired": sorted(gate_rows),
        })
    dist = {}
    for r in reps_out:
        dist[str(r["gate_fires_total"])] = dist.get(
            str(r["gate_fires_total"]), 0) + 1
    dup = [r["rep"] for r in reps_out if r["gate_fires_total"] > 1]
    errs = [r["rep"] for r in reps_out
            if any(x.get("error") for x in r["racers"])]
    served_other = []
    if value_a != value_b:
        for r in reps_out:
            for x in r["racers"]:
                dec = (x.get("result") or {}).get("decision") \
                    if isinstance(x.get("result"), dict) else None
                if dec is not None and dec != x["value"] \
                        and f"gate:{x['value']}" not in r["gate_values_fired"]:
                    served_other.append(r["rep"])
    conflicts = [sum(1 for x in r["racers"]
                     if "RemitConsumeConflict" in (x.get("error") or ""))
                 for r in reps_out]
    return {
        "reps": reps, "shim": shim, "shim_gate": shim_gate,
        "values": [value_a, value_b],
        "claim_rejections_per_rep": conflicts,
        "gate_fire_distribution": dist,
        "reps_with_duplicate_gate_fire": dup,
        "reps_with_racer_error": errs,
        "reps_where_a_racer_was_served_the_other_value": sorted(set(served_other)),
        "per_rep": reps_out,
    }


def run_contention(base, k, shim, pg_dsn):
    d0 = tempfile.mkdtemp(prefix="p159_cont_", dir=base)
    ctx0 = {"db": f"{d0}/ckpt.sqlite", "ledger": f"{base}/ledger.sqlite",
            "shim": shim, "pg_dsn": pg_dsn, "nonce": RUN_NONCE}
    workers = []
    for idx in range(k):
        ctx = dict(ctx0, idx=idx, victim=(idx == 0),
                   ready=f"{d0}/ready{idx}", go=f"{d0}/go")
        workers.append((idx, _spawn("worker", ctx)))
    deadline = time.time() + 120
    while not all(os.path.exists(f"{d0}/ready{i}") for i in range(k)):
        if time.time() > deadline:
            tails = {}
            for i, w in workers:
                unready = not os.path.exists(f"{d0}/ready{i}")
                w.kill()
                if unready:
                    try:
                        _o, e = w.communicate(timeout=5)
                        tails[i] = (e or "").strip().splitlines()[-3:]
                    except Exception:
                        tails[i] = ["<no stderr captured>"]
            raise SystemExit("contention workers never all parked; "
                             f"stderr tails: {json.dumps(tails)}")
        time.sleep(0.005)

    victim = workers[0][1]
    os.kill(victim.pid, signal.SIGKILL)
    victim.wait()
    Path(f"{d0}/go").write_text("1")
    surv = {i: _last_json(w, 180) for i, w in workers[1:]}
    rec = _spawn("recover", dict(ctx0, thread=f"{RUN_NONCE}-w0"))
    rec_out = _last_json(rec, 120)

    per_thread = {f"w{i}": ledger_thread(ctx0["ledger"], f"{RUN_NONCE}-w{i}")
                  for i in range(k)}
    ok_threads = all(
        per_thread[t].get("pre", 0) == 1
        and per_thread[t].get("gate:True", 0) == 1
        and sum(v for kk, v in per_thread[t].items()
                if kk.startswith("gate:")) == 1
        for t in per_thread)
    return {
        "k": k, "shim": shim,
        "victim_exit": ("SIGKILL" if victim.returncode == -signal.SIGKILL
                        else str(victim.returncode)),
        "survivors": surv, "victim_recovery": rec_out,
        "per_thread_ledger": per_thread,
        "per_thread_exactly_once": ok_threads,
        "victim_recovered_ok": rec_out.get("error") is None
        and isinstance(rec_out.get("result"), dict),
    }


def parent_main(args):
    base = tempfile.mkdtemp(prefix="probe159_")
    ledger_init(f"{base}/ledger.sqlite")
    arms = {}
    arms["race_same_stock"] = run_race(base, "rs", True, True,
                                       args.reps, False, args.pg_dsn)
    arms["race_diff_stock"] = run_race(base, "rd", True, False,
                                       args.reps, False, args.pg_dsn)
    arms["contention_kill"] = run_contention(base, args.k, False, args.pg_dsn)
    if HAVE_REMIT:
        arms["race_same_shim"] = run_race(base, "rss", True, True,
                                          args.reps, True, args.pg_dsn)
        arms["race_same_shim_gate"] = run_race(base, "rsg", True, True,
                                               args.reps, True, args.pg_dsn,
                                               shim_gate=True)
        arms["race_diff_shim_gate"] = run_race(base, "rdg", True, False,
                                               args.reps, True, args.pg_dsn,
                                               shim_gate=True)
    result = {
        "probe": "159_p14_multiproc_saver",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": "postgres" if args.pg_dsn else "sqlite",
        "pins": {
            "langgraph": version("langgraph"),
            "langgraph-checkpoint": version("langgraph-checkpoint"),
            "langgraph-checkpoint-sqlite":
                version("langgraph-checkpoint-sqlite"),
            "remit-contract":
                (version("remit-contract") if HAVE_REMIT else None),
        },
        "have_remit": HAVE_REMIT,
        "arms": arms,
        "stable": {
            "co_concurrent_double_resume_inert_all_reps":
                not arms["race_same_stock"]["reps_with_duplicate_gate_fire"],
            "race_same_gate_fire_distribution":
                arms["race_same_stock"]["gate_fire_distribution"],
            "race_diff_gate_fire_distribution":
                arms["race_diff_stock"]["gate_fire_distribution"],
            "race_diff_double_consumption_reps":
                arms["race_diff_stock"]["reps_with_duplicate_gate_fire"],
            "race_diff_served_other_value_reps":
                arms["race_diff_stock"]
                    ["reps_where_a_racer_was_served_the_other_value"],
            "contention_per_thread_exactly_once":
                arms["contention_kill"]["per_thread_exactly_once"],
            "contention_victim_park_kill_recovered":
                arms["contention_kill"]["victim_recovered_ok"],
            "shim_race_inert_all_reps":
                (not arms["race_same_shim"]["reps_with_duplicate_gate_fire"])
                if HAVE_REMIT else None,
            "shim_gate_race_same_fire_distribution":
                arms["race_same_shim_gate"]["gate_fire_distribution"]
                if HAVE_REMIT else None,
            "shim_gate_race_same_duplicate_reps":
                arms["race_same_shim_gate"]["reps_with_duplicate_gate_fire"]
                if HAVE_REMIT else None,
            "shim_gate_race_diff_fire_distribution":
                arms["race_diff_shim_gate"]["gate_fire_distribution"]
                if HAVE_REMIT else None,
            "shim_gate_race_diff_served_other_value_reps":
                arms["race_diff_shim_gate"]
                    ["reps_where_a_racer_was_served_the_other_value"]
                if HAVE_REMIT else None,
            "shim_gate_loser_rejected_loudly_every_race_rep":
                (all(c == 1 for c in
                     arms["race_same_shim_gate"]["claim_rejections_per_rep"])
                 and all(c == 1 for c in
                         arms["race_diff_shim_gate"]
                             ["claim_rejections_per_rep"]))
                if HAVE_REMIT else None,
        },
    }
    print(json.dumps(result, indent=2, default=str))
    out = Path(os.environ.get(
        "PROBE159_OUT",
        Path(__file__).resolve().parents[1] / "results" / "multiproc"))
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_pg" if args.pg_dsn else ""
    (out / f"159_results{suffix}.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")
    (out / f"159_stable{suffix}.json").write_text(json.dumps(
        {"probe": result["probe"], "host": result["host"],
         "utc": result["utc"], "backend": result["backend"],
         "pins": result["pins"], "stable": result["stable"]}, indent=2) + "\n")
    print(f"\nwrote {out}/159_results{suffix}.json and 159_stable{suffix}.json",
          file=sys.stderr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode in ("park", "racer", "worker", "recover"):
        ctx = json.loads(os.environ["PROBE159_CTX"])
        {"park": park_main, "racer": racer_main,
         "worker": worker_main, "recover": recover_main}[mode](ctx)
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--reps", type=int, default=10)
        ap.add_argument("--k", type=int, default=4)
        ap.add_argument("--smoke", action="store_true")
        ap.add_argument("--pg-dsn", default=os.environ.get("PROBE_PG_DSN"))
        args = ap.parse_args()
        if args.smoke:
            args.reps, args.k = 3, 2
        parent_main(args)
