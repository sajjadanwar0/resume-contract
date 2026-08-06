--------------------------- MODULE LangGraphFork ---------------------------

EXTENDS Naturals, Sequences

CONSTANTS
  Values,
  NoWrite,
  MaxInvokes,
  ForkKeyed

ASSUME /\ NoWrite \notin Values
       /\ MaxInvokes \in Nat \ {0}
       /\ ForkKeyed \in BOOLEAN

F(v) == v

VARIABLES
  slot,
  slots,
  supplied,
  served

vars == << slot, slots, supplied, served >>

Init ==
  /\ slot     = NoWrite
  /\ slots    = [k \in 1..MaxInvokes |-> NoWrite]
  /\ supplied = << >>
  /\ served   = << >>

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
          THEN /\ slot' = v
               /\ served' = Append(served, F(v))
               /\ slots' = slots
          ELSE /\ served' = Append(served, F(slot))
               /\ slots' = slots
               /\ slot' = slot

Next == \E v \in Values : Invoke(v)

Spec == Init /\ [][Next]_vars

--------------------------------------------------------------------------
TypeOK ==
  /\ slot \in Values \cup {NoWrite}
  /\ Len(supplied) = Len(served)
  /\ Len(supplied) <= MaxInvokes

ForkDeterminism ==
  \A k \in 1..Len(served) : served[k] = F(supplied[k])

ReplayIdempotence ==
  \A k \in 1..Len(served) :
     ( /\ ~ForkKeyed
       /\ k > 1
       /\ supplied[k] = supplied[1] ) => served[k] = F(supplied[1])

===============================================================================
