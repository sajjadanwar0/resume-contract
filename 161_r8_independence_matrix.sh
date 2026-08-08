#!/usr/bin/env bash

set -euo pipefail

REPO="."; BOUNDS="r8"; ONLY=""; RESUME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bounds)  BOUNDS="$2"; shift 2 ;;
    --only)    ONLY="$2";   shift 2 ;;
    --resume)  RESUME=1;    shift   ;;
    *)         REPO="$1";   shift   ;;
  esac
done

REPO="$(cd "$REPO" && pwd)"
TLA_DIR="$REPO/formal/tla"
[ -f "$TLA_DIR/ResumeContract.tla" ] || { echo "ERROR: $TLA_DIR/ResumeContract.tla not found -- pass the paper repo root"; exit 2; }
case "$BOUNDS" in r8) IX_NAME="independence_r8" ;; reference) IX_NAME="independence_ref_rerun" ;; *) echo "bad --bounds"; exit 2 ;; esac
IX_DIR="$TLA_DIR/$IX_NAME"; RES_DIR="$REPO/results/tla/$IX_NAME"
mkdir -p "$IX_DIR" "$RES_DIR"
WORKERS="${TLC_WORKERS:-auto}"
JOPTS="${TLC_JAVA_OPTS:--Xmx8g -XX:+UseParallelGC}"

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
  echo "java $JOPTS -cp $jar tlc2.TLC"
}

TLC="$(resolve_tlc)"
echo "TLC command: $TLC   (workers=$WORKERS, bounds=$BOUNDS)"

python3 - "$IX_DIR" "$BOUNDS" << 'PYGEN'
import sys, os
ix, bounds = sys.argv[1], sys.argv[2]
faults = {
    "replay": "FaultReplay",
    "forkignore": "FaultForkIgnore",
    "invalidpersist": "FaultInvalidPersist",
    "nondetrec": "FaultNondetRecovery",
    "doubleconsume": "FaultDoubleConsume",
    "prefixreplay": "FaultPrefixReplay",
}
invs = ["EffectExactlyOnce", "PrefixConsistency", "ForkDeterminism",
        "CheckpointValidity", "ConsumeOnce", "RecoveryDeterminism"]
if bounds == "r8":
    consts = dict(ntasks=10, ip=5, values="{va, vb, vc, vd}",
                  resumes=5, extra=4)
    crashes = lambda ftag: 4
    r7 = dict(ntasks=10, crashes=4)
else:
    consts = dict(ntasks=3, ip=2, values="{va, vb}", resumes=2, extra=1)
    crashes = lambda ftag: 2 if ftag in ("nondetrec", "prefixreplay") else 1
    r7 = dict(ntasks=3, crashes=1)
base = """SPECIFICATION Spec
CONSTANTS
  NTasks = {ntasks}
  IP = {ip}
  Values = {values}
  NoVal = NoVal
  MaxResumes = {resumes}
  MaxCrashes = {cr}
  MaxExtraResumes = {extra}
  FaultReplay = {Replay}
  FaultForkIgnore = {ForkIgnore}
  FaultInvalidPersist = {InvalidPersist}
  FaultNondetRecovery = {NondetRecovery}
  FaultDoubleConsume = {DoubleConsume}
  FaultPrefixReplay = {PrefixReplay}
INVARIANTS
  {inv}
"""
for ftag, fconst in faults.items():
    flags = {k: ("TRUE" if v == fconst else "FALSE") for k, v in
             [("Replay", "FaultReplay"), ("ForkIgnore", "FaultForkIgnore"),
              ("InvalidPersist", "FaultInvalidPersist"),
              ("NondetRecovery", "FaultNondetRecovery"),
              ("DoubleConsume", "FaultDoubleConsume"),
              ("PrefixReplay", "FaultPrefixReplay")]}
    for inv in invs:
        with open(os.path.join(ix, f"IX8_{ftag}__{inv}.cfg"), "w") as f:
            f.write(base.format(inv=inv, cr=crashes(ftag), **consts, **flags))
r7tpl = """SPECIFICATION Spec
CONSTANTS
  NTasks = {ntasks}
  MaxCrashes = {crashes}
  FaultStateRebuild = TRUE
INVARIANTS
  {inv}
"""
for inv in ["TypeOK", "EffectExactlyOnce", "PrefixContinuation"]:
    with open(os.path.join(ix, f"IX8_staterebuild__{inv}.cfg"), "w") as f:
        f.write(r7tpl.format(inv=inv, **r7))
print(f"wrote 39 configs at {bounds} bounds")
PYGEN

cd "$IX_DIR"

for cfg in IX8_*.cfg; do
  name="${cfg%.cfg}"
  [[ -n "$ONLY" && "$name" != IX8_${ONLY}__* ]] && continue
  if [[ "$RESUME" == 1 && -f "$name.out" ]] && \
     grep -qE "No error has been found|Invariant [A-Za-z]+ is violated" "$name.out"; then
    echo "skip (resume): $name"; continue
  fi
  mod=ResumeContract; [[ "$name" == IX8_staterebuild__* ]] && mod=R7_StateRebuild
  rm -rf "meta_$name"; mkdir -p "meta_$name"
  t0=$(date +%s)
  $TLC -deadlock -metadir "meta_$name" -config "$cfg" -workers "$WORKERS" \
    "../$mod.tla" > "$name.out" 2>&1 || true   # violation runs exit nonzero
  rm -rf "meta_$name"

  if grep -qE "Invariant [A-Za-z]+ is violated" "$name.out" && [[ "$WORKERS" != "1" ]]; then
    rm -rf "meta_${name}_w1"; mkdir -p "meta_${name}_w1"
    $TLC -deadlock -metadir "meta_${name}_w1" -config "$cfg" -workers 1 \
      "../$mod.tla" > "$name.out" 2>&1 || true
    rm -rf "meta_${name}_w1"
  fi
  t1=$(date +%s)
  v=$(grep -oE "No error has been found|Invariant [A-Za-z]+ is violated" "$name.out" | head -1)
  echo "$name :: ${v:-UNKNOWN} :: $((t1-t0))s"
done

python3 - "$IX_DIR" "$BOUNDS" << 'PYCHK'
import sys, os, re, json, glob
ix, bounds = sys.argv[1], sys.argv[2]
EXPECTED = {  # reference-bound footprints (145_independence_matrix.sh)
 "replay":        {"EffectExactlyOnce":"v","PrefixConsistency":"v","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"h"},
 "forkignore":    {"EffectExactlyOnce":"h","PrefixConsistency":"h","ForkDeterminism":"v","CheckpointValidity":"h","ConsumeOnce":"h","RecoveryDeterminism":"h"},
 "invalidpersist":{"EffectExactlyOnce":"h","PrefixConsistency":"h","ForkDeterminism":"h","CheckpointValidity":"v","ConsumeOnce":"h","RecoveryDeterminism":"h"},
 "nondetrec":     {"EffectExactlyOnce":"v","PrefixConsistency":"v","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"v"},
 "doubleconsume": {"EffectExactlyOnce":"v","PrefixConsistency":"h","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"v","RecoveryDeterminism":"h"},
 "prefixreplay":  {"EffectExactlyOnce":"v","PrefixConsistency":"v","ForkDeterminism":"h","CheckpointValidity":"h","ConsumeOnce":"h","RecoveryDeterminism":"h"},
 "staterebuild":  {"TypeOK":"h","EffectExactlyOnce":"h","PrefixContinuation":"v"},
}
matrix, bad, missing = {}, [], []
for fault, invs in EXPECTED.items():
    for inv in invs:
        out = os.path.join(ix, f"IX8_{fault}__{inv}.out")
        if not os.path.exists(out):
            missing.append(f"{fault} x {inv}")
            continue
        txt = open(out).read()
        if "No error has been found" in txt: verdict = "holds"
        elif re.search(r"Invariant \w+ is violated", txt): verdict = "violated"
        else: verdict = "unknown"
        gen = re.search(r"([\d,]+) states generated", txt)
        dis = re.search(r"([\d,]+) distinct states found", txt)
        gdep = re.search(r"depth of the complete state graph search is (\d+)", txt)
        ce_depth = len(re.findall(r"(?m)^State \d+", txt)) or None
        matrix.setdefault(fault, {})[inv] = {
            "verdict": verdict,
            "states_generated": gen.group(1) if gen else None,
            "states_distinct": dis.group(1) if dis else None,
            "graph_depth": int(gdep.group(1)) if gdep else None,
            "ce_depth": ce_depth if verdict == "violated" else None,
        }
        exp = {"v": "violated", "h": "holds"}[EXPECTED[fault][inv]]
        if verdict != exp:
            bad.append(f"{fault} x {inv}: got {verdict}, expected {exp} (reference-bound footprint)")
meta = {
  "bounds": bounds,
  "constants": ("R8: NTasks=10 IP=5 |Values|=4 MaxResumes=5 MaxCrashes=4 MaxExtraResumes=4; "
                "R7 scaled NTasks=10 MaxCrashes=4" if bounds == "r8"
                else "reference: 145_independence_matrix.sh constants verbatim"),
  "semantics": "holds = invariant satisfied over the faulty model's entire reachable "
               "state space at these constants (TLC complete, all workers); violated = BFS "
               "counterexample re-run at -workers 1, so ce_depth is the minimal witness "
               "and reproduces across hosts; parallel search returns non-minimal traces "
               "at this scale",
}
json.dump({"meta": meta, "matrix": matrix, "missing": missing},
          open(os.path.join(ix, "r8_matrix.json"), "w"), indent=2)
order = ["EffectExactlyOnce","PrefixConsistency","ForkDeterminism","CheckpointValidity","ConsumeOnce","RecoveryDeterminism"]
lines = [f"# Per-invariant fault matrix at {bounds} bounds", "",
         "| Fault | EO | PC | FD | CV | CO | RD |", "|---|---|---|---|---|---|---|"]
for f in ["replay","forkignore","invalidpersist","nondetrec","doubleconsume","prefixreplay"]:
    row = [f]
    for inv in order:
        c = matrix.get(f, {}).get(inv)
        if not c: row.append("MISSING"); continue
        row.append(f"VIOLATED(d{c['ce_depth']})" if c["verdict"] == "violated"
                   else f"holds(full,{c['states_distinct']} distinct)")
    lines.append("| " + " | ".join(row) + " |")
r7 = matrix.get("staterebuild", {})
if r7:
    lines += ["", "R7 StateRebuild (scaled): " + "; ".join(
        f"{k} {v['verdict']}" for k, v in r7.items())]
open(os.path.join(ix, "r8_matrix.md"), "w").write("\n".join(lines) + "\n")
print("\n".join(lines))
if missing:
    print("\nINCOMPLETE: missing cells:"); [print("  " + m) for m in missing]; sys.exit(2)
if bad:
    print("\n" + "=" * 68)
    print("FOOTPRINT-DIVERGENCE vs the reference-bound matrix -- this is a")
    print("FINDING, not necessarily a bug: a fault's violation footprint has")
    print("changed at scale. Bring the diff back before touching the paper.")
    print("=" * 68)
    [print("  " + b) for b in bad]; sys.exit(1)
print("\nOK: verdict pattern identical to the reference-bound footprints.")
PYCHK

cp "$IX_DIR"/r8_matrix.json "$IX_DIR"/r8_matrix.md "$RES_DIR"/ 2>/dev/null || true
echo "receipts: $IX_DIR and $RES_DIR"
