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

(***************************************************************************)
(* OBLIGATION 1 -- initiation.                                             *)
(***************************************************************************)
THEOREM Initiation == ASSUME Reference PROVE Init => Inv
  OBVIOUS \* expected to close by definition expansion; if not, BY DEF Init, Inv

(***************************************************************************)
(* OBLIGATION 2 -- consecution. One case per action. Each case is a        *)
(* separate obligation, so a failure localizes to one action.             *)
(***************************************************************************)
THEOREM Consecution == ASSUME Reference PROVE Inv /\ [Next]_vars => Inv'
<1> SUFFICES ASSUME Inv, [Next]_vars PROVE Inv'
    OBVIOUS
<1>1. CASE ExecTask
      \* pc > frontier from (frontier = pc-1), so the pcRegress guard is
      \* false and PC is preserved; frontier' = pc = pc'-1; ckpts and
      \* effects each advance by exactly one at index pc.
      OMITTED
<1>2. CASE EmitInterrupt
      \* Only waiting changes; the guard gives pc = IP and
      \* consumedVal = NoVal, which is the waiting conjunct.
      OMITTED
<1>3. CASE \E v \in Values : Consume(v)
      \* waiting gives pc = IP; with frontier = pc-1 we get frontier = IP-1,
      \* hence effects[IP] = 0 before the increment, hence EO survives; and
      \* frontier' = IP = (IP+1)-1 = pc'-1.
      OMITTED
<1>4. CASE \E v \in Values : ForkResume(v)
      \* Reference: forkOuts' appends f(v) alongside forkVals' appending v,
      \* so the FD conjunct extends. No other variable moves.
      OMITTED
<1>5. CASE CrashRecover
      \* Reference branch only: pc' = frontier+1 = pc, so frontier' = pc'-1
      \* is preserved and no effect or checkpoint moves; recHist' appends
      \* dec = "skip", so the RD conjunct extends and crashes' matches.
      OMITTED
<1>6. CASE ExtraResume
      \* Reference: effects UNCHANGED (the increment is under
      \* FaultDoubleConsume), so every conjunct is stable.
      OMITTED
<1>7. CASE UNCHANGED vars
      OMITTED
<1>8. QED BY <1>1, <1>2, <1>3, <1>4, <1>5, <1>6, <1>7 DEF Next

(***************************************************************************)
(* OBLIGATION 3 -- the invariant implies the contract.                     *)
(***************************************************************************)
THEOREM Sufficiency == ASSUME Reference PROVE Inv => ContractConjunction
  OMITTED

(***************************************************************************)
(* THE PAPER'S CLAIM, UNBOUNDED. Quantified over the CONSTANTS by the      *)
(* module's ASSUME, so no constant appears in the statement.               *)
(***************************************************************************)
THEOREM RefSafety == ASSUME Reference PROVE Spec => []ContractConjunction
<1>1. Init => Inv                        BY Initiation
<1>2. Inv /\ [Next]_vars => Inv'         BY Consecution
<1>3. Spec => []Inv                      BY <1>1, <1>2, PTL DEF Spec
<1>4. Inv => ContractConjunction         BY Sufficiency
<1>5. QED                                BY <1>3, <1>4, PTL

(***************************************************************************)
(* COROLLARY -- the CO-e / EO containment, promoted from "a fact about two *)
(* formulas" (Sec. 3.4(iv)) to a discharged theorem. This is the cheapest  *)
(* obligation in the file and should be the first one closed.              *)
(***************************************************************************)
THEOREM ConsumeOnceFromEO ==
  ASSUME IP \in Tasks, EffectExactlyOnce PROVE ConsumeOnce
  BY DEF EffectExactlyOnce, ConsumeOnce

(***************************************************************************)
(* LEMMA -- durable-frontier monotonicity, named in Sec. 9 as              *)
(* lemma-shaped. It follows from the frontier conjunct but is worth        *)
(* standing alone, because it is the property a reader expects a          *)
(* persistence plane to have and it holds for EVERY configuration, not    *)
(* just the reference one.                                                 *)
(***************************************************************************)
FrontierMonotone == [][frontier' >= frontier]_vars

THEOREM Monotonicity == Spec => FrontierMonotone
  OMITTED

=============================================================================
