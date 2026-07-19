"""Decision-free LangGraph checkpointer veneer over the REMIT Rust core.

Design rule, stated once and enforced by review: **this module makes no
contract decision.** Its job is mechanical:

* ``get_tuple``/``aget_tuple`` — describe the invocation's addressing to the
  core (:func:`remit._core.fork_view`) and apply the returned verdict:
  ``"strip"`` removes recorded ``__resume__`` pending writes from the loaded
  checkpoint view so a fork-intent invocation consults its own supplied
  value (the read-path repair of LangGraph #6663, probe 134 of the paper's
  artifact); ``"keep"`` leaves the tuple untouched, preserving
  ordinary-address replay idempotence and consume-once.
* ``put``/``aput`` — report the user validator's answer to the core's
  validity gate; the core journals it and, when the answer is "invalid",
  raises :class:`remit._core.RemitValidityError` *before* anything is
  delegated to the inner saver. Nothing invalid is ever persisted, and the
  rejection is loud (contrast the silent persistence of schema-invalid
  state, LangGraph #6491 class, probe 123).
* ``put_writes``/``aput_writes`` — journal the submission in the core's
  per-thread sequencer so the durable order of persistence operations is a
  total order recoverable from the journal (the substrate RD relies on;
  contrast LangGraph #8039).

The veneer works by dynamic subclassing of any ``BaseCheckpointSaver``
implementation — the same interposition point probe 134 used with
``SqliteSaver`` — so the inner saver's own storage behavior, version
scheme, and serializer are inherited unchanged. This module deliberately
imports nothing from ``langgraph``: it only subclasses what you hand it.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Type

from remit import _core

#: LangGraph's resume channel: recorded ``Command(resume=...)`` payloads
#: appear as pending writes on this channel of the interrupt checkpoint.
RESUME_CHANNEL = "__resume__"

Validator = Callable[[dict], None]


def _thread_of(config: Optional[dict]) -> str:
    conf = (config or {}).get("configurable", {}) or {}
    return str(conf.get("thread_id", ""))


class RemitSaverMixin:
    """Mixin installing REMIT's core-decided behavior on any saver class.

    Keyword arguments consumed by the mixin (all others pass through to the
    inner saver's ``__init__``):

    ``remit_validator``
        Optional callable receiving the checkpoint ``dict``; raise to mark
        the state schema-invalid. The raise is reported to the core, which
        answers with a loud :class:`RemitValidityError` before persistence.
    ``remit_core``
        Optional shared :class:`remit._core.Core`; one is created per saver
        otherwise.
    ``remit_fork_on_explicit_checkpoint``
        Treat an explicit ``checkpoint_id`` in the invocation config as fork
        intent (LangGraph's documented branch-creating address — the
        contract's Definition 2, clause 3). Default ``True``. Set ``False``
        in deployments where subgraph plumbing supplies checkpoint ids on
        the ordinary path, and use the fork flag instead.
    ``remit_fork_flag_key``
        Name of an explicit boolean fork flag in ``configurable`` (the
        contract's Definition 2, clause 2 discriminator). Default
        ``"remit_fork"``.
    """

    def __init__(
        self,
        *args: Any,
        remit_validator: Optional[Validator] = None,
        remit_core: Optional[_core.Core] = None,
        remit_fork_on_explicit_checkpoint: bool = True,
        remit_fork_flag_key: str = "remit_fork",
        **kwargs: Any,
    ) -> None:
        self._remit_validator = remit_validator
        self._remit_core = remit_core if remit_core is not None else _core.Core()
        self._remit_fork_on_explicit = remit_fork_on_explicit_checkpoint
        self._remit_fork_flag_key = remit_fork_flag_key
        super().__init__(*args, **kwargs)

    # -- core access -------------------------------------------------------

    @property
    def remit_core(self) -> _core.Core:
        """The Rust core taking every contract decision for this saver."""
        return self._remit_core

    # -- read path: FI / FD (probe-134 rule, decided by the core) ----------

    def _remit_fork_filter(self, config: Optional[dict], t: Any) -> Any:
        if t is None:
            return t
        conf = (config or {}).get("configurable", {}) or {}
        explicit = bool(conf.get("checkpoint_id")) and self._remit_fork_on_explicit
        flag = bool(conf.get(self._remit_fork_flag_key))
        pending = list(getattr(t, "pending_writes", None) or [])
        has_recorded = any(len(w) > 1 and w[1] == RESUME_CHANNEL for w in pending)
        verdict = _core.fork_view(explicit, flag, has_recorded)
        if verdict == "strip":
            kept = [w for w in pending if not (len(w) > 1 and w[1] == RESUME_CHANNEL)]
            t = t._replace(pending_writes=kept)
        return t

    def get_tuple(self, config: dict) -> Any:  # type: ignore[override]
        return self._remit_fork_filter(config, super().get_tuple(config))  # type: ignore[misc]

    async def aget_tuple(self, config: dict) -> Any:  # type: ignore[override]
        return self._remit_fork_filter(config, await super().aget_tuple(config))  # type: ignore[misc]

    # -- write path: CV gate + RD sequencing (decided by the core) ---------

    def _remit_gate(self, config: Optional[dict], checkpoint: Any) -> str:
        thread = _thread_of(config)
        if self._remit_validator is None:
            ok, reason = True, ""
        else:
            try:
                self._remit_validator(checkpoint)
                ok, reason = True, ""
            except Exception as exc:  # the validator answered "invalid"
                ok, reason = False, f"{type(exc).__name__}: {exc}"
        # The *decision* — invalid ⇒ loud error, nothing persisted — is the
        # core's; RemitValidityError propagates from Rust when ok is False.
        self._remit_core.validity_gate(thread, ok, reason)
        return thread

    def put(self, config: dict, checkpoint: Any, metadata: Any, new_versions: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        thread = self._remit_gate(config, checkpoint)
        ckpt_id = str((checkpoint or {}).get("id", "")) if isinstance(checkpoint, dict) else ""
        self._remit_core.sequence_op(thread, "put", ckpt_id)
        return super().put(config, checkpoint, metadata, new_versions, *args, **kwargs)  # type: ignore[misc]

    async def aput(self, config: dict, checkpoint: Any, metadata: Any, new_versions: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        thread = self._remit_gate(config, checkpoint)
        ckpt_id = str((checkpoint or {}).get("id", "")) if isinstance(checkpoint, dict) else ""
        self._remit_core.sequence_op(thread, "put", ckpt_id)
        return await super().aput(config, checkpoint, metadata, new_versions, *args, **kwargs)  # type: ignore[misc]

    def put_writes(self, config: dict, writes: Any, task_id: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self._remit_core.sequence_op(_thread_of(config), "put_writes", str(task_id))
        return super().put_writes(config, writes, task_id, *args, **kwargs)  # type: ignore[misc]

    async def aput_writes(self, config: dict, writes: Any, task_id: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self._remit_core.sequence_op(_thread_of(config), "put_writes", str(task_id))
        return await super().aput_writes(config, writes, task_id, *args, **kwargs)  # type: ignore[misc]


_CLASS_CACHE: Dict[type, type] = {}


def saver_class(inner_cls: Type) -> Type:
    """The REMIT-wrapped subclass of ``inner_cls`` (cached)."""
    cls = _CLASS_CACHE.get(inner_cls)
    if cls is None:
        cls = type(f"Remit{inner_cls.__name__}", (RemitSaverMixin, inner_cls), {})
        _CLASS_CACHE[inner_cls] = cls
    return cls


def wrap(
    inner_cls: Type,
    *args: Any,
    validator: Optional[Validator] = None,
    core: Optional[_core.Core] = None,
    fork_on_explicit_checkpoint: bool = True,
    fork_flag_key: str = "remit_fork",
    **kwargs: Any,
) -> Any:
    """Construct a REMIT-wrapped instance of ``inner_cls``.

    ``wrap(InMemorySaver)`` or ``wrap(SqliteSaver, conn, validator=v)`` —
    positional and keyword arguments other than the REMIT ones go to the
    inner saver's constructor unchanged.
    """
    cls = saver_class(inner_cls)
    return cls(
        *args,
        remit_validator=validator,
        remit_core=core,
        remit_fork_on_explicit_checkpoint=fork_on_explicit_checkpoint,
        remit_fork_flag_key=fork_flag_key,
        **kwargs,
    )
