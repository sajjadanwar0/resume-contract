#!/usr/bin/env bash

set -u
REPO="${1:?usage: n3_sync_check.sh /path/to/repo}"
LIB="$REPO/crates/remit/src/lib.rs"
VER="$REPO/crates/remit/proof/remit_verus_recover_exec.rs"
[[ -f "$LIB" && -f "$VER" ]] || { echo "ERROR: missing $LIB or $VER"; exit 2; }

extract () {
  sed -n '/N3-CORE-BODY-BEGIN/,/N3-CORE-BODY-END/p' "$1"
}

A="$(mktemp)"; B="$(mktemp)"; trap 'rm -f "$A" "$B"' EXIT
extract "$LIB" | sed 's/[[:space:]]*$//' > "$A"
extract "$VER" | sed '/VERUS-ONLY-BEGIN/,/VERUS-ONLY-END/d' \
               | sed 's/[[:space:]]*$//' > "$B"

[[ -s "$A" && -s "$B" ]] || { echo "ERROR: core-body markers not found in one of the files (the N3-CORE-BODY-BEGIN/END comments must delimit the core in BOTH files; see header)"; exit 2; }

if diff -u "$A" "$B" > /tmp/n3_core.diff; then
  echo "IDENTICAL: lib.rs recover_core body == verified exec body (outside proof blocks)."
  echo "The 'line-identical verified core' claim holds. Wire this script into CI."
else
  echo "DRIFT DETECTED between the shipped core and the verified core:"
  cat /tmp/n3_core.diff
  echo "Fix one side (and re-run verus if the verified side changed) before committing."
  exit 1
fi
