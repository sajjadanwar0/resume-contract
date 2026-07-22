#!/usr/bin/env python3
"""
156_p11_mutation_adequacy.py  (E-S1)
Source-level mutation adequacy of the conformance harness.

Answers the reviewer charge that the harness is tuned to known bugs / that
the fault space should be derived from the program: apply first-order
mutants to the resume-serving region of the ACTUAL framework source
(vendored copy; system install untouched) and check that the UNCHANGED
probe suite detects each one via divergent stable fields.

A mutant is KILLED if any non-version JSON field of any probe diverges from
the same-probe baseline run in the same vendored (unmutated) environment.
Survivors are the interesting output: each is either argued equivalent in
the manifest or is a harness hole to close (and say so in the paper).

Usage:
  python3 156_p11_mutation_adequacy.py --verify-anchors      # anchors exist & unique
  python3 156_p11_mutation_adequacy.py                       # full study
  python3 156_p11_mutation_adequacy.py --mutants M1,M6       # subset
Probes run per mutant (edit PROBES for your layout): 113, 118, 127.
Add 126/134/138/141 for the full study (SQLite probes need the sqlite pkg
in the vendor dir: pip install --target vendor langgraph-checkpoint-sqlite==3.1.0).

Demonstrated kill (container, 2026-07-21, pins 1.2.9/4.1.1): M1 flips
  T2_fork_on_resume_values_#6663.violation_second_resume_ignored True->False
i.e. a one-line change at the paper's located binding site is caught by the
suite -- the verdict is causally coupled to the mechanism account.
"""
import argparse, json, os, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(HERE, "vendor")
PROBE_DIR = os.environ.get("PROBE_DIR", os.path.join(HERE, "..", "probes"))
PROBES = ["113_p1_langgraph_regressions.py",
          "118_p3_langgraph_rd_interleavings.py",
          "127_p6_langgraph_resume_map.py"]
PINS = ["langgraph==1.2.9", "langgraph-checkpoint==4.1.1",
        "langgraph-checkpoint-sqlite==3.1.0"]

def ensure_vendor():
    if not os.path.isdir(os.path.join(VENDOR, "langgraph")):
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--target", VENDOR, *PINS], check=True)

def load_mutants(path):
    return [(m["id"], os.path.join(VENDOR, m["file"]), m["desc"], m["old"], m["new"])
            for m in json.load(open(path))]

def run_probe(script):
    env = dict(os.environ, PYTHONPATH=VENDOR)
    p = subprocess.run([sys.executable, os.path.join(PROBE_DIR, script)],
                       capture_output=True, text=True, env=env, timeout=600)
    try:
        return json.loads(p.stdout[p.stdout.index("{"):])
    except Exception:
        return {"_probe_crashed": True, "_rc": p.returncode,
                "_tail": (p.stdout + p.stderr)[-400:]}

def flat(d, p=""):
    out = {}
    for k, v in d.items():
        kk = f"{p}.{k}" if p else k
        if isinstance(v, dict): out.update(flat(v, kk))
        else: out[kk] = repr(v)
    return out

def diff(a, b):
    fa, fb = flat(a), flat(b)
    return sorted(k for k in set(fa) | set(fb)
                  if fa.get(k) != fb.get(k) and "version" not in k.lower())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify-anchors", action="store_true")
    ap.add_argument("--mutants", default="")
    args = ap.parse_args()
    ensure_vendor()
    mutants = load_mutants(os.path.join(HERE, "mutants.json"))
    if args.mutants:
        keep = set(args.mutants.split(","))
        mutants = [m for m in mutants if m[0] in keep]

    if args.verify_anchors:
        ok = True
        for mid, f, desc, old, new in mutants:
            n = open(f).read().count(old)
            print(f"{mid}: anchor x{n} in {os.path.relpath(f, VENDOR)}"
                  + ("" if n == 1 else "  <-- FIX"))
            ok &= (n == 1)
        sys.exit(0 if ok else 1)

    print("== baselines (vendored, unmutated) ==")
    base = {s: run_probe(s) for s in PROBES}
    report = {"pins": PINS, "probes": PROBES, "mutants": {}}
    for mid, f, desc, old, new in mutants:
        src = open(f).read()
        assert src.count(old) == 1, f"{mid}: anchor not unique"
        shutil.copy(f, f + ".orig")
        open(f, "w").write(src.replace(old, new))
        try:
            kills = {}
            for s in PROBES:
                d = diff(base[s], run_probe(s))
                if d: kills[s] = d
        finally:
            shutil.move(f + ".orig", f)
        report["mutants"][mid] = {"description": desc,
                                  "killed": bool(kills),
                                  "killing_probes": kills}
        print(f"{mid}: {'KILLED' if kills else 'SURVIVED'}"
              + (f" by {sorted(kills)}" if kills else "  <-- argue equivalence or close the hole"))
    n = len(report["mutants"]); k = sum(m["killed"] for m in report["mutants"].values())
    report["kill_rate"] = f"{k}/{n}"
    print(f"\nkill rate: {k}/{n}")
    json.dump(report, open(os.path.join(HERE, "156_mutation_report.json"), "w"), indent=2)

if __name__ == "__main__":
    main()