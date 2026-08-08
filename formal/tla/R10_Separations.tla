--------------------------- MODULE R10_Separations ---------------------------
EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,
  IP,
  Values,
  NoVal,
  MaxResumes,
  MaxCrashes,
  MaxExtraResumes,
  FaultRegateNondet,
  FaultRebuild,
  FaultRedeliver

ASSUME /\ NTasks \in Nat \ {0}
       /\ IP \in 2..NTasks
       /\ NoVal \notin Values
       /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc, effects, ckpts, frontier, waiting, consumedVal,
  forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress,
  lineage,
  replaying

vars == << pc, effects, ckpts, frontier, waiting, consumedVal,
           forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress,
           lineage, replaying >>

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
  /\ lineage      = "log"
  /\ replaying    = FALSE

--------------------------------------------------------------------------
ExecTask ==
  /\ pc \in Tasks
  /\ ~waiting
  /\ ~replaying
  /\ (pc = IP) => (consumedVal # NoVal)
  /\ effects'   = [effects EXCEPT ![pc] = @ + 1]
  /\ ckpts'     = Append(ckpts, [idx |-> pc, valid |-> TRUE])
  /\ frontier'  = IF pc > frontier THEN pc ELSE frontier
  /\ pcRegress' = (pcRegress \/ (pc <= frontier))
  /\ pc'        = pc + 1
  /\ UNCHANGED << waiting, consumedVal, forkVals, forkOuts,
                  crashes, recHist, extraResumes, lineage, replaying >>

MemoReplayTask ==
  /\ replaying
  /\ pc <= frontier
  /\ pc'        = pc + 1
  /\ replaying' = (pc + 1 <= frontier)
  /\ waiting'   = FALSE
  /\ UNCHANGED << effects, ckpts, frontier, consumedVal, forkVals, forkOuts,
                  crashes, recHist, extraResumes, pcRegress, lineage >>

EmitInterrupt ==
  /\ pc = IP
  /\ ~waiting
  /\ ~replaying
  /\ consumedVal = NoVal
  /\ waiting' = TRUE
  /\ UNCHANGED << pc, effects, ckpts, frontier, consumedVal, forkVals,
                  forkOuts, crashes, recHist, extraResumes, pcRegress,
                  lineage, replaying >>

Consume(v) ==
  /\ waiting
  /\ ~replaying
  /\ Len(forkVals) < MaxResumes
  /\ waiting'     = FALSE
  /\ consumedVal' = v
  /\ effects'     = IF effects[IP] = 0
                    THEN [effects EXCEPT ![IP] = @ + 1]
                    ELSE effects
  /\ ckpts'       = Append(ckpts, [idx |-> IP, valid |-> TRUE])
  /\ frontier'    = IF IP > frontier THEN IP ELSE frontier
  /\ pc'          = IP + 1
  /\ forkVals'    = Append(forkVals, v)
  /\ forkOuts'    = Append(forkOuts, f(v))
  /\ UNCHANGED << crashes, recHist, extraResumes, pcRegress,
                  lineage, replaying >>

ForkResume(v) ==
  /\ consumedVal # NoVal
  /\ ~replaying
  /\ Len(forkVals) < MaxResumes
  /\ forkVals' = Append(forkVals, v)
  /\ forkOuts' = Append(forkOuts, f(v))
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress,
                  lineage, replaying >>

--------------------------------------------------------------------------
RecSkip ==
  /\ ~FaultRebuild
  /\ pc'        = frontier + 1
  /\ effects'   = effects
  /\ waiting'   = FALSE
  /\ lineage'   = "log"
  /\ replaying' = FALSE
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "skip"])

RecRegate ==
  /\ FaultRegateNondet
  /\ consumedVal # NoVal
  /\ frontier >= IP
  /\ pc'        = IP
  /\ effects'   = effects
  /\ waiting'   = TRUE
  /\ lineage'   = "log"
  /\ replaying' = FALSE
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "regate"])

RecRebuild ==
  /\ FaultRebuild
  /\ pc'        = 1
  /\ effects'   = effects
  /\ waiting'   = FALSE
  /\ lineage'   = "initial"
  /\ replaying' = (frontier >= 1)
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "rebuild"])

RecRedeliver ==
  /\ FaultRedeliver
  /\ frontier \in Tasks
  /\ frontier # IP
  /\ pc'        = frontier + 1
  /\ effects'   = [effects EXCEPT ![frontier] = @ + 1]
  /\ waiting'   = FALSE
  /\ lineage'   = "log"
  /\ replaying' = FALSE
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "skip"])

CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ ~waiting
  /\ ~replaying
  /\ crashes' = crashes + 1
  /\ \/ RecSkip
     \/ RecRegate
     \/ RecRebuild
     \/ RecRedeliver
  /\ UNCHANGED << ckpts, frontier, consumedVal, forkVals, forkOuts,
                  extraResumes, pcRegress >>

ExtraResume ==
  /\ pc = NTasks + 1
  /\ ~replaying
  /\ extraResumes < MaxExtraResumes
  /\ extraResumes' = extraResumes + 1
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  forkVals, forkOuts, crashes, recHist, pcRegress,
                  lineage, replaying >>

Next ==
  \/ ExecTask
  \/ MemoReplayTask
  \/ EmitInterrupt
  \/ \E v \in Values : Consume(v)
  \/ \E v \in Values : ForkResume(v)
  \/ CrashRecover
  \/ ExtraResume

Spec == Init /\ [][Next]_vars

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
  /\ lineage \in {"log", "initial"}
  /\ replaying \in BOOLEAN

EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1
PrefixConsistency   == ~pcRegress /\ ((crashes > 0) => lineage = "log")
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid
ConsumeOnce         == effects[IP] <= 1
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec

===============================================================================
