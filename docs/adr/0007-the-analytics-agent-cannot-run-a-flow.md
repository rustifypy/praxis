# The analytics agent cannot run a Flow

Asking the chat agent "run the weekly revenue flow" does not work, and this is deliberate. There
is no `run_flow` in `agent/tools/`. A **Run** is started from the Flows page or from the flow
chat, whose agent is built one tier up.

The reason is a cycle. Running an agent **Phase** means calling `run_turn`, so the Flow runner
sits above `agent/`. A tool in `agent/tools/` that started a Run would have to import that runner,
which is an upward import — the thing `tests/test_layering.py` exists to refuse. The tier table is
not a convention that can be bent for one convenient tool; `flows/` is tier 3 and `api/` moved to
tier 4 to make room for it.

The alternative considered was injecting the runner into the tool as a callback at build time.
That works and would satisfy the import graph, because the edge would no longer be visible to a
static parse. That is exactly the objection: it would be a cycle `test_layering` cannot see, and
the next person to reason about the tiers would be reasoning about a graph that does not describe
the program. `if TYPE_CHECKING:` is already called out in AGENTS.md as a cycle that has not failed
yet; a callback is the same trick with better manners.

What is lost is smaller than it sounds. "Run it from the agent" is still true — it is the flow
chat that does it, and that chat is where someone is already talking about the Flow. What is not
possible is starting a Run from the middle of a conversation about last quarter's revenue, and
that conversation has a `Session` pinned to `main` and a transcript in sqlite, neither of which a
Run wants.
