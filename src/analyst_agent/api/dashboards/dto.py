"""What the dashboard routes send and receive.

Only the two writes are typed. A board on the page is assembled from four tables plus a
compile pass over every tile, and the shape is `ResultChart`'s to read rather than a contract
either side is holding the other to — so `read` answers a dict, as it always has.
"""

from pydantic import BaseModel


class NewDashboard(BaseModel):
    title: str = "New dashboard"


class Rename(BaseModel):
    title: str


class Layout(BaseModel):
    """A layout as the browser posts it. `spec` is checked by `boards.validate_layout`, which
    is the same validator the agent's `set_layout` tool runs."""

    spec: dict = {}
    note: str = ""
