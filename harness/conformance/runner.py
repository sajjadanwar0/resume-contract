"""Conformance matrix runner.

Executes each probe in matrix.toml inside its pinned uv environment
(envs/<env>/), captures the probe's JSON verdict from stdout, and audits it
against the committed baseline. Only *stable* fields are compared: violation
verdicts, effect/execution counters, counter arithmetic, and package
versions. Volatile fields (UUIDs, timestamped checkpoint filenames,
tracebacks) are excluded by design, so the audit is deterministic.

Usage:
  uv run python -m conformance.runner --plan matrix.toml \\
      --baseline results/pilot [--update]

Exit status: 0 iff every probe ran and every stable field matches its
baseline (or --update rewrote the baselines).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

STABLE_EXACT = {
    "identical",
    "copies_byte_identical",
    "rd_reflexive_same_log_identical_all",
    "pc_observable_holds",
    "invalid_write_rejected_loudly",
    "get_tuple_error_after_corrupt_write",
    "result",
    "outcome",
    "crashed",
    "corrupt_load_error",
    "stray_next_result",
    "write_rejected_loudly",
    "expected_outcome_if_exactly_once",
    "resume_true_value",
    "resume_false_value",
    "invalid_state_persisted",
    "n_checkpoints",
    "history_error",
    "invoke_error",
    "expected_counter_if_exactly_once",
    "expected_counter_if_docs_hold",
    "interrupts_on_first_run",
    "interrupts_on_resume",
    "replay_raised",
    "silent_noop_documented_event",
    "checkpoints_written_documented_event",
    "to_dict",
    "from_dict",
    "bad_key_in_snapshot",
    "resume_error",
    "crash",
    "first_run",
}


def is_stable_key(key: str) -> bool:
    return (
        key.startswith("violation")
        or key.endswith("_version")
        or key.endswith("_execs")
        or "execs_" in key
        or key.startswith("counter_")
        or key.startswith("pre_execs")
        or key.startswith("post_execs")
        or key.startswith("prefix_execs")
        or key.startswith("suffix_execs")
        or key.startswith("b_post")
        or key.startswith("charges")
        or key.startswith("s1_execs")
        or key.startswith("a_execs")
        or key.startswith("b_execs")
        or key in STABLE_EXACT
    )


def stable_view(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, dict):
                sub = stable_view(v)
                if sub:
                    out[k] = sub
            elif is_stable_key(k):
                out[k] = v
        return out
    return obj


def run_probe(env: str, script: str) -> dict:
    cmd = [
        "uv", "run", "--project", str(ROOT / "envs" / env),
        "python", str(ROOT / script),
    ]
    # Inherit the full invoking environment (API keys for live probes,
    # proxies, locale) and only ADD the telemetry opt-outs. An earlier
    # version passed a minimal env dict, which silently stripped exported
    # API keys and made live probes self-skip even on keyed shells.
    env = dict(subprocess.os.environ)
    env.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
    env.setdefault("OTEL_SDK_DISABLED", "true")
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, timeout=900, env=env,
    )
    # Probes emit human-readable progress before their JSON document, and
    # that progress may itself contain braces -- probe 171 prints Python dict
    # reprs of its predicted/observed pairs, whose single quotes are not JSON.
    # Taking the first "{" in stdout and parsing to EOF therefore fails on any
    # probe whose prose mentions a brace, and fails LATE, mid-audit. Scan every
    # candidate offset instead and accept the first that actually decodes,
    # which is the earliest well-formed document and so the outermost one when
    # objects nest. Trailing prose after the document is tolerated because
    # raw_decode stops at the closing brace rather than requiring EOF.
    out = proc.stdout
    dec = json.JSONDecoder()
    for i, ch in enumerate(out):
        if ch != "{":
            continue
        try:
            obj, _ = dec.raw_decode(out[i:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj:
            return obj
    raise RuntimeError(
        f"{script}: no decodable JSON object on stdout (rc={proc.returncode})\n"
        f"stdout tail: {out[-500:]}\n"
        f"stderr tail: {proc.stderr[-500:]}"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default="matrix.toml")
    ap.add_argument("--baseline", default="results/pilot")
    ap.add_argument("--update", action="store_true",
                    help="rewrite baselines from this run")
    args = ap.parse_args()

    plan = tomllib.loads((ROOT / args.plan).read_text())
    basedir = ROOT / args.baseline
    basedir.mkdir(parents=True, exist_ok=True)

    # Validate the whole plan before executing any of it. A missing key used
    # to surface as a bare KeyError partway through the run, after minutes of
    # probe execution and with no indication of which entry was malformed --
    # and, because the crash came late, the entries after it were silently
    # never audited at all. Report every offender at once, up front.
    malformed = []
    for i, probe in enumerate(plan["probe"]):
        gaps = [k for k in ("id", "env", "script") if k not in probe]
        if gaps:
            malformed.append((i, probe.get("script", "<no script>"), gaps))
    if malformed:
        print(f"FATAL: {args.plan} has {len(malformed)} malformed entr"
              f"{'y' if len(malformed) == 1 else 'ies'}:")
        for i, script, gaps in malformed:
            print(f"  [[probe]] #{i} ({script}) missing: {', '.join(gaps)}")
        return 2

    # A probe that writes its own <id>_stable.json cannot be a plan row. The
    # runner compares stable_view(stdout) against that filename, but a
    # self-writing probe puts a receipt wrapper there ({backend, host, pins,
    # probe, stable, utc}) rather than a stable view, so the comparison fails
    # on SHAPE and reports every field as run=None -- which reads like a
    # verdict regression and is not one. This was previously enforced only by
    # a comment in the plan; enforce it here.
    selfwriters = []
    for probe in plan["probe"]:
        src = ROOT / probe["script"]
        if src.exists() and "_stable.json" in src.read_text():
            selfwriters.append((probe["id"], probe["script"]))
    if selfwriters:
        print(f"FATAL: {args.plan} lists {len(selfwriters)} self-writing probe(s);")
        print("       these are gated on committed receipts, not re-executed here:")
        for pid, script in selfwriters:
            print(f"  [[probe]] id={pid} ({script}) writes its own <id>_stable.json")
        return 2

    failures = 0
    manifests = {}
    for probe in plan["probe"]:
        pid, env, script = probe["id"], probe["env"], probe["script"]
        pdir = ROOT / probe.get("baseline", args.baseline)
        pdir.mkdir(parents=True, exist_ok=True)
        print(f"== probe {pid} ({env}) :: {script}")

        missing = [k for k in probe.get("requires_env", [])
                   if not subprocess.os.environ.get(k)]
        if missing:
            print(f"   SKIPPED: requires env {missing} (not set)")
            manifests.setdefault(pdir, []).append(
                {"id": pid, "env": env, "script": script,
                 "status": f"skipped (missing {','.join(missing)})"})
            continue

        result = run_probe(env, script)
        if isinstance(result, dict) and set(result) == {"skipped"}:
            print(f"   SKIPPED by probe: {result['skipped']}")
            manifests.setdefault(pdir, []).append(
                {"id": pid, "env": env, "script": script,
                 "status": f"skipped ({result['skipped']})"})
            continue
        got = stable_view(result)
        manifests.setdefault(pdir, []).append(
            {"id": pid, "env": env, "script": script, "status": "run"})

        raw_path = pdir / f"{pid}_results.json"
        stable_path = pdir / f"{pid}_stable.json"
        if args.update or not stable_path.exists():
            raw_path.write_text(json.dumps(result, indent=2, default=str) + "\n")
            stable_path.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
            print(f"   baseline written: {stable_path.relative_to(ROOT)}")
            continue

        want = json.loads(stable_path.read_text())
        if got == want:
            print("   OK: stable fields match committed baseline")
        else:
            failures += 1
            print("   MISMATCH vs committed baseline:")
            for k in sorted(set(want) | set(got)):
                if want.get(k) != got.get(k):
                    print(f"     {k}: baseline={want.get(k)!r} run={got.get(k)!r}")

    if args.update:
        for pdir, entries in manifests.items():
            all_skipped = all(e["status"].startswith("skipped") for e in entries)
            if all_skipped and (pdir / "MANIFEST.json").exists():
                # Preserve run provenance (e.g. live cells recorded on a keyed
                # host) when this update could not execute any probe here.
                print(f"   manifest preserved (all probes skipped): "
                      f"{(pdir / 'MANIFEST.json').relative_to(ROOT)}")
                continue
            man = {"plan": args.plan, "probes": entries,
                   "note": ("stable_view fields only are audited; raw JSON "
                            "may contain volatile ids/timestamps")}
            (pdir / "MANIFEST.json").write_text(
                json.dumps(man, indent=2) + "\n")
            print(f"   manifest written: {(pdir / 'MANIFEST.json').relative_to(ROOT)}")

    if failures:
        print(f"AUDIT FAILED: {failures} probe(s) diverged from baselines")
        return 1
    print("AUDIT CLEAN: all stable verdict fields match committed baselines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
