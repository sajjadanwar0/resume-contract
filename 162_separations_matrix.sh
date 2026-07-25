#!/usr/bin/env bash
# 162_separations_matrix.sh -- conjunction-independence witnesses for RD, PC,
# and EO (Proposition "Partial independence", clause (v)).
# ===========================================================================
# The reference fault set in ResumeContract.tla cannot separate RD, PC, or EO
# from the conjunction of the other properties: nondeterministic recovery's
# footprint is {EO, PC, CO, RD} because its replay branch re-executes the
# prefix, and no switch isolates EO. R10_Separations.tla supplies the three
# missing witnesses as effect-safe / control-safe variants of mechanisms the
# study observed:
#   regate    -- recovery re-arms the consumed gate, re-consumption served
#                from the durable record          -> RD violated, five hold
#   rebuild   -- deterministic restart with state from initial values,
#                prefix effects memoized          -> PC violated, five hold
#   redeliver -- at-least-once re-issue of the frontier task's effect,
#                gated task excluded              -> EO violated, five hold
# plus an all-switches-off reference cell in which all six hold (the module's
# own sanity check).
#
# 4 configurations x 7 invariants (six properties + TypeOK) = 28 TLC runs,
# each a full exploration at the reference bounds (NTasks=3, IP=2, |V|=2,
# MaxResumes=2, MaxCrashes=2, MaxExtraResumes=1). Every cell's verdict is
# diffed against the map established in the container on 2026-07-25
# (OpenJDK 21, single worker); any mismatch exits nonzero.
#
# Usage:  bash 162_separations_matrix.sh [REPO_ROOT]
# Output: REPO/formal/tla/separations/{SEP_*.cfg,SEP_*.out,
#           separations_matrix.json, separations_matrix.md}
#         and a copy under REPO/results/tla/separations/.
# TLC:    resolved as in 145/161 ($TLC_CMD > $TLA_TOOLS_JAR > ./tla2tools.jar
#         > $HOME/tla2tools.jar > download).
set -euo pipefail
REPO="$(cd "${1:-.}" && pwd)"
TLA_DIR="$REPO/formal/tla"
SEP_DIR="$TLA_DIR/separations"
RES_DIR="$REPO/results/tla/separations"
[ -f "$TLA_DIR/R10_Separations.tla" ] || { echo "ERROR: $TLA_DIR/R10_Separations.tla not found -- drop the module first"; exit 2; }
mkdir -p "$SEP_DIR" "$RES_DIR"

resolve_tlc () {
  if [[ -n "${TLC_CMD:-}" ]]; then echo "$TLC_CMD"; return; fi
  local jar=""
  if   [[ -n "${TLA_TOOLS_JAR:-}" && -f "${TLA_TOOLS_JAR:-}" ]]; then jar="$TLA_TOOLS_JAR"
  elif [[ -f "$TLA_DIR/tla2tools.jar" ]];                        then jar="$TLA_DIR/tla2tools.jar"
  elif [[ -f "$HOME/tla2tools.jar" ]];                           then jar="$HOME/tla2tools.jar"
  else
    echo "fetching tla2tools.jar to \$HOME (tlaplus/tlaplus releases)" >&2
    curl -sL -o "$HOME/tla2tools.jar" \
      https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
    jar="$HOME/tla2tools.jar"
  fi
  echo "java -XX:+UseParallelGC -cp $jar tlc2.TLC"
}
TLC="$(resolve_tlc)"
echo "TLC command: $TLC"

# ---- 1. generate the 28 configs ----
python3 - "$SEP_DIR" << 'PYGEN'
import sys, os
sep = sys.argv[1]
invs = ["TypeOK", "EffectExactlyOnce", "PrefixConsistency", "ForkDeterminism",
        "CheckpointValidity", "ConsumeOnce", "RecoveryDeterminism"]
switches = {"reference": (0, 0, 0), "regate": (1, 0, 0),
            "rebuild": (0, 1, 0), "redeliver": (0, 0, 1)}
tpl = """SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {{va, vb}}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = 2
  MaxExtraResumes = 1
  FaultRegateNondet = {a}
  FaultRebuild = {b}
  FaultRedeliver = {c}
INVARIANTS
  {inv}
"""
B = lambda x: "TRUE" if x else "FALSE"
for tag, (a, b, c) in switches.items():
    for inv in invs:
        with open(os.path.join(sep, f"SEP_{tag}__{inv}.cfg"), "w") as f:
            f.write(tpl.format(a=B(a), b=B(b), c=B(c), inv=inv))
print("wrote 28 configs")
PYGEN

# ---- 2. run TLC per cell ----
cd "$SEP_DIR"
for cfg in SEP_*.cfg; do
  name="${cfg%.cfg}"
  rm -rf "meta_$name"; mkdir -p "meta_$name"
  $TLC -deadlock -metadir "meta_$name" -config "$cfg" -workers 1 \
    ../R10_Separations.tla > "$name.out" 2>&1 || true   # violations exit nonzero
  rm -rf "meta_$name"
  grep -oE "No error has been found|Invariant [A-Za-z]+ is violated" "$name.out" \
    | head -1 | sed "s|^|$name :: |"
done

# ---- 3. matrix + diff against the established verdict map ----
python3 - "$SEP_DIR" << 'PYCHK'
import sys, os, re, json
sep = sys.argv[1]
invs = ["TypeOK", "EffectExactlyOnce", "PrefixConsistency", "ForkDeterminism",
        "CheckpointValidity", "ConsumeOnce", "RecoveryDeterminism"]
# container 2026-07-25, OpenJDK 21, single worker; v = violated, h = holds
EXPECTED = {
 "reference": {i: "h" for i in invs},
 "regate":    dict({i: "h" for i in invs}, RecoveryDeterminism="v"),
 "rebuild":   dict({i: "h" for i in invs}, PrefixConsistency="v"),
 "redeliver": dict({i: "h" for i in invs}, EffectExactlyOnce="v"),
}
matrix, bad, missing = {}, [], []
for tag, row in EXPECTED.items():
    for inv in invs:
        out = os.path.join(sep, f"SEP_{tag}__{inv}.out")
        if not os.path.exists(out):
            missing.append(f"{tag} x {inv}"); continue
        txt = open(out).read()
        if "No error has been found" in txt: verdict = "holds"
        elif re.search(r"Invariant \w+ is violated", txt): verdict = "violated"
        else: verdict = "unknown"
        gen = re.search(r"([\d,]+) states generated, ([\d,]+) distinct states found", txt)
        matrix.setdefault(tag, {})[inv] = {
            "verdict": verdict,
            "states_generated": gen.group(1) if gen else None,
            "states_distinct": gen.group(2) if gen else None,
            "ce_depth": len(re.findall(r"(?m)^State \d+", txt)) if verdict == "violated" else None,
        }
        exp = {"v": "violated", "h": "holds"}[row[inv]]
        if verdict != exp:
            bad.append(f"{tag} x {inv}: got {verdict}, expected {exp}")
meta = {
  "module": "R10_Separations.tla",
  "bounds": "NTasks=3 IP=2 Values={va,vb} MaxResumes=2 MaxCrashes=2 MaxExtraResumes=1",
  "claim": ("each switch violates exactly one property while the other five hold over the "
            "entire reachable state space (TLC complete); the reference cell holds on all six, "
            "which is the module's sanity check"),
  "invocation": "tlc2.TLC -deadlock -config <cfg> -workers 1 R10_Separations.tla",
}
json.dump({"meta": meta, "matrix": matrix, "missing": missing},
          open(os.path.join(sep, "separations_matrix.json"), "w"), indent=2)
order = invs[1:]
lines = ["# Conjunction-independence witnesses (Proposition 2, clause (v))", "",
         "| Switch | EO | PC | FD | CV | CO | RD | TypeOK |", "|---|---|---|---|---|---|---|---|"]
for tag in ["reference", "regate", "rebuild", "redeliver"]:
    row = [tag]
    for inv in order + ["TypeOK"]:
        c = matrix.get(tag, {}).get(inv)
        row.append("MISSING" if not c else
                   (f"VIOLATED(d{c['ce_depth']})" if c["verdict"] == "violated"
                    else f"holds({c['states_distinct']})"))
    lines.append("| " + " | ".join(row) + " |")
open(os.path.join(sep, "separations_matrix.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
if missing:
    print("\nINCOMPLETE:"); [print("  " + m) for m in missing]; sys.exit(2)
if bad:
    print("\nMISMATCH vs the established verdict map:"); [print("  " + b) for b in bad]; sys.exit(1)
print("\nOK: 28/28 cells match. RD, PC, and EO each have a conjunction-independence witness.")
PYCHK

cp "$SEP_DIR"/separations_matrix.json "$SEP_DIR"/separations_matrix.md "$RES_DIR"/ 2>/dev/null || true
echo "receipts: $SEP_DIR and $RES_DIR"
