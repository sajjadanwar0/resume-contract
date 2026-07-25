#!/usr/bin/env bash
# prefreeze_check.sh -- one-shot pre-freeze battery. Runs every remaining
# check that can run on this machine, applies the one pending repo action
# (probe 151 -> _wip, idempotent), and prints a paste-friendly summary.
#
# Usage:  ./prefreeze_check.sh
#         PAPER_TEX=/path/to/resume_contract_r14.tex ./prefreeze_check.sh
set -o pipefail
cd "$(dirname "$0")"
PASS=(); WARN=(); FAIL=()
say() { printf '\n=== %s ===\n' "$*"; }

say "[1/5] probe 151 disposition (template -> _wip)"
if [[ -f probes/151_p11_openai_sdk_deterministic.py ]]; then
  git mv probes/151_p11_openai_sdk_deterministic.py \
         probes/151_p11_openai_sdk_deterministic_wip.py 2>/dev/null \
    || mv probes/151_p11_openai_sdk_deterministic.py \
          probes/151_p11_openai_sdk_deterministic_wip.py
  rm -f results/matrix/151_results.json
  PASS+=("151 renamed to _wip; stray results/matrix/151_results.json removed")
elif [[ -f probes/151_p11_openai_sdk_deterministic_wip.py ]]; then
  PASS+=("151 already _wip -- nothing to do")
else
  WARN+=("probe 151 not found under either name -- check probes/")
fi

say "[2/5] probe inventory gate (paper <-> probes/)"
TEX="${PAPER_TEX:-}"
if [[ -z "$TEX" ]]; then
  TEX=$(ls resume_contract_r*.tex ../resume_contract_r*.tex 2>/dev/null | sort -V | tail -1)
fi
if [[ ! -f probe_inventory_gate.sh ]]; then
  WARN+=("probe_inventory_gate.sh not at repo root -- copy it in, then rerun")
elif [[ -n "$TEX" && -f "$TEX" ]]; then
  if bash probe_inventory_gate.sh "$TEX" probes; then
    PASS+=("inventory gate OK against $(basename "$TEX")")
  else
    FAIL+=("inventory gate FAILED against $(basename "$TEX")")
  fi
else
  WARN+=("no paper tex found -- rerun with PAPER_TEX=/path/to/resume_contract_rNN.tex")
fi

say "[3/5] line-identical verified core (n3 sync gate)"
if bash n3_sync_check.sh . ; then
  PASS+=("n3 sync: IDENTICAL")
else
  FAIL+=("n3 sync: FAILED -- lib.rs vs verified exec body drifted")
fi

say "[4/5] conformance audit on this machine (no --update)"
if command -v uv >/dev/null 2>&1; then
  if uv run python -m conformance.runner --plan matrix.toml; then
    PASS+=("audit CLEAN on this machine")
  else
    FAIL+=("conformance audit FAILED -- see above")
  fi
else
  WARN+=("uv not on PATH -- audit skipped here")
fi

say "[5/5] cannot be automated from this machine"
cat <<'REMIND'
  - CROSS-ENVIRONMENT RECEIPT: run, inside the PILOT CONTAINER with keys
    exported:   uv run python -m conformance.runner --plan matrix.toml
    (no --update). Its AUDIT CLEAN line is the cross-env receipt.
  - PYPI: twine upload of the two remit-contract dist artifacts
    (PUBLISHING.md, path A) -- then the Sec. 7 availability sentence goes in.
  - ACMART: the TOSEM-required [manuscript] single-column conversion.
REMIND

say "SUMMARY (paste this block back)"
for p in "${PASS[@]}";  do echo "  PASS  $p"; done
if [[ ${#WARN[@]} -gt 0 ]]; then for w in "${WARN[@]}"; do echo "  WARN  $w"; done; fi
if [[ ${#FAIL[@]} -gt 0 ]]; then for f in "${FAIL[@]}"; do echo "  FAIL  $f"; done; exit 1; fi
echo "  ALL RUNNABLE CHECKS GREEN"