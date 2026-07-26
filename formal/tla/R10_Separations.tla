--------------------------- MODULE R10_Separations ---------------------------
(***************************************************************************)
(* Separating models for the three properties the reference fault set      *)
(* cannot separate from the conjunction of the others.                     *)
(*                                                                         *)
(* Proposition 2 establishes conjunction-independence for FD (ForkIgnore)  *)
(* and CV (InvalidPersist) only: every other fault switch in               *)
(* ResumeContract.tla breaks several properties at once, so RD has no      *)
(* separating model at all and PC and EO carry single-property             *)
(* separations. This module closes those three cells. Each switch is an    *)
(* effect-safe or control-safe variant of a mechanism the study observed,  *)
(* chosen so that exactly ONE contract property fails:                     *)
(*                                                                         *)
(*   FaultRegateNondet  RD only. At identical durable state one recovery   *)
(*                      continues past the consumed gate and another       *)
(*                      re-arms it, the re-consumption served from the     *)
(*                      durable record. The recovery DECISION differs at   *)
(*                      equal durable state -- which tasks wait versus     *)
(*                      continue -- while no effect fires twice and no     *)
(*                      completed task re-executes. This is the #8039      *)
(*                      ambiguity with the effect duplication removed.     *)
(*                                                                         *)
(*   FaultRebuild       PC only. Recovery restarts from task 1 with the    *)
(*                      working state rebuilt from initial values rather   *)
(*                      than re-derived from the log, every prefix effect  *)
(*                      served from the durable record. Deterministic, so  *)
(*                      RD holds; memoized, so EO and CO hold. This is     *)
(*                      R7_StateRebuild's class, lifted into the full      *)
(*                      plane so that FD, CV, and CO are present and       *)
(*                      checkable alongside.                               *)
(*                                                                         *)
(*   FaultRedeliver     EO only. Recovery re-issues the durable frontier   *)
(*                      task's external effect -- at-least-once delivery   *)
(*                      at the effect layer -- while control resumes at    *)
(*                      frontier+1 and never re-enters the prefix. The     *)
(*                      gated task is excluded, so CO holds; the rule is   *)
(*                      deterministic, so RD holds. This is the           *)
(*                      documented at-least-once idiom (LlamaIndex         *)
(*                      Workflows) modeled as a fault against the          *)
(*                      contract's exactly-once requirement.               *)
(*                                                                         *)
(* PC's encoding here is the CONJUNCTION of the two committed encodings:   *)
(* ResumeContract's re-entry flag (~pcRegress) and R7_StateRebuild's       *)
(* state-provenance clause (after a crash the working state derives from   *)
(* the log). Property 1 admits memoized replay, so re-entry that executes  *)
(* nothing and fires no effect is not a violation under either encoding;   *)
(* rebuilding from initial values is a violation under the second. The     *)
(* reference configuration (all three switches FALSE) satisfies all six    *)
(* invariants, which is the module's own sanity cell.                      *)
(*                                                                         *)
(* All six invariant formulas are otherwise character-identical to         *)
(* ResumeContract.tla, so a "holds" cell here concerns the same property.  *)
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
  lineage,        \* "log" | "initial" : provenance of the working state
  replaying       \* TRUE while traversing the prefix in memoized replay

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
(* Ordinary forward execution of a non-gated task. Never fires while a   *)
(* memoized replay pass is traversing the durable prefix.                *)
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

(* Memoized traversal of the durable prefix: the task is served from the *)
(* durable record, so no effect fires and nothing re-executes. Property 1 *)
(* admits this; the PC violation of FaultRebuild is carried by lineage.   *)
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

(* Consuming the interrupt. The gated effect fires only if the durable    *)
(* record does not already carry it: a re-armed gate (FaultRegateNondet)  *)
(* is served from the record, which is what keeps EO and CO intact while  *)
(* the recovery DECISION still differs at equal durable state.            *)
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
(* Reference recovery: continue from the durable frontier.                *)
RecSkip ==
  /\ ~FaultRebuild
  /\ pc'        = frontier + 1
  /\ effects'   = effects
  /\ waiting'   = FALSE
  /\ lineage'   = "log"
  /\ replaying' = FALSE
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "skip"])

(* RD-only: re-arm the consumed gate. Control moves to IP with the        *)
(* interrupt pending; the re-consumption is served from the record.       *)
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

(* PC-only: rebuild the working state from initial values and traverse    *)
(* the prefix memoized.                                                   *)
RecRebuild ==
  /\ FaultRebuild
  /\ pc'        = 1
  /\ effects'   = effects
  /\ waiting'   = FALSE
  /\ lineage'   = "initial"
  /\ replaying' = (frontier >= 1)
  /\ recHist'   = Append(recHist, [dur |-> frontier, dec |-> "rebuild"])

(* EO-only: at-least-once redelivery of the frontier task's effect while  *)
(* control resumes past it. The gated task is excluded, so CO holds.      *)
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

EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1                 \* EO
PrefixConsistency   == ~pcRegress /\ ((crashes > 0) => lineage = "log") \* PC
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])                  \* FD
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid          \* CV
ConsumeOnce         == effects[IP] <= 1                                 \* CO
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec          \* RD

===============================================================================
