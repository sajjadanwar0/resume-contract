#!/usr/bin/env python3
"""
169_p16_harness_mutation.py  (campaign p16)

The check probe 156 is claimed to be, but structurally cannot be.

Probe 156 mutates the FRAMEWORK source at the resume-serving sites the
mechanism accounts already name, then reports that all eight mutants were
killed.  That is a causal-coupling check and Sec. 9 should claim no more:
mutants hand-placed where the harness is known to look cannot answer
"is the harness tuned to the known bugs," because a harness tuned to a
fixed expected output would kill exactly those mutants too.

The complementary and much sharper question is the inverse: mutate the
HARNESS and confirm that each mutation flips at least one committed
verdict.  A harness mutant that changes no verdict identifies a cell that
is not load-bearing -- a check the paper performs whose outcome nothing
depends on.  A harness mutant that flips a verdict the paper reports as
robust identifies a cell whose verdict is an artifact of one oracle choice.
Either outcome is a finding.  Only "every mutant flips something, and each
flips what its semantics predicts" supports the determinism claim.

MUTATION OPERATORS (each is a semantic change to the ORACLE or the
PROTOCOL, never to the framework):

  M1  effect_undercount   ledger append is skipped on the 2nd+ fire within
                          a run.  PREDICTS: every EO-crash and CO-concurrent
                          violation cell flips to conformant.  If any stays
                          violated, that cell is not reading the ledger.
  M2  effect_overcount    ledger append is duplicated on the 1st fire.
                          PREDICTS: every conformant EO cell flips to
                          violated.  If a conformant cell stays conformant,
                          it is not actually checking effect multiplicity.
  M3  crash_noop          the SIGKILL / exception injection is replaced by
                          a no-op.  PREDICTS: all crash-path cells flip to
                          conformant.  A crash-path cell that survives M3
                          never depended on the crash.
  M4  fork_value_ignore   the FD comparison compares outcome-to-outcome
                          instead of outcome-to-supplied-value.  PREDICTS:
                          #6663 flips to conformant.  This is the mutant
                          that would catch an FD oracle that only checks
                          self-consistency.
  M5  label_rule_invert   the U/X classification rule is inverted.
                          PREDICTS: CrewAI @persist cells move U -> X and
                          NO pairwise separation in observation (i) changes
                          -- the sensitivity claim Table 5 note b asserts
                          in prose but never runs.  M5 runs it.
  M6  state_only_oracle   verdicts read framework-visible state instead of
                          the external ledger.  PREDICTS: the CrewAI
                          CheckpointConfig duplicate and the probe-159
                          duplicate both become INVISIBLE (both are
                          reported as invisible in framework state), i.e.
                          two headline violations flip to conformant.  This
                          is the mutant that justifies the external oracle;
                          if the cells survive it, the external oracle
                          bought nothing and Sec. 5.1 overstates.
  M7  barrier_removed     the filesystem barrier synchronizing the kill is
                          replaced by a fixed sleep.  PREDICTS: verdicts
                          unchanged but repetition-to-repetition stability
                          degrades -- the mechanical form of the
                          "timing-free" claim.
  M8  pin_drift           a framework pin is relaxed to the newest release.
                          PREDICTS: nothing about the contract; this mutant
                          exists to confirm reproduce.sh's lockfile audit
                          REFUSES the run rather than silently reporting a
                          verdict from a different resolution.

Each mutant is applied to a COPY of the harness, one at a time, with the
framework source byte-unchanged (the exact inverse of probe 156).

Usage:
  .venv/bin/python3 probes/169_p16_harness_mutation.py --baseline
  .venv/bin/python3 probes/169_p16_harness_mutation.py --run-all
  .venv/bin/python3 probes/169_p16_harness_mutation.py --mutant M4
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Cells this study treats as load-bearing.  Each entry: the probe that
# decides it, the committed stable-view key, and the verdict the paper
# reports.  A mutant "kills" a cell when the re-run verdict differs.
# --------------------------------------------------------------------------
LOADBEARING = [
    # cell                  probe   committed stable-view key                                  paper reports
    {"cell": "LG.EO.crash",      "probe": "118",  "key": "violation_EO_crash_resume_reexecutes_durable_task", "paper": True},
    {"cell": "LG.EO.sigkill",    "probe": "133",  "key": "violation_completed_durable_task_reexecuted_after_real_kill", "paper": True},
    {"cell": "LG.FD.6663",       "probe": "125",  "key": "violation_FD_through_shim",           "paper": True},
    {"cell": "LG.CV.repair",     "probe": "123",  "key": "write_rejected_loudly",               "paper": True},
    {"cell": "LG.CV.nopersist",  "probe": "123",  "key": "invalid_state_persisted",             "paper": False},
    {"cell": "LG.RD.reflexive",  "probe": "118",  "key": "rd_reflexive_same_log_identical_all", "paper": True},
    {"cell": "LG.RD.orderinv",   "probe": "118",  "key": "violation_RD_recovery_depends_on_persistence_order", "paper": False},
    {"cell": "LG.CO.concurrent", "probe": "159",  "key": "co_concurrent_double_resume_inert_all_reps", "paper": False},
    {"cell": "LG.killsweep",     "probe": "160",  "key": "freeze_exact_at_every_point",         "paper": True},
    {"cell": "LG.killsweep.rec", "probe": "160",  "key": "all_points_recoverable",              "paper": True},
    {"cell": "CA.restore.dup",   "probe": "115b", "key": "s1_execs_total",                      "paper": 2},
    {"cell": "AG.restore.EO",    "probe": "140",  "key": "restore_EO_completed_tool_not_refired", "paper": True},
    {"cell": "AG.doublerestore", "probe": "140",  "key": "double_restore_added_effects",        "paper": 0},
    {"cell": "PG.parkkill.ok",   "probe": "158c", "key": "between_node_snapshot_restorable_after_kill", "paper": True},
    {"cell": "ORACLE.noovercount","probe": "163", "key": "overcount_impossible_no_record_without_effect", "paper": True},
]

MUTANTS = {
    "M1": ("effect_undercount",  ["LG.EO.crash", "LG.CO.concurrent", "CA.restore.dup", "LG.killsweep"]),
    "M2": ("effect_overcount",   ["LG.EO.interrupt", "LG.CO.sequential"]),
    "M3": ("crash_noop",         ["LG.EO.crash", "LG.killsweep", "PG.unrecoverable", "CA.restore.dup"]),
    "M4": ("fork_value_ignore",  ["LG.FD.6663"]),
    "M5": ("label_rule_invert",  []),          # predicts NO cell flips
    "M6": ("state_only_oracle",  ["CA.restore.dup", "LG.CO.concurrent"]),
    "M7": ("barrier_removed",    []),          # predicts NO verdict flips
    "M8": ("pin_drift",          ["__REFUSAL__"]),  # predicts audit refusal
}


def read_stable(results_root, probe, key):
    """Read one committed stable-view field.  Returns None when absent so a
    missing receipt is distinguishable from a flipped verdict."""
    pats = [f"{probe}_stable*.json", f"{probe}_results*.json", f"{probe}.json"]
    cands = [q for pat in pats for q in Path(results_root).rglob(pat)]
    for p in cands:
        try:
            d = json.loads(p.read_text())
        except Exception:                                     # noqa: BLE001
            continue
        blob = d.get("stable", d)
        if key in blob:
            return blob[key]
        for v in blob.values():
            if isinstance(v, dict) and key in v:
                return v[key]
    return None


def baseline(results_root):
    out = {}
    for c in LOADBEARING:
        got = read_stable(results_root, c["probe"], c["key"])
        out[c["cell"]] = {"observed": got, "paper_reports": c["paper"],
                          "receipt_found": got is not None,
                          "agrees_with_paper": (got == c["paper"])
                          if got is not None else None}
    return out


def apply_mutant(harness_root, work, mutant):
    """Copy the harness to `work` and apply one mutation operator.

    NOTE: the operators below are stated as the edits to make; each is a
    two-to-five line change at a single named site.  They are deliberately
    NOT automated with regex rewriting -- a regex that silently matches
    nothing would produce a green mutant that changed no code, which is the
    exact failure mode this probe exists to detect.  Apply by hand at the
    site named, commit the mutant under mutants/169_<Mn>.patch, and let this
    script drive the re-run and the diff.
    """
    shutil.copytree(harness_root, work, dirs_exist_ok=True)
    patch = Path(harness_root).parent / "mutants" / f"169_{mutant}.patch"
    if not patch.exists():
        return {"applied": False,
                "reason": f"mutant patch {patch} not present -- write it first"}
    r = subprocess.run(["git", "apply", "--directory", str(work), str(patch)],
                       capture_output=True, text=True)
    return {"applied": r.returncode == 0, "stderr": r.stderr.strip()[:400]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--harness", default="harness")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--run-all", action="store_true")
    ap.add_argument("--mutant", default=None)
    ap.add_argument("--out", default="results/mutation")
    args = ap.parse_args()

    base = baseline(args.results)
    missing = [k for k, v in base.items() if not v["receipt_found"]]
    disagree = [k for k, v in base.items() if v["agrees_with_paper"] is False]

    report = {"probe": "169_p16_harness_mutation",
              "host": os.uname().nodename,
              "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "baseline": base,
              "baseline_receipts_missing": missing,
              "baseline_disagreeing_with_paper": disagree,
              "mutants": {}}

    if missing:
        print(f"BASELINE GAP: no committed receipt for {missing}. "
              "Mutation results are uninterpretable for those cells.",
              file=sys.stderr)
    if disagree:
        print(f"BASELINE CONFLICT: committed receipt disagrees with the "
              f"paper for {disagree}. Fix before mutating.", file=sys.stderr)

    todo = list(MUTANTS) if args.run_all else ([args.mutant] if args.mutant else [])
    for m in todo:
        name, predicted = MUTANTS[m]
        work = tempfile.mkdtemp(prefix=f"p169_{m}_")
        applied = apply_mutant(args.harness, work, m)
        report["mutants"][m] = {
            "operator": name,
            "predicted_kills": predicted,
            "apply": applied,
            "observed_kills": None,
            "verdict": "NOT_RUN" if not applied["applied"] else "PENDING_RERUN",
            "note": ("Re-run the probes named in LOADBEARING against the "
                     "mutated harness at %s, diff each stable-view field "
                     "against baseline, and record the cells that flipped."
                     % work),
        }
        print(f"{m:4s} {name:20s} predicted kills: "
              f"{predicted or 'NONE (that is the prediction)'}")

    Path(args.out).mkdir(parents=True, exist_ok=True)
    p = Path(args.out) / "169_report.json"
    p.write_text(json.dumps(report, indent=1))
    print(f"\nwrote {p}")
    print("\nAcceptance rule: a mutant whose observed kill set differs from "
          "its predicted set is a FINDING, not a bug in this script. M5 and "
          "M7 predicting an EMPTY kill set are the two cells that discharge "
          "Table 5 note b's sensitivity claim and the timing-free claim "
          "mechanically rather than in prose.")


if __name__ == "__main__":
    main()
