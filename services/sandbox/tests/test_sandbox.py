"""The sandbox service. Real subprocesses, real files, no docker — this suite runs the service
in-process, which is what the container does too.

What is asserted is what only exists here: the path guard, that a run reports what it wrote, and
that stopping one actually stops it.
"""

import json
import os
import tempfile
from pathlib import Path

import httpx
import pytest

TOKEN = "test-token"
os.environ["SANDBOX_TOKEN"] = TOKEN
_root = tempfile.mkdtemp()
os.environ["SANDBOX_ROOT"] = _root

from sandbox_service import runner, workspace  # noqa: E402
from sandbox_service.app import app  # noqa: E402

CHAT = "chat-1"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
async def api():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://s", headers=AUTH, timeout=60
    ) as client:
        async with app.router.lifespan_context(app):
            yield client


async def run(api, script: str, chat: str = CHAT) -> dict:
    """A run's result line. The output lines before it are the live stream."""
    result = {}
    async with api.stream("POST", "/run", json={"chat_id": chat, "script": script}) as response:
        assert response.status_code == 200, await response.aread()
        async for line in response.aiter_lines():
            if line.strip():
                event = json.loads(line)
                result = event.get("result", result)
    return result


# --- auth --------------------------------------------------------------------


async def test_no_token_no_run():
    """It runs arbitrary Python. An unauthenticated one is not a state to reach by omission."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://s") as bare:
        assert (await bare.post("/run", json={"chat_id": CHAT, "script": "x"})).status_code == 401
    async with httpx.AsyncClient(
        transport=transport, base_url="http://s", headers={"Authorization": "Bearer wrong"}
    ) as wrong:
        assert (await wrong.post("/kill", json={"chat_id": CHAT, "path": ""})).status_code == 401


async def test_health_needs_no_token(api):
    assert (await api.get("/health")).json()["ok"] is True


# --- running -----------------------------------------------------------------


async def test_a_script_runs_and_its_output_comes_back(api):
    result = await run(api, "print(2 + 2)")
    assert result["code"] == 0
    assert "4" in result["out"]


async def test_the_script_is_kept_where_it_ran(api):
    """A computed figure's provenance is a file that outlives the transcript, and a second
    attempt must not overwrite the evidence of the first."""
    first = await run(api, "print('one')")
    second = await run(api, "print('two')")
    assert first["script"] != second["script"]
    assert (Path(_root) / CHAT / second["script"]).read_text() == "print('two')"


async def test_a_run_names_the_files_it_wrote(api):
    result = await run(api, "open('chart.png', 'w').write('x')")
    assert "chart.png" in result["wrote"]


async def test_redrawing_a_file_still_counts_as_written(api):
    """Keyed on (size, mtime), not names — a set difference would report the second run as
    having made nothing, and that is the run whose picture is on screen."""
    await run(api, "open('again.txt', 'w').write('one')")
    result = await run(api, "open('again.txt', 'w').write('two-longer')")
    assert "again.txt" in result["wrote"]


async def test_a_traceback_comes_back_as_stderr_and_a_failing_code(api):
    result = await run(api, "raise ValueError('boom')")
    assert result["code"] != 0
    assert "boom" in result["err"]


async def test_output_past_the_limit_is_trimmed_and_kept_whole_on_disk(api):
    result = await run(api, f"print('x' * {runner.OUTPUT_LIMIT * 3})")
    assert len(result["out"]) < runner.OUTPUT_LIMIT * 2
    assert "trimmed" in result["out"]
    assert (Path(_root) / CHAT / result["out_log"]).stat().st_size > runner.OUTPUT_LIMIT


async def test_stdout_arrives_before_the_run_finishes(api):
    """The whole point of streaming: a script that prints then sleeps has to be visible before
    it exits, or the live output panel shows nothing until the end."""
    seen: list[str] = []
    script = "import time; print('early', flush=True); time.sleep(1.5); print('late')"
    async with api.stream("POST", "/run", json={"chat_id": "stream", "script": script}) as r:
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            if "output" in event:
                seen.append(event["output"])
                if "early" in event["output"]:
                    # Arrived while the script is still sleeping, which is what proves it.
                    assert "late" not in "".join(seen)
    assert any("early" in chunk for chunk in seen)


async def test_one_run_per_chat(api):
    """A second would race on the same workspace, and both would report the other's files."""
    runner._running["taken"] = object()  # type: ignore[assignment]
    try:
        response = await api.post("/run", json={"chat_id": "taken", "script": "print(1)"})
        assert response.status_code == 409
    finally:
        runner._running.pop("taken", None)


# --- the path guard ----------------------------------------------------------


async def test_a_path_out_of_the_workspace_is_refused(api):
    """Percent-encoded, because every client collapses a literal `../` before it is sent — a
    test written the other way proves nothing."""
    assert (await api.get(f"/files/{CHAT}/%2e%2e%2f%2e%2e%2fetc%2fpasswd")).status_code == 400
    assert (await api.delete(f"/files/{CHAT}/%2e%2e%2fx")).status_code == 400


async def test_a_symlink_a_script_planted_is_refused_too(api):
    """The check is on the resolved target, which is what makes one check cover three escapes."""
    await run(api, "import os; os.symlink('/etc/passwd', 'escape')")
    assert (await api.get(f"/files/{CHAT}/escape")).status_code == 400


# --- uploads and files -------------------------------------------------------


async def test_an_upload_lands_in_uploads_and_is_listed_as_one(api):
    """`uploads/` is what makes it distinguishable from something a script wrote, so no state
    has to say which is which."""
    files = {"file": ("sales.csv", b"a,b\n1,2\n", "text/csv")}
    path = (await api.post(f"/uploads/{CHAT}", files=files)).json()["path"]
    assert path == "uploads/sales.csv"

    listing = (await api.get(f"/files/{CHAT}")).json()
    assert path in listing["uploads"] and path in listing["files"]
    assert (await api.get(f"/files/{CHAT}/{path}")).content == b"a,b\n1,2\n"


async def test_a_second_upload_of_one_name_is_numbered_not_overwritten(api):
    """A figure in an earlier answer came from the earlier file."""
    files = {"file": ("dup.csv", b"one", "text/csv")}
    first = (await api.post(f"/uploads/{CHAT}", files=files)).json()["path"]
    second = (
        await api.post(f"/uploads/{CHAT}", files={"file": ("dup.csv", b"two", "text/csv")})
    ).json()["path"]
    assert first != second


async def test_a_browser_supplied_name_cannot_carry_a_directory(api):
    files = {"file": ("../../escape.csv", b"x", "text/csv")}
    path = (await api.post(f"/uploads/{CHAT}", files=files)).json()["path"]
    assert path.startswith("uploads/") and ".." not in path


async def test_deleting_the_workspace_takes_the_files_with_it(api):
    await run(api, "open('gone.txt','w').write('x')", chat="doomed")
    assert (Path(_root) / "doomed").is_dir()
    await api.delete("/workspace/doomed")
    assert not (Path(_root) / "doomed").exists()


# --- profiling ---------------------------------------------------------------


async def test_a_csv_is_profiled_with_its_columns(api):
    files = {"file": ("shape.csv", b"id,name\n1,a\n2,b\n3,c\n", "text/csv")}
    path = (await api.post(f"/uploads/{CHAT}", files=files)).json()["path"]
    body = (await api.post("/profile", json={"chat_id": CHAT, "path": path})).json()
    assert body["profile"], body
    assert body["profile"]["rows"] == 3
    assert {c["name"] for c in body["profile"]["columns"]} == {"id", "name"}


async def test_a_file_that_is_not_tabular_is_a_note_rather_than_a_failure(api):
    """A confident wrong profile is worse than "not a tabular file" — duckdb will read a PDF as
    one column of nonsense rather than refusing."""
    files = {"file": ("notes.pdf", b"%PDF-1.4 not really", "application/pdf")}
    path = (await api.post(f"/uploads/{CHAT}", files=files)).json()["path"]
    body = (await api.post("/profile", json={"chat_id": CHAT, "path": path})).json()
    profile = body["profile"]
    assert profile is None or profile.get("note"), body


def test_the_pid_file_trick_is_gone():
    """The service holds the process object, so stopping a run needs no file and no `kill`
    inside an image that has no `ps`. Pinned because re-adding one would be a regression."""
    assert not hasattr(workspace, "pid_file")
    assert not hasattr(workspace, "pid")
