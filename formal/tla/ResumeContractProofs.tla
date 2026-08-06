------------------------- MODULE ResumeContractProofs -------------------------

EXTENDS ResumeContract, TLAPS, SequenceTheorems, NaturalsInduction

Reference ==
  /\ ~FaultReplay
  /\ ~FaultForkIgnore
  /\ ~FaultInvalidPersist
  /\ ~FaultNondetRecovery
  /\ ~FaultDoubleConsume
  /\ ~FaultPrefixReplay

CkptRec == [idx : Tasks, valid : BOOLEAN]
Decs    == {"skip", "replay", "reexec", "prefixreplay"}
RecRec  == [dur : 0..NTasks, dec : Decs]

TypeOKS ==
  /\ pc           \in 1..NTasks + 1
  /\ effects      \in [Tasks -> 0..1]
  /\ frontier     \in 0..NTasks
  /\ waiting      \in BOOLEAN
  /\ consumedVal  \in Values \cup {NoVal}
  /\ crashes      \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ pcRegress    \in BOOLEAN
  /\ ckpts        \in Seq(CkptRec)
  /\ forkVals     \in Seq(Values)
  /\ forkOuts     \in Seq({f(v) : v \in Values})
  /\ recHist      \in Seq(RecRec)


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

ContractConjunction ==
  /\ EffectExactlyOnce
  /\ PrefixConsistency
  /\ ForkDeterminism
  /\ CheckpointValidity
  /\ ConsumeOnce
  /\ RecoveryDeterminism

THEOREM Initiation == ASSUME Reference PROVE Init => Inv
  OBVIOUS

THEOREM Consecution == ASSUME Reference PROVE Inv /\ [Next]_vars => Inv'
<1> SUFFICES ASSUME Inv, [Next]_vars PROVE Inv'
    OBVIOUS
<1>1. CASE ExecTask
      OMITTED
<1>2. CASE EmitInterrupt
      OMITTED
<1>3. CASE \E v \in Values : Consume(v)
      OMITTED
<1>4. CASE \E v \in Values : ForkResume(v)
      OMITTED
<1>5. CASE CrashRecover
      OMITTED
<1>6. CASE ExtraResume
      OMITTED
<1>7. CASE UNCHANGED vars
      OMITTED
<1>8. QED BY <1>1, <1>2, <1>3, <1>4, <1>5, <1>6, <1>7 DEF Next

THEOREM Sufficiency == ASSUME Reference PROVE Inv => ContractConjunction
  OMITTED

THEOREM RefSafety == ASSUME Reference PROVE Spec => []ContractConjunction
<1>1. Init => Inv                        BY Initiation
<1>2. Inv /\ [Next]_vars => Inv'         BY Consecution
<1>3. Spec => []Inv                      BY <1>1, <1>2, PTL DEF Spec
<1>4. Inv => ContractConjunction         BY Sufficiency
<1>5. QED                                BY <1>3, <1>4, PTL


THEOREM ConsumeOnceFromEO ==
  ASSUME IP \in Tasks, EffectExactlyOnce PROVE ConsumeOnce
  BY DEF EffectExactlyOnce, ConsumeOnce

FrontierMonotone == [][frontier' >= frontier]_vars

THEOREM Monotonicity == Spec => FrontierMonotone
  OMITTED

=============================================================================
