"""REMIT — Rust-core enforcement of the Resume Contract.

The Resume Contract fixes six obligations on checkpoint/interrupt/resume
machinery in LLM-agent frameworks — prefix continuation (PC), effect
exactly-once (EO), fork determinism (FD), checkpoint validity (CV),
consume-once (CO), and recovery determinism (RD) — plus the fork-intent
discriminator FI. This package ships:

* ``remit._core`` — a PyO3 extension module over ``remit-core``, the Rust
  production twin of the paper's Verus-verified abstract model. Every
  contract decision (effect admission, commit gating, fork resolution,
  sequencing, recovery) is taken inside Rust.
* ``remit.langgraph_shim`` — a decision-free checkpointer veneer for
  LangGraph: it asks the core what to do (strip or keep recorded resume
  writes, raise or delegate on validity) and does exactly that. The veneer
  contains no branch on contract semantics of its own.

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
"""

from remit._core import (
    Core,
    RemitDuplicateEffect,
    RemitError,
    RemitOrderViolation,
    RemitPrefixViolation,
    RemitValidityError,
    fork_view,
    recover_from_log,
)
from remit.langgraph_shim import (
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
    "fork_view",
    "recover_from_log",
    "RESUME_CHANNEL",
    "RemitSaverMixin",
    "saver_class",
    "wrap",
]

__version__ = "0.1.0"
