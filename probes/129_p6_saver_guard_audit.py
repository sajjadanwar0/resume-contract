#!/usr/bin/env python3
"""
129_p6_saver_guard_audit.py
P6 - Source audit: locate the resume-write dedup guard in each installed
checkpointer implementation. The paper's mechanism account for #6663 quotes
InMemorySaver.put_writes's `if inner_key in outer_writes_: continue`. This
probe answers whether the durable savers share the semantics: it prints the
put_writes implementation region of each saver with file path and line
numbers, and greps for the dedup idiom (Python-guard or SQL conflict-clause
form). Static evidence; probes 126/127 provide the behavioral confirmation.
"""
import inspect
import json
import re
from importlib.metadata import version

RESULTS = {}


def audit(modname, clsname, key):
    try:
        mod = __import__(modname, fromlist=[clsname])
        cls = getattr(mod, clsname)
        fn = cls.put_writes
        src = inspect.getsource(fn)
        srcfile = inspect.getsourcefile(fn)
        firstline = inspect.getsourcelines(fn)[1]
        idioms = {
            "python_guard_continue": bool(
                re.search(r"if\s+inner_key\s+in\s+outer_writes_?\s*:\s*\n?\s*continue", src)
            ),
            "sql_insert_or_ignore": "INSERT OR IGNORE" in src,
            "sql_on_conflict_do_nothing": "DO NOTHING" in src,
            "conditional_on_writes_idx_map": "WRITES_IDX_MAP" in src,
        }
        RESULTS[key] = {
            "module": modname,
            "class": clsname,
            "source_file": srcfile,
            "put_writes_first_line": firstline,
            "dedup_idioms_found": idioms,
            "shares_dedup_semantics": any(
                [idioms["python_guard_continue"], idioms["sql_insert_or_ignore"],
                 idioms["sql_on_conflict_do_nothing"]]
            ),
            "put_writes_source": src,
        }
    except Exception as e:
        RESULTS[key] = {"error": f"{type(e).__name__}: {e}"}


def main():
    for pkg in ("langgraph-checkpoint", "langgraph-checkpoint-sqlite",
                "langgraph-checkpoint-postgres"):
        try:
            RESULTS[f"version_{pkg}"] = version(pkg)
        except Exception:
            RESULTS[f"version_{pkg}"] = "not installed"
    audit("langgraph.checkpoint.memory", "InMemorySaver", "InMemorySaver")
    audit("langgraph.checkpoint.sqlite", "SqliteSaver", "SqliteSaver")
    audit("langgraph.checkpoint.postgres", "PostgresSaver", "PostgresSaver")
    print(json.dumps(RESULTS, indent=2, default=str))


if __name__ == "__main__":
    main()
