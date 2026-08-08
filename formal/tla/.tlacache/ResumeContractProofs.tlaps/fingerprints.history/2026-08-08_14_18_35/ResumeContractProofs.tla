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

ASSUME ConstAssumption ==
  /\ NTasks \in Nat \ {0}
  /\ IP \in 2..NTasks
  /\ NoVal \notin Values
  /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat

LEMMA ConstFacts ==
  /\ NTasks \in Nat \ {0}
  /\ IP \in 2..NTasks
  /\ NoVal \notin Values
  /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat
BY ConstAssumption

THEOREM Initiation == ASSUME Reference PROVE Init => Inv
<1> SUFFICES ASSUME Init PROVE Inv
    OBVIOUS
<1>1. TypeOKS
      BY ConstFacts DEF Init, TypeOKS, Tasks, CkptRec, RecRec
<1>2. frontier = pc - 1
      BY DEF Init
<1>3. Len(ckpts) = frontier /\ Len(recHist) = crashes
      /\ Len(forkOuts) = Len(forkVals)
      BY DEF Init
<1>4. waiting => (pc = IP /\ consumedVal = NoVal)
      BY DEF Init
<1>5. \A t \in Tasks : effects[t] = (IF t <= frontier THEN 1 ELSE 0)
      BY ConstFacts DEF Init, Tasks
<1>6. /\ \A k \in 1..Len(ckpts)    : ckpts[k].valid
      /\ \A k \in 1..Len(forkVals) : forkOuts[k] = f(forkVals[k])
      /\ \A k \in 1..Len(recHist)  : recHist[k].dec = "skip"
      BY DEF Init
<1>7. pcRegress = FALSE
      BY DEF Init
<1>8. QED
      BY <1>1, <1>2, <1>3, <1>4, <1>5, <1>6, <1>7 DEF Inv

THEOREM Consecution == ASSUME Reference PROVE Inv /\ [Next]_vars => Inv'
<1> SUFFICES ASSUME Inv, [Next]_vars PROVE Inv'
    OBVIOUS
<1> USE ConstFacts DEF Reference, Tasks
<1>1. CASE ExecTask
  <2>1. TypeOKS'
        BY <1>1 DEF ExecTask, Inv, TypeOKS, CkptRec, AppendProperties
  <2>2. frontier' = pc' - 1
        BY <1>1 DEF ExecTask, Inv, TypeOKS
  <2>3. Len(ckpts') = frontier'
        BY <1>1 DEF ExecTask, Inv, TypeOKS
  <2>4. Len(recHist') = crashes' /\ Len(forkOuts') = Len(forkVals')
        BY <1>1 DEF ExecTask, Inv, TypeOKS
  <2>5. (waiting' => (pc' = IP /\ consumedVal' = NoVal))
        BY <1>1 DEF ExecTask, Inv
  <2>6. \A t \in Tasks : effects'[t] = (IF t <= frontier' THEN 1 ELSE 0)
        BY <1>1 DEF ExecTask, Inv, TypeOKS, Tasks
  <2>7. \A k \in 1..Len(ckpts') : ckpts'[k].valid
        BY <1>1 DEF ExecTask, Inv, TypeOKS
  <2>8. \A k \in 1..Len(forkVals') : forkOuts'[k] = f(forkVals'[k])
        BY <1>1 DEF ExecTask, Inv
  <2>9. \A k \in 1..Len(recHist') : recHist'[k].dec = "skip"
        BY <1>1 DEF ExecTask, Inv
  <2>10. pcRegress' = FALSE
        BY <1>1 DEF ExecTask, Inv, TypeOKS
  <2>11. QED
        BY <2>1, <2>2, <2>3, <2>4, <2>5, <2>6, <2>7, <2>8, <2>9, <2>10
        DEF Inv
<1>2. CASE EmitInterrupt
      BY <1>2 DEF EmitInterrupt, Inv, TypeOKS
<1>3. CASE \E v \in Values : Consume(v)
  <2> PICK v \in Values : Consume(v)
      BY <1>3
  <2>a. pc = IP /\ consumedVal = NoVal /\ frontier = IP - 1
        BY DEF Consume, Inv
  <2>1. TypeOKS'
        BY <2>a DEF Consume, Inv, TypeOKS, CkptRec
  <2>2. frontier' = pc' - 1
        BY <2>a DEF Consume, Inv, TypeOKS
  <2>3. Len(ckpts') = frontier'
        BY <2>a DEF Consume, Inv, TypeOKS
  <2>4. Len(recHist') = crashes' /\ Len(forkOuts') = Len(forkVals')
        BY <2>a DEF Consume, Inv, TypeOKS
  <2>5. (waiting' => (pc' = IP /\ consumedVal' = NoVal))
        BY DEF Consume
  <2>6. \A t \in Tasks : effects'[t] = (IF t <= frontier' THEN 1 ELSE 0)
        BY <2>a DEF Consume, Inv, TypeOKS, Tasks
  <2>7. \A k \in 1..Len(ckpts') : ckpts'[k].valid
        BY <2>a DEF Consume, Inv, TypeOKS
  <2>8. \A k \in 1..Len(forkVals') : forkOuts'[k] = f(forkVals'[k])
        BY <2>a DEF Consume, Inv, TypeOKS
  <2>9. \A k \in 1..Len(recHist') : recHist'[k].dec = "skip"
        BY DEF Consume, Inv
  <2>10. pcRegress' = FALSE
        BY DEF Consume, Inv
  <2>11. QED
        BY <2>1, <2>2, <2>3, <2>4, <2>5, <2>6, <2>7, <2>8, <2>9, <2>10
        DEF Inv
<1>4. CASE \E v \in Values : ForkResume(v)
  <2> PICK v \in Values : ForkResume(v)
      BY <1>4
  <2>b. ~waiting
        BY DEF ForkResume, Inv
  <2>1. TypeOKS'
        BY DEF ForkResume, Inv, TypeOKS
  <2>2. Len(forkOuts') = Len(forkVals')
        BY DEF ForkResume, Inv, TypeOKS
  <2>3. \A k \in 1..Len(forkVals') : forkOuts'[k] = f(forkVals'[k])
        BY DEF ForkResume, Inv, TypeOKS
  <2>4. QED
        BY <2>b, <2>1, <2>2, <2>3 DEF ForkResume, Inv
<1>5. CASE CrashRecover
  <2>c. /\ pc'      = frontier + 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "skip"])
        BY <1>5 DEF CrashRecover
  <2>1. TypeOKS'
        BY <1>5, <2>c DEF CrashRecover, Inv, TypeOKS, RecRec, Decs
  <2>2. frontier' = pc' - 1
        BY <1>5, <2>c DEF CrashRecover, Inv, TypeOKS
  <2>3. Len(recHist') = crashes'
        BY <1>5, <2>c DEF CrashRecover, Inv, TypeOKS
  <2>4. \A k \in 1..Len(recHist') : recHist'[k].dec = "skip"
        BY <1>5, <2>c DEF CrashRecover, Inv, TypeOKS
  <2>5. QED
        BY <1>5, <2>c, <2>1, <2>2, <2>3, <2>4 DEF CrashRecover, Inv
<1>6. CASE ExtraResume
      BY <1>6 DEF ExtraResume, Inv, TypeOKS
<1>7. CASE UNCHANGED vars
      BY <1>7 DEF vars, Inv, TypeOKS
<1>8. QED
      BY <1>1, <1>2, <1>3, <1>4, <1>5, <1>6, <1>7 DEF Next

THEOREM Sufficiency == ASSUME Reference PROVE Inv => ContractConjunction
<1> SUFFICES ASSUME Inv PROVE ContractConjunction
    OBVIOUS
<1> USE ConstFacts
<1>1. EffectExactlyOnce
      BY DEF Inv, TypeOKS, EffectExactlyOnce, Tasks
<1>2. PrefixConsistency
      BY DEF Inv, PrefixConsistency
<1>3. ForkDeterminism
      BY DEF Inv, ForkDeterminism
<1>4. CheckpointValidity
      BY DEF Inv, CheckpointValidity
<1>5. ConsumeOnce
      BY DEF Inv, TypeOKS, ConsumeOnce, Tasks
<1>6. RecoveryDeterminism
      BY DEF Inv, RecoveryDeterminism
<1>7. QED
      BY <1>1, <1>2, <1>3, <1>4, <1>5, <1>6 DEF ContractConjunction

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

THEOREM Monotonicity == ASSUME Reference PROVE Spec => FrontierMonotone
<1>1. Inv /\ [Next]_vars => frontier' >= frontier
  <2> SUFFICES ASSUME Inv, [Next]_vars PROVE frontier' >= frontier
      OBVIOUS
  <2> USE ConstFacts DEF Reference, Tasks
  <2>1. CASE ExecTask       BY <2>1 DEF ExecTask, Inv, TypeOKS
  <2>2. CASE EmitInterrupt  BY <2>2 DEF EmitInterrupt, Inv, TypeOKS
  <2>3. CASE \E v \in Values : Consume(v)
        BY <2>3 DEF Consume, Inv, TypeOKS
  <2>4. CASE \E v \in Values : ForkResume(v)
        BY <2>4 DEF ForkResume, Inv, TypeOKS
  <2>5. CASE CrashRecover   BY <2>5 DEF CrashRecover, Inv, TypeOKS
  <2>6. CASE ExtraResume    BY <2>6 DEF ExtraResume, Inv, TypeOKS
  <2>7. CASE UNCHANGED vars BY <2>7 DEF vars, Inv, TypeOKS
  <2>8. QED BY <2>1, <2>2, <2>3, <2>4, <2>5, <2>6, <2>7 DEF Next
<1>2. Init => Inv                        BY Initiation
<1>3. Inv /\ [Next]_vars => Inv'         BY Consecution
<1>4. Spec => []Inv                      BY <1>2, <1>3, PTL DEF Spec
<1>5. QED
      BY <1>1, <1>4, PTL DEF Spec, FrontierMonotone

=============================================================================
