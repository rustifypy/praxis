"""What the agent is told about a file it was given.

The profiler itself runs in a container and is checked by `make sandbox-check`. What is here is
everything around it: that a file with no readable shape is still named, and that what reaches the
prompt is the shape rather than the file.
"""

import pytest

from analyst_agent.core.proc import Result
from analyst_agent.sandbox import container, profile, scratch

CHAT = "profile00001"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    from analyst_agent.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    return tmp_path


def a_profile(**kwargs) -> profile.Profile:
    return profile.Profile(
        path="uploads/orders.csv",
        bytes=2048,
        rows=1000,
        columns=[
            profile.ColumnProfile(
                name="order_id", type="BIGINT", distinct=1000, min="1", max="1000", unique=True
            ),
            profile.ColumnProfile(
                name="customer", type="VARCHAR", distinct=12, nulls=0.02, min="ACME", max="Zinc"
            ),
        ],
        head=[{"order_id": "1", "customer": "ACME"}],
        **kwargs,
    )


def test_the_prompt_carries_the_shape_and_not_the_file():
    """A 200 MB CSV cannot go into a context. The summary is what makes an upload usable without
    a `run_python` to discover the columns first."""
    said = a_profile().as_prompt()
    assert "uploads/orders.csv" in said
    assert "1,000 rows × 2 columns" in said
    assert "order_id (BIGINT) — 1,000 distinct, unique" in said
    assert "2% null" in said
    # The head is a sample, and a sample is three rows rather than the file.
    assert said.count("ACME") == 2


def test_a_file_with_no_readable_shape_is_still_named():
    said = profile.Profile(
        path="uploads/notes.pdf", bytes=12, note="IOError: not a table"
    ).as_prompt()
    assert "uploads/notes.pdf" in said
    assert "IOError" in said


def test_an_upload_with_no_cached_profile_is_still_offered_to_the_agent():
    """The listing is the authority and the cache is only the shape. A file the sandbox never
    read is one the agent can still open."""
    scratch.save_upload(CHAT, "orders.csv", b"a,b\n1,2\n")
    saved = profile.saved(CHAT)
    assert [p.path for p in saved] == ["uploads/orders.csv"]
    assert saved[0].note == "not profiled"


async def test_a_sandbox_that_will_not_start_is_a_note_rather_than_a_failed_upload(monkeypatch):
    async def down() -> Result:
        return Result(127, "", "docker not found on PATH")

    monkeypatch.setattr(container, "ensure_up", down)
    path = scratch.save_upload(CHAT, "orders.csv", b"a,b\n1,2\n")

    read = await profile.of(CHAT, path)
    assert read.rows == 0
    assert "docker not found" in read.note
    # Cached anyway, so the prompt says the same thing on the next turn without asking again.
    assert profile.saved(CHAT)[0].note == read.note


async def test_output_that_is_not_json_is_reported_rather_than_raised(monkeypatch):
    """The script's own errors come back *as* a note inside its JSON. This is the case where it
    never got to print — killed at the timeout, or the container gone."""

    async def up() -> Result:
        return Result(0, "", "")

    async def killed(chat_id: str, command: str, timeout: float = 0) -> Result:
        return Result(124, "", "")

    monkeypatch.setattr(container, "ensure_up", up)
    monkeypatch.setattr(container, "execute", killed)
    path = scratch.save_upload(CHAT, "orders.csv", b"a,b\n1,2\n")

    read = await profile.of(CHAT, path)
    assert "exit 124" in read.note


def test_the_profiler_and_its_answers_are_hidden_from_the_workspace_listing():
    """Otherwise the profiler's own script reads as a file the run wrote, and every upload would
    put an artifact block in the transcript."""
    scratch.save_upload(CHAT, "orders.csv", b"a,b\n")
    (scratch.ensure(CHAT) / scratch.PROFILES).mkdir(parents=True, exist_ok=True)
    (scratch.ensure(CHAT) / scratch.PROFILES / "profiler.py").write_text("print(1)")
    assert scratch.listing(CHAT, "**/*") == ["uploads/orders.csv"]
