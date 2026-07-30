#!/usr/bin/env bash
###############################################################################
# reproduce.sh -- single-command audit for the Resume Contract artifact.
#
# Re-derives every headline number in the paper from committed inputs:
#   [1] TLC verification matrix (Table 2 + Prop. 2 witnesses): R0 reference and
#       R6 liveness = no error; R1-R5 = counterexample for exactly the targeted
#       invariant; LGF_* = the fork model as implemented vs. keyed; SEP_* = the
#       three conjunction-independence witnesses (R10_Separations.tla).
#   [2] Committed matrix receipts, verified without re-running: the 39-cell
#       per-invariant fault matrix (161, reference and R8 bounds) and the
#       28-cell separations matrix (162). Seconds; the runs themselves are
#       minutes and hours respectively, so this step audits their JSON.
#   [3] Conformance plan (matrix.toml): every planned probe re-run inside its
#       pinned per-framework env; stable verdict fields diffed against the
#       committed baselines (live-keyed probes skip without API keys).
#   [2c] IndCheck inductive-invariant matrix: Inv is checked INDUCTIVE (every
#       invariant-state taken as an initial state, every successor checked --
#       stronger than reachability, since it quantifies over unreachable
#       states too) at three constant sets between which each of the six
#       configuration bounds varies. Audits the committed receipt; --scaled
#       re-runs all three (~5 min, R2 dominates).
#   [0] Structural: every probe number the paper cites exists in the artifact
#       (probe_inventory_gate.sh). Skipped, not failed, when the paper is not
#       in the tree -- the artifact is distributable without it.
#   [4] Remit skeleton invariants: cargo test (seven property-named tests).
#   [5] Remit Verus proofs: every positive target must discharge with
#       0 errors; every negative certificate must fail in exactly the
#       expected shape (2 verified, 1 errors).
# Modes:
#   ./reproduce.sh            full audit ([1]..[5]); syncs envs on demand
#   ./reproduce.sh --tlc-only [1]+[2] only (no Python env setup, no Rust)
#   ./reproduce.sh --scaled   re-runs 161 (both bound sets) and 162 first,
#                             then [1]+[2] -- hours, not minutes
#
# WORKER COUNT IS LOAD-BEARING. TLC runs below are pinned to -workers 1. At
# reference bounds this costs nothing; at scale it is the difference between a
# reproducible receipt and an unstable one. Measured 2026-07-25: the R8 fork
# fault reports a 9-state trace under 16 workers and an 8-state trace under
# one, the longer trace carrying a CrashRecover step the shorter reaches the
# same violation without -- parallel breadth-first search returns non-minimal
# witnesses once levels stop draining before workers race ahead. Verdicts are
# worker-invariant; depths are not. Every depth this artifact reports, and
# every depth the paper cites, is a single-worker depth.
#
# TLC location (first match wins; shell aliases do not reach scripts):
#   $TLC_CMD | $TLA_TOOLS_JAR | formal/tla/tla2tools.jar |
#   $HOME/tla2tools.jar | download to $HOME.
#
# Requirements: uv >= 0.4, Java >= 11, Rust stable (cargo), network on first
# run (env resolution; jar download if absent everywhere).
###############################################################################
set -euo pipefail
cd "$(dirname "$0")"

TLC_ONLY=0
SCALED=0
case "${1:-}" in
  --tlc-only) TLC_ONLY=1 ;;
  --scaled)   TLC_ONLY=1; SCALED=1 ;;
esac

fail=0
note () { printf '\n=== %s ===\n' "$*"; }

resolve_tlc () {
  if [[ -n "${TLC_CMD:-}" ]]; then echo "$TLC_CMD"; return; fi
  local jar=""
  if   [[ -n "${TLA_TOOLS_JAR:-}" && -f "${TLA_TOOLS_JAR:-}" ]]; then jar="$TLA_TOOLS_JAR"
  elif [[ -f formal/tla/tla2tools.jar ]];                        then jar=formal/tla/tla2tools.jar
  elif [[ -f "$HOME/tla2tools.jar" ]];                           then jar="$HOME/tla2tools.jar"
  else
    echo "fetching tla2tools.jar to \$HOME (tlaplus/tlaplus releases)" >&2
    curl -sL -o "$HOME/tla2tools.jar" \
      https://github.com/tlaplus/tlaplus/releases/latest/download/tla2tools.jar
    jar="$HOME/tla2tools.jar"
  fi
  # jar path is made absolute: TLC runs from formal/tla below.
  case "$jar" in /*) : ;; *) jar="$PWD/$jar" ;; esac
  echo "java -XX:+UseParallelGC -cp $jar tlc2.TLC"
}

# ------------------------------------------------- [0] optional re-derive ---
if [[ $SCALED -eq 1 ]]; then
  note "[--scaled] re-running the matrices before auditing their receipts"
  bash 162_separations_matrix.sh . || fail=1
  bash 161_r8_independence_matrix.sh . --bounds reference || fail=1
  bash 161_r8_independence_matrix.sh . --bounds r8 || fail=1
fi

# ---------------------------------------------------------------- [1] TLC ---
# ----------------------------------------- [0] paper <-> artifact sync ---
# Forward direction is HARD: a probe number the paper cites with no file
# behind it is a broken claim. Reverse direction emits NOTEs only. The gate
# needs the paper, which the artifact does not require; absent it, SKIP.
note "[0/6] Structural: paper<->probe inventory"
PAPER="${PAPER:-}"
if [[ -z "$PAPER" ]]; then
  for cand in resume-contract.tex resume_contract.tex paper/resume-contract.tex; do
    [[ -f "$cand" ]] && { PAPER="$cand"; break; }
  done
fi
if [[ -z "$PAPER" || ! -f "$PAPER" ]]; then
  echo "  SKIP -- no paper .tex in tree (set PAPER=<path> to enable)"
elif [[ ! -x probe_inventory_gate.sh && ! -f probe_inventory_gate.sh ]]; then
  echo "  SKIP -- probe_inventory_gate.sh absent"
else
  bash probe_inventory_gate.sh "$PAPER" probes . || fail=1
fi

note "[1/6] TLC verification matrix (ResumeContract, LangGraphFork, R10, R11)"
TLC="$(resolve_tlc)"
echo "TLC command: $TLC   (-workers 1: see header)"
pushd formal/tla > /dev/null
declare -A MODULE=(
  [R0_reference]=ResumeContract [R1_replay]=ResumeContract
  [R2_forkignore]=ResumeContract [R3_invalidpersist]=ResumeContract
  [R4_nondetrec]=ResumeContract [R5_doubleconsume]=ResumeContract
  [R6_liveness]=ResumeContract
  [LGF_AsImplemented]=LangGraphFork [LGF_ForkKeyed]=LangGraphFork
  [SEP_reference]=R10_Separations [SEP_regate]=R10_Separations
  [SEP_rebuild]=R10_Separations [SEP_redeliver]=R10_Separations
  [C0_reference]=R11_ConsumeCount [C1_raceconsume]=R11_ConsumeCount
  [C2_raceeffect]=R11_ConsumeCount
)
declare -A EXPECT=(
  [R0_reference]="Model checking completed. No error has been found."
  [R6_liveness]="Model checking completed. No error has been found."
  [LGF_AsImplemented]="Invariant ForkDeterminism is violated"
  [LGF_ForkKeyed]="Model checking completed. No error has been found."
  [R1_replay]="Invariant EffectExactlyOnce is violated"
  [R2_forkignore]="Invariant ForkDeterminism is violated"
  [R3_invalidpersist]="Invariant CheckpointValidity is violated"
  [R4_nondetrec]="Invariant RecoveryDeterminism is violated"
  [R5_doubleconsume]="Invariant ConsumeOnce is violated"
  [SEP_reference]="Model checking completed. No error has been found."
  [SEP_regate]="Invariant RecoveryDeterminism is violated"
  [SEP_rebuild]="Invariant PrefixConsistency is violated"
  [SEP_redeliver]="Invariant EffectExactlyOnce is violated"
  # R11 splits Property 5 into CO-c (consumption count) and CO-e (effect
  # inertness). C0 is the conservative-extension gate: it MUST reproduce
  # R0's 87/59 exactly, which [2] audits from the committed receipt.
  # C1 is the separating witness for Proposition 2(iv): CO-c fails while
  # the other seven invariants hold over the entire reachable space.
  [C0_reference]="Model checking completed. No error has been found."
  [C1_raceconsume]="Invariant ConsumeOnceCount is violated"
  [C2_raceeffect]="Invariant EffectExactlyOnce is violated"
)
# The four SEP_* headline cells are the conjunction-independence witnesses
# (Proposition 2, clause (v)). Every SEP config checks ALL SEVEN invariants
# (the R0_reference convention, not R1-R5's single-invariant one), so a fault
# cell reporting its target property violated is also evidence that no other
# invariant breaks first on the way there. What these four cells do NOT
# establish is that the other five hold over the faulty model's entire
# reachable state space -- that needs one complete run per (switch,
# invariant) pair, which is 162_separations_matrix.sh, audited in [2].
for cfg in R0_reference R1_replay R2_forkignore R3_invalidpersist \
           R4_nondetrec R5_doubleconsume R6_liveness \
           LGF_AsImplemented LGF_ForkKeyed \
           SEP_reference SEP_regate SEP_rebuild SEP_redeliver \
           C0_reference C1_raceconsume C2_raceeffect; do
  $TLC -deadlock -config "$cfg.cfg" -workers 1 "${MODULE[$cfg]}.tla" \
       > "$cfg.audit.out" 2>&1 || true
  if grep -q "${EXPECT[$cfg]}" "$cfg.audit.out"; then
    echo "  $cfg: OK (${EXPECT[$cfg]})"
  else
    echo "  $cfg: FAIL -- expected: ${EXPECT[$cfg]}"; fail=1
  fi
done
popd > /dev/null

# ------------------------------------------------- [2] committed receipts ---
note "[2/6] Committed matrix receipts (161 independence, 162 separations)"
python3 - << 'PYAUDIT' || fail=1
import json, os, sys

# 161: per-invariant fault matrix. The SAME verdict pattern is required at the
# reference bounds and at R8 -- that identity is what makes the separations
# bound-relative rather than bound-dependent.
IX = {
 "replay":        {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"holds"},
 "forkignore":    {"EffectExactlyOnce":"holds","PrefixConsistency":"holds","ForkDeterminism":"violated","CheckpointValidity":"holds","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "invalidpersist":{"EffectExactlyOnce":"holds","PrefixConsistency":"holds","ForkDeterminism":"holds","CheckpointValidity":"violated","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "nondetrec":     {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"violated"},
 "doubleconsume": {"EffectExactlyOnce":"violated","PrefixConsistency":"holds","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"violated","RecoveryDeterminism":"holds"},
 "prefixreplay":  {"EffectExactlyOnce":"violated","PrefixConsistency":"violated","ForkDeterminism":"holds","CheckpointValidity":"holds","ConsumeOnce":"holds","RecoveryDeterminism":"holds"},
 "staterebuild":  {"TypeOK":"holds","EffectExactlyOnce":"holds","PrefixContinuation":"violated"},
}
INVS = ["TypeOK","EffectExactlyOnce","PrefixConsistency","ForkDeterminism",
        "CheckpointValidity","ConsumeOnce","RecoveryDeterminism"]
# 162: each witness isolates exactly one property; the reference cell holds on
# all six, which is the module's sanity check against a broken encoding.
SEP = {
 "reference": {i: "holds" for i in INVS},
 "regate":    dict({i: "holds" for i in INVS}, RecoveryDeterminism="violated"),
 "rebuild":   dict({i: "holds" for i in INVS}, PrefixConsistency="violated"),
 "redeliver": dict({i: "holds" for i in INVS}, EffectExactlyOnce="violated"),
}

def audit(path, expected, label):
    """Return 0 ok, 1 fail, 2 absent."""
    if not os.path.exists(path):
        print(f"  {label}: SKIP -- no receipt at {path}")
        return 2
    d = json.load(open(path))
    m, bad, n = d.get("matrix", {}), [], 0
    for grp, row in expected.items():
        for inv, want in row.items():
            got = m.get(grp, {}).get(inv, {}).get("verdict")
            n += 1
            if got != want:
                bad.append(f"{grp} x {inv}: receipt says {got}, expected {want}")
    if bad:
        print(f"  {label}: FAIL ({len(bad)}/{n} cells disagree)")
        for b in bad[:8]:
            print(f"      {b}")
        return 1
    bounds = d.get("meta", {}).get("bounds", "n/a")
    print(f"  {label}: OK ({n} cells, bounds={bounds})")
    return 0

rc, absent = 0, 0
for path, exp, lab in [
    ("formal/tla/independence_ref_rerun/r8_matrix.json", IX,  "161 @ reference bounds"),
    ("formal/tla/independence_r8/r8_matrix.json",        IX,  "161 @ R8 bounds"),
    ("formal/tla/separations/separations_matrix.json",   SEP, "162 separations"),
]:
    r = audit(path, exp, lab)
    rc = max(rc, 1 if r == 1 else 0)
    absent += (r == 2)

# p14 stable blocks: the campaign whose probes are too slow (or too
# kill-happy) for the conformance runner, gated here on their committed
# receipts. Values are the two-environment result reported in the paper.
P14 = {
  "results/parkkill/158_stable.json":
    {"all_park_kill_cells_conformant": True},
  "results/parkkill/158b_stable.json":
    {"all_park_kill_cells_conformant": True},
  "results/parkkill/158c_stable.json":
    {"between_node_snapshot_restorable_after_kill": True, "outcome": 11,
     "b_execs_total": 1},
  "results/parkkill/160_stable.json":
    {"points_with_duplicate_effect": [1, 2, 3, 4], "points_unrecoverable": [],
     "all_points_recoverable": True, "freeze_exact_at_every_point": True},
  "results/multiproc/159_stable.json":
    {"race_same_gate_fire_distribution": {"2": 10},
     "race_diff_gate_fire_distribution": {"2": 10},
     "contention_per_thread_exactly_once": True,
     "shim_race_inert_all_reps": False},
  "results/multiproc/159_stable_pg.json":
    {"race_same_gate_fire_distribution": {"2": 10},
     "race_diff_gate_fire_distribution": {"2": 10},
     "contention_per_thread_exactly_once": True,
     "shim_race_inert_all_reps": False},
}
for path, want in P14.items():
    lab = "p14 " + (os.path.basename(path)
                    .replace("_stable_pg.json", " (postgres)")
                    .replace("_stable.json", ""))
    if not os.path.exists(path):
        print(f"  {lab}: SKIP -- no receipt at {path}"); absent += 1; continue
    got = json.load(open(path)).get("stable", {})
    bad = [f"{k}: {got.get(k)!r} != {v!r}" for k, v in want.items()
           if got.get(k) != v]
    if bad:
        print(f"  {lab}: FAIL"); [print("      " + b) for b in bad]; rc = 1
    else:
        print(f"  {lab}: OK ({len(want)} fields)")

if absent:
    print("  (missing receipts are not a failure -- re-derive with:")
    print("     bash 161_r8_independence_matrix.sh . --bounds reference")
    print("     bash 161_r8_independence_matrix.sh . --bounds r8")
    print("     bash 162_separations_matrix.sh .")
    print("   p14 receipts come from probes/158*, 159, 160 -- see matrix.toml")
    print("   or ./reproduce.sh --scaled to run all three matrices, then audit.)")
sys.exit(rc)
PYAUDIT

# ------------------------------------- [2b] p16 concurrency + self-audit ---
note "[2b/6] p16 receipts (167 consume-count, 168 multi-racer, 169 audit)"
python3 - << 'PYP16' || fail=1
import json, os, sys
rc = 0

# --- 167: the R11 matrix. Two gates, both load-bearing for Prop 2(iv). ---
p167 = "formal/tla/consumecount/167_matrix.json"
if not os.path.exists(p167):
    print("  167: ABSENT (re-derive: ./167_consumecount_matrix.sh <tla2tools.jar>)")
else:
    m = json.load(open(p167))["cells"]
    # Gate A: C0 must equal R0's 87/59 on every invariant -> conservative
    # extension. If it does not, R11 is a different model and the
    # separation it witnesses is not a separation of THIS contract.
    c0 = {k: v for k, v in m.items() if k.startswith("C0_reference.")}
    bad = [k for k, v in c0.items()
           if v.get("verdict") != "holds"
           or (v.get("generated"), v.get("distinct")) != (87, 59)]
    print(f"  167 C0 conservative-extension gate: "
          f"{'OK (87/59 on all %d)' % len(c0) if not bad else 'FAIL ' + str(bad)}")
    rc |= bool(bad)
    # Gate B: C1 must violate ConsumeOnceCount and NOTHING else.
    c1 = {k.split(".")[1]: v for k, v in m.items()
          if k.startswith("C1_raceconsume.")}
    viol = sorted(k for k, v in c1.items() if v.get("verdict") == "violated")
    ok = viol == ["ConsumeOnceCount"]
    print(f"  167 C1 separating witness: "
          f"{'OK (CO-c only)' if ok else 'FAIL -- violated: %s' % viol}")
    rc |= (not ok)

# --- 168: the concurrency characterization. ---
p168 = "results/multiproc/168_stable.json"
if not os.path.exists(p168):
    print("  168: ABSENT (re-derive: probes/168_p16_multiracer_sweep.py)")
else:
    st = json.load(open(p168))["stable"]
    # P3 is null on a TARGETED sweep (--ks / --jitters excluding k=2/j=0).
    # That is "not evaluated", not "failed": treat it as SKIP so a window
    # dose-response run does not red-flag an intact artifact. Only an
    # explicit False is a failure.
    p3 = st.get("P3_reproduces_probe159_k2_j0")
    print(f"  168 P3 reproduces probe 159 at k=2/j=0: "
          f"{'OK' if p3 is True else 'SKIP (cell not in this sweep)' if p3 is None else 'FAIL'}")
    rc |= (p3 is False)
    for lab, good in [
        ("P1 duplicates scale beyond two", st.get("P1_duplicates_scale_beyond_two") is True),
        ("no racer errors",                st.get("any_racer_errors") is False)]:
        print(f"  168 {lab}: {'OK' if good else 'FAIL'}")
        rc |= (not good)
    if st.get("gate_duration_ms"):
        print(f"  168 window dose-response: gate {st['gate_duration_ms']} ms, "
              f"edge {st.get('window_edge_ms')} ms")
    sat = st.get("saturation_mean_fires_over_k", {})
    if sat:
        lo = min(sat.values())
        print(f"  168 saturation: {sum(1 for v in sat.values() if v >= 0.999)}"
              f"/{len(sat)} cells at 1.0, min {lo}")

# --- provenance: a copied receipt must not pass as a replication -------
# Two runs on two machines cannot share a wall-clock second. This gate
# exists because a container receipt was once produced by copying its
# developer-host twin and rewriting only the host field: every cell
# agreed, which is exactly what made it invisible. Cell agreement is the
# claim, so it cannot also be the check.
import glob, itertools
stamps = {}
for q in glob.glob("results/**/*.json", recursive=True) + \
         glob.glob("formal/**/*.json", recursive=True):
    try:
        j = json.load(open(q))
    except Exception:
        continue
    if isinstance(j, dict) and "host" in j and "utc" in j:
        stamps.setdefault(j["utc"], []).append((q, j["host"]))
clashes = [(u, v) for u, v in stamps.items()
           if len({h for _, h in v}) > 1]
if clashes:
    for u, v in clashes:
        print(f"  PROVENANCE FAIL: utc {u} shared by differing hosts:")
        for q, h in v:
            print(f"      {h:20s} {q}")
    rc |= 1
else:
    print(f"  provenance: OK ({len(stamps)} timestamped receipts, "
          f"no cross-host collision)")

# a receipt's FILENAME must not contradict its CONTENTS. A file named
# _memory that records backend "sqlite" is a mislabeled copy, and no
# timestamp or host check catches it: same machine, same second, same
# run. Both mislabelings this artifact has seen came from copying a
# receipt under a name describing a run that never happened.
FILENAME_CLAIMS = {"_memory": ("backend", "memory"),
                   "_sqlite": ("backend", "sqlite"),
                   "_postgres": ("backend", "postgres"),
                   "_inmemory": ("backend", "memory")}
for q in glob.glob("results/**/*.json", recursive=True) + \
         glob.glob("formal/**/*.json", recursive=True):
    base = os.path.basename(q)
    for token, (field, want) in FILENAME_CLAIMS.items():
        if token not in base:
            continue
        try:
            j = json.load(open(q))
        except Exception:
            continue
        got = j.get(field)
        if got is not None and got != want:
            print(f"  PROVENANCE FAIL: {q} is named {token} but records "
                  f"{field}={got!r}")
            rc |= 1

# a _container receipt must not carry its canonical twin's timestamp
for cq in glob.glob("results/**/*_container.json", recursive=True) + \
          glob.glob("formal/**/*_container.json", recursive=True):
    tq = cq.replace("_container.json", ".json")
    if not os.path.exists(tq):
        continue
    try:
        a, b = json.load(open(cq)), json.load(open(tq))
    except Exception:
        continue
    if a.get("utc") and a.get("utc") == b.get("utc"):
        print(f"  PROVENANCE FAIL: {cq} shares utc with {tq}")
        rc |= 1

# --- 169: does the committed evidence agree with the paper? ---
p169 = "results/mutation/169_report.json"
if not os.path.exists(p169):
    print("  169: ABSENT (re-derive: probes/169_p16_harness_mutation.py --baseline)")
else:
    r = json.load(open(p169))
    miss, dis = r["baseline_receipts_missing"], r["baseline_disagreeing_with_paper"]
    n = len(r["baseline"])
    print(f"  169 receipt self-audit: {n - len(miss)}/{n} present, "
          f"{len(dis)} disagreeing with the paper")
    if miss:
        print(f"    MISSING: {miss}")
    if dis:
        print(f"    DISAGREEING: {dis}")
    rc |= bool(dis)          # a disagreement is a hard failure; absence is not

sys.exit(rc)
PYP16

# ------------------------------------------- [2c] IndCheck (inductive) ---
# ---- 171: out-of-sample prediction test for LangGraphFork.tla ----------
# Gated on its committed receipt rather than re-executed: 171 writes its own
# <id>_stable.json, so it cannot be a matrix.toml row (see the note there).
# The claim this gates is Sec. 4.3's: four protocols the model was never
# exercised on, predictions registered in the probe source BEFORE the run,
# all four confirmed -- with P-D the negative control that carries the weight,
# since a degenerate model serving the first value ever seen would satisfy the
# other three and fail it.
python3 - << 'PY171' || fail=1
import json, os, sys
rc = 0
found = False
for path in ("results/matrix/171_stable.json",
             "results/matrix/171_stable_memory.json",
             "results/matrix/171_stable_sqlite.json"):
    if not os.path.exists(path):
        continue
    found = True
    st = json.load(open(path)).get("stable", {})
    reg  = st.get("predictions_registered_in_source", [])
    conf = st.get("confirmed", [])
    bad = []
    if len(reg) != 4:                                bad.append(f"registered={len(reg)}")
    if sorted(conf) != sorted(reg):                  bad.append("confirmed != registered")
    if st.get("mispredicted"):                       bad.append(f"mispredicted={st['mispredicted']}")
    if st.get("errored"):                            bad.append(f"errored={st['errored']}")
    if st.get("model_out_of_sample_confirmed") is not True: bad.append("out_of_sample not confirmed")
    if st.get("negative_control_PD_confirmed") is not True: bad.append("negative control P-D not confirmed")
    label = os.path.basename(path)
    if bad:
        print(f"  171 {label}: FAIL -- {', '.join(bad)}"); rc = 1
    else:
        print(f"  171 {label}: OK (4/4 registered predictions confirmed, "
              f"P-D negative control holds)")
if not found:
    print("  171: ABSENT (re-derive: envs/langgraph/.venv/bin/python "
          "probes/171_p16_lgf_outofsample.py --out results/matrix)")
sys.exit(rc)
PY171

note "[2c/6] IndCheck inductive-invariant matrix (R0/R2/R3)"
if [[ $SCALED -eq 1 ]]; then
  if [[ -f 173_indcheck_matrix.sh ]]; then
    echo "  [--scaled] re-running all three cells (~5 min; R2 dominates)"
    bash 173_indcheck_matrix.sh 4 || fail=1
  else
    echo "  173_indcheck_matrix.sh absent -- cannot re-run"; fail=1
  fi
fi
python3 - << 'PYIND' || fail=1
import json, os, sys

# Expected cells. Counts are deterministic in the worker count here: this
# enumerates every state satisfying the invariant as an initial state rather
# than racing a breadth-first search to a counterexample, so there is no
# parallel-search nondeterminism to pin. The single-worker convention that
# governs counterexample DEPTHS elsewhere in this script does not apply.
EXP = {
  "R0": (19659,   8610),
  "R2": (1009076, 450926),
  "R3": (249915,  97800),
}
p = "results/tla/indcheck/indcheck_matrix.json"
if not os.path.exists(p):
    print(f"  SKIP -- no receipt at {p} (re-derive: ./173_indcheck_matrix.sh)")
    sys.exit(0)

m = json.load(open(p))
rc = 0
seen = {c["label"]: c for c in m.get("cells", [])}

missing = sorted(set(EXP) - set(seen))
if missing:
    print(f"  FAIL -- receipt missing cells {missing}"); rc = 1

for label, (gen, dist) in EXP.items():
    c = seen.get(label)
    if c is None:
        continue
    bad = []
    if c.get("verdict") != "no_error":         bad.append(f"verdict={c.get('verdict')}")
    if c.get("generated") != gen:              bad.append(f"generated={c.get('generated')}")
    if c.get("distinct")  != dist:             bad.append(f"distinct={c.get('distinct')}")
    if c.get("depth")     != 1:                bad.append(f"depth={c.get('depth')}")
    if c.get("audit")     != "pass":           bad.append(f"audit={c.get('audit')}")
    if bad:
        print(f"  {label}: FAIL -- {', '.join(bad)}"); rc = 1
    else:
        print(f"  {label}: OK ({c.get('constants','')}) "
              f"{gen} generated / {dist} distinct, inductive")

# Coverage gate. Three passing cells prove nothing about generality if they
# are the same configuration three times; this asserts the six bounds are
# actually varied, which is the claim Sec. 9 makes.
if not missing:
    def bounds(c):
        return dict(kv.split("=", 1) for kv in c["constants"].split())
    b = {k: bounds(v) for k, v in seen.items() if k in EXP}
    keys = set().union(*(set(v) for v in b.values()))
    pinned = [k for k in sorted(keys) if len({v.get(k) for v in b.values()}) == 1]
    if pinned:
        print(f"  FAIL -- bounds never varied across cells: {pinned}"); rc = 1
    else:
        print(f"  coverage: OK (all {len(keys)} bounds vary in at least one pair)")

if m.get("matrix_audit") != "pass":
    print(f"  FAIL -- matrix_audit={m.get('matrix_audit')}"); rc = 1
sys.exit(rc)
PYIND

if [[ $TLC_ONLY -eq 1 ]]; then
  [[ $fail -eq 0 ]] && echo "TLC-only audit: CLEAN" || echo "TLC-only audit: FAILED"
  exit $fail
fi

# -------------------------------------------------- [3] conformance plan ---
note "[3/6] Conformance plan (matrix.toml vs committed baselines)"
uv sync --quiet
# langgraph-durable carries the durable-backend and p14 probes (157-160) and
# declares remit-contract, whose shim arms degrade silently if it is absent.
for env in langgraph langgraph-durable llamaindex crewai pydantic-graph \
           openai-agents langgraph-live langgraph-1.1; do
  [[ -d "envs/$env" ]] && uv sync --quiet --project "envs/$env"
done
uv run python -m conformance.runner \
  --plan matrix.toml --baseline results/pilot || fail=1

# --------------------------------------------------------- [4] Remit tests ---
note "[4/6] Remit invariants + line-identical core (cargo test + n3 gate)"
# Toolchain absence is a SKIP, not a failure -- the same posture step [5]
# takes for Verus. A reviewer without a Rust toolchain should see which
# evidence was not exercised, not a red AUDIT RESULT on an intact artifact.
if command -v cargo >/dev/null 2>&1; then
  cargo test --workspace --quiet || fail=1
else
  echo "  Rust toolchain not on PATH -- skipping cargo test (7 property-named"
  echo "  tests + the differential suite). Install: https://rustup.rs, re-run."
fi
# The sync gate is pure text comparison and runs without a toolchain.
bash n3_sync_check.sh . || fail=1

# ------------------------------------------------------ [5] Verus proofs ----
note "[5/6] Remit Verus proofs (crates/remit/proof)"
if command -v verus >/dev/null 2>&1; then
  for f in remit_verus remit_verus_cv remit_verus_all \
           remit_verus_fd_machine remit_verus_rd_interp \
           remit_verus_recover_exec remit_verus_ledger_exec; do
    p="crates/remit/proof/$f.rs"
    if verus "$p" 2>&1 | tee "/tmp/verus_$f.out" \
         | grep -qE "[0-9]+ verified, 0 errors"; then
      echo "  $f: $(grep -oE '[0-9]+ verified, [0-9]+ errors' "/tmp/verus_$f.out" | head -1)"
    else
      echo "  $f: FAILED"; tail -5 "/tmp/verus_$f.out"; fail=1
    fi
  done
  # Negative certificates: each must FAIL in exactly the expected shape.
  # verus exits nonzero here BY DESIGN; capture output first (a direct
  # pipeline under `set -o pipefail` reads as failure even on a match).
  for p in crates/remit/proof/negative/*.rs; do
    [[ -e "$p" ]] || continue
    b=$(basename "$p")
    out=$(verus "$p" 2>&1 || true)
    printf '%s\n' "$out" > "/tmp/verus_neg_$b.out"
    if grep -q "2 verified, 1 errors" <<<"$out"; then
      echo "  negative/$b: OK (expected falsification: 2 verified, 1 errors)"
    else
      echo "  negative/$b: FAIL -- expected '2 verified, 1 errors'; got:"
      grep -m1 "verification results" <<<"$out" || tail -3 <<<"$out"; fail=1
    fi
  done
else
  echo "  Verus not on PATH -- skipping SMT discharge (proof logic already"
  echo "  cross-checked by cargo test tests/proof_logic.rs above)."
  echo "  Install: https://github.com/verus-lang/verus, then re-run."
fi

note "AUDIT RESULT"
if [[ $fail -eq 0 ]]; then
  echo "CLEAN: every headline number re-derived from committed data."
  echo "(Sections reporting SKIP were not exercised -- missing API keys or"
  echo " toolchains -- and are listed above; nothing skipped is a verdict.)"
else
  echo "FAILED: see sections above."
fi
exit $fail