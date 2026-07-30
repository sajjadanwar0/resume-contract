------------------------- MODULE ResumeContractProofs -------------------------
(***************************************************************************)
(* TLAPS proof obligations for the REFERENCE configuration of              *)
(* ResumeContract.tla.                                                     *)
(*                                                                         *)
(* STATUS -- read this before citing anything from this file.              *)
(*                                                                         *)
(*   Inv (below) is IDENTICAL to the predicate in IndCheck.tla, which was  *)
(*   machine-checked INDUCTIVE by TLC at the reference constants           *)
(*   (NTasks=3, IP=2, |Values|=2, MaxResumes=2, MaxCrashes=1,              *)
(*   MaxExtraResumes=1): 8,610 distinct Inv-states enumerated as initial   *)
(*   states, every successor checked, "Model checking completed. No error  *)
(*   has been found."  That run also discharged InvImpliesContract and     *)
(*   InitImpliesInv as state predicates.                                   *)
(*                                                                         *)
(*   The THEOREMs below are NOT YET DISCHARGED. Every proof body is a      *)
(*   skeleton with OMITTED leaves. Do not cite this file as proved until   *)
(*   `tlapm` reports every obligation closed and the tally is recorded in  *)
(*   the artifact the way VERIFICATION.md records the Verus tallies.       *)
(*                                                                         *)
(* WHY THIS IS THE RIGHT TARGET.  TLC establishes the conjunction over the *)
(* full reachable space AT STATED CONSTANTS and is silent beyond them --   *)
(* the "bound-relative" half of Sec. 3.4.  Theorem RefSafety below is      *)
(* quantified over the CONSTANTS, so discharging it converts the paper's   *)
(* universal claim from bound-relative to unbounded in NTasks, IP,         *)
(* |Values|, MaxResumes, MaxCrashes, and MaxExtraResumes.  That is the     *)
(* single addition that answers "the authors rely on bounded model         *)
(* checking without proving cut-off existence."                            *)
(*                                                                         *)
(* WHAT IT DOES NOT DO.  It does not generalize the SEPARATION witnesses   *)
(* to parameterized families, and it should not: a separation claim is     *)
(* existential and one exhaustively-checked finite witness settles it      *)
(* outright.  Proving families would concede ground that does not need     *)
(* conceding.                                                              *)
(***************************************************************************)
EXTENDS ResumeContract, TLAPS, SequenceTheorems, NaturalsInduction

(***************************************************************************)
(* The reference configuration: all six fault switches off.                *)
(***************************************************************************)
Reference ==
  /\ ~FaultReplay
  /\ ~FaultForkIgnore
  /\ ~FaultInvalidPersist
  /\ ~FaultNondetRecovery
  /\ ~FaultDoubleConsume
  /\ ~FaultPrefixReplay

(***************************************************************************)
(* Bounded type invariant. Identical in content to IndCheck.tla's TypeOKS  *)
(* minus the sequence-domain bounds, which the counter conjuncts of Inv    *)
(* now imply (that was the point of discovering them).                     *)
(***************************************************************************)
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

(***************************************************************************)
(* THE INDUCTIVE INVARIANT.                                                *)
(*                                                                         *)
(* Three conjuncts carry the argument and two of them were found by TLC    *)
(* REJECTING weaker candidates, not by inspection:                         *)
(*                                                                         *)
(*   frontier = pc - 1     the durable frontier trails the program counter *)
(*                         by exactly one; this is what makes PC hold,     *)
(*                         because ExecTask's sole write to pcRegress is   *)
(*                         guarded by (pc <= frontier), which this forbids *)
(*                                                                         *)
(*   Len(ckpts) = frontier one completion record per completed task.       *)
(*                         WITHOUT THIS the check fails: an Inv-state with *)
(*                         a long ckpts and a small pc lets ExecTask push  *)
(*                         ckpts past any a-priori bound.                  *)
(*                                                                         *)
(*   Len(recHist) = crashes one recovery record per crash.  WITHOUT THIS   *)
(*                         the check fails the same way via CrashRecover.  *)
(*                                                                         *)
(* The effects conjunct is an EQUALITY, not the <= 1 the contract states.  *)
(* That is deliberate: <= 1 is not inductive (nothing stops a second       *)
(* increment of a task at 0), while the equality pins each counter to the  *)
(* frontier and yields EO and CO-e as immediate consequences.              *)
(***************************************************************************)
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
