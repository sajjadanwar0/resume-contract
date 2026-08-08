#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

LOADBEARING = [
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
    "M1": ("effect_undercount",  ["LG.CO.concurrent"]),
    "M2": ("effect_overcount",   ["LG.CO.concurrent"]),
    "M3": ("crash_noop",         ["LG.CO.concurrent"]),
    "M4": ("fork_value_ignore",  ["LG.FD.6663"]),
    "M5": ("label_rule_invert",  []),
    "M6": ("state_only_oracle",  ["LG.CO.concurrent"]),
    "M7": ("barrier_removed",    []),
    "M8": ("pin_drift",          ["__REFUSAL__"]),
}

def read_stable(results_root, probe, key):
    """Read one committed stable-view field.  Returns None when absent so a
    missing receipt is distinguishable from a flipped verdict."""
    pats = [f"{probe}_stable*.json", f"{probe}_results*.json", f"{probe}.json"]
    cands = [q for pat in pats for q in Path(results_root).rglob(pat)]
    for p in cands:
        try:
            d = json.loads(p.read_text())
        except Exception:
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
