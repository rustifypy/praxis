# The three service clients share no transport module

> **Superseded in part — there are two clients now, not three.** `cube/client.py` and
> `modelstore/client.py` were merged into one `ModelStore`. That is *not* the option refused
> below, which was a shared transport module *underneath* clients that stay separate. The reason
> is narrower and was not considered here: Cube and the store are not two services sharing a
> helper, they are **one address**. `store_url` serves both, the store proxies Cube at
> `/refs/{ref}/…` and mints its JWT, so the two clients were two connection pools and two error
> types for one deployment.
>
> What the merge kept is the finding that mattered: the query half still **returns** rather than
> raising on a status, because Cube's rejection is the gate's material, so `_ask` and `_send` are
> still two methods and not one parameterised base. `StoreError.missing` kept its three callers;
> `CubeError` was deleted rather than unified, its discriminators having had none.
> `StoreError.where` (`store` | `cube`) is new, and is the one thing a caller genuinely needed to
> ask across the seam — which is the reopen condition named at the end of this document.
>
> **`sandbox/client.py` is untouched and the decision below still governs it.** Two clients, two
> `_send`s, one of them raising on 4xx. Reopen again if a third appears.

`cube/client.py`, `modelstore/client.py` and `sandbox/client.py` each rstrip a URL, build one
`httpx.AsyncClient`, compose a bearer header, implement `__aenter__`/`aclose`, and turn
`httpx.RequestError` into their own error type. Roughly twenty lines, three times over. They stay
that way.

## Considered options

**One tier-1 transport module underneath them, raising a single error carrying `status` and
`unreachable`** — proposed by an architecture review of this seam, on the argument that three
adapters already exist, all three re-derive the same band, and the three error types cannot be
asked the same question.

The last clause was true and irrelevant. Nothing downstream asks:

| discriminator | production callers |
|---|---|
| `StoreError.missing` | 3 — `modelgen/read.py`, `modelgen/publish.py`, `model_files` |
| `CubeError.unreachable` | 0 (two tests) |
| `SandboxError.unconfigured` | 0 (one test) |

`CubeError.unreachable` existed for `health.serving`, which no longer exists;
`SandboxError.unconfigured` worked by matching a message literal written in `open_sandbox`. Both
were deleted rather than unified, along with `CubeError`'s unread `status` and its `cause` field —
`raise ... from exc` already preserves the original.

What was left to share is one block: the raise-on-4xx `_send`, identical in the store and sandbox
clients bar the error class. Cube cannot join it — its `_send` returns `(status, body, text)` and
never raises on status, because the gate needs Cube's rejection as data rather than as an
exception. So sharing means a base class parameterised by error type, existing to hold one method,
for two of the three clients.

## Consequences

The duplication is real and stays visible, which is the cost. Two things follow from accepting it:
the identical non-JSON fallbacks are allowed to differ (`{"text": r.text}` in the store, `{}` in
the sandbox — both unreachable today, on services that version independently), and a future
divergence in the 4xx block will not be caught by a compiler.

Reopen this if either changes: a fourth client appears, or a caller genuinely needs to ask
"unreachable, or refused?" across two of these services at once. The second is the one to watch —
it is the question `reachable.py` answers, and it answers it without a client on purpose
(see 0001), so the pressure would have to come from somewhere else.
