-------------------------- MODULE R7_StateRebuild --------------------------
(***************************************************************************)
(* R7: the isolated-PC fault configuration referenced by the paper's       *)
(* independence subsection.  Under the contract's PC (prefix               *)
(* continuation, revised): recovery must continue from the durably         *)
(* recorded frontier state, or from a state re-derived deterministically   *)
(* from the durable log alone; memoized replay conforms.                   *)
(*                                                                         *)
(* FaultStateRebuild models the CrewAI-shaped failure with the EO leg      *)
(* surgically removed: on recovery the prefix is traversed with every      *)
(* effect served MEMOIZED from the durable ledger (so EO holds), but the   *)
(* working state is re-derived from INITIAL values, not from the log.      *)
(* PC fails alone: the post-recovery state lineage is "initial".           *)
(*                                                                         *)
(* Expected TLC results:                                                   *)
(*   FaultStateRebuild = FALSE : EO, PC, CO all hold (reference).          *)
(*   FaultStateRebuild = TRUE  : EO and CO hold; PrefixContinuation is     *)
(*                               violated (counterexample of depth <= 6).  *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,             \* number of tasks (e.g. 3)
  MaxCrashes,         \* bound on crash events (e.g. 1)
  FaultStateRebuild   \* the R7 switch

ASSUME NTasks \in Nat \ {0} /\ MaxCrashes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc,           \* next task index, 1..NTasks+1
  effects,      \* [Tasks -> Nat] live external-effect counts
  frontier,     \* highest durably completed task
  lineage,      \* "log" | "initial" : provenance of the working state
  replaying,    \* TRUE while traversing the prefix in memoized replay
  crashes

vars == << pc, effects, frontier, lineage, replaying, crashes >>

Init ==
  /\ pc = 1 /\ effects = [t \in Tasks |-> 0] /\ frontier = 0
  /\ lineage = "log" /\ replaying = FALSE /\ crashes = 0

(* Normal execution: fires the live effect, extends the durable frontier. *)
ExecTask ==
  /\ pc \in Tasks /\ ~replaying
  /\ effects'  = [effects EXCEPT ![pc] = @ + 1]
  /\ frontier' = IF pc > frontier THEN pc ELSE frontier
  /\ pc'       = pc + 1
  /\ UNCHANGED << lineage, replaying, crashes >>

(* Crash + recover.  Reference: continue at frontier+1 with log-derived   *)
(* state.  FaultStateRebuild: restart at task 1 with state re-derived     *)
(* from initial values; the prefix will be traversed memoized.            *)
CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ crashes' = crashes + 1
  /\ IF FaultStateRebuild
       THEN /\ pc' = 1 /\ lineage' = "initial" /\ replaying' = TRUE
       ELSE /\ pc' = frontier + 1 /\ lineage' = "log" /\ replaying' = FALSE
  /\ UNCHANGED << effects, frontier >>

(* Memoized replay of a durably completed prefix task: the effect is      *)
(* served from the ledger -- the live counter does NOT increment -- so EO *)
(* is preserved even though the prefix is re-traversed.                   *)
MemoReplayTask ==
  /\ replaying /\ pc <= frontier
  /\ pc' = pc + 1
  /\ replaying' = (pc + 1 <= frontier)
  /\ UNCHANGED << effects, frontier, lineage, crashes >>

Next == ExecTask \/ CrashRecover \/ MemoReplayTask

Spec == Init /\ [][Next]_vars

--------------------------------------------------------------------------
TypeOK ==
  /\ pc \in 1..NTasks + 1
  /\ effects \in [Tasks -> Nat]
  /\ frontier \in 0..NTasks
  /\ lineage \in {"log", "initial"}
  /\ replaying \in BOOLEAN
  /\ crashes \in 0..MaxCrashes

(* EO: every live effect at most once, across crash and replay.           *)
EffectExactlyOnce == \A t \in Tasks : effects[t] <= 1

(* PC (prefix continuation, revised): whenever execution has recovered,   *)
(* the working state must be derived from the durable log.  Memoized      *)
(* re-traversal (replaying with lineage = "log") would conform; a state   *)
(* rebuilt from initial values does not.                                  *)
PrefixContinuation == (crashes > 0) => (lineage = "log")

==========================================================================
