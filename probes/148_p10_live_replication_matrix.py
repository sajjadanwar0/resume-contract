#!/usr/bin/env python3
"""
148_p10_live_replication_matrix.py
Closes the live-ecology scope stated in Sec. "Live ecological cells": the
r2 replications are same-model, same-host; this probe produces the
multi-model, multi-host matrix. It does NOT reimplement any cell -- it
drives probe 135 (which re-executes the byte-identical live probes 121,
122, 131 and aggregates only the audited counter verdicts), adds a host
manifest, writes a per-host result file, and merges files from several
hosts into one cross-model x cross-host table with per-cell and pooled
Wilson intervals.

KEY-GATED, HOST-RUN. Nothing here executes without ANTHROPIC_API_KEY /
OPENAI_API_KEY; run it on each host you want in the matrix.

One-time, before first collection (no keys needed):
    python3 probes/148_p10_live_replication_matrix.py --patch-probes
    # Makes probes 121/122/131 genuinely honor PROBE_MODEL (121 passes it
    # to Agent; 122 to ChatAnthropic; 131 to init_chat_model -- 131
    # previously RECORDED the env var while using the hardcoded model,
    # which would have mislabeled multi-model data). Idempotent; with
    # PROBE_MODEL unset every probe behaves exactly as before, so the
    # paper's N=40 receipts are unaffected. Collection refuses to run on
    # unpatched probes.

Collect (per host, provider-consistent batches -- a model list is applied
to every target probe, so keep Anthropic and OpenAI targets in separate
invocations; use a distinct P148_HOSTTAG per batch, since one file is
written per (hosttag, UTC day) and a same-day rerun overwrites it):
    P148_N=20 P148_TARGETS="122,131" \
        P148_MODELS="claude-haiku-4-5,claude-sonnet-4-6" \
        P148_HOSTTAG="$(hostname)-anthropic" \
        python3 probes/148_p10_live_replication_matrix.py
    P148_N=20 P148_TARGETS="121" \
        P148_MODELS="gpt-4o-mini,gpt-4.1-mini" \
        P148_HOSTTAG="$(hostname)-openai" \
        python3 probes/148_p10_live_replication_matrix.py
    # -> results/live/148_<hosttag>_<utcdate>.json (one per batch)

Merge (any machine, no keys needed):
    python3 probes/148_p10_live_replication_matrix.py --merge results/live/
    # -> results/live/148_matrix.json + a markdown table on stdout

Env:
    P148_N        repetitions per (probe, model) on this host (default 20)
    P148_MODELS   comma list of models (REQUIRED for collection; each is
                  exported as ANTHROPIC_MODEL / PROBE_MODEL via probe 135)
    P148_TARGETS  probe prefixes (default "121,122,131", as probe 135)
    P148_HOSTTAG  override the host label (default: hostname)

Paper wiring: pooled per-cell proportions with Wilson intervals replace the
same-model N=40 figures; per-(model, host) rows go to the artifact. The
existence/stability framing is unchanged -- this widens the replication
axes, it does not create a prevalence claim.
"""
import json
import math
import os
import platform
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
OUTDIR = REPO / "results" / "live"


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def host_manifest():
    vers = {}
    for pkg in ("langgraph", "langgraph-checkpoint", "anthropic", "openai",
                "openai-agents", "remit-contract"):
        try:
            from importlib.metadata import version
            vers[pkg] = version(pkg)
        except Exception:
            pass
    return {
        "host": os.environ.get("P148_HOSTTAG", socket.gethostname()),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": vers,
    }


PROBE_PATCHES = [
    ("121_p5_live_openai_agents_session.py", [
        ('RESULTS = {"openai_agents_version": version("openai-agents")}',
         'RESULTS = {"openai_agents_version": version("openai-agents"),\n'
         '           "model": os.environ.get("PROBE_MODEL", "sdk-default")}'),
        ('        tools=[charge],\n        model_settings=ModelSettings(temperature=0),',
         '        tools=[charge],\n        model=os.environ.get("PROBE_MODEL"),\n'
         '        model_settings=ModelSettings(temperature=0),'),
    ]),
    ("122_p5_live_langgraph_anthropic_interrupt.py", [
        ('RESULTS = {"langgraph_version": version("langgraph"),\n'
         '           "model": "claude-haiku-4-5"}',
         'RESULTS = {"langgraph_version": version("langgraph"),\n'
         '           "model": os.environ.get("PROBE_MODEL", "claude-haiku-4-5")}'),
        ('    model = ChatAnthropic(model="claude-haiku-4-5", temperature=0, max_tokens=512)',
         '    model = ChatAnthropic(model=os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),\n'
         '                          temperature=0, max_tokens=512)'),
    ]),
    ("131_p5_live_fd_violation.py", [
        ('model = init_chat_model("anthropic:claude-haiku-4-5", temperature=0)',
         'model = init_chat_model(\n'
         '    "anthropic:" + os.environ.get("PROBE_MODEL", "claude-haiku-4-5"),\n'
         '    temperature=0)'),
    ]),
]


def patch_probes():
    changed = 0
    for fname, pairs in PROBE_PATCHES:
        path = HERE / fname
        text = path.read_text()
        for old_s, new_s in pairs:
            if new_s in text:
                continue
            if old_s not in text:
                sys.exit(f"ERROR: expected text not found in {fname}; probe drifted "
                         f"from the audited version -- refusing to guess. Missing:\n{old_s}")
            text = text.replace(old_s, new_s, 1)
            changed += 1
        path.write_text(text)
    print(f"probes patched ({changed} edits applied; 0 means already patched). "
          f"With PROBE_MODEL unset, behavior is unchanged.")


def ensure_patched():
    stale = []
    for fname, pairs in PROBE_PATCHES:
        text = (HERE / fname).read_text()
        for old_s, _ in pairs:
            if old_s in text:
                stale.append(fname)
                break
    if stale:
        sys.exit("Probes not model-patched (would silently mislabel multi-model "
                 f"runs): {', '.join(stale)}.\n"
                 "Run once first:  python3 probes/148_p10_live_replication_matrix.py --patch-probes")


def collect():
    models = [m for m in os.environ.get("P148_MODELS", "").split(",") if m]
    if not models:
        sys.exit("P148_MODELS is required for collection (comma list of model ids). "
                 "Merging existing files needs no keys: --merge <dir>.")
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")):
        sys.exit("No API keys in the environment; this probe is key-gated by design.")
    ensure_patched()

    env = dict(os.environ)
    env["PROBE135_N"] = os.environ.get("P148_N", "20")
    env["PROBE135_MODELS"] = ",".join(models)
    env["PROBE135_TARGETS"] = os.environ.get("P148_TARGETS", "121,122,131")

    p135 = sorted(HERE.glob("135_*.py"))[0]
    n_runs = len(models) * len(env["PROBE135_TARGETS"].split(",")) * int(env["PROBE135_N"])
    print(f"[148] launching probe 135: {len(models)} model(s) x "
          f"targets {env['PROBE135_TARGETS']} x N={env['PROBE135_N']} "
          f"= {n_runs} live runs; per-run progress follows on stderr:",
          flush=True)
    t0 = time.time()
    # stderr is INHERITED (not captured) so probe 135's "[135] ... run i/N"
    # progress lines stream to the terminal live; only stdout (the final
    # JSON aggregate) is captured.
    proc = subprocess.Popen([sys.executable, str(p135)], env=env,
                            stdout=subprocess.PIPE, stderr=None, text=True)
    out, _ = proc.communicate()
    if proc.returncode != 0 and not out.strip():
        sys.exit("probe 135 failed (see its output above)")
    i = out.find("{")
    doc = json.loads(out[i:])
    out = {
        "probe": 148,
        "manifest": host_manifest(),
        "n_per_cell": int(env["PROBE135_N"]),
        "models": models,
        "elapsed_s": round(time.time() - t0, 1),
        "probe135": doc,
    }
    OUTDIR.mkdir(parents=True, exist_ok=True)
    tag = out["manifest"]["host"].replace("/", "_")
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = OUTDIR / f"148_{tag}_{day}.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"wrote {path}")
    print(json.dumps({"host": tag, "models": models,
                      "n_per_cell": out["n_per_cell"]}, indent=2))


def iter_verdicts(p135doc):
    """Yield (probe, model, field, k, n) from a probe-135 aggregate:
    cells["<script>::<model>"] -> {"runs", "errors", "verdict_fields":
    {field: {"true", "of", "proportion", "wilson95"}}}."""
    for cell_key, cell in (p135doc.get("cells") or {}).items():
        if not isinstance(cell, dict) or "verdict_fields" not in cell:
            continue
        probe, _, model = cell_key.partition("::")
        for field, stat in cell["verdict_fields"].items():
            yield probe, model or "default", field, stat["true"], stat["of"]


def merge(dirpath):
    files = sorted(Path(dirpath).glob("148_*_*.json"))
    files = [f for f in files if f.name != "148_matrix.json"]
    if not files:
        sys.exit(f"no 148_<host>_<date>.json files under {dirpath}")
    rows = {}
    hosts = []
    for f in files:
        doc = json.loads(f.read_text())
        host = doc["manifest"]["host"]
        hosts.append({"host": host, "file": f.name, "manifest": doc["manifest"],
                      "n_per_cell": doc.get("n_per_cell")})
        for probe, model, field, k, n in iter_verdicts(doc.get("probe135", {})):
            key = (probe, model, field)
            rows.setdefault(key, {"per_host": {}, "k": 0, "n": 0})
            rows[key]["per_host"][host] = {"k": k, "n": n, "wilson": wilson(k, n)}
            rows[key]["k"] += k
            rows[key]["n"] += n or 0
    table = []
    for (probe, model, field), agg in sorted(rows.items()):
        table.append({
            "probe": probe, "model": model, "field": field,
            "pooled_k": agg["k"], "pooled_n": agg["n"],
            "pooled_wilson": wilson(agg["k"], agg["n"]),
            "per_host": agg["per_host"],
        })
    out = {"probe": 148, "mode": "merge", "hosts": hosts, "cells": table}
    outpath = Path(dirpath) / "148_matrix.json"
    outpath.write_text(json.dumps(out, indent=2))
    print(f"wrote {outpath}\n")
    print("| probe | model | field | pooled | Wilson 95% | hosts |")
    print("|---|---|---|---|---|---|")
    for t in table:
        print(f"| {t['probe']} | {t['model']} | {t['field']} | "
              f"{t['pooled_k']}/{t['pooled_n']} | {t['pooled_wilson']} | "
              f"{len(t['per_host'])} |")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--merge":
        merge(sys.argv[2] if len(sys.argv) > 2 else str(OUTDIR))
    elif len(sys.argv) > 1 and sys.argv[1] == "--patch-probes":
        patch_probes()
    else:
        collect()