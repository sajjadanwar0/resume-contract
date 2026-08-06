---------------------------- MODULE ResumeContract ----------------------------

EXTENDS Naturals, Sequences

CONSTANTS
  NTasks,
  IP,
  Values,
  NoVal,
  MaxResumes,
  MaxCrashes,
  MaxExtraResumes,
  FaultReplay,
  FaultForkIgnore,
  FaultInvalidPersist,
  FaultNondetRecovery,
  FaultDoubleConsume,
  FaultPrefixReplay

ASSUME /\ NTasks \in Nat \ {0}
       /\ IP \in 2..NTasks
       /\ NoVal \notin Values
       /\ MaxResumes \in Nat /\ MaxCrashes \in Nat /\ MaxExtraResumes \in Nat

Tasks == 1..NTasks

VARIABLES
  pc,
  effects,
  ckpts,
  frontier,
  waiting,
  consumedVal,
  forkVals,
  forkOuts,
  crashes,
  recHist,
  extraResumes,
  pcRegress

vars == << pc, effects, ckpts, frontier, waiting, consumedVal,
           forkVals, forkOuts, crashes, recHist, extraResumes, pcRegress >>

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

--------------------------------------------------------------------------
ExecTask ==
  /\ pc \in Tasks
  /\ ~waiting
  /\ (pc = IP) => (consumedVal # NoVal)   \* IP first executes via Consume
  /\ effects'   = [effects EXCEPT ![pc] =
                     IF FaultPrefixReplay /\ pc = IP THEN @ ELSE @ + 1]
  /\ ckpts'     = Append(ckpts,
                     [idx   |-> pc,
                      valid |-> ~(FaultInvalidPersist /\ pc = NTasks)])
  /\ frontier'  = IF pc > frontier THEN pc ELSE frontier
  /\ pcRegress' = (pcRegress \/ (pc <= frontier))
  /\ pc'        = pc + 1
  /\ UNCHANGED << waiting, consumedVal, forkVals, forkOuts,
                  crashes, recHist, extraResumes >>

EmitInterrupt ==
  /\ pc = IP
  /\ ~waiting
  /\ consumedVal = NoVal
  /\ waiting' = TRUE
  /\ UNCHANGED << pc, effects, ckpts, frontier, consumedVal, forkVals,
                  forkOuts, crashes, recHist, extraResumes, pcRegress >>

Consume(v) ==
  /\ waiting
  /\ Len(forkVals) < MaxResumes
  /\ waiting'     = FALSE
  /\ consumedVal' = v
  /\ effects'     = [effects EXCEPT ![IP] = @ + 1]
  /\ ckpts'       = Append(ckpts, [idx |-> IP, valid |-> TRUE])
  /\ frontier'    = IF IP > frontier THEN IP ELSE frontier
  /\ pc'          = IP + 1
  /\ forkVals'    = Append(forkVals, v)
  /\ forkOuts'    = Append(forkOuts, f(v))
  /\ UNCHANGED << crashes, recHist, extraResumes, pcRegress >>

ForkResume(v) ==
  /\ consumedVal # NoVal
  /\ Len(forkVals) < MaxResumes
  /\ forkVals' = Append(forkVals, v)
  /\ forkOuts' = Append(forkOuts,
                        IF FaultForkIgnore THEN forkOuts[1] ELSE f(v))
  /\ UNCHANGED << pc, effects, ckpts, frontier, waiting, consumedVal,
                  crashes, recHist, extraResumes, pcRegress >>

CrashRecover ==
  /\ crashes < MaxCrashes
  /\ pc \in 2..NTasks
  /\ ~waiting
  /\ crashes' = crashes + 1
  /\ \/ /\ ~FaultReplay /\ ~FaultNondetRecovery /\ ~FaultPrefixReplay
        /\ pc'      = frontier + 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "skip"])
     \/ /\ FaultReplay
        /\ pc'      = 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "replay"])
     \/ /\ FaultPrefixReplay
        /\ pc'      = 1
        /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> "prefixreplay"])
     \/ /\ FaultNondetRecovery
        /\ \E d \in {"skip", "reexec"} :
             /\ pc' = IF d = "skip" THEN frontier + 1 ELSE frontier
             /\ recHist' = Append(recHist, [dur |-> frontier, dec |-> d])
  /\ UNCHANGED << effects, ckpts, frontier, waiting, consumedVal,
                  forkVals, forkOuts, extraResumes, pcRegress >>

ExtraResume ==
  /\ pc = NTasks + 1
  /\ extraResumes < MaxExtraResumes
  /\ extraResumes' = extraResumes + 1
  /\ effects' = IF FaultDoubleConsume
                THEN [effects EXCEPT ![IP] = @ + 1]
                ELSE effects
  /\ UNCHANGED << pc, ckpts, frontier, waiting, consumedVal, forkVals,
                  forkOuts, crashes, recHist, pcRegress >>

Next ==
  \/ ExecTask
  \/ EmitInterrupt
  \/ \E v \in Values : Consume(v)
  \/ \E v \in Values : ForkResume(v)
  \/ CrashRecover
  \/ ExtraResume

Spec == Init /\ [][Next]_vars

FairSpec == Init /\ [][Next]_vars /\ WF_vars(Next)
EventuallyCompletes == <>(pc = NTasks + 1)

--------------------------------------------------------------------------
(* Type correctness *)
TypeOK ==
  /\ pc \in 1..NTasks + 1
  /\ effects \in [Tasks -> Nat]
  /\ frontier \in 0..NTasks
  /\ waiting \in BOOLEAN
  /\ consumedVal \in Values \cup {NoVal}
  /\ crashes \in 0..MaxCrashes
  /\ extraResumes \in 0..MaxExtraResumes
  /\ pcRegress \in BOOLEAN

EffectExactlyOnce   == \A t \in Tasks : effects[t] <= 1
PrefixConsistency   == ~pcRegress
ForkDeterminism     == \A k \in 1..Len(forkOuts) :
                          forkOuts[k] = f(forkVals[k])
CheckpointValidity  == \A k \in 1..Len(ckpts) : ckpts[k].valid
ConsumeOnce         == effects[IP] <= 1

GateMemoWitness ==
  ~( consumedVal # NoVal /\ crashes > 0
     /\ pc > IP /\ effects[IP] = 1 /\ effects[1] = 2
     /\ \E i \in 1..Len(recHist) : recHist[i].dur = IP )
RecoveryDeterminism == \A i, j \in 1..Len(recHist) :
                          recHist[i].dur = recHist[j].dur
                            => recHist[i].dec = recHist[j].dec

===============================================================================
