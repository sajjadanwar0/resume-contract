# resume-contract

**Resume Means Resume: a conformance contract for checkpoint / interrupt /
resume semantics in LLM-agent frameworks.**

Six properties over the framework resume plane -- PC prefix consistency,
EO effect exactly-once, FD fork determinism, CV checkpoint validity,
CO consume-once, RD recovery determinism -- with (i) a machine-checked
TLA+ model (TLC: reference and liveness configs clean on all six
invariants; five single-fault configs each yield exactly the targeted
counterexample; a two-config LangGraph-fork submodel), (ii) a
deterministic, LLM-free, timing-free conformance harness spanning
LangGraph, LlamaIndex Workflows, CrewAI, pydantic-graph, and the OpenAI
Agents SDK at pinned releases, plus key-gated live cells and a
release sweep across prior and post-pin framework versions
(`results/sweep/`), and (iii) `remit`, the reference resume sequencer /
effect ledger: a fully discharged Verus suite (35 spec-mode + 18
exec-mode items, 0 errors; falsifiability certificates under
`crates/remit/proof/negative/`), with the verified executable recover
core line-identical to the shipped Rust (`n3_sync_check.sh`, CI-gated).

## Availability

The repair artifact is published on PyPI:

```bash
pip install remit-contract        # v0.1.0 = the exact build evaluated in the paper
```

Prebuilt `abi3` wheel for CPython >= 3.9 on x86_64 manylinux2014; source
distribution everywhere else. Published artifacts, sha256:

```
2aed2cbfc56e1725fbb0ac98a665d23058d518544642309d223759d0746fc58d  wheel
566aa0b76da674bbf3785084a520458ac56f4578fa27a234131ef74076c9d146  sdist
```

Package sources live in `remit-contract/` (its own README documents the
API, tested pins, and verification chain); release automation in
`remit-contract/.github/workflows/release.yml`.

## Single-command audit

`./reproduce.sh` re-derives every headline number from committed data in
four stages: [1] the TLC verification matrix, [2] the full `matrix.toml`
conformance plan diffed against committed stable baselines (live-keyed
probes skip without API keys), [3] the remit invariant/conformance/
concurrency suites plus the line-identical-core gate, [4] the complete
Verus proof suite, including the negative certificates, which must fail
in exactly their expected shape.

## Layout

```
Cargo.toml                 Rust workspace root (crates/remit)
crates/remit/              reference sequencer + append-only effect ledger;
                           proof/ holds the Verus suite (+ negative/)
                           and VERIFICATION.md, the correspondence ledger
remit-contract/            the published PyPI package (Rust core + PyO3
                           bindings + decision-free LangGraph shim)
pyproject.toml             root uv project: harness package `conformance`
envs/                      ONE ENV = ONE MATRIX CELL (framework@version);
                           per-cell uv projects, incl. version-suffixed
                           regression cells and the live/durable variants
harness/conformance/       runner.py: executes matrix.toml through the env
                           projects, audits stable verdict fields vs
                           committed baselines
matrix.toml                probe -> env plan
probes/                    numbered probes, verbatim receipts (113..152;
                           `_wip` marks unfinished templates)
formal/tla/                ResumeContract.tla + LangGraphFork.tla and the
                           TLC configurations reproduce.sh drives
results/                   committed evidence, one subdirectory per
                           campaign (pilot, matrix, live, regression,
                           revision, r12, sweep, tla, ...), with
                           generated manifests
sweep_nonlg.sh             release sweep of the non-LangGraph headline
                           cells (back- and forward-versions; receipts +
                           release-date dump into results/sweep/)
n3_sync_check.sh           byte-level gate: verified exec recover body ==
                           shipped lib.rs body
prefreeze_check.sh         one-shot pre-submission battery (includes the
                           paper<->probes inventory gate)
reproduce.sh               single-command audit (stages [1]..[4])
Makefile                   setup / pilot / tlc / rust / audit
archaeology/               issue-archaeology protocol and seed set
docs/decisions/            decision records
```

Design note -- why per-framework env projects instead of one lockfile: the
study's Threats section requires results tied to exact framework versions,
and the matrix includes per-version regression sweeps (e.g., langgraph
1.1.x and 1.2.x are distinct cells). A uv *workspace* forces one shared
resolution; independent env projects give one pinned resolution per cell.
The cells are deliberately trivial (framework pins only, no local path
dependencies): probes import only their framework, and the harness always
runs from the ROOT env.

## Security posture

The environments under `envs/` are frozen measurement instruments: each
pins the exact framework release a probe campaign measured, and those
pins are never updated, because the paper's receipts are meaningless
against any other version. Dependency alerts against `envs/*` lockfiles
are therefore expected and dismissed with a stated reason; alerts against
the artifact's own runtime (root manifests, the Rust workspaces, CI
actions) are fixed promptly -- e.g., the published package's PyO3 was
bumped to the advisories' patch floor before release. Nothing in this
repository is a deployable service.

## Prerequisites

* uv >= 0.4 (https://docs.astral.sh/uv/) -- manages Python 3.12 itself;
  no system Python setup needed
* Java >= 11 (TLC; `reproduce.sh` fetches `tla2tools.jar` on first run)
* Rust: `crates/remit` builds on rustc >= 1.75; the published package
  workspace (`remit-contract/`, lockfile v4) needs rustc >= 1.83 -- on
  Ubuntu 24.04 both are distribution packages
  (`apt install rustc-1.83 cargo-1.83`), no rustup required (rustup works
  too)
* Verus (optional): stage [4] discharges the proof suite when `verus` is
  on PATH and states the skip otherwise

## Step-by-step setup

```bash
git clone https://github.com/sajjadanwar0/resume-contract
cd resume-contract

make setup            # uv sync for the root project and every matrix cell
make tlc              # TLC matrix: reference/liveness clean, faults targeted
make rust             # remit invariant + conformance + concurrency suites
make pilot            # conformance plan vs committed baselines
./reproduce.sh        # everything at once, gate-style ([1]..[4])
```

Run a single probe in its pinned cell:

```bash
uv run --project envs/langgraph python probes/113_p1_langgraph_regressions.py
```

## PyCharm setup

1. **Open** the repo root as the project.
2. **Interpreters** (Settings > Project > Python Interpreter > Add
   Interpreter > *Add Local Interpreter* > **uv**): add one interpreter
   per uv project you work in -- the repo root plus each `envs/<cell>`
   you touch. Keep the **root** env as the project default; it owns the
   harness and dev tooling.
3. **Sources**: mark `harness/` as a *Sources Root* so `import conformance`
   resolves in the editor.
4. **Run configurations**: per probe, *Script* = the probe file, *Working
   directory* = repo root, *Interpreter* = that probe's env (per
   `matrix.toml`); plus one config running module `conformance.runner`
   with `--plan matrix.toml` on the root interpreter.
5. Exclude `envs/*/.venv`, `.venv`, and `target/` from indexing.

## RustRover setup

1. **Open** the repo root; the Cargo workspace attaches from
   `./Cargo.toml` (`crates/remit`). Open `remit-contract/` separately for
   the package workspace.
2. Toolchains: `crates/remit` on any stable >= 1.75; `remit-contract/`
   needs >= 1.83 (see Prerequisites).
3. **Run**: gutter runners on the tests, or a Cargo configuration with
   `test --workspace`.
4. The repo carries no paper sources by design.

## Workflows

**Extend the matrix.** One new cell = one directory:

```bash
cp -r envs/langgraph envs/<framework>   # edit its pyproject: name + pin
uv sync --project envs/<framework>
# add the probe under probes/NNN_*.py, register it in matrix.toml,
# generate its baseline once:
uv run python -m conformance.runner --plan matrix.toml --update
```

Per-version regression sweeps are the same recipe with version-suffixed
cells (`envs/langgraph-1.1/`), which is precisely why envs are projects.

**Release sweep.** `./sweep_nonlg.sh` re-runs the non-LangGraph headline
probes across prior and post-pin releases (idempotent per receipt;
`--versions <pkg>` lists finals; `--summary` re-adjudicates committed
receipts, salvaging JSON from stdout-polluting frameworks).

**Refresh baselines deliberately** (never implicitly): re-run the runner
with `--update`, review the diff, and commit -- git history holds every
prior baseline. Baselines are receipts; the audit exists to catch
upstream drift, which is a finding, not noise.

**Packaged artifact.** `remit-contract/` is the published package
(PyPI: `remit-contract`); its README covers the API and pins, its
`VERIFICATION.md` the verification chain, and `PUBLISHING`-style release
steps run through the committed trusted-publishing workflow.

## Conventions

American English; ASCII-only in artifacts; complete files (git holds
history, not filename suffixes); numbered probes/scripts continue from
the highest committed number; every audit gates on committed baselines;
probe verdicts are deterministic, LLM-free, and timing-free by
construction -- if a verdict changes, a package version changed.

## TLC reproduction note

Invoking TLC directly on the shipped `.cfg` files requires `-deadlock`
(or `CHECK_DEADLOCK FALSE` in the cfg): the raw R0 run halts at 71/51
with a benign deadlock before the paper's 87/59 total is reached;
`reproduce.sh` passes the flag.
