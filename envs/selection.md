# Framework selection manifest (E-R1)

**Selection rule (as applied at selection time):** deep-probed frameworks are
drawn from the most widely adopted Python agent-orchestration stacks that
expose a documented durable interrupt/resume or crash-recovery mechanism on a
public checkpointer/persistence API. Adoption proxied by GitHub stars of the
hosting repository and PyPI download volume; both recorded below with
retrieval dates. Counts are adoption *proxies*, not usage measurements:
downloads include CI, mirrors, and dependency-driven installs (see caveats).

Retrieved 2026-07-21 (UTC) via https://pypistats.org/api/packages/<pkg>/recent
and https://api.github.com/repos/<org>/<repo>.

| Package (probed) | PyPI downloads, last month | Host repository | Stars |
|---|---|---|---|
| langgraph | 66,803,416 | langchain-ai/langgraph | 37,725 |
| llama-index-workflows | 11,447,459 | run-llama/llama_index | 50,971 |
| crewai | TODO — pypistats returned 429; retry: `sleep 120 && curl -s https://pypistats.org/api/packages/crewai/recent` | crewAIInc/crewAI | 55,873 |
| pydantic-graph | 106,409,941 | pydantic/pydantic-ai | 18,686 |
| autogen-agentchat | 1,013,801 | microsoft/autogen | 59,858 |

**Caveats recorded up front.** (1) `pydantic-graph` is installed as a
dependency of `pydantic-ai`; its download count is dependency-inflated and is
not comparable one-to-one with directly-installed packages. (2)
`llama-index-workflows` and `autogen-agentchat` are packages inside monorepos;
stars are repository-level, not package-level. (3) Star counts and download
windows move; the figures above are point-in-time with dates, per the
paper's citation of this manifest.

**Exclusions despite adoption (fill before committing):**
- Durable-execution engines (Temporal, DBOS, Step Functions): different layer;
  probed separately as engine baseline (probe 147), not as contract subjects.
- OpenAI Agents SDK: live cell only in the current revision; deterministic
  harness twin is probe 151 (in progress).
- TODO: any framework you evaluated and rejected (no crash-time durability
  control on a public path, etc.), with one-line reasons.