#!/usr/bin/env python3
"""
170_p16_mutation_operators.py  (campaign p16)

The eight operators probe 169 predicts kill sets for. This module APPLIES
them; 169 diffs the resulting verdicts against the committed baseline.

Why operators and not patch files. A .patch breaks on any drift in the
surrounding lines and fails loudly, which is fine. A regex rewrite that
matches NOTHING succeeds silently and yields a "mutant" identical to the
original -- which then trivially fails to kill anything, and the study
reports a surviving mutant that was never applied. That is the one
failure mode this study cannot tolerate, because a surviving mutant is
supposed to be a FINDING about the harness. Every operator below
therefore asserts its site count and refuses to write a file whose
content did not change.

Operators mutate the HARNESS (oracle, protocol, classification), never
the framework. This is the exact inverse of probe 156, whose mutants sit
in the vendored framework source at sites the harness already watches --
a causal-coupling check, not an answer to "is the harness tuned to the
known bugs."

  M1  effect_undercount    ledger append becomes conditional on the tag
                           not already being present: a second fire is
                           silently dropped.
                           PREDICTS kills: LG.EO.crash, LG.EO.sigkill,
                           LG.CO.concurrent, CA.restore.dup, LG.killsweep
  M2  effect_overcount     every append writes twice.
                           PREDICTS kills: every conformant EO/CO cell
                           (LG.CO.sequential, AG.restore.EO,
                           AG.doublerestore)
  M3  crash_noop           SIGKILL is replaced by a wait; exception
                           crashes become pass.
                           PREDICTS kills: LG.EO.crash, LG.EO.sigkill,
                           LG.killsweep, PG.parkkill.ok, CA.restore.dup
  M4  fork_value_ignore    FD compares outcome to outcome instead of
                           outcome to the value supplied.
                           PREDICTS kills: LG.FD.6663
  M5  label_rule_invert    U/X classification inverted in the plan.
                           PREDICTS kills: NONE. This is Table 5 note b's
                           sensitivity claim, currently asserted in prose.
                           M5 discharges it mechanically or refutes it.
  M6  state_only_oracle    verdicts read framework-visible state instead
                           of the external ledger.
                           PREDICTS kills: CA.restore.dup,
                           LG.CO.concurrent -- both are reported as
                           invisible in framework state, so if they
                           SURVIVE M6 the external oracle bought nothing
                           and Sec. 5.1 overstates.
  M7  barrier_removed      filesystem barrier replaced by a fixed sleep.
                           PREDICTS kills: NONE, with degraded rep-to-rep
                           stability. The mechanical form of the
                           timing-free claim.
  M8  pin_drift            a framework pin is relaxed to a floating spec.
                           PREDICTS: reproduce.sh REFUSES the run rather
                           than reporting a verdict from a different
                           resolution.

Usage:
  probes/170_p16_mutation_operators.py --list
  probes/170_p16_mutation_operators.py --apply M4 --into /tmp/mut_M4
  probes/170_p16_mutation_operators.py --apply-all --into /tmp/mut
  probes/170_p16_mutation_operators.py --verify        # dry run, all sites
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


class SiteError(RuntimeError):
    """An operator's site was not found, or was found the wrong number of
    times. Always fatal: a silently unapplied mutant is worse than none."""


def edit(root, relpath, pattern, replacement, expect, flags=0):
    """Rewrite `pattern` -> `replacement` in one file, asserting the match
    count is exactly `expect` and that the file content actually changed."""
    p = Path(root) / relpath
    if not p.exists():
        raise SiteError(f"{relpath}: file absent under {root}")
    src = p.read_text()
    hits = len(re.findall(pattern, src, flags))
    if hits != expect:
        raise SiteError(f"{relpath}: pattern matched {hits}x, expected {expect}\n"
                        f"    pattern: {pattern}")
    out = re.sub(pattern, replacement, src, flags=flags)
    if out == src:
        raise SiteError(f"{relpath}: substitution produced no change")
    p.write_text(out)
    return hits


# --------------------------------------------------------------------------
# Operators. Each returns a list of (relpath, sites_touched).
# --------------------------------------------------------------------------
def m1_effect_undercount(root):
    """Drop a second append for the same tag: the oracle undercounts."""
    touched = []
    # Keyed on THREAD ONLY, deliberately. An earlier revision guarded on
    # (thread, task) and killed nothing: probe 159's ledger records the
    # gated effect under a per-value task key (gate:True / gate:False),
    # and the race arm the cross-process cell reads supplies DIFFERENT
    # values to each racer, so the two fires land under different task
    # keys and a (thread, task) guard never triggers. That operator was a
    # "dedup identical effects" mutation, which is a strictly weaker
    # thing than an undercount and cannot suppress this duplicate. The
    # thread-only guard drops the second fire whatever value it carried.
    for rel, pat, rep, n in [
        ("probes/159_p14_multiproc_saver.py",
         r'(\s+)c\.execute\("INSERT INTO effects \(thread, task\) VALUES \(\?, \?\)",',
         r'\1if not c.execute("SELECT 1 FROM effects WHERE thread=?",'
         r' (thread,)).fetchone():\1    c.execute('
         r'"INSERT INTO effects (thread, task) VALUES (?, ?)",', 1),
        ("probes/126_p6_langgraph_sqlite_durable.py",
         r'(\s+)con\.execute\("INSERT INTO effects \(name\) VALUES \(\?\)", \(name,\)\)',
         r'\1if not con.execute("SELECT 1 FROM effects WHERE name=?",'
         r' (name,)).fetchone():\1    con.execute('
         r'"INSERT INTO effects (name) VALUES (?)", (name,))', 1),
    ]:
        touched.append((rel, edit(root, rel, pat, rep, n)))
    return touched


def m2_effect_overcount(root):
    """Every append writes twice: the oracle overcounts."""
    touched = []
    for rel, pat, rep, n in [
        ("probes/159_p14_multiproc_saver.py",
         r'(\s+)(c\.execute\("INSERT INTO effects \(thread, task\) VALUES \(\?, \?\)",\n?\s*\(thread, task\)\))',
         r'\1\2\1\2', 1),
        ("probes/126_p6_langgraph_sqlite_durable.py",
         r'(\s+)(con\.execute\("INSERT INTO effects \(name\) VALUES \(\?\)", \(name,\)\))',
         r'\1\2\1\2', 1),
    ]:
        touched.append((rel, edit(root, rel, pat, rep, n)))
    return touched


def m3_crash_noop(root):
    """Kills become no-ops. Any crash-path verdict that survives this
    never depended on the crash."""
    touched = []
    for rel in ["probes/159_p14_multiproc_saver.py",
                "probes/126_p6_langgraph_sqlite_durable.py"]:
        p = Path(root) / rel
        if not p.exists():
            continue
        src = p.read_text()
        if "os.kill" not in src and "SIGKILL" not in src:
            continue
        out = re.sub(r'os\.kill\(([^)]*)\)', r'None  # M3: os.kill(\1)', src)
        if out == src:
            raise SiteError(f"{rel}: M3 found no os.kill call to disable")
        p.write_text(out)
        touched.append((rel, out.count("# M3:")))
    if not touched:
        raise SiteError("M3: no probe in this tree performs a kill; "
                        "the operator has no site and cannot be evaluated")
    return touched


def m4_fork_value_ignore(root):
    """FD compares outcome to outcome rather than outcome to the value
    supplied. #6663 should flip to conformant."""
    rel = "probes/125_p4_shim_fd_langgraph.py"
    # The FD predicate is: served(True)==1 AND served(False)==0. Mutating
    # the second conjunct to compare the FALSE branch against the TRUE
    # branch's expected value makes the oracle self-consistent instead of
    # value-checking -- exactly the blind spot an FD oracle could have.
    return [(rel, edit(root, rel,
                       r'r_false\.get\("value"\) == 0',
                       r'r_false.get("value") == r_false.get("value")  # M4',
                       1))]


def m5_label_rule_invert(root):
    """NOT MECHANIZABLE, and reported as such rather than faked.

    Table 5 note b's rule -- absent a citable statement, measured
    re-execution of completed effect-bearing work is U, not X -- is
    applied when a HUMAN reads a verdict into the matrix. It is nowhere
    in the measurement path: no probe stores a U or an X, and matrix.toml
    carries no such column. There is therefore nothing to mutate.

    An earlier revision faked a site by renaming the CrewAI duplication
    field. That does not invert a label rule; it deletes the receipt, and
    the cell then reads back as absent -- indistinguishable, to any diff,
    from a mutant that changed the verdict. The hack would have produced
    a green M5 that tested nothing.

    The claim note b makes is that under the strictest alternative rule
    every U becomes X and no pairwise separation of Sec. 6's observation
    (i) changes. Discharging it requires RECOMPUTING THE MATRIX under the
    alternative labeling and re-deriving the separations -- a table, not
    a mutant. Sec. 6 should supply that table; this operator cannot.
    """
    raise SiteError(
        "M5: the U/X label rule is not in the measurement path (no probe "
        "records it, matrix.toml has no such column), so it has no "
        "mechanical site. Table 5 note b's sensitivity claim must be "
        "discharged by recomputing the matrix under the alternative rule, "
        "not by mutation. Reported as a scope limit, not a passing mutant.")


def m6_state_only_oracle(root):
    """Read framework state instead of the external ledger. Cells the
    paper calls invisible-in-state must die here; survivors mean the
    external oracle is not load-bearing for them."""
    rel = "probes/159_p14_multiproc_saver.py"
    return [(rel, edit(root, rel,
                       r'"gate_fires_total": sum\(gate_rows\.values\(\)\)',
                       r'"gate_fires_total": 1', 1))]


def m7_barrier_removed(root):
    """Barrier -> fixed sleep. Predicts no verdict flips, degraded
    stability: the timing-free claim, mechanically."""
    rel = "probes/159_p14_multiproc_saver.py"
    p = Path(root) / rel
    src = (Path(root) / rel).read_text()
    out = re.sub(r'while not (?:os\.path\.exists|Path)\([^)]*\)[^\n]*:\n\s+time\.sleep\([^)]*\)',
                 'time.sleep(0.35)  # M7: barrier removed', src)
    if out == src:
        raise SiteError(f"{rel}: M7 found no barrier spin loop")
    p.write_text(out)
    return [(rel, 1)]


def m8_pin_drift(root):
    """Relax a pin. reproduce.sh's lockfile audit must REFUSE."""
    for rel in ["envs/langgraph/pyproject.toml", "pyproject.toml"]:
        p = Path(root) / rel
        if not p.exists():
            continue
        src = p.read_text()
        out = re.sub(r'langgraph\s*==\s*[\d.]+', 'langgraph', src)
        if out != src:
            p.write_text(out)
            return [(rel, 1)]
    raise SiteError("M8: no pinned langgraph spec found to relax")


OPERATORS = {
    # A prediction may only name cells the operator can REACH -- cells
    # whose backing probe it actually mutates. An earlier revision
    # predicted kills in cells backed by probes these operators never
    # touch (118, 133, 160, 115, 158c), which is unsatisfiable by
    # construction and would have been misread as the harness failing to
    # detect the mutation. Probe 172 now refuses to score an incoherent
    # prediction. M1-M3 and M6-M7 mutate only 159/126, so their sole
    # reachable cell is LG.CO.concurrent.
    "M1": ("effect_undercount", m1_effect_undercount,
           ["LG.CO.concurrent"]),
    # M2 reaches only LG.CO.concurrent, and that cell CANNOT be flipped
    # by overcounting: its verdict is the boolean
    # co_concurrent_double_resume_inert_all_reps, already False because
    # duplicates are seen. Doubling every append produces more
    # duplicates, which keeps it False. An overcount operator can only
    # kill a cell that is currently CONFORMANT, and M2 reaches none.
    # Predicting a kill here was a reasoning error, not a harness defect;
    # the empty prediction is the correct one.
    "M2": ("effect_overcount", m2_effect_overcount, []),
    "M3": ("crash_noop", m3_crash_noop,
           ["LG.CO.concurrent"]),
    "M4": ("fork_value_ignore", m4_fork_value_ignore, ["LG.FD.6663"]),
    "M5": ("label_rule_invert", m5_label_rule_invert, []),
    "M6": ("state_only_oracle", m6_state_only_oracle,
           ["LG.CO.concurrent"]),
    "M7": ("barrier_removed", m7_barrier_removed, []),
    # M8 reaches no conformance cell: its subject is reproduce.sh's
    # lockfile audit, which must REFUSE the run. Probe 172 cannot test
    # that and reports M8 as out of its scope rather than as a survivor.
    "M8": ("pin_drift", m8_pin_drift, ["__REFUSAL__"]),
}


def apply_one(mid, src_root, into):
    name, fn, predicted = OPERATORS[mid]
    dest = Path(into) / mid
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src_root, dest,
                    ignore=shutil.ignore_patterns(
                        ".git", ".venv*", "target", "__pycache__",
                        "results", "tools", "*.jar"))
    try:
        touched = fn(str(dest))
        return {"operator": name, "applied": True, "tree": str(dest),
                "sites": [{"file": r, "count": n} for r, n in touched],
                "predicted_kills": predicted, "error": None}
    except SiteError as e:
        return {"operator": name, "applied": False, "tree": str(dest),
                "sites": [], "predicted_kills": predicted, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--into", default="/tmp/p170_mutants")
    ap.add_argument("--apply", default=None)
    ap.add_argument("--apply-all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true",
                    help="apply every operator to a throwaway copy and "
                         "report which have live sites; changes nothing")
    ap.add_argument("--out", default="results/mutation")
    a = ap.parse_args()

    if a.list:
        for m, (n, _, pred) in OPERATORS.items():
            print(f"{m}  {n:20s} predicts: {pred or 'NO KILLS'}")
        return 0

    todo = list(OPERATORS) if (a.apply_all or a.verify) else \
        ([a.apply] if a.apply else [])
    if not todo:
        ap.error("give --list, --verify, --apply Mn, or --apply-all")

    into = "/tmp/p170_verify" if a.verify else a.into
    Path(into).mkdir(parents=True, exist_ok=True)
    res = {m: apply_one(m, a.root, into) for m in todo}

    dead = [m for m, r in res.items() if not r["applied"]]
    for m, r in res.items():
        mark = "OK " if r["applied"] else "DEAD"
        print(f"  {m} {mark} {r['operator']:20s} "
              f"{r['sites'] if r['applied'] else r['error'].splitlines()[0]}")

    report = {"probe": "170_p16_mutation_operators",
              "host": os.uname().nodename,
              "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "mode": "verify" if a.verify else "apply",
              "operators": res,
              "operators_without_live_sites": dead}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    Path(a.out, "170_operators.json").write_text(json.dumps(report, indent=1))
    print(f"\nwrote {a.out}/170_operators.json")

    if dead:
        print(f"\n{len(dead)} operator(s) have no live site: {dead}")
        print("An operator that cannot be applied is NOT a passing mutant. "
              "Either the site moved (re-anchor it) or the harness no longer "
              "contains the machinery the operator targets -- which is itself "
              "reportable. Do not record these as survivors.")
        return 1
    if a.verify:
        shutil.rmtree(into, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
