# An unsaved model edit lives in the browser and nowhere else

> **Superseded.** A ref being edited now has a **Draft** on the portal's disk, and an unpublished
> edit lives there. What survives is the constraint this was reasoned from — *Cube only compiles
> what is in the store*, so Save still writes to a throwaway ref — and the second gate, for the
> same reason restated: a draft can be edited between Save and Publish, so what is published is
> still not provably what was checked. What changed is the answer to "should the portal keep a
> copy": the two options rejected below were both a *second source of truth*, and a draft is not
> one, because no history lives in it. The store owns every commit, and a draft's staleness is a
> `rev` comparison away. The three consequences invert: a reload keeps saved work, two tabs share
> one draft, and the server knows what the model looks like — so the editor agent is no longer
> told, and `EditContext.staged` now carries only one turn's proposals.

The model editor has two actions: **Save**, which asks whether Cube would accept the whole dirty
set and writes nothing, and **Commit & sync**, which lands it on the branch. Between the two, the
change exists only in the tab that made it. A reload discards it and re-reads the store.

This is a decision, not an omission. It was reached by asking where the bytes go while they are
"saved but not pushed", and the honest answer is that they cannot go somewhere convenient without
becoming a second source of truth for a model whose truth is the deployment.

## The constraint that shapes it

**Cube only compiles what is in the store.** So "check this before I commit it" cannot be answered
locally at all — the change set has to reach the store for Cube to have an opinion. That is what
`workspace.verify` does: it writes the set to a throwaway `preview` ref, asks Cube, and deletes
the ref. The same shape `publish.install` uses for its candidate, for the same reason — nothing
that answers a query moves until something has compiled.

Given that a round trip to the store is unavoidable anyway, the only question left was whether
the *portal* should also keep a copy.

## Considered options

**A shadow ref in the store — `wip/<branch>`, created from the branch, committed to on Save,
merged into the branch on Commit & sync.** The most durable option: staging survives a reload,
two tabs agree, and the compile happens naturally because the bytes are already where Cube reads
them. Rejected because it makes the store hold a state the store has no concept of. Every ref in
that repository is a model someone can query, and a `wip/` ref is a model nobody may query but
Cube compiles anyway — so the ref list needs filtering by name, a crashed session leaves a
half-edited model in a list of real ones, and "the deployment is the source of truth" becomes
"the deployment is the source of truth except for the refs that are not".

**A table in `analyst.db`, keyed by (ref, path).** Truly local, and it survives a reload. Rejected
because it is the shadow ref plus a second copy: the compile still needs a throwaway ref, so the
same bytes would exist in the portal's database and in the store, and the portal's copy would be
the one going stale. It also makes the portal the owner of an edit to a model it does not own,
which is the arrangement the local model directory was deleted for.

**The browser.** Chosen. `ModelEditor` holds `path -> {base, text, staged}`, posts the dirty set
to `check`, marks it staged on a clean verdict, and posts the same set to `commit`. No table, no
directory, no ref.

## Consequences

Three, all real:

- **A reload loses unsaved *and* saved-but-uncommitted work**, including changes accepted from the
  editor agent hunk by hunk. The bar says so in words rather than letting someone discover it.
- **Two tabs on one branch will fight.** The last Commit & sync wins, and neither tab is told. One
  person, one portal, and the store's history is the record either way.
- **The server cannot know what the model currently looks like.** So the editor agent has to be
  *told*: `POST /api/model/chats/{id}/messages` carries the staged set, and `EditContext.staged`
  is what its tools read before the store. Without that, its second proposal in a turn silently
  reverts the first — the failure this consequence exists to prevent.

And one that reads as a downside and is not: `commit` runs the gate again rather than trusting the
verdict `check` gave. It has to, because what arrives at `commit` is not provably what was
checked — and that is true of any staging area a client owns, so the second gate is the price of
the whole arrangement rather than a redundancy to remove.
