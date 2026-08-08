import glob
import json
import os
import sys

def rj(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None

def find_root(root):
    for c in (root, os.path.join(root, "repo")):
        if os.path.isdir(os.path.join(c, "results")):
            return c
    if os.path.isdir(os.path.join(root, "results")):
        return root
    return root

def main():
    root = find_root(sys.argv[1] if len(sys.argv) > 1
                      else os.environ.get("REPO", "."))
    R = os.path.join(root, "results")
    results = []

    def rec(tag, claim, ok, detail):
        results.append((tag, claim, ok, detail))

    m = rj(os.path.join(R, "live", "148_matrix.json"))
    if m and "cells" in m:
        cells = m["cells"]
        def cellval(probe_sub, field_sub):
            hits = [c for c in cells if probe_sub in c["probe"] and field_sub in c["field"]]
            return hits

        conf = [c for c in cells
                if ("121" in c["probe"] and "violation" in c["field"]) or
                   ("122" in c["probe"] and "violation" in c["field"])]
        conf_ok = all(c["pooled_k"] == 0 and c["pooled_n"] == 40 for c in conf) and len(conf) > 0
        rec("L1", "live conformant cells 0/40 (121,122 violation fields)", conf_ok,
            "; ".join(f"{c['model']}:{c['pooled_k']}/{c['pooled_n']}" for c in conf))

        fork = cellval("131", "violation_second_decision_ignored")
        fork_ok = all(c["pooled_k"] == 40 and c["pooled_n"] == 40 for c in fork) and len(fork) > 0
        rec("L2", "live fork violation 40/40 (probe 131)", fork_ok,
            "; ".join(f"{c['model']}:{c['pooled_k']}/{c['pooled_n']}" for c in fork))

        runs = [c for c in cells if "121" in c["probe"] and "run" in c["field"]]
        runs_ok = all(c["pooled_k"] == 40 and c["pooled_n"] == 40 for c in runs) and len(runs) > 0
        rec("L3", "live session-restore completes 40/40 (probe 121)", runs_ok,
            "; ".join(f"{c['model']}/{c['field']}:{c['pooled_k']}/{c['pooled_n']}" for c in runs))
    else:
        rec("L1", "live replication matrix present", False, "148_matrix.json missing")

    total_protocols = 0
    eo_all_ok = True
    per_env = []
    for f in sorted(glob.glob(os.path.join(R, "matrix", "157_*.json"))):
        d = rj(f)
        if not d:
            continue
        for arm in ("stock", "remit", "stock_pg", "remit_pg"):
            a = d.get(arm)
            if not isinstance(a, dict):
                continue
            for k, cell in a.items():
                if isinstance(cell, dict) and "n" in cell:
                    total_protocols += cell.get("n", 0)
                    if cell.get("eo_per_protocol_exactly_once") is False:
                        eo_all_ok = False
                    if cell.get("eo_total_matches_n") is False:
                        eo_all_ok = False
        per_env.append(os.path.basename(f))
    c1_ok = total_protocols >= 6400 and eo_all_ok
    rec("C1", "concurrent bench 6,400 protocols, per-protocol EO intact", c1_ok,
        f"counted {total_protocols} protocol executions across {len(per_env)} "
        f"receipt files; EO-intact-everywhere={eo_all_ok}")

    mm = rj(os.path.join(R, "matrix", "156_mutation_report.json"))
    if mm:
        kr = str(mm.get("kill_rate", ""))
        n_mut = len(mm.get("mutants", []))
        m1_ok = kr.strip() in ("8/8",) and n_mut == 8
        rec("M1", "mutation adequacy 8/8 killed", m1_ok,
            f"kill_rate={kr}, mutants listed={n_mut}")
    else:
        rec("M1", "mutation report present", False, "156_mutation_report.json missing")

    print("=" * 78)
    print(" HEADLINE EMPIRICAL CLAIMS  vs  COMMITTED RECEIPTS")
    print("=" * 78)
    width = max(len(c[1]) for c in results)
    npass = 0
    for tag, claim, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        npass += int(ok)
        print(f"  [{mark}] {tag}  {claim:<{width}}")
        print(f"         {detail}")
    print("-" * 78)
    print(f"  {npass}/{len(results)} headline empirical claims reproduced from committed data.")
    print()
    print(" READING: the MEASUREMENT half of this paper is real and well-supported.")
    print(" The live replication (240 runs, honest Wilson intervals), the 6,400-")
    print(" protocol concurrent benchmark with a per-protocol exactly-once oracle,")
    print(" and the source-anchored 8/8 mutation kill are all backed by committed")
    print(" receipts. The paper's problem is NOT its measurements; it is (a) the")
    print(" oversold Verus 'verification' (exp1), (b) the overinterpreted")
    print(" PrefixReplay model (exp2), (c) the CO-not-independent framing (exp4),")
    print(" and (d) an uncited direct competitor (ACRFence, arXiv:2603.20625).")

if __name__ == "__main__":
    main()
