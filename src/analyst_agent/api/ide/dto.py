"""What the editor sends and receives.

`extra="forbid"` on `Draft` and `FileBody` on purpose: a renamed field in the browser fails loudly
here instead of acting on the default ref, which is main — the one ref nothing may write to.
"""

from pydantic import BaseModel, ConfigDict

from ...core.deployment import MAIN


class NewRef(BaseModel):
    name: str


class Message(BaseModel):
    ref: str = MAIN


class Draft(BaseModel):
    """Which ref a save, a check, a discard or a publish is about.

    `extra="forbid"` so a renamed field in the browser fails loudly here instead of acting on the
    default ref.
    """

    model_config = ConfigDict(extra="forbid")

    ref: str
    message: str = "edit model"
    # Discard only. None is the whole draft; a path is one file.
    path: str | None = None


class FileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str
    text: str


# --- shared -------------------------------------------------------------------


# The kinds are the workspace's; the codes are HTTP's.


class Question(BaseModel):
    """One message to the editor agent.

    Nothing about the model travels with it any more: the agent reads the ref's draft, which is
    where every unpublished edit already lives.
    """

    question: str
