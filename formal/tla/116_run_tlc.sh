#!/usr/bin/env bash
# 116_run_tlc.sh -- TLC verification matrix for ResumeContract.tla
#
# TLC location (first match wins; shell aliases do not reach scripts, so the
# jar is located directly):
#   1. $TLC_CMD           full command override, e.g. TLC_CMD="java -cp /opt/tla.jar tlc2.TLC"
#   2. $TLA_TOOLS_JAR     explicit jar path
#   3. ./tla2tools.jar    next to this script
#   4. $HOME/tla2tools.jar
#   5. download (4) from tlaplus/tlaplus releases
set -u
cd "$(dirname "$0")"

resolve_tlc () {
  if [[ -n "${TLC_CMD:-}" ]]; then echo "$TLC_CMD"; return; fi
  local jar=""
  if   [[ -n "${TLA_TOOLS_JAR:-}" && -f "${TLA_TOOLS_JAR:-}" ]]; then jar="$TLA_TOOLS_JAR"
  elif [[ -f ./tla2tools.jar ]];                                 then jar=./tla2tools.jar
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

declare -A MODULE=(
  [R0_reference]=ResumeContract [R1_replay]=ResumeContract
  [R2_forkignore]=ResumeContract [R3_invalidpersist]=ResumeContract
  [R4_nondetrec]=ResumeContract [R5_doubleconsume]=ResumeContract
  [R6_liveness]=ResumeContract
  [LGF_AsImplemented]=LangGraphFork [LGF_ForkKeyed]=LangGraphFork
)
for cfg in R0_reference R1_replay R2_forkignore R3_invalidpersist R4_nondetrec R5_doubleconsume R6_liveness LGF_AsImplemented LGF_ForkKeyed; do
  echo "=== $cfg (${MODULE[$cfg]}) ==="
  $TLC -deadlock -config "$cfg.cfg" -workers 1 "${MODULE[$cfg]}.tla" > "$cfg.out" 2>&1
  grep -E "No error has been found|Invariant .* is violated|states generated" "$cfg.out" | head -6
  echo
done
