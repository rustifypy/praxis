# A branch that cannot fast-forward is replayed, never merged by hand

The store fast-forwards into main and does nothing else: no three-way merge, no merge commit. Once
main moves, an older branch can never land as it is. `workspace.land` answers that in three steps —
fast-forward, else rebase-then-fast-forward, else **delete the branch, recreate it from main, and
re-commit its files**.

## Why this is not the coward's option

Main is *generated*. Re-running the pipeline rewrites every `cubes/*.yml` in it, so a store-side
rebase of an edit branch onto a regenerated main conflicts on files nobody touched. The old store
did a real `merge-tree`, which hid this; fast-forward-only surfaces it, and surfacing it as a
conflict-resolution UI would be building a git client inside a portal that deliberately has no git.

The branch's own content **is** the intent. Replaying it onto a fresh branch off main is exactly
"rebase, ours wins" — and it needs no conflict resolution because there is nothing to reconcile: a
file the branch carries replaces main's, and a file only main carries survives.

What makes that safe is that the guard is elsewhere and already ran. `edits.check` compares the
whole proposed model against **main** on every `stage`, cubes append-only, and `land` cannot reach
a state that gate has not passed. So "ours wins" cannot quietly drop a measure a dashboard reads —
that is refused at Save, long before anything lands.

## What it costs

The branch's intermediate store-side commits. They are an audit trail rather than a merge base,
every one of them was a full-set write, and the ref is one branch of a model rather than a
project's history. Worth saying in the response, which is why the route reports `how`:
`merged`, `rebased` or `replayed`.

## The refusal that makes it coherent

`land` refuses a branch whose **draft** is unpublished. Those bytes are on the portal's disk and
invisible to the store, so a merge would silently land without them — and the replay path would
then re-commit the branch's *committed* files over the top, discarding local work with no
indication. This is the same rule the store applies to its own dirty worktree, for the same
reason, and it is why publish and merge are two actions rather than one button.
