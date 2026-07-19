--------------------------- MODULE LangGraphFork ---------------------------
(***************************************************************************)
(* A framework-derived operational model of LangGraph's interrupt-resume   *)
(* path, abstracted from the persistence documentation and the reproduced  *)
(* behavior of issue #6663 (probe 113, T2).                                *)
(*                                                                         *)
(* The modeled algorithm (AsImplemented): a resume value delivered to a    *)
(* (thread, checkpoint) is recorded as a PENDING WRITE attached to that    *)
(* checkpoint. An invocation addressed to a checkpoint that ALREADY        *)
(* carries a resume write does not record a new value: it replays the      *)
(* recorded write and re-serves the existing branch outcome. This rule is  *)
(* what makes same-value re-invocation idempotent -- a designed property   *)
(* -- and, as TLC shows, it is ALSO exactly what breaks fork determinism:  *)
(* the violation is emergent from the algorithm, not injected by a fault   *)
(* switch.                                                                 *)
(*                                                                         *)
(* The repaired algorithm (ForkKeyed = TRUE): pending resume writes are    *)
(* keyed by (checkpoint, resume ordinal) instead of (checkpoint). TLC      *)
(* verifies that fork determinism AND replay idempotence hold together --  *)
(* the repair implemented by Remit's branch keying.                        *)
(*                                                                         *)
(* Invariants:                                                             *)
(*   ForkDeterminism   every recorded outcome equals the branch semantics  *)
(*                     applied to the value that invocation supplied       *)
(*   ReplayIdempotence re-invoking with a value already recorded for the   *)
(*                     addressed slot re-serves the same outcome           *)
(* AsImplemented satisfies ReplayIdempotence and violates ForkDeterminism; *)
(* ForkKeyed satisfies both.                                               *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS
  Values,      \* resume-value domain, e.g. {"va","vb"}
  NoWrite,     \* model value: slot holds no recorded resume write
  MaxInvokes,  \* bound on resume invocations addressed to the checkpoint
  ForkKeyed    \* FALSE = algorithm as implemented; TRUE = repaired keying

ASSUME /\ NoWrite \notin Values
       /\ MaxInvokes \in Nat \ {0}
       /\ ForkKeyed \in BOOLEAN

(* Branch semantics: the outcome a branch computes from a consumed value.  *)
(* Injective by construction (identity), so distinct values must yield     *)
(* distinct outcomes under a correct fork.                                 *)
F(v) == v

VARIABLES
  slot,      \* AsImplemented: the checkpoint's single pending-write slot
  slots,     \* ForkKeyed: pending writes keyed by resume ordinal
  supplied,  \* Seq(Values): value each invocation supplied
  served     \* Seq(Values): outcome each invocation was served

vars == << slot, slots, supplied, served >>

Init ==
  /\ slot     = NoWrite
  /\ slots    = [k \in 1..MaxInvokes |-> NoWrite]
  /\ supplied = << >>
  /\ served   = << >>

(* One resume invocation addressed to the interrupt checkpoint with value  *)
(* v. AsImplemented consults the checkpoint's single slot; ForkKeyed       *)
(* consults the slot for THIS invocation's ordinal.                        *)
Invoke(v) ==
  /\ Len(supplied) < MaxInvokes
  /\ supplied' = Append(supplied, v)
  /\ IF ForkKeyed
     THEN LET k == Len(supplied) + 1 IN
          IF slots[k] = NoWrite
          THEN /\ slots' = [slots EXCEPT ![k] = v]
               /\ served' = Append(served, F(v))
               /\ slot' = slot
          ELSE /\ served' = Append(served, F(slots[k]))
               /\ slots' = slots
               /\ slot' = slot
     ELSE IF slot = NoWrite
          THEN /\ slot' = v                       \* record the write
               /\ served' = Append(served, F(v))
               /\ slots' = slots
          ELSE /\ served' = Append(served, F(slot)) \* replay recorded write
               /\ slots' = slots
               /\ slot' = slot

Next == \E v \in Values : Invoke(v)

Spec == Init /\ [][Next]_vars

--------------------------------------------------------------------------
TypeOK ==
  /\ slot \in Values \cup {NoWrite}
  /\ Len(supplied) = Len(served)
  /\ Len(supplied) <= MaxInvokes

(* FD: what you supplied is what your branch computes on. *)
ForkDeterminism ==
  \A k \in 1..Len(served) : served[k] = F(supplied[k])

(* Idempotence of same-value re-invocation against the addressed slot:    *)
(* if the slot consulted by invocation k already held exactly supplied[k], *)
(* the served outcome equals the recorded one. In this model that reduces *)
(* to: any invocation re-supplying the recorded value is served F(value). *)
ReplayIdempotence ==
  \A k \in 1..Len(served) :
     ( /\ ~ForkKeyed
       /\ k > 1
       /\ supplied[k] = supplied[1] ) => served[k] = F(supplied[1])

===============================================================================
