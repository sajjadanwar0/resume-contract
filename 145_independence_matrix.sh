#!/usr/bin/env bash
# 145_independence_matrix.sh -- per-invariant TLC fault matrix (Table tab:ix)
# ===========================================================================
# Builds and runs the 33-cell independence matrix behind the paper's
# Proposition "Partial independence, machine-checked":
#   * 5 single-fault models on ResumeContract.tla x 6 invariants each,
#     using the artifact's R1-R5 constants VERBATIM (the R4/nondetrec row
#     keeps MaxCrashes=2, without which RD is vacuously clean);
#   * the R7_StateRebuild module x its 3 invariants.
# Each cell is ONE TLC run checking ONE invariant, so a "holds" cell means
# the invariant holds over the faulty model's ENTIRE reachable state space
# (TLC complete, no error) -- the separating-model evidence -- and a
# "violated" cell reports the counterexample depth.
#
# The script then diffs the fresh verdicts against the expected matrix
# (embedded below; container run 2026-07-18, OpenJDK 21, tla2tools latest)
# and exits nonzero on any mismatch, so a host replication is self-auditing.
#
# Usage:   bash 145_independence_matrix.sh [REPO_ROOT]   # default: .
# Output:  REPO_ROOT/formal/tla/independence/{IX_*.cfg,IX_*.out,
#            independence_matrix.json, independence_matrix.md}
#          and a copy of the two artifacts under results/tla/independence/.
# TLC:     resolved like 116_run_tlc.sh ($TLC_CMD > $TLA_TOOLS_JAR >
#          ./tla2tools.jar > $HOME/tla2tools.jar > download from
#          tlaplus/tlaplus releases).
set -euo pipefail
REPO="$(cd "${1:-.}" && pwd)"
TLA_DIR="$REPO/formal/tla"
IX_DIR="$TLA_DIR/independence"
RES_DIR="$REPO/results/tla/independence"
[ -f "$TLA_DIR/ResumeContract.tla" ] || { echo "ERROR: $TLA_DIR/ResumeContract.tla not found -- pass the paper repo root"; exit 2; }
mkdir -p "$IX_DIR" "$RES_DIR"

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

# ---- 1. generate the 33 configs (constants verbatim from R1-R5 / R7) ----
python3 - "$IX_DIR" << 'PYGEN'
import sys, os
ix = sys.argv[1]
faults = {
    "replay": "FaultReplay",
    "forkignore": "FaultForkIgnore",
    "invalidpersist": "FaultInvalidPersist",
    "nondetrec": "FaultNondetRecovery",
    "doubleconsume": "FaultDoubleConsume",
}
invs = ["EffectExactlyOnce", "PrefixConsistency", "ForkDeterminism",
        "CheckpointValidity", "ConsumeOnce", "RecoveryDeterminism"]
base = """SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  IP = 2
  Values = {{va, vb}}
  NoVal = NoVal
  MaxResumes = 2
  MaxCrashes = {crashes}
  MaxExtraResumes = 1
  FaultReplay = {Replay}
  FaultForkIgnore = {ForkIgnore}
  FaultInvalidPersist = {InvalidPersist}
  FaultNondetRecovery = {NondetRecovery}
  FaultDoubleConsume = {DoubleConsume}
INVARIANTS
  {inv}
"""
for ftag, fconst in faults.items():
    flags = {k: ("TRUE" if v == fconst else "FALSE") for k, v in
             [("Replay", "FaultReplay"), ("ForkIgnore", "FaultForkIgnore"),
              ("InvalidPersist", "FaultInvalidPersist"),
              ("NondetRecovery", "FaultNondetRecovery"),
              ("DoubleConsume", "FaultDoubleConsume")]}
    crashes = 2 if ftag == "nondetrec" else 1   # R4 uses MaxCrashes = 2
    for inv in invs:
        with open(os.path.join(ix, f"IX_{ftag}__{inv}.cfg"), "w") as f:
            f.write(base.format(inv=inv, crashes=crashes, **flags))
r7 = """SPECIFICATION Spec
CONSTANTS
  NTasks = 3
  MaxCrashes = 1
  FaultStateRebuild = TRUE
INVARIANTS
  {inv}
"""
for inv in ["TypeOK", "EffectExactlyOnce", "PrefixContinuation"]:
    with open(os.path.join(ix, f"IX_staterebuild__{inv}.cfg"), "w") as f:
        f.write(r7.format(inv=inv))
print("wrote 33 configs")
PYGEN

# ---- 2. run TLC per cell ----
cd "$IX_DIR"
for cfg in IX_*.cfg; do
  name="${cfg%.cfg}"
  mod=ResumeContract; [[ "$name" == IX_staterebuild__* ]] && mod=R7_StateRebuild
  rm -rf "meta_$name"; mkdir -p "meta_$name"
  $TLC -deadlock -metadir "meta_$name" -config "$cfg" -workers 1 "../$mod.tla" \
    > "$name.out" 2>&1 || true   # violation runs exit nonzero by design
  rm -rf "meta_$name"
  grep -oE "No error has been found|Invariant [A-Za-z]+ is violated" "$name.out" | head -1 \
    | sed "s|^|$name :: |"
done

# ---- 3. build matrix json/md and diff against the expected verdicts ----
python3 - "$IX_DIR" << 'PYCHK'
import sys, os, re, json, glob
ix = sys.argv[1]
EXPECTED = {  # container run 2026-07-18; v=violated, h=holds
 "replay":        {"EffectExactlyOnce":"v","PrefixConsistency":"v","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"h"},
 "forkignore":    {"EffectExactlyOnce":"h","PrefixConsistency":"h","ForkDeterminism":"v","CheckpointValidity":"h","ConsumeOnce":"h","RecoveryDeterminism":"h"},
 "invalidpersist":{"EffectExactlyOnce":"h","PrefixConsistency":"h","ForkDeterminism":"h","CheckpointValidity":"v","ConsumeOnce":"h","RecoveryDeterminism":"h"},
 "nondetrec":     {"EffectExactlyOnce":"v","PrefixConsistency":"v","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"v"},
 "doubleconsume": {"EffectExactlyOnce":"v","PrefixConsistency":"h","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"h"},
 "staterebuild":  {"TypeOK":"h","EffectExactlyOnce":"h","PrefixContinuation":"v"},
}
matrix, bad = {}, []
for out in sorted(glob.glob(os.path.join(ix, "IX_*.out"))):
    name = os.path.basename(out)[:-4]
    fault, inv = name[3:].split("__")
    txt = open(out).read()
    if "No error has been found" in txt: verdict = "holds"
    elif re.search(r"Invariant \w+ is violated", txt): verdict = "violated"
    else: verdict = "unknown"
    st = re.search(r"(\d+) states generated", txt)
    dp = re.search(r"depth of the complete state graph search is (\d+)", txt)
    matrix.setdefault(fault, {})[inv] = {
        "verdict": verdict,
        "states": int(st.group(1)) if st else None,
        "depth": int(dp.group(1)) if dp else None,
    }
    exp = {"v": "violated", "h": "holds"}[EXPECTED[fault][inv]]
    if verdict != exp:
        bad.append(f"{fault} x {inv}: got {verdict}, expected {exp}")
meta = {
  "invocation": "tlc2.TLC -deadlock -config <cfg> -workers 1 <Module>.tla",
  "constants": "R1-R5 rows use the artifact fault-config constants verbatim (nondetrec row: MaxCrashes=2); staterebuild rows use R7_staterebuild.cfg constants",
  "semantics": "holds = invariant satisfied over the faulty model's entire reachable state space (TLC complete); violated = counterexample found",
}
json.dump({"meta": meta, "matrix": matrix},
          open(os.path.join(ix, "independence_matrix.json"), "w"), indent=2)
order = ["EffectExactlyOnce","PrefixConsistency","ForkDeterminism","CheckpointValidity","ConsumeOnce","RecoveryDeterminism"]
lines = ["# Per-invariant fault matrix (Table tab:ix receipts)", "",
         "| Fault | EO | PC | FD | CV | CO | RD |", "|---|---|---|---|---|---|---|"]
for f in ["replay","forkignore","invalidpersist","nondetrec","doubleconsume"]:
    row = [f]
    for inv in order:
        c = matrix[f][inv]
        row.append(("VIOLATED(d%s)" % c["depth"]) if c["verdict"]=="violated" else "holds(full)")
    lines.append("| " + " | ".join(row) + " |")
r7 = matrix["staterebuild"]
lines += ["", "R7 StateRebuild module: TypeOK %s; EO %s; PC(PrefixContinuation) %s" %
          (r7["TypeOK"]["verdict"], r7["EffectExactlyOnce"]["verdict"], r7["PrefixContinuation"]["verdict"])]
open(os.path.join(ix, "independence_matrix.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
if bad:
    print("\nMISMATCH vs expected verdicts:"); [print("  " + b) for b in bad]; sys.exit(1)
print("\nAll 33 verdicts match the expected matrix. Receipts: formal/tla/independence/")
PYCHK

cp "$IX_DIR/independence_matrix.json" "$IX_DIR/independence_matrix.md" "$RES_DIR/"
echo "== done. Commit formal/tla/independence/ and results/tla/independence/."