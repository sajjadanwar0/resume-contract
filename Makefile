# Resume Contract workspace. Conventions: ASCII-only artifacts; complete files
# (git holds history, not filename suffixes); numbered scripts continue from 117;
# every audit gates on committed baselines.
UV ?= uv

.PHONY: setup pilot tlc rust audit clean

setup:            ## resolve+install all envs (root + one per matrix cell)
	$(UV) sync
	$(UV) sync --project envs/langgraph
	$(UV) sync --project envs/llamaindex
	$(UV) sync --project envs/crewai

pilot:            ## run the pilot conformance matrix, diff vs committed results
	$(UV) run python -m conformance.runner --plan matrix.toml --baseline results/pilot

tlc:              ## verify ResumeContract.tla (R0 reference + R1-R5 fault counterexamples)
	cd formal/tla && ./116_run_tlc.sh

rust:             ## build + test the Remit reference sequencer/ledger skeleton
	cargo test --workspace


audit:            ## single-command audit: re-derive every headline number
	./reproduce.sh

clean:
	rm -rf target envs/langgraph/.venv envs/llamaindex/.venv envs/crewai/.venv .venv formal/tla/states
