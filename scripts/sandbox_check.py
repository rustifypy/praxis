"""Drive the real sandbox and report what a script can and cannot do.

`make sandbox-check`. This is the other half of the repo's testing rule: `tests/` covers the
deterministic parts — the argv, the path resolution, the CSV export, the trimming — and what a
container actually *does* is checked here, against a live one, rather than mocked.

Writes to a scratch DATA_DIR of its own and removes it afterwards, so it never touches
`~/.analyst`. Model calls happen only with `--ask`.

    make sandbox-check                       the isolation battery, no model calls
    make sandbox-check ARGS="--stale"        also prove recovery from a replaced mount
    make sandbox-check ARGS='--ask "..."'    one real turn against a live source and model
    make sandbox-check ARGS='--ask "..." --file orders.csv'   with a file attached to it
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


async def files(chat: str) -> None:
    """The upload loop, from the file arriving to the chart coming back out. The routes either
    end are unit-tested; what needs a container is the profiler and the artifact diff."""
    from analyst_agent.sandbox import profile, scratch

    print("\n— a file in, a file out —")
    rows = "\n".join(f"{i},cust-{i % 4},{i * 1.5}" for i in range(1, 201))
    path = scratch.save_upload(chat, "orders.csv", f"order_id,customer,amount\n{rows}\n".encode())

    read = await profile.of(chat, path)
    check("an uploaded CSV is profiled", read.rows == 200 and not read.note, read.note)
    check(
        "its columns are named and typed",
        [c.name for c in read.columns] == ["order_id", "customer", "amount"],
        ", ".join(f"{c.name} {c.type}" for c in read.columns),
    )
    key = next((c for c in read.columns if c.unique), None)
    check("a candidate key is found exactly", key is not None and key.name == "order_id")
    check("a low-cardinality column is not one", read.columns[1].distinct == 4)
    check("the shape is what reaches the prompt", "200 rows × 3 columns" in read.as_prompt())

    run = runner(chat)
    out = await run.ainvoke(
        {
            "script": "import pandas as pd, matplotlib.pyplot as plt\n"
            "frame = pd.read_csv('uploads/orders.csv')\n"
            "frame.groupby('customer')['amount'].sum().plot(kind='bar')\n"
            "plt.savefig('by-customer.png')\n"
            "frame.to_parquet('orders.parquet')\n"
            "print('total', frame.amount.sum())"
        }
    )
    check("a script reads the upload", "exited 0" in out and "total" in out, tail(out))
    check("what it wrote is named back", "wrote by-customer.png, orders.parquet" in out, tail(out))

    # The second run makes no new name, which is what `snapshot` reads mtimes for.
    again = await run.ainvoke(
        {
            "script": "import matplotlib.pyplot as plt\n"
            "plt.plot([1, 2])\nplt.savefig('by-customer.png')\nprint('redrew')"
        }
    )
    check("a redrawn file is reported again", "wrote by-customer.png" in again, tail(again))

    parquet = await profile.of(chat, "orders.parquet")
    check("a parquet is profiled too", parquet.rows == 200 and not parquet.note, parquet.note)

    unreadable = scratch.save_upload(chat, "notes.pdf", b"%PDF-1.4 not a table")
    said = await profile.of(chat, unreadable)
    check("a file with no shape is a note, not a failure", bool(said.note), said.note)


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


async def one_turn(question: str, attach: list[str]) -> None:
    """A real model driving the whole surface: query the active source, export, compute, and read
    whatever was attached. The only path here that spends money, and the only one that needs a
    source to be up."""
    from pathlib import Path

    from analyst_agent.agent.turn import stream_turn
    from analyst_agent.cube.client import CubeClient
    from analyst_agent.sandbox import profile, scratch
    from analyst_agent.transcript import ChatStore, checkpointer

    print(f"\n— one real turn —\n  {question}\n")
    store = ChatStore()
    await store.init()
    chat = await store.create("sandbox check")

    # What `POST /files` does, without the route: the bytes land in the workspace and the profile
    # goes into the next turn's prompt. The paths ride on the user event.
    files = []
    for host in attach:
        path = scratch.save_upload(chat.id, Path(host).name, Path(host).read_bytes())
        read = await profile.of(chat.id, path)
        print(f"  attached {path}: {read.note or f'{read.rows} rows × {len(read.columns)} cols'}")
        files.append(path)

    answer = ""
    async with checkpointer() as saver, CubeClient.active() as cube:
        async for event in stream_turn(chat.id, question, store, saver, cube, None, files):
            data = event.data
            if event.type == "tool_start":
                print(f"  → {data['tool']}: {str(data.get('label', ''))[:76]}")
            elif event.type == "tool_end":
                code = f" exit={data['exit_code']}" if "exit_code" in data else ""
                note = f" {data['note']}" if data.get("note") else ""
                wrote = f" wrote {', '.join(data['wrote'])}" if data.get("wrote") else ""
                print(f"  ← {data['tool']} ok={data['ok']}{code}{note}{wrote}")
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
    parser.add_argument(
        "--file", metavar="PATH", action="append", default=[], help="attach a file to --ask"
    )
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
        await files(chat)
        if args.stale:
            await stale_mount(chat)
        if args.ask:
            await one_turn(args.ask, args.file)
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
