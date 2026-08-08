#!/usr/bin/env python3
import argparse, json, os, socket, sqlite3, statistics, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from importlib.metadata import version

REQUIRED_LANGGRAPH = "1.2.9"

def _fail(msg):
    print(json.dumps({"probe_refused": msg, "interpreter": sys.executable}), file=sys.stderr)
    sys.exit(3)

_lg = version("langgraph")
if _lg != REQUIRED_LANGGRAPH and not os.environ.get("PROBE_ALLOW_OFFPIN"):
    _fail(f"langgraph=={_lg} but the paper pin is {REQUIRED_LANGGRAPH}. "
          f"This interpreter is {sys.executable}; launch with the repo venv's "
          f"absolute python (e.g. /path/to/repo/.venv/bin/python3), or set "
          f"PROBE_ALLOW_OFFPIN=1 for a non-paper exploratory run.")

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver

try:
    from remit import langgraph_shim as _rls
    HAVE_REMIT = True
except Exception as _e:
    HAVE_REMIT = False
    _REMIT_ERR = f"{type(_e).__name__}: {_e}"

class S(TypedDict):
    val: str

LEDGER, LLOCK = {}, threading.Lock()

def build(saver):
    def gate(state: S):
        pid = state["val"]
        ans = interrupt("decision?")
        with LLOCK:
            LEDGER[pid] = LEDGER.get(pid, 0) + 1
        return {"val": str(ans)}
    g = StateGraph(S)
    g.add_node("gate", gate)
    g.add_edge(START, "gate"); g.add_edge("gate", END)
    return g.compile(checkpointer=saver)

def protocol(app, i):
    cfg = {"configurable": {"thread_id": f"t{i}"}}
    t0 = time.perf_counter()
    r = app.invoke({"val": str(i)}, cfg)
    assert "__interrupt__" in r
    app.invoke(Command(resume=True), cfg)
    return time.perf_counter() - t0

def sqlite_factory(wrap):
    def make(tag):
        db = tempfile.mktemp(suffix=f"_{tag}.sqlite")
        conn = sqlite3.connect(db, check_same_thread=False)
        saver = _rls.wrap(SqliteSaver, conn) if wrap else SqliteSaver(conn)
        def cleanup():
            conn.close(); os.path.exists(db) and os.unlink(db)
        return saver, cleanup
    return make

def pg_factory(dsn, wrap):
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver
    def make(tag):
        dbname = f"bench157_{tag}"
        admin = psycopg.connect(dsn, autocommit=True)
        admin.execute(f"DROP DATABASE IF EXISTS {dbname}")
        admin.execute(f"CREATE DATABASE {dbname}")
        admin.close()
        base = dsn.rsplit("/", 1)[0]
        conn = psycopg.connect(f"{base}/{dbname}", autocommit=True)
        saver = _rls.wrap(PostgresSaver, conn) if wrap else PostgresSaver(conn)
        saver.setup()
        def cleanup():
            conn.close()
            adm = psycopg.connect(dsn, autocommit=True)
            adm.execute(f"DROP DATABASE IF EXISTS {dbname}")
            adm.close()
        return saver, cleanup
    return make

def run_arm(name, make, ks, n):
    out = {}
    for k in ks:
        LEDGER.clear()
        saver, cleanup = make(f"{name}_{k}")
        try:
            app = build(saver)
            lat, t0 = [], time.perf_counter()
            with ThreadPoolExecutor(max_workers=k, thread_name_prefix=f"w{k}") as ex:
                for dt in ex.map(lambda i: protocol(app, i), range(n)):
                    lat.append(dt)
            wall = time.perf_counter() - t0
        finally:
            cleanup()
        qs = statistics.quantiles(lat, n=100)
        out[k] = {"n": n, "p50_ms": round(qs[49]*1000, 2),
                  "p95_ms": round(qs[94]*1000, 2), "p99_ms": round(qs[98]*1000, 2),
                  "throughput_per_s": round(n / wall, 2),
                  "total_gate_effects": sum(LEDGER.values()),
                  "eo_total_matches_n": sum(LEDGER.values()) == n,
                  "eo_per_protocol_exactly_once": (
                      len(LEDGER) == n and all(v == 1 for v in LEDGER.values())),
                  "protocols_with_duplicate_effect": sorted(
                      pid for pid, v in LEDGER.items() if v != 1)}
        print(f"{name} k={k}: p50={out[k]['p50_ms']}ms p95={out[k]['p95_ms']}ms "
              f"thru={out[k]['throughput_per_s']}/s "
              f"eo_per_protocol={out[k]['eo_per_protocol_exactly_once']}", file=sys.stderr)
    return out

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", nargs="+", type=int, default=[1, 4, 16, 64])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--stock-only", action="store_true",
                    help="permit a run without the remit shim arm (non-paper)")
    ap.add_argument("--pg-dsn", default=os.environ.get("PROBE_PG_DSN", ""),
                    help="add Postgres arms; superuser/CREATEDB-capable DSN, "
                         "e.g. postgresql://user:pass@localhost:5432/postgres")
    a = ap.parse_args()
    if a.smoke: a.k, a.n = [2], 3
    if not HAVE_REMIT and not a.stock_only:
        _fail("remit shim not importable from this interpreter "
              f"({_REMIT_ERR}). Paper runs need both arms: build the wheel "
              "into THIS venv (cd remit-contract && maturin develop --release) "
              "or pass --stock-only for an explicitly stock-only run.")
    R = {"langgraph": _lg, "pins_ok": _lg == REQUIRED_LANGGRAPH,
         "interpreter": sys.executable, "host": socket.gethostname(),
         "have_remit": HAVE_REMIT,
         "stock": run_arm("stock", sqlite_factory(wrap=False), a.k, a.n)}
    if HAVE_REMIT:
        R["remit"] = run_arm("remit", sqlite_factory(wrap=True), a.k, a.n)
    if a.pg_dsn:
        from langgraph.checkpoint import postgres as _pgmod
        R["langgraph_checkpoint_postgres"] = version("langgraph-checkpoint-postgres")
        R["stock_pg"] = run_arm("stockpg", pg_factory(a.pg_dsn, wrap=False), a.k, a.n)
        if HAVE_REMIT:
            R["remit_pg"] = run_arm("remitpg", pg_factory(a.pg_dsn, wrap=True), a.k, a.n)
    print(json.dumps(R, indent=2))
