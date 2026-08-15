"""`proc.run`, against real subprocesses. No docker and no containers: what is checked here is
the streaming contract, which is the same whether the program is `docker` or `python`.
"""

import sys
import time

from analyst_agent.core import proc


async def test_output_arrives_while_the_command_is_still_running():
    """The whole point: a two-minute script that prints as it goes is not two minutes of
    nothing."""
    seen: list[tuple[float, str]] = []
    started = time.monotonic()
    result = await proc.run(
        sys.executable,
        "-c",
        "import time; print('one', flush=True); time.sleep(0.4); print('two')",
        on_output=lambda chunk: seen.append((time.monotonic(), chunk)),
    )

    assert result.ok
    assert "".join(chunk for _, chunk in seen) == result.out
    assert seen[0][0] - started < 0.3, "the first line waited for the whole run"
    assert time.monotonic() - started > 0.4, "the command did not actually run for its 0.4s"


async def test_a_line_longer_than_the_pipe_buffer_still_streams():
    """Why chunks and not lines: `readline` raises past its 64 KiB buffer, and a script printing
    a dataframe without a newline would fail rather than stream."""
    seen: list[str] = []
    result = await proc.run(
        sys.executable, "-c", "print('x' * 200_000, end='')", on_output=seen.append
    )
    assert len(result.out) == 200_000
    assert "".join(seen) == result.out


async def test_stderr_is_streamed_with_stdout_and_still_kept_apart_in_the_result():
    seen: list[str] = []
    result = await proc.run(
        sys.executable,
        "-c",
        "import sys; print('out'); print('err', file=sys.stderr)",
        on_output=seen.append,
    )
    assert result.out.strip() == "out"
    assert result.err.strip() == "err"
    assert "out" in "".join(seen) and "err" in "".join(seen)


async def test_a_streaming_command_is_still_killed_at_its_timeout():
    result = await proc.run(
        sys.executable,
        "-c",
        "import time; time.sleep(5)",
        timeout=0.3,
        on_output=lambda _chunk: None,
    )
    assert result.code == 124


async def test_without_a_callback_nothing_about_a_command_changes():
    result = await proc.run(sys.executable, "-c", "print('hi')")
    assert result.ok
    assert result.out.strip() == "hi"
