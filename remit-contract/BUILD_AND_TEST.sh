#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

VENV="${REMIT_VENV:-.venv-remit}"

echo "== 0/6 isolated environment ($VENV)"
command -v uv >/dev/null || { echo "FATAL: uv not on PATH"; exit 2; }
uv venv "$VENV"
export VIRTUAL_ENV="$PWD/$VENV"
export PATH="$VIRTUAL_ENV/bin:$PATH"
echo "   python: $(python -V) at $(command -v python)"

echo '== 1/6 Rust core tests (default profile; includes exhaustive conformance)'
cargo test -p remit-core

echo '== 2/6 heavy model conformance + concurrency (release)'
REMIT_MODEL_CASES=20000 REMIT_STRESS_THREADS=64 REMIT_STRESS_OPS=500 \
  cargo test -p remit-core --release

echo '== 3/6 scaled exhaustive conformance (release; ~30-60 s)'
REMIT_EXH_FORKS=3 REMIT_EXH_CRASHES=2 REMIT_EXH_EXTRAS=2 REMIT_EXH_INVALIDS=2 \
  cargo test -p remit-core --release --test exhaustive_conformance -- --nocapture

echo '== 4/6 build wheel'

PINNED="dist/remit_contract-0.1.2-cp39-abi3-manylinux_2_17_x86_64.manylinux2014_x86_64.whl"
if [[ ! -f "$PINNED" ]]; then
  echo "   WARNING: pinned wheel absent -- reproduce.sh step [3] will fail."
  echo "   Restore: pip download remit-contract==0.1.0 --no-deps --only-binary=:all: -d dist/"
fi
uv pip install -q maturin pytest
rm -rf dist-local
maturin build --release -o dist-local
ls -la dist-local/

echo '== 5/6 install wheel + binding tests'
uv pip install --reinstall dist-local/remit_contract-*.whl
python -m pytest tests/test_core_bindings.py -q

echo '== 6/6 LangGraph integration at the paper pins'
uv pip install -q 'langgraph==1.2.9' 'langgraph-checkpoint==4.1.1' \
                  'langgraph-checkpoint-sqlite==3.1.0'
python -m pytest tests/test_langgraph_repair.py tests/test_crossproc_gate.py -v

echo
echo 'ALL GREEN: package matches the paper-claimed evidence.'
echo "Wheel under test: $(ls dist-local/*.whl)"
echo
echo 'NOTE: this is a local wheel, not the manylinux wheel on PyPI. To rebuild'
echo 'the distributable artifact:  maturin build --release --zig  (or'
echo '--manylinux 2_28). A plain build tags linux_x86_64 and will not install'
echo 'on the platforms the PyPI wheel targets.'