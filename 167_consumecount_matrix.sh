#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TLADIR="$HERE/formal/tla"
OUT="$TLADIR/consumecount"
JAR="${1:-${TLA2TOOLS_JAR:-$HERE/tools/tla2tools.jar}}"

[ -f "$JAR" ] || { echo "tla2tools.jar not found at $JAR" >&2; exit 2; }
[ -f "$TLADIR/R11_ConsumeCount.tla" ] || {
  echo "R11_ConsumeCount.tla not found in $TLADIR" >&2; exit 2; }

mkdir -p "$OUT/logs"
TLC="java -XX:+UseParallelGC -cp $JAR tlc2.TLC"

INVARIANTS="TypeOK EffectExactlyOnce PrefixConsistency ForkDeterminism \
CheckpointValidity ConsumeOnceEffect ConsumeOnceCount RecoveryDeterminism"

emit_cfg() {
  local name="$1" inv="$2" ntasks="$3" ip="$4" vals="$5"
  local maxres="$6" maxcr="$7" maxex="$8" fcc="$9" fre="${10}"
  cat > "$OUT/${name}_${inv}.cfg" <<EOF
SPECIFICATION Spec
CONSTANTS
  NTasks = ${ntasks}
  IP = ${ip}
  Values = ${vals}
  NoVal = NoVal
  MaxResumes = ${maxres}
  MaxCrashes = ${maxcr}
  MaxExtraResumes = ${maxex}
  FaultConcurrentConsume = ${fcc}
  FaultRaceEffect = ${fre}
INVARIANT ${inv}
EOF
}

run_cell() {
  local name="$1" inv="$2" cfg="$3"
  local log="$OUT/logs/${name}_${inv}.log"
  set +e
  $TLC -deadlock -metadir "$OUT/meta_${name}_${inv}" \
       -workers 1 -config "$cfg" "$TLADIR/R11_ConsumeCount.tla" > "$log" 2>&1
  set -e

  if grep -q "Error: Invariant" "$log"; then
    local depth
    depth=$(grep -cE '^State [0-9]+:' "$log" || true)
    printf '%-16s %-20s VIOLATED  depth %s\n' "$name" "$inv" "$depth"
    echo "\"$name.$inv\": {\"verdict\": \"violated\", \"ce_depth\": $depth}," >> "$OUT/.cells"
    return 0
  fi

  local gen dis
  gen=$(grep -oE '[0-9]+ states generated' "$log" | head -1 | grep -oE '^[0-9]+' || true)
  dis=$(grep -oE '[0-9]+ distinct states found' "$log" | head -1 | grep -oE '^[0-9]+' || true)

  if [ -z "$gen" ] || [ -z "$dis" ]; then
    echo
    echo "!! TLC produced no result for $name/$inv."
    echo "!! config: $cfg"
    echo "!! log:    $log"
    echo "!! ---- last 20 lines of the log ----"
    tail -20 "$log" | sed 's/^/!! /'
    echo "!! ----------------------------------"
    echo "!! Common causes: tla2tools too old for this module; module file not"
    echo "!! at $TLADIR/R11_ConsumeCount.tla; module name/filename mismatch."
    exit 5
  fi

  printf '%-16s %-20s HOLDS     %s/%s\n' "$name" "$inv" "$gen" "$dis"
  echo "\"$name.$inv\": {\"verdict\": \"holds\", \"generated\": $gen, \"distinct\": $dis}," >> "$OUT/.cells"
}

: > "$OUT/.cells"
echo "=== R11 consume-count matrix (24 reference cells + 8 scaled) ==="

for spec in "C0_reference 3 2 {va,vb} 2 1 1 FALSE FALSE" \
            "C1_raceconsume 3 2 {va,vb} 2 1 1 TRUE FALSE" \
            "C2_raceeffect 3 2 {va,vb} 2 1 1 TRUE TRUE" \
            "S1_scale 6 3 {va,vb,vc} 3 2 2 TRUE FALSE"; do
  set -- $spec
  name=$1
  for inv in $INVARIANTS; do
    emit_cfg "$1" "$inv" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9"
    run_cell "$name" "$inv" "$OUT/${name}_${inv}.cfg"
  done
done

{
  echo '{'
  echo '  "script": "167_consumecount_matrix.sh",'
  echo '  "module": "R11_ConsumeCount.tla",'
  echo "  \"host\": \"$(hostname)\","
  echo "  \"utc\": \"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\","
  echo '  "invocation": "tlc2.TLC -deadlock -config <cfg> -workers 1 R11_ConsumeCount.tla",'
  echo '  "cells": {'
  sed '$ s/,$//' "$OUT/.cells" | sed 's/^/    /'
  echo '  }'
  echo '}'
} > "$OUT/167_matrix.json"
rm -f "$OUT/.cells"
rm -rf "$OUT"/meta_*

echo
echo "Receipt: $OUT/167_matrix.json"
echo
echo "GATE: C0_reference must read 87/59 on every invariant (conservative"
echo "extension of R0). C1_raceconsume must violate ConsumeOnceCount ONLY."
echo "If C1 violates anything else, the witness is not a separation and the"
echo "Proposition 2(iv) claim in the paper must be withdrawn."
