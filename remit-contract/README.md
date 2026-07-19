# remit-contract

**REMIT** — Rust-core enforcement of the **Resume Contract** for LLM-agent
checkpoint, interrupt, and resume machinery, with a decision-free
[LangGraph](https://github.com/langchain-ai/langgraph) checkpointer shim.

Companion package of the paper *"Resume Means Resume: A Conformance Contract
for Checkpoint, Interrupt, and Resume Semantics in LLM-Agent Frameworks"*
(paper artifact: [`resume-contract-paper`](https://github.com/sajjadanwar0/resume-contract-paper)).

```bash
pip install remit-contract
```

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
│ Verus abstract model · 11 + 4 lemmas, 0 errors             │  machine-checked
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
cargo test -p remit-core          # 16 Rust tests
REMIT_MODEL_CASES=20000 cargo test -p remit-core --release
pytest tests/                     # bindings + LangGraph integration
```

The Rust workspace builds on rustc ≥ 1.75 (Ubuntu 24.04's distribution
toolchain); the test suite has zero external Rust dependencies.

## License

MIT © 2026 Sajjad Khan
