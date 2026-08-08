-------------------------- MODULE R7_StateRebuild --------------------------
EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,
  MaxCrashes,
  FaultStateRebuild

ASSUME NTasks \in Nat \ {0} /\ MaxCrashes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc,
  effects,
  frontier,
  lineage,
  replaying,
  crashes

vars == << pc, effects, frontier, lineage, replaying, crashes >>

Init ==
  /\ pc = 1 /\ effects = [t \in Tasks |-> 0] /\ frontier = 0
  /\ lineage = "log" /\ replaying = FALSE /\ crashes = 0

ExecTask ==
  /\ pc \in Tasks /\ ~replaying
  /\ effects'  = [effects EXCEPT ![pc] = @ + 1]
  /\ frontier' = IF pc > frontier THEN pc ELSE frontier
  /\ pc'       = pc + 1
  /\ UNCHANGED << lineage, replaying, crashes >>

CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ crashes' = crashes + 1
  /\ IF FaultStateRebuild
       THEN /\ pc' = 1 /\ lineage' = "initial" /\ replaying' = TRUE
       ELSE /\ pc' = frontier + 1 /\ lineage' = "log" /\ replaying' = FALSE
  /\ UNCHANGED << effects, frontier >>

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

EffectExactlyOnce == \A t \in Tasks : effects[t] <= 1

PrefixContinuation == (crashes > 0) => (lineage = "log")

==========================================================================
