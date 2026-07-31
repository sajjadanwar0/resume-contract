"""Decision-free LangGraph checkpointer veneer over the REMIT Rust core.

Design rule, stated once and enforced by review: **this module makes no
contract decision.** Its job is mechanical:

* ``get_tuple``/``aget_tuple`` — describe the invocation's addressing to the
  core (:func:`remit._core.fork_view`) and apply the returned verdict:
  ``"strip"`` removes recorded ``__resume__`` pending writes from the loaded
  checkpoint view so a fork-intent invocation consults its own supplied
  value (the read-path repair of LangGraph #6663, probe 134 of the paper's
  artifact); ``"keep"`` leaves the tuple untouched, preserving
  ordinary-address replay idempotence and consume-once. When the
  cross-process gate is enabled, the same read then describes the loaded
  tuple to :func:`remit._core.consume_view`; on ``"attempt"`` the veneer
  takes the ``(thread, checkpoint)`` claim in the saver's own store with one
  ``INSERT`` under a uniqueness constraint (SQLite primary key / Postgres
  ``ON CONFLICT DO NOTHING`` — the compare-and-swap of probe 165) and
  reports the outcome to :func:`remit._core.consume_claim_check`, which
  serves the winner and raises
  :class:`remit._core.RemitConsumeConflict` for the loser *before any node
  executes* (contrast the stock plane's cross-process double consumption,
  probe 159, 10/10 on both durable backends).
* ``put``/``aput`` — report the user validator's answer to the core's
  validity gate; the core journals it and, when the answer is "invalid",
  raises :class:`remit._core.RemitValidityError` *before* anything is
  delegated to the inner saver. Nothing invalid is ever persisted, and the
  rejection is loud (contrast the silent persistence of schema-invalid
  state, LangGraph #6491 class, probe 123).
* ``put_writes``/``aput_writes`` — journal the submission in the core's
  per-thread sequencer so the durable order of persistence operations is a
  total order recoverable from the journal (the substrate RD relies on;
  contrast LangGraph #8039). When the cross-process gate is enabled, a
  ``__resume__`` submission additionally re-takes the same claim as a
  secondary latch — idempotent for the winner, and a late loud stop for any
  path that reaches the journal without the read (the falsified write-path
  v1 of probe 165, retained exactly as the probe retains it: secondary,
  never primary, because interposition below the decision point cannot veto
  the decision).

Cross-process gate scope, stated plainly (mirrors probe 165):

* **Opt-in.** ``cross_process_gate`` defaults to ``False``; the default
  wrap is bit-for-bit the shipped v0.1.0 behavior.
* **Claim key and store.** ``(thread_id, checkpoint_id-of-the-loaded
  parked checkpoint)`` in a ``remit_claims`` table created lazily in the
  saver's own database — the shared store both racers already use, which
  is what makes the ``INSERT`` a cross-process compare-and-swap.
* **Read intent.** ``get_tuple`` carries no read-intent discriminator, so
  a bare inspection of a parked thread would consume the claim; pass
  ``{"configurable": {..., "remit_inspect": True}}`` on inspection reads.
  This is the read-path analogue of the FI gap, exposed as an explicit
  opt-out rather than left as an interface defect.
* **Fork intent.** A fork-addressed delivery (explicit ``checkpoint_id``
  under the default policy, or the fork flag) claims a fresh branch key
  through the FD machinery instead; the consumption gate stands aside
  (probe 155's composition).
* **Backends.** Synchronous savers exposing ``.conn`` — the measured
  ``SqliteSaver`` and ``PostgresSaver`` cells. Postgres connections must be
  ``autocommit=True`` (the configuration LangGraph's own ``setup()`` path
  and probes 159/165 use); the gate refuses a transactional connection
  loudly rather than entangling the claim with the caller's transaction.
  Async savers are refused loudly at first use, not silently degraded.

The veneer works by dynamic subclassing of any ``BaseCheckpointSaver``
implementation — the same interposition point probe 134 used with
``SqliteSaver`` — so the inner saver's own storage behavior, version
scheme, and serializer are inherited unchanged. This module deliberately
imports nothing from ``langgraph``: it only subclasses what you hand it.
"""

from __future__ import annotations

import inspect as _inspect
import sqlite3
from contextlib import nullcontext
from typing import Any, Callable, Dict, Optional, Set, Tuple, Type

from remit import _core

#: LangGraph's resume channel: recorded ``Command(resume=...)`` payloads
#: appear as pending writes on this channel of the interrupt checkpoint.
RESUME_CHANNEL = "__resume__"

#: LangGraph's interrupt channel: a parked run's consumable authority
#: appears as a pending write on this channel of the latest checkpoint
#: (paper Sec. 4.3; probe 165's primary interposition point).
INTERRUPT_CHANNEL = "__interrupt__"

#: The shared-store claims table taken by the cross-process gate.
CLAIMS_TABLE = "remit_claims"

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
    ``remit_cross_process_gate``
        Enable the cross-process consume-once gate (probe 165's read-path
        repair, promoted). Default ``False``; see the module docstring for
        the claim key, store, and scope.
    ``remit_inspect_key``
        Name of the boolean read-intent flag in ``configurable`` that
        exempts an inspection read from taking the consumption claim.
        Default ``"remit_inspect"``.
    """

    def __init__(
        self,
        *args: Any,
        remit_validator: Optional[Validator] = None,
        remit_core: Optional[_core.Core] = None,
        remit_fork_on_explicit_checkpoint: bool = True,
        remit_fork_flag_key: str = "remit_fork",
        remit_cross_process_gate: bool = False,
        remit_inspect_key: str = "remit_inspect",
        **kwargs: Any,
    ) -> None:
        self._remit_validator = remit_validator
        self._remit_core = remit_core if remit_core is not None else _core.Core()
        self._remit_fork_on_explicit = remit_fork_on_explicit_checkpoint
        self._remit_fork_flag_key = remit_fork_flag_key
        self._remit_gate_enabled = bool(remit_cross_process_gate)
        self._remit_inspect_key = remit_inspect_key
        self._remit_claims_ready = False
        self._remit_claims_held: Set[Tuple[str, str]] = set()
        #: thread -> checkpoint id claimed at the read; lets the secondary
        #: latch re-take the *same* key idempotently for the winner instead
        #: of opening a second key. A journal-only path (no prior read)
        #: falls back to the "__latest__" domain — probe 165's stated
        #: secondary-latch scope: late, and never a substitute for the read.
        self._remit_last_claim: Dict[str, str] = {}
        super().__init__(*args, **kwargs)
        if self._remit_gate_enabled and getattr(self, "conn", None) is None:
            raise RuntimeError(
                "remit: cross_process_gate=True requires a saver exposing a "
                "database connection as `.conn` (SqliteSaver / PostgresSaver, "
                "the measured backends); "
                f"{type(self).__name__} exposes none"
            )

    # -- core access -------------------------------------------------------

    @property
    def remit_core(self) -> _core.Core:
        """The Rust core taking every contract decision for this saver."""
        return self._remit_core

    # -- addressing extraction (config fields only; no decisions) ----------

    def _remit_address(self, config: Optional[dict]) -> Tuple[bool, bool, bool]:
        conf = (config or {}).get("configurable", {}) or {}
        explicit = bool(conf.get("checkpoint_id")) and self._remit_fork_on_explicit
        flag = bool(conf.get(self._remit_fork_flag_key))
        inspect_intent = bool(conf.get(self._remit_inspect_key))
        return explicit, flag, inspect_intent

    # -- read path: FI / FD (probe-134 rule, decided by the core) ----------

    def _remit_fork_filter(self, config: Optional[dict], t: Any) -> Any:
        if t is None:
            return t
        explicit, flag, _ = self._remit_address(config)
        pending = list(getattr(t, "pending_writes", None) or [])
        has_recorded = any(len(w) > 1 and w[1] == RESUME_CHANNEL for w in pending)
        verdict = _core.fork_view(explicit, flag, has_recorded)
        if verdict == "strip":
            kept = [w for w in pending if not (len(w) > 1 and w[1] == RESUME_CHANNEL)]
            t = t._replace(pending_writes=kept)
        return t

    # -- read path: CO across processes (probe-165 rule, decided by core) --

    def _remit_is_postgres_conn(self, conn: Any) -> bool:
        return type(conn).__module__.split(".", 1)[0] == "psycopg"

    def _remit_claims_ddl(self, conn: Any, is_pg: bool) -> None:
        if self._remit_claims_ready:
            return
        ddl = (
            f"CREATE TABLE IF NOT EXISTS {CLAIMS_TABLE} "
            "(thread TEXT NOT NULL, ckpt TEXT NOT NULL, "
            " PRIMARY KEY (thread, ckpt))"
        )
        try:
            conn.execute(ddl)
            if not is_pg:
                conn.commit()
        except Exception as exc:
            # Two processes' FIRST claims on a fresh database can race the
            # DDL itself: Postgres enforces catalog uniqueness before the
            # IF NOT EXISTS check settles and refuses one creator with
            # SQLSTATE 42P07 (duplicate_table) or 23505 (unique_violation
            # on the pg_type row) -- measured live at probe 159's PG rep 0
            # on a fresh database, 2026-07-31. Either code means the table
            # exists, which is exactly this function's postcondition;
            # absorb it and proceed to the claim (autocommit keeps the
            # connection usable after the refused statement).
            if getattr(exc, "sqlstate", None) not in ("42P07", "23505"):
                raise
        self._remit_claims_ready = True

    def _remit_take_claim(self, thread: str, ckpt: str) -> bool:
        """One ``INSERT`` under a uniqueness constraint in the saver's own
        store — the cross-process compare-and-swap. Idempotent per saver
        instance (the winner's in-process latch); returns whether this
        instance holds the claim. Raises nothing itself: the serve/refuse
        decision on the outcome belongs to the core
        (:func:`remit._core.consume_claim_check`)."""
        key = (str(thread), str(ckpt))
        if key in self._remit_claims_held:
            return True
        conn = self.conn  # presence checked at construction
        exec_ = getattr(conn, "execute", None)
        if exec_ is None or _inspect.iscoroutinefunction(exec_):
            raise RuntimeError(
                "remit: cross_process_gate=True supports synchronous savers "
                "(SqliteSaver / PostgresSaver, the measured backends); async "
                "connections are not yet supported"
            )
        is_pg = self._remit_is_postgres_conn(conn)
        if is_pg and getattr(conn, "autocommit", True) is False:
            raise RuntimeError(
                "remit: cross_process_gate=True requires an autocommit "
                "Postgres connection (the configuration PostgresSaver.setup() "
                "and the paper's probes 159/165 use); a transactional "
                "connection would entangle the consumption claim with the "
                "caller's transaction"
            )
        lock = getattr(self, "lock", None)
        ctx = lock if lock is not None else nullcontext()
        with ctx:
            self._remit_claims_ddl(conn, is_pg)
            if is_pg:
                cur = conn.execute(
                    f"INSERT INTO {CLAIMS_TABLE} (thread, ckpt) "
                    "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    key,
                )
                won = cur.rowcount == 1
            else:
                try:
                    conn.execute(
                        f"INSERT INTO {CLAIMS_TABLE} (thread, ckpt) VALUES (?, ?)",
                        key,
                    )
                    conn.commit()
                    won = True
                except sqlite3.IntegrityError:
                    # Release the implicit write transaction the failed
                    # INSERT opened: left open, its lock starves the
                    # WINNER's subsequent writes on a sibling connection
                    # for the full busy timeout (G1 finding).
                    conn.rollback()
                    won = False
        if won:
            self._remit_claims_held.add(key)
        return won

    def _remit_consume_gate(self, config: Optional[dict], t: Any) -> Any:
        if t is None:
            return t
        explicit, flag, inspect_intent = self._remit_address(config)
        pending = list(getattr(t, "pending_writes", None) or [])
        has_pending_interrupt = any(
            len(w) > 1 and w[1] == INTERRUPT_CHANNEL for w in pending
        )
        view = _core.consume_view(
            has_pending_interrupt,
            self._remit_gate_enabled,
            explicit or flag,
            inspect_intent,
        )
        if view == "attempt":
            tconf = ((getattr(t, "config", None) or {}).get("configurable", {}) or {})
            ckpt = tconf.get("checkpoint_id") or "__latest__"
            thread = _thread_of(config)
            won = self._remit_take_claim(thread, ckpt)
            if won:
                self._remit_last_claim[thread] = ckpt
            _core.consume_claim_check(won, thread, ckpt)
        return t

    def get_tuple(self, config: dict) -> Any:  # type: ignore[override]
        t = self._remit_fork_filter(config, super().get_tuple(config))  # type: ignore[misc]
        return self._remit_consume_gate(config, t)

    async def aget_tuple(self, config: dict) -> Any:  # type: ignore[override]
        t = self._remit_fork_filter(config, await super().aget_tuple(config))  # type: ignore[misc]
        return self._remit_consume_gate(config, t)

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

    def _remit_resume_latch(self, config: Optional[dict], writes: Any) -> None:
        """Secondary latch of the cross-process gate at the resume journal
        write (probe 165's retained v1 site): re-claim the same key so a
        path that reaches the journal without the read is still stopped —
        late, at superstep join, which is exactly why the read path is
        primary. Idempotent for the winner; every conditional here routes a
        core verdict or extracts a config field."""
        resume_seen = False
        for w in writes or ():
            try:
                ch = w[0]
            except Exception:
                ch = None
            if ch == RESUME_CHANNEL:
                resume_seen = True
                break
        explicit, flag, inspect_intent = self._remit_address(config)
        view = _core.consume_view(
            resume_seen,
            self._remit_gate_enabled,
            explicit or flag,
            inspect_intent,
        )
        if view == "attempt":
            conf = (config or {}).get("configurable", {}) or {}
            thread = _thread_of(config)
            ckpt = (
                conf.get("checkpoint_id")
                or self._remit_last_claim.get(thread)
                or "__latest__"
            )
            won = self._remit_take_claim(thread, ckpt)
            _core.consume_claim_check(won, thread, ckpt)

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
        self._remit_resume_latch(config, writes)
        self._remit_core.sequence_op(_thread_of(config), "put_writes", str(task_id))
        return super().put_writes(config, writes, task_id, *args, **kwargs)  # type: ignore[misc]

    async def aput_writes(self, config: dict, writes: Any, task_id: Any, *args: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        self._remit_resume_latch(config, writes)
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
    cross_process_gate: bool = False,
    inspect_key: str = "remit_inspect",
    **kwargs: Any,
) -> Any:
    """Construct a REMIT-wrapped instance of ``inner_cls``.

    ``wrap(InMemorySaver)`` or ``wrap(SqliteSaver, conn, validator=v)`` —
    positional and keyword arguments other than the REMIT ones go to the
    inner saver's constructor unchanged. Pass ``cross_process_gate=True``
    on a durable saver to enable the probe-165 consume-once gate (see the
    module docstring for its claim key, store, and scope).
    """
    cls = saver_class(inner_cls)
    return cls(
        *args,
        remit_validator=validator,
        remit_core=core,
        remit_fork_on_explicit_checkpoint=fork_on_explicit_checkpoint,
        remit_fork_flag_key=fork_flag_key,
        remit_cross_process_gate=cross_process_gate,
        remit_inspect_key=inspect_key,
        **kwargs,
    )
