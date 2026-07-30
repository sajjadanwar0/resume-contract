#!/usr/bin/env python3
"""
172_p16_mutation_diff.py  (campaign p16)

Stage 3 of the harness mutation study: run the load-bearing cells against
each mutant tree produced by probe 170, diff the verdicts against the
committed baseline, and compare the observed kill set to the operator's
registered prediction.

This exists because the shell loop it replaces failed silently three ways
at once, and every one of them would have been reported as "the mutant
survived":

  1. probes redirected to /dev/null, so a probe that crashed on import
     was indistinguishable from one that ran and changed nothing;
  2. only three of the ten probes behind the fifteen load-bearing cells
     were invoked, so twelve cells were absent rather than unchanged;
  3. the report was truncated, so the absences never surfaced.

A cell that did not RUN is not a cell that did not CHANGE. This driver
keeps those two outcomes in separate columns and refuses to score a
mutant whose probes failed.

Each probe runs under the env matrix.toml assigns it, resolved against
the ORIGINAL repo (already synced) while executing the MUTATED file --
mutant trees carry pyproject.toml but no .venv by construction.

Usage:
  probes/172_p16_mutation_diff.py --mutants /tmp/mut --base .
  probes/172_p16_mutation_diff.py --mutants /tmp/mut --base . --only M5,M7
  probes/172_p16_mutation_diff.py --mutants /tmp/mut --base . --list-cells
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# cell -> (probe filename stem, env). Mirrors probe 169's LOADBEARING
# table; a cell whose probe is absent here can never be scored.
CELL_PROBES = {
    "LG.EO.crash":        ("118_p3_langgraph_rd_interleavings", "langgraph"),
    "LG.RD.reflexive":    ("118_p3_langgraph_rd_interleavings", "langgraph"),
    "LG.RD.orderinv":     ("118_p3_langgraph_rd_interleavings", "langgraph"),
    "LG.EO.sigkill":      ("133_p7_langgraph_real_kill", "langgraph-durable"),
    "LG.FD.6663":         ("125_p4_shim_fd_langgraph", "langgraph"),
    "LG.CV.repair":       ("123_p4_shim_cv_langgraph", "langgraph"),
    "LG.CV.nopersist":    ("123_p4_shim_cv_langgraph", "langgraph"),
    "LG.CO.concurrent":   ("159_p14_multiproc_saver", "langgraph-durable"),
    "LG.killsweep":       ("160_p14_kill_point_sweep", "langgraph-durable"),
    "LG.killsweep.rec":   ("160_p14_kill_point_sweep", "langgraph-durable"),
    "CA.restore.dup":     ("115b_p2_crewai_checkpointconfig", "crewai"),
    "AG.restore.EO":      ("140_p8_autogen_state_column", "autogen"),  # no envs/autogen: falls back to root project
    "AG.doublerestore":   ("140_p8_autogen_state_column", "autogen"),
    "PG.parkkill.ok":     ("158c_p14_pydantic_graph_park_kill", "pydantic-graph"),
    "ORACLE.noovercount": ("163_p15_oracle_atomicity", "langgraph-durable"),
}

PREDICTED = {
    "M1": ["LG.CO.concurrent"], "M2": [],  # reaches only an already-violated boolean; cannot flip it
    "M3": ["LG.CO.concurrent"], "M4": ["LG.FD.6663"], "M5": [],
    "M6": ["LG.CO.concurrent"], "M7": [], "M8": ["__REFUSAL__"],
}

# Files each operator mutates. A cell is REACHABLE by an operator only if
# the probe backing it appears here; predicting a kill in an unreachable
# cell is a statement no run can satisfy, and scoring it as a miss blames
# the harness for the prediction's incoherence.
MUTATES = {
    "M1": {"159_p14_multiproc_saver", "126_p6_langgraph_sqlite_durable"},
    "M2": {"159_p14_multiproc_saver", "126_p6_langgraph_sqlite_durable"},
    "M3": {"159_p14_multiproc_saver"}, "M4": {"125_p4_shim_fd_langgraph"},
    "M5": set(), "M6": {"159_p14_multiproc_saver"},
    "M7": {"159_p14_multiproc_saver"}, "M8": set(),
}


def resolve(tree, stem):
    hits = sorted(Path(tree, "probes").glob(f"{stem}*.py"))
    return hits[0] if hits else None


def run_probe(tree, base, stem, env, timeout=900):
    """Execute one probe inside `tree` using `base`'s synced env.
    Returns (ok, note) -- never swallows the failure."""
    f = resolve(tree, stem)
    if f is None:
        return False, f"probe file {stem}*.py absent in mutant tree"
    proj = Path(base, "envs", env)
    if not proj.exists():
        # Not every probe has a dedicated env; some run under the root
        # project. Fall back rather than report a spurious env failure --
        # which would land the cell in UNSCORED and look like an absence.
        root = Path(base)
        if (root / "pyproject.toml").exists():
            proj = root
        else:
            return False, f"env {env} absent at {proj} and no root project"
    penv = {**os.environ, "VIRTUAL_ENV": ""}
    penv.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    penv.setdefault("OTEL_SDK_DISABLED", "true")
    try:
        r = subprocess.run(
            ["uv", "run", "--project", str(proj), "python",
             str(f.relative_to(tree))],
            cwd=str(tree), capture_output=True,
            text=True, timeout=timeout, env=penv)
    except subprocess.TimeoutExpired:
        # Under M3 (crash_noop) a probe that waits on a kill will hang by
        # construction. That is the operator working, not the driver
        # failing -- but the cell is still unscored, because a hung probe
        # produced no verdict to compare.
        return False, (f"timeout after {timeout}s (expected under "
                       f"crash_noop: the kill it waits on was disabled)")
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()
        return False, f"rc={r.returncode}: {tail[-1] if tail else 'no output'}"

    # Two probe classes exist in this suite and only one writes its own
    # receipt. Probes 115/118/123/125/133 emit JSON on stdout and rely on
    # harness/conformance/runner.py to persist it; probes 158c/159/160/163
    # write files themselves. An earlier revision of this driver invoked
    # probes directly, so the stdout class left nothing on disk, every one
    # of their cells read back as None, and None-vs-baseline scored as a
    # flipped verdict -- which is why all eight mutants reported the same
    # eight kills. Persist stdout JSON here, exactly as the runner does.
    start = r.stdout.find("{")
    if start >= 0:
        try:
            doc = json.loads(r.stdout[start:])
        except json.JSONDecodeError:
            doc = None
        if doc is not None:
            num = Path(f).name.split("_")[0]
            dest = Path(tree, "results", "pilot")
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{num}_results.json").write_text(json.dumps(doc, indent=1))
            return True, f"ok (env {proj.name}, stdout persisted)"
    return True, f"ok (env {proj.name})"


def read_cells(results_root, base):
    """Re-derive the fifteen cells from a results tree using probe 169's
    reader, so this driver and the baseline audit agree by construction."""
    sys.path.insert(0, str(Path(base, "probes")))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "p169", str(Path(base, "probes", "169_p16_harness_mutation.py")))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return {c["cell"]: m.read_stable(results_root, c["probe"], c["key"])
            for c in m.LOADBEARING}, {c["cell"]: c["paper"] for c in m.LOADBEARING}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutants", default="/tmp/mut")
    ap.add_argument("--base", default=".")
    ap.add_argument("--only", default=None, help="e.g. M5,M7")
    ap.add_argument("--list-cells", action="store_true")
    ap.add_argument("--out", default="results/mutation")
    a = ap.parse_args()

    if a.list_cells:
        for c, (s, e) in CELL_PROBES.items():
            print(f"  {c:22s} {s:38s} env={e}")
        return 0

    baseline, paper = read_cells(str(Path(a.base, "results")), a.base)
    missing = [c for c, v in baseline.items() if v is None]
    if missing:
        print(f"BASELINE GAP: {missing}\nNo mutant can be scored against "
              f"cells with no committed value.", file=sys.stderr)

    todo = a.only.split(",") if a.only else sorted(PREDICTED)
    report = {}
    for M in todo:
        tree = Path(a.mutants, M)
        if not tree.exists():
            print(f"{M}: tree absent at {tree} -- run 170 --apply-all first")
            continue
        print(f"=== {M} ===")
        # run only the probes that back at least one scorable cell
        need = {}
        for cell, (stem, env) in CELL_PROBES.items():
            if baseline.get(cell) is not None:
                need.setdefault((stem, env), []).append(cell)
        runs = {}
        for (stem, env), cells in sorted(need.items()):
            ok, note = run_probe(str(tree), a.base, stem, env)
            runs[stem] = {"ok": ok, "note": note, "cells": cells}
            print(f"   {'run ' if ok else 'FAIL'} {stem:38s} {note}")

        mut_cells, _ = read_cells(str(tree / "results"), a.base)
        killed, unchanged, unscored, no_receipt = [], [], [], []
        for cell, base_v in baseline.items():
            if base_v is None:
                continue
            stem = CELL_PROBES.get(cell, (None, None))[0]
            mut_v = mut_cells.get(cell)
            if stem is None or not runs.get(stem, {}).get("ok"):
                unscored.append(cell)
            elif mut_v is None:
                # The probe exited 0 but left no readable receipt for this
                # cell. That is ABSENCE, not a flipped verdict. An earlier
                # revision compared None against the baseline and scored
                # every such cell as killed, which made all eight mutants
                # report an identical kill set -- the tell that the number
                # was an artifact rather than a measurement.
                no_receipt.append(cell)
            elif mut_v != base_v:
                killed.append(cell)
            else:
                unchanged.append(cell)

        reach = {c for c, (stem, _) in CELL_PROBES.items()
                 if stem in MUTATES.get(M, set())}
        pred = [c for c in PREDICTED[M] if c != "__REFUSAL__"]
        incoherent = [c for c in pred if c not in reach]
        if incoherent:
            print(f"   INCOHERENT PREDICTION: {incoherent} -- {M} does not "
                  f"mutate the probe backing these cells. Fix the prediction.")
        verdict = ("INCOHERENT" if incoherent else
                   "OUT-OF-SCOPE" if not reach else
                   "UNSCORABLE" if (unscored or no_receipt) and
                   (set(unscored) | set(no_receipt)) & reach else
                   "AS PREDICTED" if sorted(killed) == sorted(pred) else
                   "FINDING")
        report[M] = {"verdict": verdict, "predicted_kills": PREDICTED[M],
                     "observed_kills": sorted(killed),
                     "unchanged": sorted(unchanged),
                     "unscored_probe_failed": sorted(unscored),
                     "unscored_no_receipt": sorted(no_receipt),
                     "reachable_cells": sorted(reach),
                     "incoherent_predictions": sorted(incoherent),
                     "runs": runs}
        print(f"   -> {verdict}: killed {sorted(killed) or 'NONE'}"
              + (f", PROBE-FAILED {sorted(unscored)}" if unscored else "")
              + (f", NO-RECEIPT {sorted(no_receipt)}" if no_receipt else ""))

    out = {"probe": "172_p16_mutation_diff", "host": os.uname().nodename,
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "baseline_gaps": missing, "mutants": report}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, "172_diff.json").write_text(json.dumps(out, indent=1))
    print(f"\nwrote {a.out}/172_diff.json")

    findings = [m for m, r in report.items() if r["verdict"] == "FINDING"]
    unsc = [m for m, r in report.items() if r["verdict"] == "UNSCORABLE"]
    if unsc:
        print(f"\nUNSCORABLE: {unsc}. A probe that failed to run is NOT a "
              f"surviving mutant. Fix the run before reading anything into "
              f"these.", file=sys.stderr)
    if findings:
        print(f"\nFINDINGS: {findings}. Each is a real result about the "
              f"harness -- M5 or M7 killing anything refutes a claim the "
              f"paper currently makes in prose; M1-M4/M6 failing to kill "
              f"means the cell is not reading what the paper says it reads.",
              file=sys.stderr)
    return 1 if (unsc or findings) else 0


if __name__ == "__main__":
    sys.exit(main())
