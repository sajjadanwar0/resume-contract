---------------------------- MODULE IndCheck ----------------------------
(***************************************************************************)
(* Inductive-invariant check for the REFERENCE configuration of            *)
(* ResumeContract.tla.                                                     *)
(*                                                                         *)
(* METHOD.  TLC is run with INIT <- InvFast and NEXT <- Next.  Every state *)
(* satisfying the invariant is taken as an initial state and every         *)
(* successor is checked against Inv, so a clean run establishes            *)
(*                                                                         *)
(*     Inv /\ [Next]_vars => Inv'                                          *)
(*                                                                         *)
(* i.e. Inv is INDUCTIVE at the configured constants -- strictly stronger  *)
(* than reachability, because it quantifies over unreachable Inv-states    *)
(* too.  InitImpliesInv and InvImpliesContract are state predicates        *)
(* checked in the same run.                                                *)
(*                                                                         *)
(* RECEIPTS (TLC 2026.04.09, 4 workers, CHECK_DEADLOCK FALSE):             *)
(*   IndCheck.cfg     N=3 IP=2 |V|=2 R=2 C=1 E=1 ->   8,610 st., no error  *)
(*   IndCheck_R2.cfg  N=4 IP=3 |V|=2 R=2 C=2 E=1 -> 450,926 st., no error  *)
(*   IndCheck_R3.cfg  N=3 IP=2 |V|=3 R=3 C=1 E=2 ->  97,800 st., no error  *)
(* Three sets, between which EACH of the six configuration bounds varies   *)
(* in at least one pair: R2 moves the task/crash axes, R3 moves the fork   *)
(* axes (value domain, resume budget, stray-resume budget) that R0 and R2  *)
(* leave pinned and that the FD and forkOuts conjuncts depend on.  The     *)
(* inductive argument is per-action rather than constant-dependent, which  *)
(* is the evidence that TLAPS should discharge the unbounded form          *)
(* (ResumeContractProofs.tla).                                            *)
(*                                                                         *)
(* CHECK_DEADLOCK FALSE IS REQUIRED.  An Inv-state with no enabled action  *)
(* is normal here (e.g. waiting with the resume budget exhausted) and is   *)
(* not a defect.  With deadlock checking on, TLC reports it as an error at *)
(* depth 1 and the run says nothing.                                       *)
(*                                                                         *)
(* WORKER COUNT DOES NOT AFFECT THE COUNTS.  This enumerates all initial   *)
(* states rather than racing a BFS to a counterexample, so counts are      *)
(* deterministic across -workers.  The paper's single-worker convention    *)
(* for counterexample depths does not apply to this module.                *)
(*                                                                         *)
(* WHY TWO FORMS OF THE SAME PREDICATE.  Inv is the readable form and the  *)
(* one the paper quotes.  It leads with TypeOKS, so TLC enumerates the     *)
(* full variable cross product and only then filters: 1.3e9 candidates at  *)
(* N=3 and 2.8e12 at N=4 -- three minutes at the reference constants and   *)
(* roughly 113 hours at the wider set.  InvFast instead ASSIGNS every      *)
(* variable Inv pins (frontier, effects, forkOuts, pcRegress) and narrows  *)
(* each sequence domain to the exact length Inv forces.  Same predicate,   *)
(* 38x faster at N=3, wider set under five minutes.  EquivGuard is checked *)
(* on every visited state so the re-expression is not taken on trust, and  *)
(* the whole-run check is that the distinct-state count must be IDENTICAL  *)
(* to the naive form's 8,610 at the reference constants.                   *)
(*                                                                         *)
(* THE THREE CONJUNCTS THAT CARRY THE ARGUMENT.  Two were found by TLC     *)
(* REJECTING weaker candidates, not by inspection:                         *)
(*   frontier = pc - 1       ExecTask is the sole writer of pcRegress, via *)
(*                           pcRegress' = pcRegress \/ (pc <= frontier),   *)
(*                           and this makes that disjunct permanently      *)
(*                           false.  PC therefore holds STRUCTURALLY, not  *)
(*                           by stipulation -- the answer to the objection *)
(*                           that PrefixConsistency == ~pcRegress is a     *)
(*                           flag the transition relation sets at will.    *)
(*   Len(ckpts) = frontier   One completion record per completed task.     *)
(*                           Without it an Inv-state with long ckpts and   *)
(*                           small pc lets ExecTask push ckpts past any    *)
(*                           a-priori bound, and the check fails.          *)
(*   Len(recHist) = crashes  One recovery record per crash.  Without it    *)
(*                           the check fails the same way, via             *)
(*                           CrashRecover.                                 *)
(* The effects conjunct is an EQUALITY, not the <= 1 the contract states:  *)
(* <= 1 is not inductive, since nothing forbids a second increment of a    *)
(* task sitting at 0.  Pinning each counter to the frontier yields EO and  *)
(* CO-e as immediate consequences, which is also how the CO-e / EO         *)
(* containment becomes a one-line corollary rather than "a fact about two  *)
(* formulas".                                                              *)
(***************************************************************************)
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

(* Enumeration-efficient re-expression of Inv: every variable that Inv    *)
(* PINS is assigned rather than enumerated-then-filtered, and every        *)
(* sequence domain is narrowed to the exact length Inv forces.            *)
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
