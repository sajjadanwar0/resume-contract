---- MODULE R7_StateRebuild_TTrace_1784626944 ----
EXTENDS Sequences, TLCExt, Toolbox, Naturals, TLC, R7_StateRebuild

_expression ==
    LET R7_StateRebuild_TEExpression == INSTANCE R7_StateRebuild_TEExpression
    IN R7_StateRebuild_TEExpression!expression
----

_trace ==
    LET R7_StateRebuild_TETrace == INSTANCE R7_StateRebuild_TETrace
    IN R7_StateRebuild_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        lineage = ("initial")
        /\
        frontier = (1)
        /\
        effects = (<<1, 0, 0>>)
        /\
        pc = (1)
        /\
        crashes = (1)
        /\
        replaying = (TRUE)
    )
----

_init ==
    /\ effects = _TETrace[1].effects
    /\ lineage = _TETrace[1].lineage
    /\ replaying = _TETrace[1].replaying
    /\ frontier = _TETrace[1].frontier
    /\ crashes = _TETrace[1].crashes
    /\ pc = _TETrace[1].pc
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ effects  = _TETrace[i].effects
        /\ effects' = _TETrace[j].effects
        /\ lineage  = _TETrace[i].lineage
        /\ lineage' = _TETrace[j].lineage
        /\ replaying  = _TETrace[i].replaying
        /\ replaying' = _TETrace[j].replaying
        /\ frontier  = _TETrace[i].frontier
        /\ frontier' = _TETrace[j].frontier
        /\ crashes  = _TETrace[i].crashes
        /\ crashes' = _TETrace[j].crashes
        /\ pc  = _TETrace[i].pc
        /\ pc' = _TETrace[j].pc

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("R7_StateRebuild_TTrace_1784626944.json", _TETrace)

=============================================================================

 Note that you can extract this module `R7_StateRebuild_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `R7_StateRebuild_TEExpression.tla` file takes precedence 
  over the module `R7_StateRebuild_TEExpression` below).

---- MODULE R7_StateRebuild_TEExpression ----
EXTENDS Sequences, TLCExt, Toolbox, Naturals, TLC, R7_StateRebuild

expression == 
    [
        \* To hide variables of the `R7_StateRebuild` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        effects |-> effects
        ,lineage |-> lineage
        ,replaying |-> replaying
        ,frontier |-> frontier
        ,crashes |-> crashes
        ,pc |-> pc
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_effectsUnchanged |-> effects = effects'
        
        \* Format the `effects` variable as Json value.
        \* ,_effectsJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(effects)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_effectsModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].effects # _TETrace[s-1].effects
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE R7_StateRebuild_TETrace ----
\*EXTENDS IOUtils, TLC, R7_StateRebuild
\*
\*trace == IODeserialize("R7_StateRebuild_TTrace_1784626944.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE R7_StateRebuild_TETrace ----
EXTENDS TLC, R7_StateRebuild

trace == 
    <<
    ([lineage |-> "log",frontier |-> 0,effects |-> <<0, 0, 0>>,pc |-> 1,crashes |-> 0,replaying |-> FALSE]),
    ([lineage |-> "log",frontier |-> 1,effects |-> <<1, 0, 0>>,pc |-> 2,crashes |-> 0,replaying |-> FALSE]),
    ([lineage |-> "initial",frontier |-> 1,effects |-> <<1, 0, 0>>,pc |-> 1,crashes |-> 1,replaying |-> TRUE])
    >>
----


=============================================================================

---- CONFIG R7_StateRebuild_TTrace_1784626944 ----
CONSTANTS
    NTasks = 3
    MaxCrashes = 1
    FaultStateRebuild = TRUE

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
\* Generated on Tue Jul 21 14:42:25 PKT 2026