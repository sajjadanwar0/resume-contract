-------------------------- MODULE R11_ConsumeCount --------------------------
(***************************************************************************)
(* Property 5 (CO) has TWO clauses:                                        *)
(*                                                                         *)
(*   (CO-c)  consumption count: an interrupt is consumed by at most one    *)
(*           resume;                                                       *)
(*   (CO-e)  effect inertness: a resume without fork intent addressed to   *)
(*           a completed run or an already-consumed interrupt is inert     *)
(*           with respect to effects.                                      *)
(*                                                                         *)
(* ResumeContract.tla formalizes CO as                                     *)
(*                                                                         *)
(*     ConsumeOnce == effects[IP] <= 1                                     *)
(*                                                                         *)
(* which is exactly EO restricted to the gated task -- CO-e alone, and     *)
(* only its effect consequence. CO-c is not represented: the module has    *)
(* no consumption counter, and Consume(v) atomically clears `waiting`, so  *)
(* a second consumption of a LIVE parked interrupt is not a behavior the   *)
(* transition relation admits. Proposition 2(iv)'s "EO => CO" is therefore *)
(* a tautology of the two formulas rather than a discovered dependence,    *)
(* and the measured cross-process failure (probe 159) -- two OS processes  *)
(* consuming ONE live parked interrupt -- has no in-model shadow.          *)
(* FaultDoubleConsume is not that shadow: it is enabled only at            *)
(* pc = NTasks + 1, i.e. on a COMPLETED run, which is the sequential       *)
(* stray-redelivery mechanism the probed plane refuses.                    *)
(*                                                                         *)
(* This module carries the two clauses as separate invariants and adds     *)
(* the lost-update mechanism the measurement exhibits:                     *)
(*                                                                         *)
(*   FaultConcurrentConsume  a second racer read `waiting` before the      *)
(*                           first racer's write cleared it, and consumes  *)
(*                           the same live interrupt. Bounded to one       *)
(*                           extra racer (raceUsed), which is exactly the  *)
(*                           two-racer shape probe 159 measures. The       *)
(*                           racer is served its OWN value (FD intact,     *)
(*                           as measured) and appends no checkpoint (the   *)
(*                           duplicate is invisible in framework state,    *)
(*                           as measured).                                 *)
(*                                                                         *)
(*   FaultRaceEffect         whether the second consumption also fires     *)
(*                           the gated effect. FALSE models an idempotent  *)
(*                           gate whose effect is served from the durable  *)
(*                           record; TRUE is the measured probe-159 shape. *)
(*                                                                         *)
(* The separation this module establishes:                                 *)
(*                                                                         *)
(*   C1 (FaultConcurrentConsume, ~FaultRaceEffect) violates CO-c while     *)
(*   EO, PC, FD, CV, CO-e, and RD all hold over the entire reachable       *)
(*   state space. Hence CO-c is independent of the conjunction of the      *)
(*   other five properties AND of CO-e -- the dependence Proposition       *)
(*   2(iv) reports holds for CO-e only.                                    *)
(*                                                                         *)
(* All invariant formulas other than the two CO clauses are                *)
(* character-identical to ResumeContract.tla, so a "holds" cell here       *)
(* concerns the same property.                                             *)
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
  FaultConcurrentConsume,
  FaultRaceEffect

ASSUME /\ NTasks \in Nat \ {0}
       /\ IP \in 2..NTasks
       /\ NoVal \notin Values
       /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc, effects, ckpts, frontier, waiting, consumedVal,
  forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress,
  consumeCount,  \* number of resumes that CONSUMED the interrupt (CO-c)
  raceUsed       \* TRUE once the single modeled extra racer has run

vars == << pc, effects, ckpts, frontier, waiting, consumedVal,
           forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress,
           consumeCount, raceUsed >>

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
  /\ consumeCount = 0
  /\ raceUsed     = FALSE

--------------------------------------------------------------------------
ExecTask ==
  /\ pc \in Tasks
  /\ ~waiting
  /\ (pc = IP) => (consumedVal # NoVal)
  /\ effects'   = [effects EXCEPT ![pc] = @ + 1]
  /\ ckpts'     = Append(ckpts, [idx |-> pc, valid |-> TRUE])
  /\ frontier'  = IF pc > frontier THEN pc ELSE frontier
  /\ pcRegress' = (pcRegress \/ (pc <= frontier))
  /\ pc'        = pc + 1
  /\ UNCHANGED << waiting, consumedVal, forkVals, forkOuts,
                  crashes, recHist, extraResumes, consumeCount, raceUsed >>

EmitInterrupt ==
  /\ pc = IP
  /\ ~waiting
  /\ consumedVal = NoVal
  /\ waiting' = TRUE
  /\ UNCHANGED << pc, effects, ckpts, frontier, consumedVal, forkVals,
                  forkOuts, crashes, recHist, extraResumes, pcRegress,
                  consumeCount, raceUsed >>

Consume(v) ==
  /\ waiting
  /\ Len(forkVals) < MaxResumes
  /\ waiting'      = FALSE
  /\ consumedVal'  = v
  /\ consumeCount' = consumeCount + 1
  /\ effects'      = [effects EXCEPT ![IP] = @ + 1]
  /\ ckpts'        = Append(ckpts, [idx |-> IP, valid |-> TRUE])
  /\ frontier'     = IF IP > frontier THEN IP ELSE frontier
  /\ pc'           = IP + 1
  /\ forkVals'     = Append(forkVals, v)
  /\ forkOuts'     = Append(forkOuts, f(v))
  /\ UNCHANGED << crashes, recHist, extraResumes, pcRegress, raceUsed >>

(* The lost update. A second OS process read `waiting` = TRUE before the   *)
(* first process's clearing write landed, and proceeds to consume the same *)
(* live parked interrupt. Control state is already past the gate, so pc,   *)
(* frontier and the durable log are untouched: the duplicate is invisible  *)
(* in framework state, which is what probe 159 measures. The racer is      *)
(* served its own value, so FD is untouched -- also as measured.           *)
RaceConsume(v) ==
  /\ FaultConcurrentConsume
  /\ ~raceUsed
  /\ consumeCount = 1
  /\ pc = IP + 1
  /\ Len(forkVals) < MaxResumes
  /\ raceUsed'     = TRUE
  /\ consumeCount' = consumeCount + 1
  /\ effects'      = IF FaultRaceEffect
                     THEN [effects EXCEPT ![IP] = @ + 1]
                     ELSE effects
  /\ forkVals'     = Append(forkVals, v)
  /\ forkOuts'     = Append(forkOuts, f(v))
  /\ UNCHANGED << pc, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress >>

ForkResume(v) ==
  /\ consumedVal # NoVal
  /\ Len(forkVals) < MaxResumes
  /\ forkVals' = Append(forkVals, v)
  /\ forkOuts' = Append(forkOuts, f(v))
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress,
                  consumeCount, raceUsed >>

CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ ~waiting
  /\ crashes' = crashes + 1
  /\ pc'      = frontier + 1
  /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "skip"])
  /\ UNCHANGED << effects, ckpts, frontier, waiting, consumedVal,
                  forkVals, forkOuts, extraResumes, pcRegress,
                  consumeCount, raceUsed >>

ExtraResume ==
  /\ pc = NTasks + 1
  /\ extraResumes < MaxExtraResumes
  /\ extraResumes' = extraResumes + 1
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  forkVals, forkOuts, crashes, recHist, pcRegress,
                  consumeCount, raceUsed >>

Next ==
  \/ ExecTask
  \/ EmitInterrupt
  \/ \E v \in Values : Consume(v)
  \/ \E v \in Values : RaceConsume(v)
  \/ \E v \in Values : ForkResume(v)
  \/ CrashRecover
  \/ ExtraResume

Spec == Init /\ [][Next]_vars
FairSpec == Init /\ [][Next]_vars /\ WF_vars(Next)
EventuallyCompletes == <>(pc = NTasks + 1)

--------------------------------------------------------------------------
TypeOK ==
  /\ pc \in 1..NTasks + 1
  /\ effects \in [Tasks -> Nat]
  /\ frontier \in 0..NTasks
  /\ waiting \in BOOLEAN
  /\ consumedVal \in Values \cup {NoVal}
  /\ crashes \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ pcRegress \in BOOLEAN
  /\ consumeCount \in Nat
  /\ raceUsed \in BOOLEAN

EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1                 \* EO
PrefixConsistency   == ~pcRegress                                       \* PC
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])                  \* FD
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid          \* CV
ConsumeOnceEffect   == effects[IP] <= 1                                 \* CO-e
ConsumeOnceCount    == consumeCount <= 1                                \* CO-c
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec          \* RD

===============================================================================
