---- MODULE ResumeContract_TTrace_1784206647 ----
EXTENDS Sequences, TLCExt, ResumeContract_TEConstants, Toolbox, Naturals, TLC, ResumeContract

_expression ==
    LET ResumeContract_TEExpression == INSTANCE ResumeContract_TEExpression
    IN ResumeContract_TEExpression!expression
----

_trace ==
    LET ResumeContract_TETrace == INSTANCE ResumeContract_TETrace
    IN ResumeContract_TETrace!trace
----

_inv ==
    ~(
        TLCGet("level") = Len(_TETrace)
        /\
        frontier = (3)
        /\
        effects = (<<1, 2, 1>>)
        /\
        ckpts = (<<[idx |-> 1, valid |-> TRUE], [idx |-> 2, valid |-> TRUE], [idx |-> 3, valid |-> TRUE]>>)
        /\
        pc = (4)
        /\
        waiting = (FALSE)
        /\
        forkVals = (<<va>>)
        /\
        consumedVal = (va)
        /\
        extraResumes = (1)
        /\
        recHist = (<<>>)
        /\
        pcRegress = (FALSE)
        /\
        forkOuts = (<<va>>)
        /\
        crashes = (0)
    )
----

_init ==
    /\ pcRegress = _TETrace[1].pcRegress
    /\ effects = _TETrace[1].effects
    /\ consumedVal = _TETrace[1].consumedVal
    /\ extraResumes = _TETrace[1].extraResumes
    /\ frontier = _TETrace[1].frontier
    /\ crashes = _TETrace[1].crashes
    /\ waiting = _TETrace[1].waiting
    /\ forkVals = _TETrace[1].forkVals
    /\ forkOuts = _TETrace[1].forkOuts
    /\ pc = _TETrace[1].pc
    /\ ckpts = _TETrace[1].ckpts
    /\ recHist = _TETrace[1].recHist
----

_next ==
    /\ \E i,j \in DOMAIN _TETrace:
        /\ \/ /\ j = i + 1
              /\ i = TLCGet("level")
        /\ pcRegress  = _TETrace[i].pcRegress
        /\ pcRegress' = _TETrace[j].pcRegress
        /\ effects  = _TETrace[i].effects
        /\ effects' = _TETrace[j].effects
        /\ consumedVal  = _TETrace[i].consumedVal
        /\ consumedVal' = _TETrace[j].consumedVal
        /\ extraResumes  = _TETrace[i].extraResumes
        /\ extraResumes' = _TETrace[j].extraResumes
        /\ frontier  = _TETrace[i].frontier
        /\ frontier' = _TETrace[j].frontier
        /\ crashes  = _TETrace[i].crashes
        /\ crashes' = _TETrace[j].crashes
        /\ waiting  = _TETrace[i].waiting
        /\ waiting' = _TETrace[j].waiting
        /\ forkVals  = _TETrace[i].forkVals
        /\ forkVals' = _TETrace[j].forkVals
        /\ forkOuts  = _TETrace[i].forkOuts
        /\ forkOuts' = _TETrace[j].forkOuts
        /\ pc  = _TETrace[i].pc
        /\ pc' = _TETrace[j].pc
        /\ ckpts  = _TETrace[i].ckpts
        /\ ckpts' = _TETrace[j].ckpts
        /\ recHist  = _TETrace[i].recHist
        /\ recHist' = _TETrace[j].recHist

\* Uncomment the ASSUME below to write the states of the error trace
\* to the given file in Json format. Note that you can pass any tuple
\* to `JsonSerialize`. For example, a sub-sequence of _TETrace.
    \* ASSUME
    \*     LET J == INSTANCE Json
    \*         IN J!JsonSerialize("ResumeContract_TTrace_1784206647.json", _TETrace)

=============================================================================

 Note that you can extract this module `ResumeContract_TEExpression`
  to a dedicated file to reuse `expression` (the module in the 
  dedicated `ResumeContract_TEExpression.tla` file takes precedence 
  over the module `ResumeContract_TEExpression` below).

---- MODULE ResumeContract_TEExpression ----
EXTENDS Sequences, TLCExt, ResumeContract_TEConstants, Toolbox, Naturals, TLC, ResumeContract

expression == 
    [
        \* To hide variables of the `ResumeContract` spec from the error trace,
        \* remove the variables below.  The trace will be written in the order
        \* of the fields of this record.
        pcRegress |-> pcRegress
        ,effects |-> effects
        ,consumedVal |-> consumedVal
        ,extraResumes |-> extraResumes
        ,frontier |-> frontier
        ,crashes |-> crashes
        ,waiting |-> waiting
        ,forkVals |-> forkVals
        ,forkOuts |-> forkOuts
        ,pc |-> pc
        ,ckpts |-> ckpts
        ,recHist |-> recHist
        
        \* Put additional constant-, state-, and action-level expressions here:
        \* ,_stateNumber |-> _TEPosition
        \* ,_pcRegressUnchanged |-> pcRegress = pcRegress'
        
        \* Format the `pcRegress` variable as Json value.
        \* ,_pcRegressJson |->
        \*     LET J == INSTANCE Json
        \*     IN J!ToJson(pcRegress)
        
        \* Lastly, you may build expressions over arbitrary sets of states by
        \* leveraging the _TETrace operator.  For example, this is how to
        \* count the number of times a spec variable changed up to the current
        \* state in the trace.
        \* ,_pcRegressModCount |->
        \*     LET F[s \in DOMAIN _TETrace] ==
        \*         IF s = 1 THEN 0
        \*         ELSE IF _TETrace[s].pcRegress # _TETrace[s-1].pcRegress
        \*             THEN 1 + F[s-1] ELSE F[s-1]
        \*     IN F[_TEPosition - 1]
    ]

=============================================================================



Parsing and semantic processing can take forever if the trace below is long.
 In this case, it is advised to uncomment the module below to deserialize the
 trace from a generated binary file.

\*
\*---- MODULE ResumeContract_TETrace ----
\*EXTENDS IOUtils, ResumeContract_TEConstants, TLC, ResumeContract
\*
\*trace == IODeserialize("ResumeContract_TTrace_1784206647.bin", TRUE)
\*
\*=============================================================================
\*

---- MODULE ResumeContract_TETrace ----
EXTENDS ResumeContract_TEConstants, TLC, ResumeContract

trace == 
    <<
    ([frontier |-> 0,effects |-> <<0, 0, 0>>,ckpts |-> <<>>,pc |-> 1,waiting |-> FALSE,forkVals |-> <<>>,consumedVal |-> NoVal,extraResumes |-> 0,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<>>,crashes |-> 0]),
    ([frontier |-> 1,effects |-> <<1, 0, 0>>,ckpts |-> <<[idx |-> 1, valid |-> TRUE]>>,pc |-> 2,waiting |-> FALSE,forkVals |-> <<>>,consumedVal |-> NoVal,extraResumes |-> 0,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<>>,crashes |-> 0]),
    ([frontier |-> 1,effects |-> <<1, 0, 0>>,ckpts |-> <<[idx |-> 1, valid |-> TRUE]>>,pc |-> 2,waiting |-> TRUE,forkVals |-> <<>>,consumedVal |-> NoVal,extraResumes |-> 0,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<>>,crashes |-> 0]),
    ([frontier |-> 2,effects |-> <<1, 1, 0>>,ckpts |-> <<[idx |-> 1, valid |-> TRUE], [idx |-> 2, valid |-> TRUE]>>,pc |-> 3,waiting |-> FALSE,forkVals |-> <<va>>,consumedVal |-> va,extraResumes |-> 0,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<va>>,crashes |-> 0]),
    ([frontier |-> 3,effects |-> <<1, 1, 1>>,ckpts |-> <<[idx |-> 1, valid |-> TRUE], [idx |-> 2, valid |-> TRUE], [idx |-> 3, valid |-> TRUE]>>,pc |-> 4,waiting |-> FALSE,forkVals |-> <<va>>,consumedVal |-> va,extraResumes |-> 0,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<va>>,crashes |-> 0]),
    ([frontier |-> 3,effects |-> <<1, 2, 1>>,ckpts |-> <<[idx |-> 1, valid |-> TRUE], [idx |-> 2, valid |-> TRUE], [idx |-> 3, valid |-> TRUE]>>,pc |-> 4,waiting |-> FALSE,forkVals |-> <<va>>,consumedVal |-> va,extraResumes |-> 1,recHist |-> <<>>,pcRegress |-> FALSE,forkOuts |-> <<va>>,crashes |-> 0])
    >>
----


=============================================================================

---- MODULE ResumeContract_TEConstants ----
EXTENDS ResumeContract

CONSTANTS va, vb

=============================================================================

---- CONFIG ResumeContract_TTrace_1784206647 ----
CONSTANTS
    NTasks = 3
    IP = 2
    Values = { va , vb }
    NoVal = NoVal
    MaxResumes = 2
    MaxCrashes = 1
    MaxExtraResumes = 1
    FaultReplay = FALSE
    FaultForkIgnore = FALSE
    FaultInvalidPersist = FALSE
    FaultNondetRecovery = FALSE
    FaultDoubleConsume = TRUE
    NoVal = NoVal
    vb = vb
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
\* Generated on Thu Jul 16 17:57:28 PKT 2026