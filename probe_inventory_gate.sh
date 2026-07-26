#!/usr/bin/env bash
# probe_inventory_gate.sh -- paper<->probe inventory sync gate.
# Forward direction (HARD): every probe number the paper cites -- via
# "probe~N", "probes~N, M", "probe N", or the Sec 5.3 campaign catalogs
# "(N, M--K)" -- must exist as probes/N_*.py or probes/N_*.sh.
# Reverse direction (NOTES, non-fatal): numbered probe files on disk the
# paper never cites in any of those forms; whitelist tooling numbers below.
#
# Usage:  bash probe_inventory_gate.sh <paper.tex> [probes_dir]
set -euo pipefail
TEX="${1:?usage: probe_inventory_gate.sh <paper.tex> [probes_dir]}"
PROBES="${2:-probes}"
WHITELIST="132 144 145 146 149"   # archaeology (132) + repo tooling .sh

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

miss=0
for n in $cited; do
  if ls "$PROBES"/${n}_*.py >/dev/null 2>&1 \
     || ls "$PROBES"/${n}_*.sh >/dev/null 2>&1; then
    :
  else
    echo "MISSING probe file for cited probe $n"
    miss=1
  fi
done

for f in "$PROBES"/[0-9]*_*.py "$PROBES"/[0-9]*_*.sh; do
  [ -e "$f" ] || continue
  case "$f" in *_wip.py|*_wip.sh) continue ;; esac
  n=$(basename "$f" | grep -o '^[0-9]\+')
  case " $WHITELIST " in *" $n "*) continue ;; esac
  case " $cited " in *" $n "*) continue ;; esac
  echo "NOTE: probe $n on disk, uncited in paper ($f)"
done

if [ "$miss" -eq 0 ]; then
  echo "probe_inventory_gate: OK ($(echo $cited | wc -w) cited probe numbers all present)"
fi
exit $miss