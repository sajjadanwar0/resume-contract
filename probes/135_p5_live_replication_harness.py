#!/usr/bin/env python3
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
N = int(os.environ.get("PROBE135_N", "20"))
TARGETS = os.environ.get("PROBE135_TARGETS", "121,122,131").split(",")
MODELS = [m for m in os.environ.get("PROBE135_MODELS", "").split(",") if m]

if not MODELS:
    MODELS = [None]

def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]

def find_probe(prefix):
    hits = sorted(HERE.glob(f"{prefix}_*.py"))
    return hits[0] if hits else None

def last_json(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    i = text.find("{")
    if i >= 0:
        try:
            return json.loads(text[i:])
        except Exception:
            return None
    return None

def run_cell(script, model):
    env = dict(os.environ)
    if model:
        env["ANTHROPIC_MODEL"] = model
        env["PROBE_MODEL"] = model
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(script)], env=env,
                           capture_output=True, text=True, timeout=600)
        doc = last_json(r.stdout)
        if doc is None:
            return {"error": "no-json", "stderr_tail":
                    r.stderr.strip().splitlines()[-1] if r.stderr.strip() else None}
        doc["_elapsed_s"] = round(time.time() - t0, 1)
        return doc
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": repr(e)}

def main():
    report = {"n_per_cell": N, "targets": TARGETS,
              "models_requested": MODELS if MODELS != [None] else "probe-default",
              "cells": {}}
    for prefix in TARGETS:
        script = find_probe(prefix.strip())
        if script is None:
            report["cells"][prefix] = {"error": f"no probe matching {prefix}_*.py"}
            continue
        for model in MODELS:
            key = f"{script.name}::{model or 'default'}"
            runs, errors = [], 0
            for i in range(N):
                print(f"[135] {script.name} ({model or 'default'}) run {i+1}/{N}", file=sys.stderr, flush=True)
                doc = run_cell(script, model)
                if "error" in doc:
                    errors += 1
                runs.append(doc)
            bool_fields = {}
            ok_runs = [d for d in runs if "error" not in d]
            for d in ok_runs:
                for k, v in d.items():
                    if isinstance(v, bool):
                        bool_fields.setdefault(k, []).append(v)
            agg = {}
            for k, vals in bool_fields.items():
                t = sum(vals)
                agg[k] = {"true": t, "of": len(vals),
                          "proportion": round(t / len(vals), 4),
                          "wilson95": wilson(t, len(vals))}
            model_reported = None
            for d in ok_runs:
                if isinstance(d.get("model"), str):
                    model_reported = d["model"]
                    break
            report["cells"][key] = {
                "runs": N, "errors": errors,
                "model_reported_by_probe": model_reported,
                "verdict_fields": agg,
            }
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
