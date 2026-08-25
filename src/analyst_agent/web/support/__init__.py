"""Infrastructure every noun needs, and no noun owns.

Two modules, and the bar for a third is high: something belongs here only when more than one of
the HTTP nouns needs it *and* it is not about any of them. `state` holds what lives on
`app.state`; `stream` holds the one SSE wire format.

**Nothing that maps a service's failure onto a status code lives here.** That was the plan for
this package and it was wrong: `StoreError` → 502, `SandboxError` → its own status or 503, the
workspace's kinds → four different codes, and Cube being down → sometimes 502 and sometimes a
field on a 200. Those are five different rules with one caller each, not five copies of one rule,
and collecting them here would have produced a module that every noun imports and none of them
can read.
"""
