## What changed and why

<!-- One or two sentences. Link an issue if there is one. -->

## How you verified it

<!--
`make test` and `make lint` cover the deterministic half only — no containers,
no model calls. If this touches anything container- or model-dependent
(a source, model generation, a live query), run it against a real source and
paste the actual result: the numbers you got, and what you compared them
against. A mock passing is not evidence here.
-->

## Checklist

- [ ] `make test` and `make lint` pass
- [ ] New behavior has a test, or I've explained above why it can't be tested
      without a container/model call
- [ ] If I removed or weakened a test, I said what still covers the behavior
- [ ] Docs (`README.md` / `AGENTS.md` / `CONTRIBUTING.md`) updated if this
      changes a documented invariant, module, or command
