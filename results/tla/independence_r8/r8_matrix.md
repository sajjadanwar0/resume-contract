# Per-invariant fault matrix at r8 bounds

| Fault | EO | PC | FD | CV | CO | RD |
|---|---|---|---|---|---|---|
| replay | VIOLATED(d4) | VIOLATED(d4) | holds(full,1,221,899 distinct) | holds(full,1,207,849 distinct) | VIOLATED(d13) | holds(full,1,049,871 distinct) |
| forkignore | holds(full,1,373,322 distinct) | holds(full,1,421,066 distinct) | VIOLATED(d9) | holds(full,1,320,733 distinct) | holds(full,1,321,953 distinct) | holds(full,1,236,707 distinct) |
| invalidpersist | holds(full,1,156,555 distinct) | holds(full,1,179,023 distinct) | holds(full,1,202,818 distinct) | VIOLATED(d14) | holds(full,1,244,359 distinct) | holds(full,1,081,143 distinct) |
| nondetrec | VIOLATED(d4) | VIOLATED(d4) | holds(full,1,255,628 distinct) | holds(full,1,248,495 distinct) | VIOLATED(d9) | VIOLATED(d5) |
| doubleconsume | VIOLATED(d13) | holds(full,1,150,908 distinct) | holds(full,1,281,701 distinct) | holds(full,1,118,452 distinct) | VIOLATED(d13) | holds(full,1,172,337 distinct) |
| prefixreplay | VIOLATED(d4) | VIOLATED(d4) | holds(full,1,164,045 distinct) | holds(full,1,046,414 distinct) | holds(full,1,064,453 distinct) | holds(full,1,130,921 distinct) |

R7 StateRebuild (scaled): TypeOK holds; EffectExactlyOnce holds; PrefixContinuation violated
