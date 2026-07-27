------------------------- MODULE ResumeContractParked -------------------------
(***************************************************************************)
(* Parked-crash companion to ResumeContract.tla.                           *)
(*                                                                         *)
(* The base module's CrashRecover carries the precondition that no         *)
(* interrupt is pending, so a crash while the run is parked awaiting the   *)
(* human is outside its transition relation (paper Sec. 4.2, "three scope  *)
(* bounds of the model itself"); probes 158/158b/158c cover that location  *)
(* empirically.  This module removes the scope bound formally: it is the   *)
(* base module verbatim plus one action, CrashWhileParked, enabled exactly *)
(* when the run is parked, whose disposition is selected by one constant:  *)
(*                                                                         *)
(*   ParkDurable = TRUE   the pending interrupt survives process death     *)
(*                        (the measured behavior of all three planes that  *)
(*                        park, probes 158/158b/158c): recovery follows    *)
(*                        the active recovery mode and the interrupt       *)
(*                        remains consumable.  Expected: all six contract  *)
(*                        invariants hold on the reference semantics, the  *)
(*                        liveness obligation holds under weak fairness,   *)
(*                        and the per-invariant fault matrix re-derives    *)
(*                        with the base module's verdict pattern.          *)
(*                                                                         *)
(*   ParkDurable = FALSE  the park is volatile: process death loses the    *)
(*                        pending interrupt and nothing re-offers it (the  *)
(*                        `emitted` latch below).  Expected: the six       *)
(*                        safety invariants hold over the full reachable   *)
(*                        space -- nothing fires, nothing regresses --     *)
(*                        while EventuallyCompletes is VIOLATED: safety    *)
(*                        by deadness, the pydantic-graph disposition of   *)
(*                        paper Sec. 6.4, exhibited as a model-level       *)
(*                        liveness counterexample.                         *)
(*                                                                         *)
(* Differences from ResumeContract.tla, exhaustively:                      *)
(*   1. CONSTANT ParkDurable (BOOLEAN).                                    *)
(*   2. VARIABLE emitted: latches TRUE at the first EmitInterrupt and is   *)
(*      never reset, so a volatile-park crash cannot be healed by silent   *)
(*      re-emission.  On base-module behaviors the latch is inert: the     *)
(*      base module already never re-emits (waiting blocks re-emission     *)
(*      until Consume, and Consume sets consumedVal # NoVal forever).      *)
(*   3. Action CrashWhileParked: precondition `waiting` (which invariantly *)
(*      implies pc = IP); consumes the same MaxCrashes budget; repositions *)
(*      control by the SAME recovery-mode disjunction as CrashRecover so   *)
(*      each fault switch keeps its own recovery discipline at the parked  *)
(*      location (a parked crash under FaultReplay replays, under          *)
(*      FaultNondetRecovery is nondeterministic, etc.); waiting' =         *)
(*      ParkDurable in every branch.                                       *)
(* Everything else -- Init, the other five actions, all six invariants,    *)
(* TypeOK (extended with emitted), the liveness pairing -- is verbatim.    *)
(*                                                                         *)
(* Kept as a companion module rather than an edit of ResumeContract.tla    *)
(* so that every committed receipt of the base module (state counts,      *)
(* counterexample depths, the 39-cell and R10 matrices) stays byte-valid.  *)
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
  ParkDurable,       \* TRUE: pending interrupt survives a parked crash
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
       /\ ParkDurable \in BOOLEAN

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
  pcRegress,     \* TRUE once execution re-enters the durable prefix
  emitted        \* TRUE once the interrupt has been offered (never reset)

vars == << pc, effects, ckpts, frontier, waiting, consumedVal,
           forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress,
           emitted >>

(* Explicit, injective, NON-identity branch semantics (as in the base     *)
(* module): outcomes are computed from values, so ForkDeterminism is a    *)
(* routing property, not v = v.                                           *)
f(v) == <<v>>

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
  /\ emitted      = FALSE

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
                  crashes, recHist, extraResumes, emitted >>

(* Reaching the gated task emits the interrupt and parks the branch.      *)
(* The emitted latch is the only change from the base module; on base     *)
(* behaviors it is inert (see header note 2).                             *)
EmitInterrupt ==
  /\ pc = IP
  /\ ~waiting
  /\ ~emitted
  /\ consumedVal = NoVal
  /\ waiting' = TRUE
  /\ emitted' = TRUE
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
  /\ forkOuts'    = Append(forkOuts, f(v))
  /\ UNCHANGED << crashes, recHist, extraResumes, pcRegress, emitted >>

(* A further resume targeted at the SAME (thread, interrupt-checkpoint):  *)
(* the contract requires a fork whose outcome reflects the new value.     *)
(* FaultForkIgnore silently substitutes the first branch's outcome.       *)
ForkResume(v) ==
  /\ consumedVal # NoVal
  /\ Len(forkVals) < MaxResumes
  /\ forkVals' = Append(forkVals, v)
  /\ forkOuts' = Append(forkOuts,
                        IF FaultForkIgnore THEN forkOuts[1] ELSE f(v))
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress, emitted >>

(* Crash strictly inside the run (NOT parked), then recover per mode --   *)
(* verbatim from the base module.                                         *)
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
                  forkVals, forkOuts, extraResumes, pcRegress, emitted >>

(* Crash while the run is parked awaiting the human -- the location the   *)
(* base module excludes by precondition.  `waiting` invariantly implies   *)
(* pc = IP.  Volatile state is erased; the durable variables (effects     *)
(* fired, ckpts, frontier, consumedVal) are preserved; the pending        *)
(* interrupt survives iff ParkDurable.  Control repositioning follows the *)
(* SAME recovery-mode disjunction as CrashRecover, so each fault keeps    *)
(* its own recovery discipline at this location too.                      *)
CrashWhileParked ==
  /\ crashes < MaxCrashes
  /\ waiting
  /\ crashes' = crashes + 1
  /\ waiting' = ParkDurable
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
  /\ UNCHANGED << effects, ckpts, frontier, consumedVal,
                  forkVals, forkOuts, extraResumes, pcRegress, emitted >>

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
                  forkOuts, crashes, recHist, pcRegress, emitted >>

Next ==
  \/ ExecTask
  \/ EmitInterrupt
  \/ \E v \in Values : Consume(v)
  \/ \E v \in Values : ForkResume(v)
  \/ CrashRecover
  \/ CrashWhileParked
  \/ ExtraResume

Spec == Init /\ [][Next]_vars

(* Liveness (as in the base module): under weak fairness the run          *)
(* eventually completes.  With ParkDurable = FALSE this obligation is     *)
(* expected to be VIOLATED -- the designed safety-by-deadness receipt.    *)
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
  /\ emitted \in BOOLEAN

(* The six contract properties -- verbatim from the base module *)
EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1                 \* EO
PrefixConsistency   == ~pcRegress                                       \* PC
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])                     \* FD
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid          \* CV
ConsumeOnce         == effects[IP] <= 1                                 \* CO
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec          \* RD

===============================================================================
