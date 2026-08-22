# Reachability is two questions, not one probe

`reachable.semantic` and `reachable.querying` both end at `GET /cube/readyz`, which reads like
duplication and is not: one asks whether an address someone just pasted is the deployment we think
it is — identity, contract, token — and runs once; the other asks whether we can query right now,
runs every fifteen seconds off the stored row, and wants a word short enough for a chip. They share
a classifier (`_why`, the 401 branch) and nothing else.

## Considered options

**Merge them into one probe**, which is what an architecture review of this seam proposed. It lost
to a fact that is invisible from the portal side: the store's `GET /health` returns a hardcoded
`{"ok": true}`, so it is not a readiness flag over the git storage — a broken repo raises and 500s
instead. And `/cube/readyz` is served *through* the store's proxy, so a 200 there already proves
the store is up and the token was accepted. The store's own `/health` therefore contributes exactly
two things: identity, and the contract number. Both matter once, when a human has just typed an
address, and are waste on a recurring dot.

## Consequences

Reversing this is cheap to write and expensive to notice. Merging makes the dot pay two round trips
every fifteen seconds, and makes the wizard's contract-mismatch warning — correct once — a
permanent nag on a page that renders it every time. Neither failure announces itself.
