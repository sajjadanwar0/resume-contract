# resume-contract

**Resume Means Resume: a conformance contract for checkpoint / interrupt /
resume semantics in LLM-agent frameworks.**

Six properties over the framework resume plane -- PC prefix consistency,
EO effect exactly-once, FD fork determinism, CV checkpoint validity,
CO consume-once, RD recovery determinism -- with (i) a machine-checked
TLA+ model (TLC: reference config clean on all six; five single-fault
configs each yield the targeted counterexample), (ii) a deterministic,
LLM-free, timing-free conformance harness with a pilot matrix over
LangGraph 1.2.9, LlamaIndex Workflows 2.22.2, and CrewAI 1.15.2, and
(iii) `remit`, the verified-reference resume sequencer / effect ledger
(Rust; Verus verification planned, see `crates/remit/VERIFICATION.md`).

Single-command audit: `./reproduce.sh` re-derives every headline number
from committed data (TLC matrix, probe verdicts vs baselines, remit
invariant tests).

## Layout

```
Cargo.toml                 Rust workspace root (open this in RustRover)
crates/
  remit/                   reference sequencer + append-only effect ledger
                           (six property-named tests; TLA module R0 is its
                           protocol spec)
pyproject.toml             root uv project: harness package `conformance`
                           + dev tooling (open repo root in PyCharm)
envs/                      ONE ENV = ONE MATRIX CELL (framework@version)
  langgraph/               langgraph==1.2.9, langgraph-checkpoint==4.1.1
  llamaindex/              llama-index-workflows==2.22.2
  crewai/                  crewai==1.15.2
harness/conformance/       runner.py: executes matrix.toml through the env
                           projects, audits stable verdict fields vs
                           committed baselines
matrix.toml                probe -> env plan (extend here for 117+)
probes/                    numbered pilot probes (verbatim receipts)
  113_p1_langgraph_regressions.py     #7361 #6663 #6792 + CO + #6491-class CV
  114_p2_conformance_llamaindex.py    snapshot/fork/dual-response/wait_for_event/CV
  115_p2_conformance_crewai.py        @persist restore + crash-resume
  115b_p2_crewai_checkpointconfig.py  documented-event silent no-op +
                                      from_checkpoint completed-work re-execution
formal/tla/
  ResumeContract.tla       the contract (6 invariants, 5 fault switches)
  R0..R5*.cfg              reference + single-fault TLC configurations
  116_run_tlc.sh           verification matrix runner
results/pilot/  committed baselines per campaign: probe JSON (raw + stable
                           view) and TLC logs -- what reproduce.sh audits
archaeology/               issue-archaeology protocol (CODEBOOK.md),
                           seed set (seeds.csv), dedup tool (dup_check.py)
docs/decisions/            GO decision + crash-validation autopsy record
scripts/                   numbered helper scripts (next free number: 117)
reproduce.sh               single-command audit
Makefile                   setup / pilot / tlc / rust / audit
```

Design note -- why per-framework env projects instead of one lockfile: the
study's Threats section requires results tied to exact framework versions,
and the full matrix includes per-version regression sweeps (e.g., langgraph
1.0.x / 1.1.x / 1.2.x are three cells). A uv *workspace* forces one shared
resolution; independent env projects give one pinned resolution per cell.
The cells are deliberately trivial (`[tool.uv] package = false`, framework
pins only, no local path dependencies): probes import only their framework,
and the harness always runs from the ROOT env, so nothing nonstandard can
interfere with resolution on any uv version or host configuration.

## Prerequisites

* uv >= 0.4 (https://docs.astral.sh/uv/) -- manages Python 3.12 itself via
  `.python-version`; no system Python setup needed
* Rust stable via rustup (`rust-toolchain.toml` pins channel/components)
* Java >= 11 (TLC; `reproduce.sh` fetches `tla2tools.jar` on first run)

## Step-by-step setup

```bash
git clone https://github.com/sajjadanwar0/resume-contract-paper resume-contract
cd resume-contract

# 1. Python: resolve + install the root project and every matrix cell
make setup            # = uv sync; uv sync --project envs/{langgraph,llamaindex,crewai}

# 2. Formal: run the TLC verification matrix. The scripts locate TLC
#    automatically: $TLC_CMD | $TLA_TOOLS_JAR | formal/tla/tla2tools.jar |
#    $HOME/tla2tools.jar | download to $HOME. An existing
#    ~/tla2tools.jar (e.g. behind an `alias tlc=...`) is picked up as is.
make tlc              # R0 clean; R1-R5 each violate exactly the target invariant

# 3. Rust: build + run the remit invariant tests
make rust             # 7 tests, one per property (+ the 11-not-12 arithmetic)

# 4. Pilot: run the conformance matrix and diff vs committed baselines
make pilot

# Everything at once, gate-style:
./reproduce.sh        # or: ./reproduce.sh --tlc-only
```

Run a single probe in its pinned cell:

```bash
uv run --project envs/langgraph python probes/113_p1_langgraph_regressions.py
```

## PyCharm setup

1. **Open** the repo root as the project.
2. **Interpreters** (Settings > Project > Python Interpreter > Add
   Interpreter > *Add Local Interpreter* > **uv**): add FOUR interpreters,
   one per uv project -- repo root, `envs/langgraph`, `envs/llamaindex`,
   `envs/crewai`. (If your PyCharm predates native uv support, run
   `make setup` first and add each `*/.venv/bin/python` as an *Existing*
   environment.) Keep the **root** env as the project default; it owns the
   harness and dev tooling.
3. **Sources**: mark `harness/` as a *Sources Root* so `import conformance`
   resolves in the editor (probes import only their framework; the
   harness always runs on the root interpreter).
4. **Run configurations**: for each probe create a Python config with
   *Script* = the probe file, *Working directory* = repo root, and
   *Interpreter* = that probe's env (per `matrix.toml`). Add one config
   `pilot` running module `conformance.runner` with parameters
   `--plan matrix.toml --baseline results/pilot` on the root
   interpreter.
5. Exclude `envs/*/.venv`, `.venv`, and `target/` from indexing
   (right-click > Mark Directory as > Excluded) to keep search fast.

## RustRover setup

1. **Open** the repo root; RustRover attaches the Cargo workspace from
   `./Cargo.toml` automatically (`crates/remit` appears as the member).
2. Toolchain is taken from `rust-toolchain.toml` (stable + rustfmt +
   clippy); no manual selection needed.
3. **Run**: use the gutter runners on the tests in
   `crates/remit/src/lib.rs`, or a Cargo run configuration with command
   `test --workspace`. Enable *Run rustfmt on save* and the clippy
   external linter in Settings > Rust.
4. The Python side is invisible to RustRover by design; the repo carries no paper
   sources by design.

## Workflows

**Extend the matrix (scripts/117 onward).** One new cell = one directory:

```bash
cp -r envs/langgraph envs/autogen        # then edit envs/autogen/pyproject.toml:
#   name = "rc-env-autogen"; dependencies = ["ag2==X.Y.Z"]
uv sync --project envs/autogen
# add the probe under probes/117_*.py, register it in matrix.toml,
# generate its baseline once:
uv run python -m conformance.runner --plan matrix.toml \
    --baseline results/pilot --update
```

Per-version regression sweeps are the same recipe with version-suffixed
cells (`envs/langgraph-1.1.4/`), which is precisely why envs are projects.

**Refresh baselines deliberately** (never implicitly): re-run the runner
with `--update`, review the diff, and commit -- git history holds every
prior baseline, so paths stay date-free by convention. Baselines are
receipts; the audit exists to catch upstream drift (e.g., a framework
fixing #6663), which is a finding, not noise.

**Archaeology.** Retrieval per `archaeology/CODEBOOK.md`; dedup candidates
with `uv run python archaeology/dup_check.py` before coding; coded corpus
and Cohen's kappa land under `results/archaeology/` (nothing is claimed
until they do).

**Remit -> framework shim (next milestone).** The first integration target
is a LangGraph `BaseCheckpointSaver` shim routing `put`/`put_writes`
through `remit::commit_checkpoint` and effect admission through
`remit::begin_effect`; acceptance = probes 113-115b through the shim with
zero violations. Verus obligations per `crates/remit/VERIFICATION.md`.

## Conventions

American English; ASCII-only in artifacts; complete files (git holds
history, not filename suffixes); numbered scripts continue from 117; every
audit gates on committed baselines; probe verdicts are deterministic,
LLM-free, and timing-free by construction -- if a verdict changes, a
package version changed.
