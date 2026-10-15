"""What the flows routes accept and return.

Reads answer with plain dicts assembled in `flows/`, because the shape of a Flow and of a Run is
that package's to decide and restating it here would be a second definition to keep true. What is
modelled is what arrives: a body a browser sends has to be refused before it reaches a folder.
"""

from pydantic import BaseModel, Field


class NewFlow(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    description: str = ""


class FileBody(BaseModel):
    text: str


class NewRun(BaseModel):
    # The **Brief**: the one thing that varies between two Runs of one Flow. Free text and not a
    # form, because a form would be a workflow builder's parameter list by another name.
    brief: str = ""


class Question(BaseModel):
    question: str


class Written(BaseModel):
    """The result of a write, with the Flow re-checked. `ok` is about the *Flow*, not the write:
    the file landed either way, and a Flow that will not run yet is the normal state of one being
    built."""

    path: str
    ok: bool
    errors: list[str] = []
    warnings: list[str] = []


class Started(BaseModel):
    run_id: str
    current: str


class Decided(BaseModel):
    decided: bool


class Stopped(BaseModel):
    stopped: bool


class Removed(BaseModel):
    removed: bool
