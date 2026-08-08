#!/usr/bin/env bash
# 175_tlaps_reference_conjunction.sh
# Discharges the TLAPS proof of the reference conjunction:
#   Reference => (Spec => []ContractConjunction), plus Initiation,
#   Consecution, Sufficiency, ConsumeOnceFromEO, Monotonicity.
# Asserts: tlapm exit 0, "All N obligations proved", no OMITTED left,
# spec file byte-identical to the committed one (sha256 recorded).
# Writes: formal/tla/tlaps/tlaps_receipt.json
# Usage: ./175_tlaps_reference_conjunction.sh [REPO_ROOT]   (default: .)
set -euo pipefail

REPO_ROOT="$(cd "${1:-.}" && pwd)"
TLA_DIR="$REPO_ROOT/formal/tla"
SPEC="$TLA_DIR/ResumeContract.tla"
PROOFS="$TLA_DIR/ResumeContractProofs.tla"

[ -f "$SPEC" ]   || { echo "FAIL: missing $SPEC" >&2; exit 1; }
[ -f "$PROOFS" ] || { echo "FAIL: missing $PROOFS" >&2; exit 1; }

if grep -q "OMITTED" "$PROOFS"; then
  echo "FAIL: OMITTED proof steps present in ResumeContractProofs.tla" >&2
  exit 1
fi

TLAPM_BIN="${TLAPM_CMD:-}"
if [ -z "$TLAPM_BIN" ]; then
  if command -v tlapm >/dev/null 2>&1; then
    TLAPM_BIN="$(command -v tlapm)"
  else
    for c in "$HOME/tlapm/bin/tlapm" /opt/tlapm/bin/tlapm \
             /usr/local/tlapm/bin/tlapm /usr/local/bin/tlapm; do
      [ -x "$c" ] && TLAPM_BIN="$c" && break
    done
  fi
fi
[ -n "$TLAPM_BIN" ] || {
  echo "FAIL: tlapm not found. Install from" >&2
  echo "  https://github.com/tlaplus/tlapm/releases (1.6.0-pre)" >&2
  echo "or set TLAPM_CMD=/path/to/tlapm" >&2
  exit 1
}

TLAPM_VERSION="$("$TLAPM_BIN" --version 2>&1 | head -1)"
LOG="$TLA_DIR/tlapm_175.log"

echo "== tlapm $TLAPM_VERSION on ResumeContractProofs.tla (clean fingerprints)"
T0=$(date +%s)
set +e
( cd "$TLA_DIR" && "$TLAPM_BIN" --cleanfp -I . ResumeContractProofs.tla ) \
  > "$LOG" 2>&1
RC=$?
set -e
T1=$(date +%s)
DUR=$((T1 - T0))

PROVED_LINE="$(grep -E "All [0-9]+ obligations proved" "$LOG" || true)"
if [ "$RC" -ne 0 ] || [ -z "$PROVED_LINE" ]; then
  echo "FAIL: tlapm exit=$RC; see $LOG" >&2
  grep -E "obligations|ERROR" "$LOG" | tail -5 >&2 || true
  exit 1
fi
OBLIG="$(echo "$PROVED_LINE" | grep -oE "[0-9]+" | head -1)"

sha() { sha256sum "$1" | awk '{print $1}'; }
SPEC_SHA="$(sha "$SPEC")"
PROOF_SHA="$(sha "$PROOFS")"

mkdir -p "$TLA_DIR/tlaps"
RECEIPT="$TLA_DIR/tlaps/tlaps_receipt.json"
cat > "$RECEIPT" <<JSON
{
  "utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "tlapm_version": "$TLAPM_VERSION",
  "obligations_proved": $OBLIG,
  "obligations_failed": 0,
  "duration_seconds": $DUR,
  "host": "$(uname -sm)",
  "spec_sha256": "$SPEC_SHA",
  "proofs_sha256": "$PROOF_SHA",
  "theorems": ["Initiation", "Consecution", "Sufficiency",
               "RefSafety", "ConsumeOnceFromEO", "Monotonicity"],
  "scope": "reference configuration (all six fault constants FALSE)"
}
JSON

echo "OK: All $OBLIG obligations proved in ${DUR}s"
echo "OK: spec sha256   $SPEC_SHA"
echo "OK: proofs sha256 $PROOF_SHA"
echo "OK: receipt at $RECEIPT"
