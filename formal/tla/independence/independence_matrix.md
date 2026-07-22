# Per-invariant fault matrix (Table tab:ix receipts)

| Fault | EO | PC | FD | CV | CO | RD |
|---|---|---|---|---|---|---|
| replay | VIOLATED(d4) | VIOLATED(d4) | holds(full) | holds(full) | VIOLATED(d7) | holds(full) |
| forkignore | holds(full) | holds(full) | VIOLATED(d5) | holds(full) | holds(full) | holds(full) |
| invalidpersist | holds(full) | holds(full) | holds(full) | VIOLATED(d5) | holds(full) | holds(full) |
| nondetrec | VIOLATED(d4) | VIOLATED(d4) | holds(full) | holds(full) | VIOLATED(d6) | VIOLATED(d4) |
| doubleconsume | VIOLATED(d6) | holds(full) | holds(full) | holds(full) | VIOLATED(d6) | holds(full) |
| prefixreplay | VIOLATED(d4) | VIOLATED(d4) | holds(full) | holds(full) | holds(full) | holds(full) |

R7 StateRebuild module: TypeOK holds; EO holds; PC(PrefixContinuation) violated
