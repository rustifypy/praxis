"""Drive the real sandbox and report what a script can and cannot do.

`make sandbox-check`. This is the other half of the repo's testing rule: `tests/` covers the
deterministic parts — the argv, the path resolution, the CSV export, the trimming — and what a
container actually *does* is checked here, against a live one, rather than mocked.

Writes to a scratch DATA_DIR of its own and removes it afterwards, so it never touches
`~/.analyst`. Model calls happen only with `--ask`.

    make sandbox-check                       the isolation battery, no model calls
    make sandbox-check ARGS="--stale"        also prove recovery from a replaced mount
    make sandbox-check ARGS='--ask "..."'    one real turn against a live source and model
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'  ok  ' if ok else ' FAIL '}] {name}" + (f"  — {detail[:88]}" if detail else ""))
    if not ok:
        failures.append(name)


def tail(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def runner(chat: str):
    """`run_python`, built on a Session with no store — everything here is about what a script can
    do once it runs, and `export_result` is covered by the unit tests."""
    from analyst_agent.agent.session import Session
    from analyst_agent.agent.tools.code import build_code_tools

    session = Session(catalog=None, client=None, grounding=None, chat_id=chat)
    return {t.name: t for t in build_code_tools(session)}["run_python"]


async def isolation(chat: str) -> None:
    """What the sandbox must and must not let a script do."""
    from analyst_agent.sandbox import container, scratch

    run = runner(chat)
    root = scratch.ensure(chat)

    async def script(body: str) -> str:
        return await run.ainvoke({"script": body})

    print("\n— the analysis stack is there —")
    out = await script(
        "import pandas, numpy, scipy, duckdb, polars, pyarrow\nprint(pandas.__version__)"
    )
    check("pandas, numpy, scipy, duckdb, polars, pyarrow import", "exited 0" in out, tail(out))

    out = await script("import pandas as pd\nprint(int(pd.Series([1, 2, 3]).sum()))")
    check("a computation runs and returns its answer", "exited 0" in out and "6" in out, tail(out))

    out = await script(
        "import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.savefig('chart.png')\nprint('drew')"
    )
    check("matplotlib writes a png on a read-only root", (root / "chart.png").is_file(), tail(out))

    print("\n— the isolation holds —")
    out = await script(
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), 3)\n"
        "    print('REACHED THE NETWORK')\n"
        "except OSError as exc:\n"
        "    print('refused:', exc)"
    )
    check("no network egress", "REACHED" not in out and "refused:" in out, tail(out))

    out = await script("import subprocess\nsubprocess.run(['pip', 'install', 'requests'])")
    check("nothing can be installed at run time", "Successfully installed" not in out, tail(out))

    out = await script(
        "try:\n    open('/nope', 'w')\n    print('WROTE TO ROOT')\n"
        "except OSError as exc:\n    print('refused:', exc)"
    )
    check("the root filesystem is read-only", "WROTE" not in out, tail(out))

    out = await script("import os\nprint(os.getuid())")
    check("not running as root", "exited 0" in out and tail(out) != "0", f"uid {tail(out)}")

    out = await script(
        "caps = [x for x in open('/proc/self/status') if x.startswith('CapEff')]\nprint(*caps)"
    )
    check("every capability is dropped", "0000000000000000" in out, tail(out))

    out = await script("print(open('/sys/fs/cgroup/memory.max').read().strip())")
    check("the memory limit is enforced by the cgroup", tail(out).isdigit(), tail(out))

    out = await script(
        "import pathlib\n"
        "found = sorted(p.name for p in pathlib.Path('/').iterdir() if 'analyst' in p.name)\n"
        "print('found:', found)"
    )
    check("the host data directory is not mounted", "analyst.db" not in out, tail(out))

    print("\n— limits and output —")
    print("  (a script that has to be killed — about 15s)")
    result = await container.execute(chat, "sleep 60", timeout=10)
    check("a long script is killed from inside", result.code in (124, 137), f"exit {result.code}")
    check("the container survives the kill", await container.state() == "running")

    out = await script("print('x' * 20000)")
    check("long output is trimmed and pointed at a log", "the whole log is at" in out)
    logs = list((root / scratch.LOGS).glob("run-*.log"))
    check("the log holds the whole output", bool(logs) and logs[0].stat().st_size > 19_000)

    saved = sorted(p.name for p in (root / scratch.SCRIPTS).glob("run-*.py"))
    check("every script was kept as evidence", len(saved) >= 10, f"{len(saved)} saved")


async def stale_mount(chat: str) -> None:
    """The mount root replaced under a running container. Every exec then fails to `chdir` until
    the container is rebuilt, and docker says so on *stdout* — which is how the recovery came to be
    written against `err` and silently do nothing."""
    from analyst_agent.core.config import settings
    from analyst_agent.sandbox import container, scratch

    print("\n— recovery from a replaced mount —")
    check("the container is up to begin with", await container.state() == "running")
    shutil.rmtree(settings.dir_path / scratch.ROOT)
    scratch.ensure(chat)

    out = await runner(chat).ainvoke({"script": "import os\nprint(os.getcwd())"})
    check("a script still succeeds", "exited 0" in out and chat in out, tail(out))
    check("the mount is live again", container.EXEC_START_FAILED not in out)


async def one_turn(question: str) -> None:
    """A real model driving the whole surface: query the active source, export, compute. The only
    path here that spends money, and the only one that needs a source to be up."""
    from analyst_agent.agent.turn import stream_turn
    from analyst_agent.cube.client import CubeClient
    from analyst_agent.sandbox import scratch
    from analyst_agent.transcript import ChatStore, checkpointer

    print(f"\n— one real turn —\n  {question}\n")
    store = ChatStore()
    await store.init()
    chat = await store.create("sandbox check")
    answer = ""
    async with checkpointer() as saver, CubeClient.active() as cube:
        async for event in stream_turn(chat.id, question, store, saver, cube):
            data = event.data
            if event.type == "tool_start":
                print(f"  → {data['tool']}: {str(data.get('label', ''))[:76]}")
            elif event.type == "tool_end":
                code = f" exit={data['exit_code']}" if "exit_code" in data else ""
                note = f" {data['note']}" if data.get("note") else ""
                print(f"  ← {data['tool']} ok={data['ok']}{code}{note}")
                if data.get("errors"):
                    print(f"      {data['errors']}")
            elif event.type == "text":
                answer += data.get("delta", "")
            elif event.type == "error":
                check("the turn ran without an error event", False, data["message"])

    print(f"\n{answer.strip()[:900]}\n")
    check("the agent produced an answer", bool(answer.strip()))
    # Not asserted: that it exported anything. The question is yours, so whether the sandbox was
    # the right tool is the agent's call — and refusing to compute over a query that came back
    # empty is the correct answer, not a missing step. Reported so you can judge it yourself.
    print(f"  workspace: {scratch.listing(chat.id, '**/*') or 'nothing written'}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ask", metavar="QUESTION", help="also run one real turn (spends tokens)")
    parser.add_argument("--stale", action="store_true", help="also check stale-mount recovery")
    parser.add_argument("--keep", action="store_true", help="leave the scratch DATA_DIR in place")
    args = parser.parse_args()

    # Set before anything imports settings, so nothing here can reach ~/.analyst.
    data_dir = tempfile.mkdtemp(prefix="sandbox-check-")
    os.environ["DATA_DIR"] = data_dir

    from analyst_agent.sandbox import container

    print(f"data dir: {data_dir}\nimage:    {container.image_tag()}")
    print("(the first run builds the image, which takes a few minutes)\n")

    result = await container.rebuild()
    check("the container starts", result.ok, result.message)
    if not result.ok:
        return 1

    chat = "sandboxcheck01"
    try:
        await isolation(chat)
        if args.stale:
            await stale_mount(chat)
        if args.ask:
            await one_turn(args.ask)
    finally:
        await container.remove()
        if args.keep:
            print(f"\nkept: {data_dir}")
        else:
            shutil.rmtree(data_dir, ignore_errors=True)

    print(f"\n{len(failures)} failed: {', '.join(failures)}" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
