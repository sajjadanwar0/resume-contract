---------------------------- MODULE ResumeContract ----------------------------
(***************************************************************************)
(* The Resume Contract: checkpoint / interrupt / resume semantics for      *)
(* agent-framework persistence planes.                                     *)
(*                                                                         *)
(* A workflow of NTasks sequential tasks, each with one non-idempotent     *)
(* external effect. Task IP is interrupt-gated: its effect fires only      *)
(* after a human resume value is consumed. Completion of each task writes  *)
(* a checkpoint record advancing the durable frontier.                     *)
(*                                                                         *)
(* Six contract properties (invariants):                                   *)
(*   EO  EffectExactlyOnce    effects fire at most once across             *)
(*                            interrupt / crash / resume                   *)
(*   PC  PrefixConsistency    execution never re-enters the durable        *)
(*                            prefix                                       *)
(*   FD  ForkDeterminism      resume value k from the interrupt            *)
(*                            checkpoint yields the outcome of value k     *)
(*   CV  CheckpointValidity   only valid states are durably persisted      *)
(*   CO  ConsumeOnce          the interrupt-gated effect fires at most     *)
(*                            once                                         *)
(*   RD  RecoveryDeterminism  identical durable state yields identical     *)
(*                            recovery decisions                           *)
(*                                                                         *)
(* Six fault switches model the violation classes observed live in the   *)
(* pilot conformance study:                                                *)
(*   FaultReplay          resume re-executes the completed prefix          *)
(*                        (CrewAI @persist / from_checkpoint, probes       *)
(*                        115/115b; LangGraph #7361 regression class)      *)
(*   FaultForkIgnore      2nd resume value from the same checkpoint is     *)
(*                        silently replaced by the 1st (LangGraph #6663)   *)
(*   FaultInvalidPersist  a schema-invalid completion record is persisted  *)
(*                        silently (LangGraph #6491 class)                 *)
(*   FaultNondetRecovery  replay-vs-re-execute decided nondeterministic-   *)
(*                        ally at equal durable state (LangGraph #8039)    *)
(*   FaultDoubleConsume   a stray resume on a completed thread re-fires    *)
(*                        the gated effect (CopilotKit #2315 class)        *)
(*   FaultPrefixReplay    recovery restarts from task 1 but serves the     *)
(*                        gated task's effect from the durable record --   *)
(*                        memoized-gate prefix replay, the LangGraph       *)
(*                        1.2.9 crash-path class (probes 118/133):         *)
(*                        completed non-gated tasks re-execute while the   *)
(*                        interrupt lifecycle stays exactly-once           *)
(*                                                                         *)
(* With all switches FALSE the module is the reference (gated) semantics;  *)
(* TLC verifies all six invariants. With exactly one switch TRUE, TLC      *)
(* produces a violation trace for the corresponding invariant.             *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,            \* number of tasks (e.g. 3)
  IP,                \* index of the interrupt-gated task (e.g. 2)
  Values,            \* resume-value domain (e.g. {"va","vb"})
  NoVal,             \* model value: no resume value consumed yet
  MaxResumes,        \* bound on resumes targeted at the interrupt ckpt
  MaxCrashes,        \* bound on crash events
  MaxExtraResumes,   \* bound on post-completion stray resumes
  FaultReplay,
  FaultForkIgnore,
  FaultInvalidPersist,
  FaultNondetRecovery,
  FaultDoubleConsume,
  FaultPrefixReplay

ASSUME /\ NTasks \in Nat \ {0}
       /\ IP \in 2..NTasks
       /\ NoVal \notin Values
       /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc,            \* next task index on the primary branch, in 1..NTasks+1
  effects,       \* [Tasks -> Nat] : external-effect execution counts
  ckpts,         \* Seq([idx : Tasks, valid : BOOLEAN]) : durable log
  frontier,      \* highest task index with a durable completion record
  waiting,       \* TRUE iff the interrupt is emitted and unconsumed
  consumedVal,   \* Values \cup {NoVal} : value consumed on primary branch
  forkVals,      \* Seq(Values) : values supplied at the interrupt ckpt
  forkOuts,      \* Seq(Values) : outcome each such resume actually observed
  crashes,       \* number of crash events so far
  recHist,       \* Seq([dur : Nat, dec : STRING]) : recovery decisions
  extraResumes,  \* stray resumes injected after completion
  pcRegress      \* TRUE once execution re-enters the durable prefix

vars == << pc, effects, ckpts, frontier, waiting, consumedVal,
           forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress >>

--------------------------------------------------------------------------
Init ==
  /\ pc           = 1
  /\ effects      = [t \in Tasks |-> 0]
  /\ ckpts        = << >>
  /\ frontier     = 0
  /\ waiting      = FALSE
  /\ consumedVal  = NoVal
  /\ forkVals     = << >>
  /\ forkOuts     = << >>
  /\ crashes      = 0
  /\ recHist      = << >>
  /\ extraResumes = 0
  /\ pcRegress    = FALSE

--------------------------------------------------------------------------
(* Execute a non-gated task (or re-execute IP during a faulty replay      *)
(* when its value was already consumed). Writes the completion record.    *)
ExecTask ==
  /\ pc \in Tasks
  /\ ~waiting
  /\ (pc = IP) => (consumedVal # NoVal)   \* IP first executes via Consume
  /\ effects'   = [effects EXCEPT ![pc] =
                     IF FaultPrefixReplay /\ pc = IP THEN @ ELSE @ + 1]
  /\ ckpts'     = Append(ckpts,
                     [idx   |-> pc,
                      valid |-> ~(FaultInvalidPersist /\ pc = NTasks)])
  /\ frontier'  = IF pc > frontier THEN pc ELSE frontier
  /\ pcRegress' = (pcRegress \/ (pc <= frontier))
  /\ pc'        = pc + 1
  /\ UNCHANGED << waiting, consumedVal, forkVals, forkOuts,
                  crashes, recHist, extraResumes >>

(* Reaching the gated task emits the interrupt and parks the branch.      *)
EmitInterrupt ==
  /\ pc = IP
  /\ ~waiting
  /\ consumedVal = NoVal
  /\ waiting' = TRUE
  /\ UNCHANGED << pc, effects, ckpts, frontier, consumedVal, forkVals,
                  forkOuts, crashes, recHist, extraResumes, pcRegress >>

(* First resume: consumes the interrupt, fires the gated effect once,     *)
(* records the branch outcome for the supplied value.                     *)
Consume(v) ==
  /\ waiting
  /\ Len(forkVals) < MaxResumes
  /\ waiting'     = FALSE
  /\ consumedVal' = v
  /\ effects'     = [effects EXCEPT ![IP] = @ + 1]
  /\ ckpts'       = Append(ckpts, [idx |-> IP, valid |-> TRUE])
  /\ frontier'    = IF IP > frontier THEN IP ELSE frontier
  /\ pc'          = IP + 1
  /\ forkVals'    = Append(forkVals, v)
  /\ forkOuts'    = Append(forkOuts, v)
  /\ UNCHANGED << crashes, recHist, extraResumes, pcRegress >>

(* A further resume targeted at the SAME (thread, interrupt-checkpoint):  *)
(* the contract requires a fork whose outcome reflects the new value.     *)
(* FaultForkIgnore silently substitutes the first branch's outcome.       *)
ForkResume(v) ==
  /\ consumedVal # NoVal
  /\ Len(forkVals) < MaxResumes
  /\ forkVals' = Append(forkVals, v)
  /\ forkOuts' = Append(forkOuts,
                        IF FaultForkIgnore THEN forkOuts[1] ELSE v)
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress >>

(* Crash strictly inside the run, then recover according to the mode.     *)
(* Reference: continue from the durable frontier (skip completed work).   *)
(* FaultReplay: restart from task 1 over restored state.                  *)
(* FaultNondetRecovery: at identical durable state the decision between   *)
(* skipping and re-executing the frontier task is unconstrained.          *)
CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ ~waiting
  /\ crashes' = crashes + 1
  /\ \/ /\ ~FaultReplay /\ ~FaultNondetRecovery /\ ~FaultPrefixReplay
        /\ pc'      = frontier + 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "skip"])
     \/ /\ FaultReplay
        /\ pc'      = 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "replay"])
     \/ /\ FaultPrefixReplay
        /\ pc'      = 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "prefixreplay"])
     \/ /\ FaultNondetRecovery
        /\ \E d \in {"skip", "reexec"} :
             /\ pc' = IF d = "skip" THEN frontier + 1 ELSE frontier
             /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> d])
  /\ UNCHANGED << effects, ckpts, frontier, waiting, consumedVal,
                  forkVals, forkOuts, extraResumes, pcRegress >>

(* A stray resume delivered to a completed thread. The contract requires  *)
(* it to be inert; FaultDoubleConsume re-fires the gated effect.          *)
ExtraResume ==
  /\ pc = NTasks + 1
  /\ extraResumes < MaxExtraResumes
  /\ extraResumes' = extraResumes + 1
  /\ effects' = IF FaultDoubleConsume
                THEN [effects EXCEPT ![IP] = @ + 1]
                ELSE effects
  /\ UNCHANGED << pc, ckpts, frontier, waiting, consumedVal, forkVals,
                  forkOuts, crashes, recHist, pcRegress >>

Next ==
  \/ ExecTask
  \/ EmitInterrupt
  \/ \E v \in Values : Consume(v)
  \/ \E v \in Values : ForkResume(v)
  \/ CrashRecover
  \/ ExtraResume

Spec == Init /\ [][Next]_vars

(* Liveness (checked on the reference configuration): under weak fairness
   the run eventually completes -- a framework that refuses every resume
   satisfies all six safety invariants vacuously, so the contract pairs
   them with this progress obligation. *)
FairSpec == Init /\ [][Next]_vars /\ WF_vars(Next)
EventuallyCompletes == <>(pc = NTasks + 1)

--------------------------------------------------------------------------
(* Type correctness *)
TypeOK ==
  /\ pc \in 1..NTasks + 1
  /\ effects \in [Tasks -> Nat]
  /\ frontier \in 0..NTasks
  /\ waiting \in BOOLEAN
  /\ consumedVal \in Values \cup {NoVal}
  /\ crashes \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ pcRegress \in BOOLEAN

(* The six contract properties *)
EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1                 \* EO
PrefixConsistency   == ~pcRegress                                       \* PC
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = forkVals[k]                     \* FD
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid          \* CV
ConsumeOnce         == effects[IP] <= 1                                 \* CO
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec          \* RD

===============================================================================
