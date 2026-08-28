"""A Flow: a sequential pipeline defined by a folder, and one Run of it.

Read this before anything else here, and `docs/adr/0006` before proposing a table for any of it.
`CONTEXT.md` has the vocabulary — Flow, Phase, Run, Gate, Brief, Reference, Working, Bundle,
Stale — and every one of those words was chosen because the obvious one was already taken.

**The folder is the definition.** There is no row, no builder, and nothing else that describes a
Flow. The numbering of `phases/NN_name/` *is* the execution order, so inserting a step is renaming
folders and reordering is renaming two. Nothing in this package writes a Flow's shape into a
sidecar index, because an index is a second thing that can disagree with `ls`.

The method is *Interpretable Context Methodology* (arXiv:2603.16021), whose claim is that for
sequential, human-reviewed, repeatable workflows the filesystem can do what a framework does in
code. Three of its five layers are folders here: a Flow's own prose routes, a Phase's prose
instructs, and `_config/` plus `references/` hold what stays true across Runs.

Two files at every level, and the same split at both:

    flow.yml / phase.yml    what the machine reads — parsed here, `extra="forbid"`
    CONTEXT.md              what the model reads — never parsed, only inlined

That split is why a typo in a field name is an error with a line number while a clumsy sentence
in the prose is simply a clumsy sentence.

## The modules

`layout.py` is every path a Flow has, and the only place that joins path components. `contract.py`
parses and refuses. `state.py` is `run.json` — where a Run is, which Phases are done, and which
have gone **Stale**.

Nothing here raises at a caller. A refusal is a `Report`, the same way a gate rejection is text
the agent can act on: the authoring agent gets told what is wrong with the file it just wrote and
writes it again.

## Two path roots, and why they are the whole layer distinction

A **Reference** input resolves against the Flow's folder and is stable across every Run. A
**Working** input resolves against the Run's folder and exists only because this Run produced it.
The paper spends a table on the difference; here it is two arguments to one function, and the
consequence downstream is that Reference material is inlined into a prompt verbatim while Working
material arrives as prose or as a shape.
"""
