---- MODULE LangGraphFork_TTrace_1784208899 ----
EXTENDS Sequences, TLCExt, LangGraphFork_TEConstants, Toolbox, Naturals, TLC, LangGraphFork

_expression ==
    LET LangGraphFork_TEExpression == INSTANCE LangGraphFork_TEExpression
    IN LangGraphFork_TEExpression!expression
----

_trace ==
    LET LangGraphFork_TETrace == INSTANCE LangGraphFork_TETrace
    IN LangGraphFork_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        slots = (<<NoWrite, NoWrite>>)
        /\
        supplied = (<<va, vb>>)
        /\
        served = (<<va, va>>)
        /\
        slot = (va)
    )
----

_init ==
    /\ slot = _TETrace[1].slot
    /\ supplied = _TETrace[1].supplied
    /\ served = _TETrace[1].served
    /\ slots = _TETrace[1].slots
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ slot  = _TETrace[i].slot
        /\ slot' = _TETrace[j].slot
        /\ supplied  = _TETrace[i].supplied
        /\ supplied' = _TETrace[j].supplied
        /\ served  = _TETrace[i].served
        /\ served' = _TETrace[j].served
        /\ slots  = _TETrace[i].slots
        /\ slots' = _TETrace[j].slots

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("LangGraphFork_TTrace_1784208899.json", _TETrace)

=============================================================================

 Note that you can extract this module `LangGraphFork_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `LangGraphFork_TEExpression.tla` file takes precedence 
  over the module `LangGraphFork_TEExpression` below).

---- MODULE LangGraphFork_TEExpression ----
EXTENDS Sequences, TLCExt, LangGraphFork_TEConstants, Toolbox, Naturals, TLC, LangGraphFork

expression == 
    [
        \* To hide variables of the `LangGraphFork` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        slot |-> slot
        ,supplied |-> supplied
        ,served |-> served
        ,slots |-> slots
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_slotUnchanged |-> slot = slot'
        
        \* Format the `slot` variable as Json value.
        \* ,_slotJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(slot)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_slotModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].slot # _TETrace[s-1].slot
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE LangGraphFork_TETrace ----
\*EXTENDS IOUtils, LangGraphFork_TEConstants, TLC, LangGraphFork
\*
\*trace == IODeserialize("LangGraphFork_TTrace_1784208899.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE LangGraphFork_TETrace ----
EXTENDS LangGraphFork_TEConstants, TLC, LangGraphFork

trace == 
    <<
    ([slots |-> <<NoWrite, NoWrite>>,supplied |-> <<>>,served |-> <<>>,slot |-> NoWrite]),
    ([slots |-> <<NoWrite, NoWrite>>,supplied |-> <<va>>,served |-> <<va>>,slot |-> va]),
    ([slots |-> <<NoWrite, NoWrite>>,supplied |-> <<va, vb>>,served |-> <<va, va>>,slot |-> va])
    >>
----


=============================================================================

---- MODULE LangGraphFork_TEConstants ----
EXTENDS LangGraphFork

CONSTANTS va, vb

=============================================================================

---- CONFIG LangGraphFork_TTrace_1784208899 ----
CONSTANTS
    Values = { va , vb }
    NoWrite = NoWrite
    MaxInvokes = 2
    ForkKeyed = FALSE
    vb = vb
    NoWrite = NoWrite
    va = va

INVARIANT
    _inv

CHECK_DEADLOCK
    \* CHECK_DEADLOCK off because of PROPERTY or INVARIANT above.
    FALSE

INIT
    _init

NEXT
    _next

CONSTANT
    _TETrace <- _trace

ALIAS
    _expression
=============================================================================
\* Generated on Thu Jul 16 18:34:59 PKT 2026