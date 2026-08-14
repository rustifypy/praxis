"""The four toolsets: `query` answers questions, `dashboards` authors boards, `code` computes
over a query's rows in the sandbox, `editor` changes a source's model. No module here imports
another; `build.py` composes them.

`query`, `dashboards` and `code` are composed into one agent, so `run_query` and a Python
interpreter are in the same hands. The gate stays load-bearing because the sandbox has no
network: it cannot reach Cube or the warehouse, and `code.export_result` is the only way rows
reach it.

A tool lives here wherever the state it edits lives, and imports the store it writes
through. The other direction — a feature package owning its own tools — makes `build.py`
reach sideways into every package that has any, and makes each of them depend on the event
writer in order to narrate itself.
"""
