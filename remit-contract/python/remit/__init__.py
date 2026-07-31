"""REMIT — Rust-core enforcement of the Resume Contract.

The Resume Contract fixes six obligations on checkpoint/interrupt/resume
machinery in LLM-agent frameworks — prefix continuation (PC), effect
exactly-once (EO), fork determinism (FD), checkpoint validity (CV),
consume-once (CO), and recovery determinism (RD) — plus the fork-intent
discriminator FI. This package ships:

* ``remit._core`` — a PyO3 extension module over ``remit-core``, the Rust
  production twin of the paper's Verus-verified abstract model. Every
  contract decision (effect admission, commit gating, fork resolution,
  consumption gating, sequencing, recovery) is taken inside Rust.
* ``remit.langgraph_shim`` — a decision-free checkpointer veneer for
  LangGraph: it asks the core what to do (strip or keep recorded resume
  writes, raise or delegate on validity, attempt or pass on the
  cross-process consumption claim, serve or refuse on its outcome) and
  does exactly that. The veneer contains no branch on contract semantics
  of its own.

Quick start (LangGraph)::

    from langgraph.checkpoint.memory import InMemorySaver
    import remit

    saver = remit.wrap(InMemorySaver)          # fork-safe checkpointer
    graph = builder.compile(checkpointer=saver)

With a state validator (CV made loud)::

    def validate(checkpoint: dict) -> None:
        # raise on schema violation; REMIT turns the raise into a
        # RemitValidityError *before* anything is persisted
        ...

    saver = remit.wrap(SqliteSaver, conn, validator=validate)

With the cross-process consume-once gate (CO across processes; the
probe-165 read-path repair, opt-in)::

    saver = remit.wrap(SqliteSaver, conn, cross_process_gate=True)
    # k processes resuming one parked interrupt: exactly one is served;
    # every other raises RemitConsumeConflict before any node executes.
    # Inspection reads pass {"configurable": {..., "remit_inspect": True}}.
"""

from remit._core import (
    Core,
    RemitConsumeConflict,
    RemitDuplicateEffect,
    RemitError,
    RemitOrderViolation,
    RemitPrefixViolation,
    RemitValidityError,
    consume_claim_check,
    consume_view,
    fork_view,
    recover_from_log,
)
from remit.langgraph_shim import (
    CLAIMS_TABLE,
    INTERRUPT_CHANNEL,
    RESUME_CHANNEL,
    RemitSaverMixin,
    saver_class,
    wrap,
)

__all__ = [
    "Core",
    "RemitError",
    "RemitDuplicateEffect",
    "RemitPrefixViolation",
    "RemitValidityError",
    "RemitOrderViolation",
    "RemitConsumeConflict",
    "fork_view",
    "consume_view",
    "consume_claim_check",
    "recover_from_log",
    "RESUME_CHANNEL",
    "INTERRUPT_CHANNEL",
    "CLAIMS_TABLE",
    "RemitSaverMixin",
    "saver_class",
    "wrap",
]

__version__ = "0.1.2"
