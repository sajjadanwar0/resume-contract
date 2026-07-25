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
other platform builds from the sdist (needs Rust >= 1.75, nothing else).

## What it enforces

The Resume Contract fixes six framework-independent obligations, each named
after the production failure it excludes:

| Property | Obligation | Failure it excludes |
|---|---|---|
| **PC** | prefix continuation | re-running completed work after resume |
| **EO** | effect exactly-once | double-charged tools across crash/resume |
| **FD** | fork determinism | a fork served the *previous* resume's value (LangGraph **#6663**) |
| **CV** | checkpoint validity | schema-invalid state persisted silently (**#6491** class) |
| **CO** | consume-once | a stray duplicate resume re-firing gated effects |
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
│   sequencer/journal · pure recovery                        │  item-for-item
├────────────────────────────────────────────────────────────┤
│ remit-py (PyO3) → remit._core                              │  type translation
├────────────────────────────────────────────────────────────┤
│ remit.langgraph_shim (Python)                              │  decision-free
│   asks the core; strips/keeps, raises/delegates            │  veneer
└────────────────────────────────────────────────────────────┘
```

Every contract decision — may this effect fire? is this state persistable?
is this invocation a fork? what does recovery do? — is taken in Rust, in
code that mirrors the Verus-verified abstract model function for function
(`VERIFICATION.md` tabulates the correspondence). The Python layers
translate types and apply verdicts; they contain no branch on contract
semantics. No mechanized refinement between the Verus model and the Rust
core is claimed; what is claimed, and checkable, is the structural mirror,
executable conformance of the core to the model's transition relation under
a seeded randomized harness (20 000 sequences in CI, six invariants
re-checked after every action), and a concurrent stress suite.

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
pip install maturin
maturin build --release           # wheel in target/wheels/
cargo test -p remit-core          # 17 Rust tests
REMIT_MODEL_CASES=20000 cargo test -p remit-core --release
pytest tests/                     # bindings + LangGraph integration
```

The Rust workspace builds on rustc ≥ 1.75 (Ubuntu 24.04's distribution
toolchain); the test suite has zero external Rust dependencies.

## Verification status

| What | Checker | Status |
|---|---|---|
| Abstract model (10 lemmas) + companion files (2 + 12 + 5 + 6) | Verus 0.2026.05.03.8b81855 | 35 items, 0 errors |
| Executable decision cores (recover 7, ledger 11) | Verus, exec mode | 18 items, 0 errors; recover body **line-identical** to `remit-core` (byte-level CI sync gate) |
| Negative certificates | Verus | each fails in the expected `2 verified, 1 errors` shape — the lemmas are falsifiable, not vacuous |
| Core ↔ model transition conformance | seeded randomized harness | 20 000 sequences, six invariants re-checked after every action |
| Rust core | `cargo test -p remit-core` | 17 tests, incl. 32-thread contention suites |
| Bindings + LangGraph repair | `pytest tests/` | 15 + 8 tests at the pins below |

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
| Python | 3.9 – 3.12 (abi3), CI on 3.12 |
| rustc (from-source builds) | ≥ 1.75 |

## Citation

```bibtex
@software{remit_contract,
  author  = {Khan, Sajjad},
  title   = {remit-contract: Rust-core enforcement of the Resume Contract
             for LLM-agent checkpoint, interrupt, and resume semantics},
  year    = {2026},
  url     = {https://github.com/sajjadanwar0/remit-contract},
  version = {0.1.0}
}
```

The companion paper (*"Resume Means Resume"*) is under submission; its
artifact lives at
[`resume-contract-paper`](https://github.com/sajjadanwar0/resume-contract-paper).

## Changelog

**0.1.0** — initial release: Rust core (effect ledger, commit gate, fork
resolution, sequencer/journal, pure recovery), PyO3 bindings, decision-free
LangGraph checkpointer shim (fork repair on the probe-134 protocol; loud CV
via user validators), verification chain as tabulated above.

## License

MIT © 2026 Sajjad Khan
