# Layering

Packages under `src/praxis/` are tiered, and **a package may import only its own tier or
below**. This is not a convention: `tests/test_layering.py` parses the import graph and fails the
suite on an upward import. A new top-level package fails until it is given a tier.

| Tier | Package | Why it sits here |
|---|---|---|
| 0 | `core/` | Settings, the sqlite file and every table's accessors (`deployment`, `transcript`, `boards`), the event format, the LLM builder, the job runner, `deployenv`. Imports nothing else in the project |
| 1 | `modelstore/` | The model store client, the served model (`Catalog`), the gate, and everything that generates or edits the model |
| 1 | `sandbox/` | The sandbox client: where the agent runs Python |
| 2 | `agent/` | Everything the LLM touches: prompts, toolsets, one turn (`run_turn`) |
| 3 | `flows/` | Flow folders, the Phase contract, one Run. Above `agent/` because running an agent Phase *is* `run_turn` |
| 4 | `api/` | HTTP, grouped by what someone is trying to do. May reach anything; nothing reaches it |

Tier 1 is external integrations, and there are exactly two. `core.llm` is an integration too,
but stays at tier 0 because every tier needs it — a builder, not a client.

## Keeping a feature inside its tier

- **What a lower tier cannot look up, it takes as an argument.** `editing.impact` is *handed*
  the saved dashboard queries it checks against, which is what keeps `modelstore` from knowing
  dashboards exist. `test_layering.FORBIDDEN` asserts `modelstore` never names `core.boards`.
- **A tool is agent vocabulary wherever its state lives.** All toolsets are in `agent/tools/`
  (Flow Phase tools in `flows/tools.py`), and all author their events through
  `agent/tools/trace.py`.
- **`if TYPE_CHECKING:` is a cycle that has not failed yet**, not a fix. Move the thing down a
  tier or pass it in.

Two consequences worth knowing:

- `core/boards.py` holds dashboard rows at tier 0, because the agent (tier 2) creates boards.
- There is no `run_flow` tool: a tool in `agent/` cannot import `flows/`. See
  [decision 0007](../adr/0007-the-analytics-agent-cannot-run-a-flow.md).

## The top of `modelstore/`

Seven modules, picked by one rule — what asking a question needs (`client`, `catalog`, `schema`,
`gate`, `shape`, `charts`) plus what both subpackages share (`definitions`). `modelgen/` builds
the model; `editing/` changes it by hand.

## Naming

- **A name is taken once.** `Catalog`/`Entity`/`MemberDoc`/`Folder` mean the *served* model.
  The generation side is `Introspection`/`Definition`/`MemberDescription`/`FolderDecision`.
- **`catalog` is the served model and nothing else.** The warehouse's tables are an
  `Introspection`.
- **`modelgen`, not `model`**, because "model" is also the LLM.
- Grep before naming; `CONTEXT.md` lists the words to avoid.
