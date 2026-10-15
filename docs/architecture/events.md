# Events and the transcript

**One flat event stream is the contract between agent and browser.** Two invariants:

- **The event log is the transcript.** Session provenance is rebuilt from it
  (`agent.session.replay`), so nothing lives only in memory.
- **Tools author their own events** through `agent/tools/trace.py` — one `start`/`end` pair — so
  a gate rejection reaches the browser with its reasons.

## The event

`core/events.py`:

```python
class Event(BaseModel):
    type: EventType  # see below
    data: dict = {}
    parent_id: str | None = None
    depth: int = 0
    id: str  # uuid4 hex
```

| Type | `data` | Stored? |
|---|---|---|
| `user` | `{text, files}` | yes |
| `thinking`, `text` | live: `{delta}`; stored: `{text}` (the whole block) | yes |
| `tool_start` | `{tool, label, …}` | yes |
| `tool_end` | `{tool, ok, …}` — errors, warnings, query, sql, rows_total, columns, result_id, chart, preview, … | yes |
| `output` | `{tool, delta}` — a running script's stdout | **no** — the whole of it lands trimmed on `tool_end` |
| `approval` | `{tool, label, script}` — the one event with a body | **no** |
| `proposal` | `{path, text, ref}` — an editor change | yes |
| `compacted` | `{before, after}` | yes |
| `error` | `{message}` | yes |
| `done` | `{turn_id}` | yes |

Each toolset declares the extra keys its events may carry (`EVENT_KEYS`), and a test holds them.
Flow `extract` events carry no `preview` and no `chart`, so no rows reach a run's log.

## Transport

Server-sent events: `data: {json}\n\n` frames, `text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no`. Every turn stream is a **POST**, so the browser reads it with `fetch`,
not `EventSource`. Closing the connection sets the turn's cancel flag.

## Shared seams

All three agents — question chat, model editor, flow chat — share `run_turn`, `api.streamTurn`,
`events.applyEvent`, `Transcript.tsx` and `Approval.tsx`. Each was two copies once, and the copy
always dropped something.

A Flow run shares `run_turn` (with its own sink and checkpointer thread), `api.streamTurn` and
`Approval`, but **not** `Transcript`: a run answers in its files, and `RunStory.tsx` renders it
Phase by Phase from `run.json` and `events.jsonl`, taking only the approval from the live stream.

## Where events are kept

- Chats: the `events` table in `praxis.db`, with `turn_id` and `seq`.
- Flow runs: `runs/<id>/events.jsonl`, the same shape plus `at`, filed under the Phase's folder
  name.
