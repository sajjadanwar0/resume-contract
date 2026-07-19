#!/usr/bin/env bash
# Full local validation of the remit-contract package.
set -euo pipefail
echo '== 1/6 Rust core tests (default profile; includes exhaustive conformance)'
cargo test -p remit-core
echo '== 2/6 heavy model conformance + concurrency (release)'
REMIT_MODEL_CASES=20000 REMIT_STRESS_THREADS=64 REMIT_STRESS_OPS=500 \
  cargo test -p remit-core --release
echo '== 3/6 scaled exhaustive conformance (release; ~30-60 s)'
REMIT_EXH_FORKS=3 REMIT_EXH_CRASHES=2 REMIT_EXH_EXTRAS=2 REMIT_EXH_INVALIDS=2 \
  cargo test -p remit-core --release --test exhaustive_conformance -- --nocapture
echo '== 4/6 build wheel'
python3 -m pip install --user -q maturin pytest 2>/dev/null || pip install -q maturin pytest
maturin build --release -o dist
echo '== 5/6 install wheel + binding tests'
pip install --force-reinstall -q dist/remit_contract-*.whl
python3 -m pytest tests/test_core_bindings.py -q
echo '== 6/6 LangGraph integration at the paper pins'
pip install -q 'langgraph==1.2.9' 'langgraph-checkpoint==4.1.1' 'langgraph-checkpoint-sqlite==3.1.0'
python3 -m pytest tests/test_langgraph_repair.py -v
echo 'ALL GREEN: package matches the paper-claimed evidence.'
