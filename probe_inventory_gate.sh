#!/usr/bin/env bash
# probe_inventory_gate.sh -- paper<->probe inventory sync gate.
#
# Forward direction (HARD): every probe number the paper cites -- via
# "probe~N", "probes~N, M", "probe N", or the Sec 5.3 campaign catalogs
# "(N, M--K)" -- must exist either as probes/N_*.py|sh or as a campaign
# driver N_*.sh at the repository root.
#
# Reverse direction (NOTES, non-fatal): numbered probe files in probes/ the
# paper never cites in any of those forms; whitelist tooling numbers below.
# Root-level campaign drivers are NOT scanned in reverse: they orchestrate
# probes rather than being probes, so an uncited driver is not a finding.
#
# Usage:  bash probe_inventory_gate.sh <paper.tex> [probes_dir] [drivers_dir]
#
# CHANGED IN THIS REVISION. The forward check previously searched probes/
# only, so a cited number whose implementation is a root-level campaign
# driver was reported MISSING. That is what probe 167
# (167_consumecount_matrix.sh, the consume-count separation matrix of
# Sec. 4) hit: cited in the paper, present in the repository, invisible to
# the gate. The failure predates the current paper revision -- it
# reproduces against r18 -- and is a gate defect, not a paper or artifact
# defect. Root drivers now satisfy the forward check.
set -euo pipefail
TEX="${1:?usage: probe_inventory_gate.sh <paper.tex> [probes_dir] [drivers_dir]}"
PROBES="${2:-probes}"
DRIVERS="${3:-.}"
WHITELIST="132 144 145 146 149"   # archaeology (132) + repo tooling .sh

[ -f "$TEX" ] || { echo "FATAL: paper not found: $TEX" >&2; exit 2; }
[ -d "$PROBES" ] || { echo "FATAL: probes dir not found: $PROBES" >&2; exit 2; }

cited=$(python3 - "$TEX" <<'PY'
import re, sys
tex = open(sys.argv[1]).read()
tex = re.sub(r"(?m)%.*$", "", tex)          # strip comments
nums = set()

# probe~N / probes~N, M / probes~N/M
for m in re.finditer(r"probes?~([0-9]+(?:[,/ ]+[0-9]+)*)", tex):
    nums.update(int(x) for x in re.findall(r"[0-9]+", m.group(1)))

# prose "probe N" (no tilde), e.g. table cells
for m in re.finditer(r"probes?\s+([0-9]{3})\b", tex):
    nums.add(int(m.group(1)))

# campaign catalogs: parenthesized lists of 3-digit numbers with ranges,
# e.g. "(118, 119, 123--125)" or "(150, 155--157)"; only accept groups
# whose every element is a 3-digit number or 3-digit range.
for m in re.finditer(r"\(([0-9]{3}[0-9,\s./b-]*)\)", tex):
    body = m.group(1)
    parts = [p.strip() for p in re.split(r"[,/;]", body) if p.strip()]
    ok, got = True, set()
    for p in parts:
        r = re.fullmatch(r"([0-9]{3})b?(?:--([0-9]{3})b?)?", p)
        if not r:
            ok = False
            break
        a = int(r.group(1))
        b = int(r.group(2)) if r.group(2) else a
        got.update(range(a, b + 1))
    if ok:
        nums.update(got)

print(" ".join(str(n) for n in sorted(nums)))
PY
)

ncited=$(echo $cited | wc -w)
echo "  paper: $TEX ($(wc -c < "$TEX") bytes), cited probe numbers: $ncited"

miss=0
for n in $cited; do
  if ls "$PROBES"/${n}_*.py  >/dev/null 2>&1 \
     || ls "$PROBES"/${n}_*.sh  >/dev/null 2>&1 \
     || ls "$DRIVERS"/${n}_*.sh >/dev/null 2>&1 \
     || ls "$DRIVERS"/${n}_*.py >/dev/null 2>&1; then
    :
  else
    echo "MISSING probe file for cited probe $n"
    miss=1
  fi
done

nnote=0
for f in "$PROBES"/[0-9]*_*.py "$PROBES"/[0-9]*_*.sh; do
  [ -e "$f" ] || continue
  case "$f" in *_wip.py|*_wip.sh) continue ;; esac
  n=$(basename "$f" | grep -o '^[0-9]\+')
  case " $WHITELIST " in *" $n "*) continue ;; esac
  case " $cited " in *" $n "*) continue ;; esac
  echo "NOTE: probe $n on disk, uncited in paper ($f)"
  nnote=$((nnote + 1))
done

# Plausibility floor. The forward check passes VACUOUSLY on a paper that
# cites almost nothing -- a truncated file, a stale draft, or the wrong path
# entirely sails through while every real probe lands in the NOTE list. The
# ratio is the signal and it is self-calibrating: in a paper that documents
# its own suite, cited should dominate uncited. More uncited than cited means
# the file being read is not the paper.
if [ "$nnote" -gt "$ncited" ]; then
  echo "FAIL: $nnote probe files uncited vs only $ncited cited -- implausible"
  echo "      citation profile. Is $TEX the right (complete) paper source?"
  miss=1
fi

if [ "$miss" -eq 0 ]; then
  echo "probe_inventory_gate: OK ($ncited cited, $nnote uncited-on-disk)"
fi
exit $miss