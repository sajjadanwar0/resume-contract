# Parked-crash matrix (ResumeContractParked.tla)

- audit vs embedded expectations: PASS
- P8_durable holds - advisory

| run | verdict | depth | audit |
|---|---|---|---|
| P0_durable | holds | - | ok |
| P0_volatile | holds | - | ok |
| PLIVE_durable | holds | - | ok |
| PLIVE_volatile | temporal_violation | temporal | ok |
| PX_replay__EffectExactlyOnce | violated | 4 | ok |
| PX_replay__PrefixConsistency | violated | 4 | ok |
| PX_replay__ForkDeterminism | holds | - | ok |
| PX_replay__CheckpointValidity | holds | - | ok |
| PX_replay__ConsumeOnce | violated | 7 | ok |
| PX_replay__RecoveryDeterminism | holds | - | ok |
| PX_forkignore__EffectExactlyOnce | holds | - | ok |
| PX_forkignore__PrefixConsistency | holds | - | ok |
| PX_forkignore__ForkDeterminism | violated | 5 | ok |
| PX_forkignore__CheckpointValidity | holds | - | ok |
| PX_forkignore__ConsumeOnce | holds | - | ok |
| PX_forkignore__RecoveryDeterminism | holds | - | ok |
| PX_invalidpersist__EffectExactlyOnce | holds | - | ok |
| PX_invalidpersist__PrefixConsistency | holds | - | ok |
| PX_invalidpersist__ForkDeterminism | holds | - | ok |
| PX_invalidpersist__CheckpointValidity | violated | 5 | ok |
| PX_invalidpersist__ConsumeOnce | holds | - | ok |
| PX_invalidpersist__RecoveryDeterminism | holds | - | ok |
| PX_nondetrec__EffectExactlyOnce | violated | 4 | ok |
| PX_nondetrec__PrefixConsistency | violated | 4 | ok |
| PX_nondetrec__ForkDeterminism | holds | - | ok |
| PX_nondetrec__CheckpointValidity | holds | - | ok |
| PX_nondetrec__ConsumeOnce | violated | 6 | ok |
| PX_nondetrec__RecoveryDeterminism | violated | 4 | ok |
| PX_doubleconsume__EffectExactlyOnce | violated | 6 | ok |
| PX_doubleconsume__PrefixConsistency | holds | - | ok |
| PX_doubleconsume__ForkDeterminism | holds | - | ok |
| PX_doubleconsume__CheckpointValidity | holds | - | ok |
| PX_doubleconsume__ConsumeOnce | violated | 6 | ok |
| PX_doubleconsume__RecoveryDeterminism | holds | - | ok |
| PX_prefixreplay__EffectExactlyOnce | violated | 4 | ok |
| PX_prefixreplay__PrefixConsistency | violated | 4 | ok |
| PX_prefixreplay__ForkDeterminism | holds | - | ok |
| PX_prefixreplay__CheckpointValidity | holds | - | ok |
| PX_prefixreplay__ConsumeOnce | holds | - | ok |
| PX_prefixreplay__RecoveryDeterminism | holds | - | ok |
