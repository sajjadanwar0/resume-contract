---------------------------- MODULE IndCheck ----------------------------

EXTENDS ResumeContract

BSeq(S, n) == UNION { [1..k -> S] : k \in 0..n }

CkptRec      == [idx : Tasks, valid : BOOLEAN]
Decs         == {"skip", "replay", "reexec", "prefixreplay"}
RecRec       == [dur : 0..NTasks, dec : Decs]
CkptRecValid == [idx : Tasks, valid : {TRUE}]
RecRecSkip   == [dur : 0..NTasks, dec : {"skip"}]

TypeOKS ==
  /\ pc           \in 1..NTasks + 1
  /\ effects      \in [Tasks -> Nat]
  /\ frontier     \in 0..NTasks
  /\ waiting      \in BOOLEAN
  /\ consumedVal  \in Values \cup {NoVal}
  /\ crashes      \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ pcRegress    \in BOOLEAN
  /\ ckpts        \in BSeq(CkptRec, NTasks)
  /\ forkVals     \in BSeq(Values, MaxResumes)
  /\ forkOuts     \in BSeq({f(v) : v \in Values}, MaxResumes)
  /\ recHist      \in BSeq(RecRec, MaxCrashes)

Inv ==
  /\ TypeOKS
  /\ frontier = pc - 1
  /\ Len(ckpts) = frontier
  /\ Len(recHist) = crashes
  /\ (waiting => (pc = IP /\ consumedVal = NoVal))
  /\ \A t \in Tasks : effects[t] = (IF t <= frontier THEN 1 ELSE 0)
  /\ \A k \in 1..Len(ckpts)    : ckpts[k].valid
  /\ Len(forkOuts) = Len(forkVals)
  /\ \A k \in 1..Len(forkVals) : forkOuts[k] = f(forkVals[k])
  /\ \A k \in 1..Len(recHist)  : recHist[k].dec = "skip"
  /\ pcRegress = FALSE

InvFast ==
  /\ pc \in 1..NTasks + 1
  /\ frontier = pc - 1
  /\ effects  = [t \in Tasks |-> IF t <= frontier THEN 1 ELSE 0]
  /\ pcRegress = FALSE
  /\ waiting \in BOOLEAN
  /\ consumedVal \in Values \cup {NoVal}
  /\ (waiting => (pc = IP /\ consumedVal = NoVal))
  /\ crashes      \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ ckpts   \in [1..frontier -> CkptRecValid]
  /\ recHist \in [1..crashes  -> RecRecSkip]
  /\ forkVals \in BSeq(Values, MaxResumes)
  /\ forkOuts  = [k \in 1..Len(forkVals) |-> f(forkVals[k])]

EquivGuard == InvFast <=> Inv

InvImpliesContract ==
  Inv => /\ EffectExactlyOnce
         /\ PrefixConsistency
         /\ ForkDeterminism
         /\ CheckpointValidity
         /\ ConsumeOnce
         /\ RecoveryDeterminism

InitImpliesInv == Init => Inv
=========================================================================
