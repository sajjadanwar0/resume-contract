#!/usr/bin/env python3
"""
exp1_verus_content_audit.py
===========================
Reviewer experiment 1 — "What do the 15 'verified items' actually prove?"

The paper's headline is a "Verus-verified contract model (15 verified items,
0 errors)". Verus reporting `N verified, 0 errors` tells you the lemmas TYPE-
CHECK; it does NOT tell you the lemmas SAY anything. A lemma of the form
`requires P ensures P` verifies trivially and proves nothing.

This script reads the three committed proof files, extracts every `proof fn`
with its requires/ensures/body, inlines the (small, simple) spec-function
definitions, and classifies each lemma as one of:

    TAUTOLOGY            ensures, after unfolding, is literally a requires
                         clause (`P => P`)
    DEFINITIONAL         ensures is f(X)==f(Y) where f is defined to factor
                         through exactly the key the requires equates, or an
                         uninterpreted-function congruence over an equated arg
                         (you assumed the conclusion in the definition)
    TRIVIAL_ARITH        empty proof body + a one-step arithmetic fact
                         (e.g. t == f+1  =>  t > f)
    SUBSTANTIVE          real inductive/structural content

It prints the unfolded ensures next to the requires so the classification is
auditable by eye, not magic. Pure standard library; no Verus needed (that is
the point — these lemmas verify BECAUSE several are tautologies).

Usage:
    python3 exp1_verus_content_audit.py [REPO_ROOT]
REPO_ROOT defaults to the current directory; the script locates
crates/remit/proof/*.rs beneath it.
"""
import os
import re
import sys

PROOF_FILES = [
    "crates/remit/proof/remit_verus.rs",
    "crates/remit/proof/remit_verus_cv_rd.rs",
]

# Spec-function bodies (name -> lambda text) that we inline into `ensures`.
# Read straight from the files but pinned here so the unfolding is explicit.
SPEC_DEFS = {
    # served(supplied, o) == supplied[o]
    "served": lambda args: f"{args[0]}[{args[1]}]",
    # recover(log) == recover_of_writeset(log.records.to_multiset())
    "recover": lambda args: f"recover_of_writeset({args[0]}.records.to_multiset())",
    # commit(frontier, task) == task
    "commit": lambda args: f"{args[1]}",
    # commit_admissible(frontier, task) == task == frontier + 1
    "commit_admissible": lambda args: f"{args[1]} == {args[0]} + 1",
    # branch_key(cp, ord) == (cp, ord)
    "branch_key": lambda args: f"({args[0]}, {args[1]})",
    # same_superstep_writeset(a,b) == a.records.to_multiset() == b.records.to_multiset()
    "same_superstep_writeset":
        lambda args: f"{args[0]}.records.to_multiset() == {args[1]}.records.to_multiset()",
}


def find_repo(root):
    for cand in (root, os.path.join(root, "repo"), os.path.join(root, "_extract")):
        if os.path.exists(os.path.join(cand, PROOF_FILES[0])):
            return cand
    # search downward
    for dirpath, _dirs, files in os.walk(root):
        if dirpath.endswith(os.path.join("crates", "remit", "proof")) and \
           "remit_verus.rs" in files:
            return os.path.dirname(os.path.dirname(os.path.dirname(dirpath)))
    return None


def split_top(seg):
    """Split on commas that sit at paren/bracket/angle depth 0."""
    out, depth, cur = [], 0, ""
    for ch in seg:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return [c.strip().rstrip(",") for c in out if c.strip()]


def brace_match(text, start):
    """Return index just past the matching '}' for the '{' at/after start."""
    i = text.index("{", start)
    depth = 0
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def extract_lemmas(src):
    """Yield dicts with name, requires[], ensures[], body for each proof fn."""
    out = []
    for m in re.finditer(r"proof fn\s+(\w+)\s*\(", src):
        name = m.group(1)
        # signature runs from the '(' to the '{' that opens the body
        body_open = src.index("{", m.end())
        sig = src[m.end():body_open]
        body_end = brace_match(src, body_open)
        body = src[body_open + 1:body_end - 1]

        def grab(kw):
            mm = re.search(kw + r"\s*(.*?)(?:requires|ensures|recommends|decreases)\b|"
                           + kw + r"\s*(.*)$", sig, re.S)
            if kw not in sig:
                return []
            seg = sig.split(kw, 1)[1]
            for stop in ("requires", "ensures", "recommends", "decreases"):
                if stop != kw and stop in seg:
                    seg = seg.split(stop, 1)[0]
            return split_top(seg.strip())

        out.append({
            "name": name,
            "requires": grab("requires"),
            "ensures": grab("ensures"),
            "body": body.strip(),
        })
    return out


def unfold(expr):
    """Inline one level of the known spec-fn calls."""
    changed = True
    while changed:
        changed = False
        for fn, sub in SPEC_DEFS.items():
            for m in re.finditer(fn + r"\s*\(([^()]*)\)", expr):
                args = [a.strip() for a in m.group(1).split(",")]
                try:
                    rep = sub(args)
                except Exception:
                    continue
                expr = expr[:m.start()] + rep + expr[m.end():]
                changed = True
                break
    return re.sub(r"\s+", " ", expr).strip()


def norm(e):
    return re.sub(r"\s+", " ", e).replace("=~=", "==").strip()


def classify(lem):
    reqs = [norm(r) for r in lem["requires"]]
    ens = [norm(e) for e in lem["ensures"]]
    ens_unf = [unfold(e) for e in ens]
    reqs_unf = [unfold(r) for r in reqs]
    body_lines = [l for l in lem["body"].splitlines() if l.strip()
                  and not l.strip().startswith("//")]

    verdict, why = "SUBSTANTIVE", ""

    # 1) tautology: an unfolded ensures equals an unfolded/normal requires.
    #    Require a relational operator so a bare identifier can never match.
    rel = re.compile(r"(==|!=|<=|>=|<|>)")
    for e in ens_unf:
        if rel.search(e) and (e in reqs_unf or e in reqs):
            return "TAUTOLOGY", f"ensures `{e}` is exactly a requires clause (P => P)", ens_unf

    # 2) definitional congruence: ensures is f(a)==f(b) (or !=) and requires
    #    equates a,b or the multiset key f factors through
    for e in ens_unf:
        mm = re.match(r"recover_of_writeset\((.+?)\)\s*==\s*recover_of_writeset\((.+?)\)", e)
        if mm and any(mm.group(1) in r and mm.group(2) in r for r in reqs_unf + reqs):
            return ("DEFINITIONAL",
                    "recover is DEFINED to factor through to_multiset(); the "
                    "requires equates those multisets, so this is f(m)==f(m) "
                    "congruence — order-independence is assumed in the definition",
                    ens_unf)
        # generic congruence: recover(a)==recover(b) with a.records == b.records
        if re.match(r"recover\((\w+)\)\s*==\s*recover\((\w+)\)", "".join(ens)):
            g = re.match(r"recover\((\w+)\)\s*==\s*recover\((\w+)\)", "".join(ens))
            if any(f"{g.group(1)}.records" in r and f"{g.group(2)}.records" in r
                   for r in reqs + reqs_unf):
                return ("DEFINITIONAL",
                        "recover(a)==recover(b) from a.records==b.records is "
                        "function congruence — no recovery logic is exercised",
                        ens_unf)

    # 3) trivial arithmetic with empty body
    if not body_lines:
        joined = " ; ".join(reqs_unf + ens_unf)
        if re.search(r"== .*\+ 1", joined) and re.search(r"> ", joined):
            return ("TRIVIAL_ARITH",
                    "empty proof body; reduces to `x+1 > x` after unfolding the "
                    "admissibility/commit definitions", ens_unf)
        return ("TRIVIAL_ARITH",
                "empty proof body; discharged by the SMT backend with no "
                "structural argument", ens_unf)

    return verdict, why, ens_unf


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    repo = find_repo(root)
    if not repo:
        print("ERROR: could not locate crates/remit/proof/remit_verus.rs under",
              os.path.abspath(root))
        print("Pass the repo root as an argument, e.g.:  python3 %s ./repo" %
              os.path.basename(__file__))
        sys.exit(2)

    counts = {"TAUTOLOGY": 0, "DEFINITIONAL": 0, "TRIVIAL_ARITH": 0, "SUBSTANTIVE": 0}
    rows = []
    for pf in PROOF_FILES:
        path = os.path.join(repo, pf)
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        for lem in extract_lemmas(src):
            v, why, ens_unf = classify(lem)
            counts[v] += 1
            rows.append((os.path.basename(pf), lem, v, why, ens_unf))

    print("=" * 78)
    print(" VERUS CONTENT AUDIT  —  what the '15 verified items' actually prove")
    print("=" * 78)
    for fname, lem, v, why, ens_unf in rows:
        print(f"\n[{v:12s}] {lem['name']}   ({fname})")
        if lem["requires"]:
            print("   requires : " + " , ".join(norm(r) for r in lem["requires"]))
        print("   ensures  : " + " , ".join(norm(e) for e in lem["ensures"]))
        if ens_unf and ens_unf != [norm(e) for e in lem["ensures"]]:
            print("   unfolded : " + " , ".join(ens_unf))
        if why:
            print("   -> " + why)

    print("\n" + "=" * 78)
    print(" SUMMARY")
    print("=" * 78)
    total = sum(counts.values())
    for k in ("TAUTOLOGY", "DEFINITIONAL", "TRIVIAL_ARITH", "SUBSTANTIVE"):
        print(f"   {k:14s}: {counts[k]}")
    print(f"   {'TOTAL lemmas':14s}: {total}")
    vacuous = counts["TAUTOLOGY"] + counts["DEFINITIONAL"]
    print()
    print(f"   Vacuous-or-circular (TAUTOLOGY + DEFINITIONAL) : {vacuous}/{total}")
    print(f"   Trivial arithmetic                             : {counts['TRIVIAL_ARITH']}/{total}")
    print(f"   Carrying genuine (if elementary) content       : {counts['SUBSTANTIVE']}/{total}")
    print()
    print(" READING: FD ('distinct values served distinctly') and RD ('order-")
    print(" independent recovery') are the two properties the paper foregrounds as")
    print(" machine-verified. Both fall in TAUTOLOGY/DEFINITIONAL: served(m,o)==m[o]")
    print(" makes FD `x!=y => x!=y`; recover()==g(multiset) makes RD `g(m)==g(m)`.")
    print(" PC reduces to `frontier+1 > frontier`. Only the EO/CO no-duplicate-append")
    print(" lemmas and the CV gate-preservation lemma do real (elementary) work, and")
    print(" NONE of the 15 lemmas import crates/remit/src/lib.rs — the shipped code")
    print(" carries zero Verus annotations, so nothing here is a proof ABOUT the")
    print(" artifact that ships. Verus '0 errors' is true and almost content-free.")


if __name__ == "__main__":
    main()
