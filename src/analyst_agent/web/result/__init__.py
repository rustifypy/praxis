"""One query's rows, kept beside the transcript so a table has something to address.

The event carries only a head of them; this is where the rest is read from, and where a CSV is
built. Labels in the header, not member ids: a person reads a spreadsheet, and `export_result`
writes the ids instead because a script refers to `sales.invoice_total`.

Three modules, in the order a request travels: `routes` is the HTTP surface, `controller` does the
work, `dto` is what goes on the wire. `router` is re-exported here because `server.py` mounts it —
that is the only thing imported from this package's root.
"""

from .routes import router

__all__ = ["router"]
