"""Workspace path resolver, imported by harness and scripts."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBES = ROOT / "probes"
ENVS = ROOT / "envs"
RESULTS = ROOT / "results"
TLA = ROOT / "formal" / "tla"
PILOT = RESULTS / "pilot"
