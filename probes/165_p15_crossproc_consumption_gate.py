#!/usr/bin/env python3
"""
165_p15_crossproc_consumption_gate.py  (campaign p15)

Closes the loop on probe 159's design implication.  159 measured the
cross-process CO failure (two OS processes resuming one parked interrupt
fire the gated effect twice, 10/10 on both durable backends) and stated the
implication: "a cross-process repair must put the consumption record in the
shared store under a compare-and-swap rather than in the interposition
layer."  This probe is that repair, as a saver-level demonstrator -- and its
development produced a mechanism finding of its own:

  DESIGN v1 (falsified in this campaign, kept here as provenance): the CAS
  claim interposed at BaseCheckpointSaver.put_writes on the __resume__
  channel.  Measured at the pins: the race still duplicates in every rep
  WITH the gate active, and the loser is rejected loudly -- after its
  effect fired.  An instrumented trace shows why: the null-task __resume__
  journal write is submitted to a background executor thread and is
  CONCURRENT with gated execution on the main thread, not ordered before
  it, so an exception raised there is observed only at superstep join.
  The write path is powerless for cross-process CO exactly as it was for
  FD (probe 125): interposition below the decision point cannot veto the
  decision.

  DESIGN v2 (this file): the claim binds at the durable-state READ the
  loop performs before gated execution -- get_tuple returning the parked
  checkpoint with a pending __interrupt__ write.  Both racers demonstrably
  load the same checkpoint there (measured: identical checkpoint ids); the
  gate claims <thread, that checkpoint id> in a shared durable claims
  table with ONE INSERT under a uniqueness constraint (SQLite primary key
  / Postgres ON CONFLICT) -- the compare-and-swap named in the paper.
  Exactly one process can win; the loser's invocation raises
  ConsumptionClaimRejected inside get_tuple, before any node executes.
  The put_writes claim is retained as a secondary latch (same key, no-op
  for the winner) for any path that reaches the journal without the read.

Arms (SQLite default; --pg-dsn replicates the race arms on PostgresSaver
with the claims table created in the same Postgres database):

  A. seq_control_gate   -- park; ONE resume under the gate.  Must complete
                           with the gated effect firing exactly once: the
                           gate must not false-positive on the legitimate
                           first consumption.
  B. race_same_gate     -- 159's two-racer byte-identical race, both racers
                           under the gate.  Expected: gate fires 1/rep in
                           every rep; exactly one racer rejected loudly.
  C. race_diff_gate     -- values True vs False under the gate.  Expected:
                           exactly one value fires; the loser is rejected,
                           not served the other branch.
  D. race_same_stock    -- differential control (reps//2, min 3): the
                           stock saver on the identical protocol.
                           Expected: reproduces 159's duplicate.
  E. stray_after_completion_gate -- park; resume to completion; then a
                           stray resume under the gate.  Measures the
                           disposition: the completed thread's checkpoint
                           carries no pending __interrupt__, so the gate
                           does not engage and the stock silent-swallow
                           inertness (probe 126) is preserved unchanged.

Scope, stated plainly (mirrors the POST-RUN paper text):
  * The gate keys the ORDINARY address only -- it serializes all resume
    deliveries addressed to one checkpoint.  A fork-flagged delivery
    carries the FI discriminator and would claim a fresh <checkpoint,
    ordinal> key; that composition is the shim's flag-keyed configuration
    (probe 155), not this demonstrator.
  * Residual interface gap, named rather than hidden: get_tuple carries no
    read-intent discriminator, so any reader of a parked checkpoint takes
    the claim -- state INSPECTION during a park would consume it and a
    later resume from another process would be rejected.  The probed
    protocols do not inspect; a production composition needs read intent
    in the invocation config, information the BaseCheckpointSaver surface
    does not carry.  This is the CO analogue, at the read path, of the FI
    gap the paper documents at the write path.

Oracle: on-disk SQLite effect ledger, identical to probe 159's.

Usage:
  .venv/bin/python3 probes/165_p15_crossproc_consumption_gate.py            # reps=10
  .venv/bin/python3 probes/165_p15_crossproc_consumption_gate.py --smoke    # reps=3
  .venv/bin/python3 probes/165_p15_crossproc_consumption_gate.py --pg-dsn "postgresql://..."
      --pg-dsn requires langgraph-checkpoint-postgres and psycopg in the
      interpreter and a reachable Postgres.  The probe calls
      PostgresSaver.setup() itself: Postgres, unlike SQLite, does not
      create the checkpointer schema lazily (first-run finding, host
      2026-07-27: every arm failed UndefinedTable before this call was
      added).  Thread ids carry a per-invocation nonce so repeated runs
      against one shared database cannot collide.
  GATE_DEBUG=1 ... : children also log the pending channels seen at
                     get_tuple and the write channels seen at put_writes
                     (VERIFY aid for the channel constants at a new pin).
Child modes (argv[1], internal): park | racer | single
"""
import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
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

RESUME_CHANNEL = "__resume__"        # LangGraph 1.2.9 pending-write channels
INTERRUPT_CHANNEL = "__interrupt__"  # (paper Sec. 4.3).  GATE_DEBUG=1 dumps
                                     # observed channels if these drift at a
                                     # future pin.


class S(TypedDict, total=False):
    x: int
    pre_out: str
    decision: bool


class ConsumptionClaimRejected(RuntimeError):
    """A second process attempted to consume an already-claimed interrupt."""


def _optional_version(dist):
    try:
        return version(dist)
    except PackageNotFoundError:
        return "not-installed"


def _pins(pg_dsn):
    pins = {
        "langgraph": version("langgraph"),
        "langgraph-checkpoint": version("langgraph-checkpoint"),
        "langgraph-checkpoint-sqlite": version("langgraph-checkpoint-sqlite"),
    }
    if pg_dsn:
        pins["langgraph-checkpoint-postgres"] = _optional_version(
            "langgraph-checkpoint-postgres")
        pins["psycopg"] = _optional_version("psycopg")
    return pins


RUN_NONCE = uuid.uuid4().hex[:6]    # isolates thread ids across invocations
                                    # that share one Postgres database


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


# --------------------------------------------------------- consumption gate
class ConsumptionGate:
    """Shared-store CAS on <thread, checkpoint>: SQLite file or Postgres
    table.  claim() is idempotent per process (in-process latch) and raises
    ConsumptionClaimRejected when another process holds the key."""

    def __init__(self, sqlite_path=None, pg_dsn=None):
        self.sqlite_path = sqlite_path
        self.pg_dsn = pg_dsn
        self._held = set()
        if pg_dsn:
            from psycopg import Connection
            self._pg = Connection.connect(pg_dsn, autocommit=True)
            self._pg.execute(
                "CREATE TABLE IF NOT EXISTS remit_claims_165 "
                "(thread text NOT NULL, ckpt text NOT NULL, "
                " PRIMARY KEY (thread, ckpt))")
        else:
            c = sqlite3.connect(sqlite_path, timeout=60)
            c.execute("CREATE TABLE IF NOT EXISTS claims "
                      "(thread TEXT NOT NULL, ckpt TEXT NOT NULL, "
                      " PRIMARY KEY (thread, ckpt))")
            c.commit()
            c.close()

    def claim(self, thread, ckpt):
        key = (str(thread), str(ckpt))
        if key in self._held:
            return
        thread, ckpt = key
        if self.pg_dsn:
            cur = self._pg.execute(
                "INSERT INTO remit_claims_165 (thread, ckpt) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING", (thread, ckpt))
            won = cur.rowcount == 1
        else:
            c = sqlite3.connect(self.sqlite_path, timeout=60)
            try:
                c.execute("INSERT INTO claims (thread, ckpt) VALUES (?, ?)",
                          (thread, ckpt))
                c.commit()
                won = True
            except sqlite3.IntegrityError:
                won = False
            finally:
                c.close()
        if not won:
            raise ConsumptionClaimRejected(
                f"interrupt consumption for thread={thread} ckpt={ckpt} "
                f"already claimed by another process")
        self._held.add(key)


def _thread_of(config):
    return str(((config or {}).get("configurable", {}) or {}).get("thread_id"))


def gated_saver_cls(base_cls):
    class GatedSaver(base_cls):
        _gate = None       # set post-construction

        # PRIMARY interposition: the durable-state read that precedes gated
        # execution.  A returned tuple carrying a pending __interrupt__
        # write is the consumable parked state; claim it here, in the
        # calling (main) thread, so a losing process aborts before any node
        # runs.
        def get_tuple(self, config):
            t = super().get_tuple(config)
            if t is None or self._gate is None:
                return t
            pend = getattr(t, "pending_writes", None) or []
            chans = []
            for w in pend:
                try:
                    chans.append(str(w[1]))   # (task_id, channel, value)
                except Exception:
                    chans.append(repr(w))
            if os.environ.get("GATE_DEBUG"):
                print(json.dumps({"gate_debug_get_tuple_pending": chans}),
                      file=sys.stderr)
            if INTERRUPT_CHANNEL in chans:
                ckpt = ((t.config or {}).get("configurable", {}) or {}).get(
                    "checkpoint_id") or "__latest__"
                self._gate.claim(_thread_of(config), ckpt)
            return t

        # SECONDARY latch: same key at the resume journal write.  A winner
        # re-claims idempotently; a path that somehow reaches the journal
        # without the read is still caught (at join, i.e. late -- the v1
        # measurement -- which is why this is secondary, not primary).
        def put_writes(self, config, writes, task_id, *a, **kw):
            channels = []
            resume_seen = False
            for w in (writes or ()):
                try:
                    ch = w[0]
                except Exception:
                    ch = repr(w)
                channels.append(str(ch))
                if ch == RESUME_CHANNEL:
                    resume_seen = True
            if os.environ.get("GATE_DEBUG"):
                print(json.dumps({"gate_debug_put_writes_channels": channels,
                                  "task_id": str(task_id)}),
                      file=sys.stderr)
            if resume_seen and self._gate is not None:
                conf = (config or {}).get("configurable", {}) or {}
                self._gate.claim(_thread_of(config),
                                 conf.get("checkpoint_id") or "__latest__")
            return super().put_writes(config, writes, task_id, *a, **kw)
    return GatedSaver


# ------------------------------------------------------------------ savers
def make_saver(ctx):
    """Return (saver, closer) for this process, per ctx backend/gate."""
    if ctx.get("pg_dsn"):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
        conn = Connection.connect(ctx["pg_dsn"], autocommit=True,
                                  prepare_threshold=0, row_factory=dict_row)
        cls = gated_saver_cls(PostgresSaver) if ctx.get("gate") else PostgresSaver
        saver = cls(conn)
        saver.setup()    # Postgres does not create its schema lazily; without
                         # this every call fails UndefinedTable ("checkpoints")
        if ctx.get("gate"):
            saver._gate = ConsumptionGate(pg_dsn=ctx["pg_dsn"])
        return saver, conn.close
    conn = sqlite3.connect(ctx["db"], check_same_thread=False, timeout=60)
    cls = gated_saver_cls(SqliteSaver) if ctx.get("gate") else SqliteSaver
    saver = cls(conn)
    if ctx.get("gate"):
        saver._gate = ConsumptionGate(sqlite_path=ctx["claims"])
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
    err, err_type, res = None, None, None
    try:
        res = _invoke(app, Command(resume=ctx["value"]), ctx["thread"])
    except Exception as e:
        err, err_type = f"{type(e).__name__}: {e}"[:300], type(e).__name__
    dt = (time.perf_counter() - t0) * 1000
    closer()
    print(json.dumps({"idx": ctx["idx"], "value": ctx["value"],
                      "result": res, "error": err, "error_type": err_type,
                      "dt_ms": round(dt, 1)}, default=str))


def single_main(ctx):
    """One resume delivery (used by the sequential and stray arms)."""
    app, closer = build(ctx, ctx["thread"])
    err, err_type, res = None, None, None
    try:
        res = _invoke(app, Command(resume=ctx["value"]), ctx["thread"])
    except Exception as e:
        err, err_type = f"{type(e).__name__}: {e}"[:300], type(e).__name__
    closer()
    print(json.dumps({"result": res, "error": err, "error_type": err_type},
                     default=str))


def _spawn(mode, ctx, capture=True):
    env = dict(os.environ, PROBE165_CTX=json.dumps(ctx))
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
        parsed["_stderr_tail"] = err.strip().splitlines()[-1][:300]
    return parsed


# ------------------------------------------------------------ parent arms
def _fresh_ctx(base, tag, i, gate, pg_dsn):
    d0 = tempfile.mkdtemp(prefix=f"p165_{tag}_{i}_", dir=base)
    thread = f"{RUN_NONCE}-{tag}{i}"
    return {"db": f"{d0}/ckpt.sqlite", "claims": f"{d0}/claims.sqlite",
            "ledger": f"{base}/ledger.sqlite", "thread": thread,
            "gate": gate, "pg_dsn": pg_dsn, "_dir": d0}


def run_race(base, tag, value_a, value_b, reps, gate, pg_dsn):
    reps_out = []
    for i in range(reps):
        ctx0 = _fresh_ctx(base, tag, i, gate, pg_dsn)
        thread, d0 = ctx0["thread"], ctx0["_dir"]
        pk_out = _last_json(_spawn("park", ctx0), 60)
        racers, flags = [], []
        for idx, val in ((0, value_a), (1, value_b)):
            ctx = dict(ctx0, idx=idx, value=val,
                       armed=f"{d0}/armed{idx}", start=f"{d0}/start")
            flags.append(ctx["armed"])
            racers.append(_spawn("racer", ctx))
        deadline = time.time() + 60
        while not all(os.path.exists(f) for f in flags):
            if time.time() > deadline:
                for r in racers:
                    r.kill()
                raise SystemExit(f"{tag} rep {i}: racers never armed")
            time.sleep(0.002)
        Path(f"{d0}/start").write_text("1")
        r_out = [_last_json(r, 120) for r in racers]
        led = ledger_thread(ctx0["ledger"], thread)
        gate_rows = {k: v for k, v in led.items() if k.startswith("gate:")}
        reps_out.append({
            "rep": i, "park": pk_out, "racers": r_out, "ledger": led,
            "gate_fires_total": sum(gate_rows.values()),
            "gate_values_fired": sorted(gate_rows),
            "claim_rejections": [x.get("error_type") for x in r_out
                                 if x.get("error_type")
                                 == "ConsumptionClaimRejected"],
        })
    dist, dup, rej_counts = {}, [], []
    for r in reps_out:
        dist[str(r["gate_fires_total"])] = dist.get(
            str(r["gate_fires_total"]), 0) + 1
        if r["gate_fires_total"] > 1:
            dup.append(r["rep"])
        rej_counts.append(len(r["claim_rejections"]))
    other_err = [r["rep"] for r in reps_out
                 if any(x.get("error") and x.get("error_type")
                        != "ConsumptionClaimRejected" for x in r["racers"])]
    return {
        "reps": reps, "gate": gate, "values": [value_a, value_b],
        "gate_fire_distribution": dist,
        "reps_with_duplicate_gate_fire": dup,
        "claim_rejections_per_rep": rej_counts,
        "reps_with_non_gate_error": other_err,
        "per_rep": reps_out,
    }


def run_single_then(base, tag, gate, pg_dsn, stray_after=False):
    ctx0 = _fresh_ctx(base, tag, 0, gate, pg_dsn)
    thread = ctx0["thread"]
    pk_out = _last_json(_spawn("park", ctx0), 60)
    first = _last_json(_spawn("single", dict(ctx0, value=True)), 120)
    out = {"park": pk_out, "first_resume": first}
    if stray_after:
        out["stray_resume"] = _last_json(
            _spawn("single", dict(ctx0, value=False)), 120)
    led = ledger_thread(ctx0["ledger"], thread)
    gate_rows = {k: v for k, v in led.items() if k.startswith("gate:")}
    out.update({"ledger": led,
                "gate_fires_total": sum(gate_rows.values()),
                "gate_values_fired": sorted(gate_rows)})
    return out


def parent_main(args):
    base = tempfile.mkdtemp(prefix="probe165_")
    ledger_init(f"{base}/ledger.sqlite")
    arms = {}
    arms["seq_control_gate"] = run_single_then(base, "sq", True, args.pg_dsn)
    arms["race_same_gate"] = run_race(base, "gs", True, True,
                                      args.reps, True, args.pg_dsn)
    arms["race_diff_gate"] = run_race(base, "gd", True, False,
                                      args.reps, True, args.pg_dsn)
    stock_reps = max(3, args.reps // 2)
    arms["race_same_stock"] = run_race(base, "ss", True, True,
                                       stock_reps, False, args.pg_dsn)
    arms["stray_after_completion_gate"] = run_single_then(
        base, "st", True, args.pg_dsn, stray_after=True)

    seq_ok = (arms["seq_control_gate"]["gate_fires_total"] == 1
              and arms["seq_control_gate"]["first_resume"].get("error") is None)
    stray = arms["stray_after_completion_gate"]
    result = {
        "probe": "165_p15_crossproc_consumption_gate",
        "host": socket.gethostname(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "backend": "postgres" if args.pg_dsn else "sqlite",
        "pins": _pins(args.pg_dsn),
        "gate_design": {
            "version": "v2 (read-path)",
            "claim_key": "<thread_id, checkpoint_id of the parked tuple>",
            "cas": ("SQLite PRIMARY KEY INSERT (IntegrityError = lose)"
                    if not args.pg_dsn else
                    "Postgres INSERT ... ON CONFLICT DO NOTHING (rowcount=0 = lose)"),
            "primary_interposition":
                "get_tuple returning a pending __interrupt__ write",
            "secondary_interposition":
                "put_writes on the __resume__ channel (late by construction; "
                "the v1 measurement)",
            "v1_falsified":
                "write-path-only gate: race still duplicated every rep; the "
                "__resume__ journal write is executor-concurrent with gated "
                "execution, so its rejection surfaces only at superstep join",
            "scope": "ordinary address only; fork-flagged deliveries out of "
                     "scope; get_tuple has no read-intent discriminator, so "
                     "inspection during a park would take the claim (the CO "
                     "analogue of the FI gap, at the read path)",
        },
        "arms": arms,
        "stable": {
            "gate_binds_at": "get_tuple",
            "seq_single_resume_unblocked_fires_once": seq_ok,
            "gate_race_same_fire_distribution":
                arms["race_same_gate"]["gate_fire_distribution"],
            "gate_race_same_duplicate_reps":
                arms["race_same_gate"]["reps_with_duplicate_gate_fire"],
            "gate_race_diff_fire_distribution":
                arms["race_diff_gate"]["gate_fire_distribution"],
            "gate_race_diff_duplicate_reps":
                arms["race_diff_gate"]["reps_with_duplicate_gate_fire"],
            "gate_loser_rejected_loudly_every_race_rep":
                all(n >= 1 for n in
                    arms["race_same_gate"]["claim_rejections_per_rep"]
                    + arms["race_diff_gate"]["claim_rejections_per_rep"]),
            "stock_control_duplicate_reps":
                arms["race_same_stock"]["reps_with_duplicate_gate_fire"],
            "stock_control_fire_distribution":
                arms["race_same_stock"]["gate_fire_distribution"],
            "stray_after_completion_effect_inert":
                stray["gate_fires_total"] == 1,
            "stray_after_completion_disposition":
                ("loud:" + stray["stray_resume"]["error_type"]
                 if stray.get("stray_resume", {}).get("error_type")
                 else "silent"),
        },
    }
    print(json.dumps(result, indent=2, default=str))
    out = Path(os.environ.get(
        "PROBE165_OUT",
        Path(__file__).resolve().parents[1] / "results" / "multiproc"))
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_pg" if args.pg_dsn else ""
    (out / f"165_results{suffix}.json").write_text(
        json.dumps(result, indent=2, default=str) + "\n")
    (out / f"165_stable{suffix}.json").write_text(json.dumps(
        {"probe": result["probe"], "host": result["host"],
         "utc": result["utc"], "backend": result["backend"],
         "pins": result["pins"], "gate_design": result["gate_design"],
         "stable": result["stable"]}, indent=2) + "\n")
    print(f"\nwrote {out}/165_results{suffix}.json and 165_stable{suffix}.json",
          file=sys.stderr)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "parent"
    if mode in ("park", "racer", "single"):
        ctx = json.loads(os.environ["PROBE165_CTX"])
        {"park": park_main, "racer": racer_main,
         "single": single_main}[mode](ctx)
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--reps", type=int, default=10)
        ap.add_argument("--smoke", action="store_true")
        ap.add_argument("--pg-dsn", default=os.environ.get("PROBE_PG_DSN"))
        args = ap.parse_args()
        if args.smoke:
            args.reps = 3
        parent_main(args)
