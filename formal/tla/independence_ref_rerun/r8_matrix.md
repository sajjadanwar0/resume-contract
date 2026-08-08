# Per-invariant fault matrix at reference bounds

| Fault | EO | PC | FD | CV | CO | RD |
|---|---|---|---|---|---|---|
| replay | VIOLATED(d4) | VIOLATED(d4) | holds(full,72 distinct) | holds(full,72 distinct) | VIOLATED(d7) | holds(full,72 distinct) |
| forkignore | holds(full,59 distinct) | holds(full,59 distinct) | VIOLATED(d6) | holds(full,59 distinct) | holds(full,59 distinct) | holds(full,59 distinct) |
| invalidpersist | holds(full,59 distinct) | holds(full,59 distinct) | holds(full,59 distinct) | VIOLATED(d6) | holds(full,59 distinct) | holds(full,59 distinct) |
| nondetrec | VIOLATED(d4) | VIOLATED(d4) | holds(full,396 distinct) | holds(full,396 distinct) | VIOLATED(d7) | VIOLATED(d4) |
| doubleconsume | VIOLATED(d7) | holds(full,59 distinct) | holds(full,59 distinct) | holds(full,59 distinct) | VIOLATED(d8) | holds(full,59 distinct) |
| prefixreplay | VIOLATED(d4) | VIOLATED(d4) | holds(full,183 distinct) | holds(full,183 distinct) | holds(full,183 distinct) | holds(full,183 distinct) |

R7 StateRebuild (scaled): TypeOK holds; EffectExactlyOnce holds; PrefixContinuation violated
