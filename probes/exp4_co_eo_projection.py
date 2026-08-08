import json
import os
import re
import sys

def find(root, rel):
    for c in (root, os.path.join(root, "_extract"), os.path.join(root, "repo")):
        p = os.path.join(c, rel)
        if os.path.exists(p):
            return p
    for dp, _d, fs in os.walk(root):
        cand = os.path.join(dp, os.path.basename(rel))
        if os.path.exists(cand) and rel.split("/")[-1] == os.path.basename(rel) \
           and rel.split("/")[-2] in dp:
            return cand
    return None

def extract_inv(tla_text, name):
    m = re.search(name + r"\s*==\s*(.+?)(?:\n[A-Za-z]\w*\s*==|\Z)", tla_text, re.S)
    if not m:
        return None
    body = m.group(1)
    body = body.split("\n\n")[0]
    return re.sub(r"\s+", " ", body).strip().rstrip("\\* EO CO ").strip()

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("REPO", ".")
    tla = find(root, "formal/tla/ResumeContract.tla")
    if not tla:
        print("ERROR: ResumeContract.tla not found under", os.path.abspath(root))
        sys.exit(2)
    text = open(tla, encoding="utf-8").read()

    eo = extract_inv(text, "EffectExactlyOnce")
    co = extract_inv(text, "ConsumeOnce")

    print("=" * 74)
    print(" CO-is-a-projection-of-EO check")
    print("=" * 74)
    print(f"  EO  (EffectExactlyOnce) : {eo}")
    print(f"  CO  (ConsumeOnce)       : {co}")
    print()
    is_proj = ("effects[IP]" in co.replace(" ", "")) and ("<=1" in co.replace(" ", "")) \
        and ("\\A" in eo and "effects[t]" in eo.replace(" ", ""))
    print("  Relationship: CO instantiates EO's \\A t at t == IP.")
    print(f"  => EO => CO is a tautology; CO is NOT independent of EO.  [{'CONFIRMED' if is_proj else 'CHECK'}]")

    mdpath = find(root, "results/tla/independence/independence_matrix.md")

    print()
    print("=" * 74)
    print(" One-way implication, from the committed per-invariant matrix (.md)")
    print("=" * 74)
    rows = {}
    if mdpath:
        for line in open(mdpath, encoding="utf-8"):
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) == 7 and cols[0].lower() not in ("fault", "---", ""):
                fault = cols[0].lower()
                props = dict(zip(["EO", "PC", "FD", "CV", "CO", "RD"], cols[1:]))
                rows[fault] = {k: ("violated" if "VIOLAT" in v.upper() else
                                   "holds" if "HOLD" in v.upper() else v)
                               for k, v in props.items()}
    if not rows:
        print("  (committed receipt results/tla/independence/independence_matrix.md")
        print("   not found; run the independence-matrix script to generate it.)")
    else:
        for fault in ("doubleconsume", "prefixreplay"):
            if fault in rows:
                r = rows[fault]
                print(f"  {fault:14s}:  EO={r['EO']:9s}  CO={r['CO']:9s}")
        print()
        dc = rows.get("doubleconsume", {})
        pr = rows.get("prefixreplay", {})
        ok1 = dc.get("CO") == "violated" and dc.get("EO") == "violated"
        ok2 = pr.get("EO") == "violated" and pr.get("CO") == "holds"
        print(f"  CO fails => EO fails (DoubleConsume) : {'CONFIRMED' if ok1 else 'no'}")
        print(f"  EO fails =/> CO fails (PrefixReplay) : {'CONFIRMED' if ok2 else 'no'}")

    print()
    print("=" * 74)
    print(" READING")
    print("=" * 74)
    print("  CO is a genuine *production-mechanism* label (stray duplicate delivery")
    print("  vs crash replay), which is worth naming -- but it is a corollary of EO,")
    print("  not a sixth independent property. Two paper-level fixes follow:")
    print("   1. Delete 'and vice versa' from Remark 1: EO=>CO makes 'satisfy EO")
    print("      while failing CO' impossible; the sentence contradicts Prop 2(iv).")
    print("   2. Stop advertising 'six properties' as if independent; the honest")
    print("      count is five logically independent obligations + one corollary +")
    print("      one protocol obligation (FI). Prop 2 already says so; the abstract")
    print("      and intro should match it.")

if __name__ == "__main__":
    main()
