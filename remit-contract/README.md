# remit-contract

[![PyPI](https://img.shields.io/pypi/v/remit-contract)](https://pypi.org/project/remit-contract/)
[![Python](https://img.shields.io/pypi/pyversions/remit-contract)](https://pypi.org/project/remit-contract/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**REMIT** — Rust-core enforcement of the **Resume Contract** for LLM-agent
checkpoint, interrupt, and resume machinery, with a decision-free
[LangGraph](https://github.com/langchain-ai/langgraph) checkpointer shim.

Companion package of the paper *"Resume Means Resume: A Conformance Contract
for Checkpoint, Interrupt, and Resume Semantics in LLM-Agent Frameworks"*
(paper artifact: [`resume-contract-paper`](https://github.com/sajjadanwar0/resume-contract-paper)).

```bash
pip install remit-contract
```

Wheels ship as `abi3` for CPython >= 3.9 on x86_64 manylinux2014; every
other platform builds from the sdist (needs Rust >= 1.83; on Ubuntu 24.04
that is still the distribution toolchain: `apt install rustc-1.83 cargo-1.83`).

## What it enforces

The Resume Contract fixes six framework-independent obligations, each named
after the production failure it excludes:

| Property | Obligation | Failure it excludes |
|---|---|---|
| **PC** | prefix continuation | re-running completed work after resume |
| **EO** | effect exactly-once | double-charged tools across crash/resume |
| **FD** | fork determinism | a fork served the *previous* resume's value (LangGraph **#6663**) |
| **CV** | checkpoint validity | schema-invalid state persisted silently (**#6491** class) |
| **CO** | consume-once | a stray duplicate resume re-firing gated effects — and, with the opt-in gate below, two *processes* consuming one parked interrupt |
| **RD** | recovery determinism | recovery dependent on racy durable-write order (**#8039**) |

plus **FI** (fork-intent expressibility): the wire carries a discriminator
separating "retry" from "fork", without which FD and CO are jointly
unsatisfiable (Proposition 1 of the paper).

## Architecture — where decisions live

```
┌────────────────────────────────────────────────────────────┐
│ TLA+ spec (ResumeContract.tla) · TLC R0–R8                 │  machine-checked
│ Verus suite · 35 spec + 18 exec items, 0 errors            │  machine-checked
├────────────────────────────────────────────────────────────┤
│ remit-core (Rust)                                          │  this package
│   effect ledger · commit gate · fork resolution ·          │  mirrors the model
│   sequencer/journal · pure recovery · consumption verdicts │  item-for-item
├────────────────────────────────────────────────────────────┤
│ remit-py (PyO3) → remit._core                              │  type translation
├────────────────────────────────────────────────────────────┤
│ remit.langgraph_shim (Python)                              │  decision-free
│   asks the core; strips/keeps, raises/delegates,           │  veneer
│   claims/refuses                                           │
└────────────────────────────────────────────────────────────┘
```

Every contract decision — may this effect fire? is this state persistable?
is this invocation a fork? should this read claim consumption, and did it
win? what does recovery do? — is taken in Rust, in code that mirrors the
Verus-verified abstract model function for function (`VERIFICATION.md`
tabulates the correspondence). The Python layers translate types and apply
verdicts; they contain no branch on contract semantics. No mechanized
refinement between the Verus model and the Rust core is claimed; what is
claimed, and checkable, is the structural mirror, executable conformance of
the core to the model's transition relation under a seeded randomized
harness (20 000 sequences in CI, six invariants re-checked after every
action), and a concurrent stress suite.

## LangGraph quick start

```python
from langgraph.checkpoint.memory import InMemorySaver
import remit

saver = remit.wrap(InMemorySaver)          # fork-safe checkpointer
graph = builder.compile(checkpointer=saver)
```

The wrapped saver repairs the fork cell on the probe-134 protocol: a second
`Command(resume=...)` addressed to the interrupt checkpoint is served **its
own** value on a fresh branch, instead of silently receiving the first
resume's recorded value. Ordinary-address resumes (retry, replay, stray
re-delivery) are byte-identical to the stock saver — replay idempotence and
consume-once are untouched.

With a state validator, CV becomes loud:

```python
def validate(checkpoint: dict) -> None:
    ...  # raise on schema violation

saver = remit.wrap(SqliteSaver, conn, validator=validate)
# an invalid state now raises remit.RemitValidityError *before* persistence
```

Deployments where subgraph plumbing puts `checkpoint_id` on the ordinary
path should key fork intent on the explicit flag instead:

```python
saver = remit.wrap(InMemorySaver, fork_on_explicit_checkpoint=False)
graph.invoke(Command(resume=v),
             {"configurable": {"thread_id": t, "checkpoint_id": c,
                               "remit_fork": True}})
```

## Cross-process consume-once (opt-in gate, v0.1.2)

Stock LangGraph duplicates gated effects when **two OS processes** resume
one parked interrupt — 10/10 on both durable backends in the paper's
measurements, and per-process interposition cannot repair it. The gate
puts the consumption record where it must live, in the shared store:

```python
saver = remit.wrap(SqliteSaver, conn, cross_process_gate=True)
# k processes resuming one parked interrupt: exactly one is served;
# every other raises remit.RemitConsumeConflict inside get_tuple,
# BEFORE any node executes.
```

Mechanics: the first gated read of a parked checkpoint takes a
`(thread, checkpoint)` claim with one `INSERT` under a uniqueness
constraint in a `remit_claims` table in the saver's **own database**
(SQLite primary key / Postgres `ON CONFLICT DO NOTHING`) — a genuine
cross-process compare-and-swap. The attempt/pass and serve/refuse
decisions are the Rust core's (`consume_view`, `consume_claim_check`).
Measured on the paper's probe: gated race arms fire `{1:10}` on both
durable backends with one typed refusal per rep; the ungated default is
unchanged and still races (`{2:10}`), by design.

Scope, stated plainly: **opt-in** (default wrap is bit-for-bit pre-gate);
synchronous savers exposing `.conn` (SQLite / Postgres, autocommit);
inspection reads must declare themselves —

```python
saver.get_tuple({"configurable": {"thread_id": t, "remit_inspect": True}})
# a bare read of a parked thread would consume the claim; this includes
# graph.get_state(...) — pass remit_inspect there too
```

— and fork-addressed deliveries bypass the gate (they claim a fresh
branch through FD instead). Two processes racing the very first claim on
a fresh database can race the table creation itself; Postgres's catalog
refusal (SQLSTATE `42P07`/`23505`) is absorbed as table-exists and the
claim proceeds.

## Using the core directly

```python
from remit import Core, RemitDuplicateEffect

core = Core()
core.begin_effect("run-1", task=1, effect_id="charge")   # admitted, seq 0
try:
    core.begin_effect("run-1", task=1, effect_id="charge")
except RemitDuplicateEffect:
    pass                                                  # EO: refused

core.commit_checkpoint("run-1", task=1, state=b"...")     # PC + CV gate
core.recover("run-1")                                     # -> 2 (pure, RD)
```

## Building from source

```bash
./BUILD_AND_TEST.sh               # the one-shot gate (uv-native)
# or piecewise:
pip install maturin
maturin build --release           # wheel in target/wheels/
cargo test -p remit-core          # 19 Rust tests
REMIT_MODEL_CASES=20000 cargo test -p remit-core --release
pytest tests/                     # bindings + LangGraph + gate
REMIT_TEST_PG_DSN="postgresql://..." pytest tests/   # + live-Postgres cells
```

The Rust workspace builds on rustc ≥ 1.83 (pyo3 0.29's MSRV; still the
Ubuntu 24.04 distribution toolchain via the versioned packages
`rustc-1.83`/`cargo-1.83` --- no rustup needed); the test suite has zero
external Rust dependencies.

## Verification status

| What | Checker | Status |
|---|---|---|
| Abstract model (10 lemmas) + companion files (2 + 12 + 5 + 6) | Verus 0.2026.05.03.8b81855 | 35 items, 0 errors |
| Executable decision cores (recover 7, ledger 11) | Verus, exec mode | 18 items, 0 errors; recover body **line-identical** to `remit-core` (byte-level CI sync gate) |
| Negative certificates | Verus | each fails in the expected `2 verified, 1 errors` shape — the lemmas are falsifiable, not vacuous |
| Core ↔ model transition conformance | seeded randomized harness | 20 000 sequences, six invariants re-checked after every action |
| Rust core | `cargo test -p remit-core` | 19 tests, incl. 32-thread contention suites and the consume-gate decision tables |
| Bindings + LangGraph repair + gate | `pytest tests/` | 15 + 18 tests at the pins below (+ 2 live-Postgres cells, env-gated on `REMIT_TEST_PG_DSN`) |
| Cross-process gate, live | paper artifact probe 159, arms E/F | `{1:10}` on both durable backends, loser `RemitConsumeConflict` before any node, every rep; ungated arm retained at `{2:10}` as the differential |

No mechanized refinement between the Verus model and the compiled core is
claimed; the PyO3 boundary and the Python veneer are tested, not proved.
`VERIFICATION.md` tabulates the lemma-to-function correspondence so the
mirror can be audited rather than trusted.

## Tested pins

| Package | Version |
|---|---|
| `langgraph` | 1.2.9 |
| `langgraph-checkpoint` | 4.1.1 |
| `langgraph-checkpoint-sqlite` | 3.1.0 |
| Postgres cells (env-gated) | `langgraph-checkpoint-postgres` + `psycopg`, via `REMIT_TEST_PG_DSN` |
| Python | 3.9 – 3.12 (abi3), CI on 3.12 |
| rustc (from-source builds) | ≥ 1.83 (Ubuntu 24.04: `rustc-1.83` package) |

## Citation

```bibtex
@software{remit_contract,
  author  = {Khan, Sajjad},
  title   = {remit-contract: Rust-core enforcement of the Resume Contract
             for LLM-agent checkpoint, interrupt, and resume semantics},
  year    = {2026},
  url     = {https://github.com/sajjadanwar0/remit-contract},
  version = {0.1.2}
}
```

The companion paper (*"Resume Means Resume"*) is under submission; its
artifact lives at
[`resume-contract-paper`](https://github.com/sajjadanwar0/resume-contract-paper).

## Changelog

**0.1.2** — absorb the fresh-database DDL race: two processes' first
claims can race `CREATE TABLE IF NOT EXISTS` itself, and Postgres refuses
one creator at the catalog (SQLSTATE `42P07`/`23505`) before the
IF NOT EXISTS check settles — caught live by the paper's probe 159 on a
fresh database under 0.1.1. The shim now treats exactly those two codes
as the table-exists postcondition and proceeds to the claim; regression
test G9.

**0.1.1** — cross-process consume-once gate (opt-in
`cross_process_gate=True`): probe 165's read-path repair promoted into
the shipped shim. `(thread, checkpoint)` claim in a `remit_claims` table
in the saver's own database; attempt/pass and serve/refuse decided in
Rust (`consume_view`, `consume_claim_verdict`); loser refused with typed
`RemitConsumeConflict` inside `get_tuple`, before any node. Read-intent
opt-out `remit_inspect`; fork-intent bypass; loud refusal of
connection-less savers, async connections, and non-autocommit Postgres.
Known defect fixed in 0.1.2: first-use DDL race on a fresh Postgres
database.

**0.1.0** — initial release: Rust core (effect ledger, commit gate, fork
resolution, sequencer/journal, pure recovery), PyO3 bindings, decision-free
LangGraph checkpointer shim (fork repair on the probe-134 protocol; loud CV
via user validators), verification chain as tabulated above.

## License

MIT © 2026 Sajjad Khan
