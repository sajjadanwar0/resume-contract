-------------------------- MODULE R11_ConsumeCount --------------------------

EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,
  IP,
  Values,
  NoVal,
  MaxResumes,
  MaxCrashes,
  MaxExtraResumes,
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
  consumeCount,
  raceUsed

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

EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1
PrefixConsistency   == ~pcRegress
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid
ConsumeOnceEffect   == effects[IP] <= 1
ConsumeOnceCount    == consumeCount <= 1
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec

===============================================================================
