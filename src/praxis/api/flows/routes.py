"""HTTP for Flows: the definition, the Runs, and the chat that authors both.

**Two routers, and the order they are registered in matters.** `/{flow}` is as broad as a path
parameter gets, so it would answer for `/chats` — the same collision that made `onboarding` run a
second router for `POST /pipeline/{stage}`. `chats` is registered first in `server.py`, and this is
the note saying why a reader must not reorder them.

A Run is not a request. `advance` streams while Phases execute and returns when it reaches a
**Gate**, so the browser calls it again to go on; the Run's own state is in `run.json` throughout,
so a closed tab loses the stream and not the Run.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from ...agent.session import Approvals
from ...core import transcript
from ...flows import authoring, contract, layout, runner, state
from ...flows import chat as flow_chat
from ..support.preview import FilePreview
from ..support.stream import turn_stream
from . import controller, dto

router = APIRouter(prefix="/api/flows")
# Its own router for the reason `onboarding.pipeline` has one: registered ahead of `router`, so a
# flow can never be named in a way that swallows the chat surface.
chats = APIRouter(prefix="/api/flows/chats")

# `chats` is a path segment under this prefix, so it cannot also be a flow's folder name.
RESERVED = frozenset({"chats"})


# --- the flow ----------------------------------------------------------------


@router.get("")
async def list_flows() -> list[dict]:
    return [flow_chat.summary(name) for name in runner.flows()]


@router.post("")
async def create_flow(body: dto.NewFlow) -> dict:
    if not layout.FLOW_NAME.match(body.name):
        raise HTTPException(400, "a flow name is lower case letters, digits and hyphens")
    if body.name in RESERVED:
        raise HTTPException(400, f"{body.name!r} is reserved")
    directory = layout.flow_dir(runner.root(), body.name)
    if directory.exists():
        raise HTTPException(409, f"{body.name} already exists")
    # Scaffolded through `authoring`, which is where what a new Flow *is* lives. The route had
    # its own copy for an hour and the two already disagreed about the opening line of CONTEXT.md.
    authoring.scaffold(runner.root(), body.name, body.title, body.description)
    return flow_chat.summary(body.name)


@router.get("/{flow}")
async def read_flow(flow: str) -> dict:
    return controller.flow_detail(flow)


@router.get("/{flow}/files")
async def list_files(flow: str) -> list[str]:
    return controller.definition_files(controller.flow_or_404(flow))


@router.get("/{flow}/files/{path:path}", response_class=PlainTextResponse)
async def read_file(flow: str, path: str) -> str:
    directory = controller.flow_or_404(flow)
    if refusal := layout.writable(path):
        raise HTTPException(400, refusal)
    target = directory / path
    if not layout.contained(directory, target) or not target.is_file():
        raise HTTPException(404, f"{path} is not there")
    return target.read_text(errors="replace")


@router.put("/{flow}/files/{path:path}", response_model=dto.Written)
async def write_file(flow: str, path: str, body: dto.FileBody) -> dict:
    return controller.write_file(controller.flow_or_404(flow), path, body.text)


@router.delete("/{flow}/files/{path:path}", response_model=dto.Written)
async def delete_file(flow: str, path: str) -> dict:
    directory = controller.flow_or_404(flow)
    if refusal := layout.writable(path):
        raise HTTPException(400, refusal)
    target = directory / path
    if not layout.contained(directory, target) or not target.is_file():
        raise HTTPException(404, f"{path} is not there")
    target.unlink()
    # An empty `NN_name/` is a Phase with no contract, and `contract.load` refuses the whole Flow
    # for it — so the folder goes when its last file does.
    if target.parent != directory and not any(target.parent.iterdir()):
        target.parent.rmdir()
    _, report = contract.load(directory)
    return {"path": path, "ok": report.ok, "errors": report.errors, "warnings": report.warnings}


# --- the runs ----------------------------------------------------------------


@router.get("/{flow}/runs")
async def list_runs(flow: str) -> list[dict]:
    return controller.run_summaries(controller.flow_or_404(flow))


@router.post("/{flow}/runs", response_model=dto.Started)
async def begin_run(flow: str, body: dto.NewRun) -> dict:
    controller.flow_or_404(flow)
    run, report = runner.begin(flow, body.brief)
    if run is None:
        # 422 and not 400: the body was fine and the *folder* is what will not run. The errors are
        # the same lines the authoring agent is given.
        raise HTTPException(422, "; ".join(report.errors) or "this flow cannot run")
    return {"run_id": run.id, "current": run.current}


@router.get("/{flow}/runs/{run_id}")
async def read_run(flow: str, run_id: str) -> dict:
    return controller.run_detail(controller.flow_or_404(flow), run_id)


@router.get("/{flow}/runs/{run_id}/events")
async def read_run_events(flow: str, run_id: str) -> list[dict]:
    return controller.run_events(controller.flow_or_404(flow), run_id)


@router.post("/{flow}/runs/{run_id}/advance")
async def advance_run(flow: str, run_id: str, request: Request):
    """Run Phases until the next **Gate**, streaming events as they happen.

    Keyed on `flow/run_id` in the same cancel registry a chat turn uses: the mechanism is
    identical — a closed tab must not leave a Phase burning model calls, nor a script waiting on
    an approval nobody is there to give.
    """
    directory = controller.flow_or_404(flow)
    _, current = controller.run_or_404(directory, run_id)
    if current.status in ("done", "failed"):
        raise HTTPException(409, f"this run is {current.status}")
    key = f"{flow}/{run_id}"
    approvals = Approvals()
    request.app.state.approvals[key] = approvals

    def open_run(cancel: asyncio.Event):
        return runner.advance(flow, run_id, cancel, approvals)

    try:
        return turn_stream(request, key, open_run)
    except runner.Busy as exc:
        raise HTTPException(409, f"a run of {flow} is already going ({exc})") from exc


@router.post("/{flow}/runs/{run_id}/stop", response_model=dto.Stopped)
async def stop_run(flow: str, run_id: str, request: Request) -> dict:
    cancel = request.app.state.cancels.get(f"{flow}/{run_id}")
    if cancel is not None:
        cancel.set()
    return {"stopped": cancel is not None}


@router.post("/{flow}/runs/{run_id}/approve", response_model=dto.Decided)
async def approve_script(flow: str, run_id: str, body: dto.Choice, request: Request) -> dict:
    """Answer the approval a Phase's script is waiting on.

    The Run blocks until this arrives — the queue is in memory inside the Phase, so an answer that
    never comes costs that Phase and not the Run's record. See `docs/adr/0006`.
    """
    queue = request.app.state.approvals.get(f"{flow}/{run_id}")
    if queue is None or not queue.answer(body.choice):
        raise HTTPException(409, "nothing is waiting for approval on this run")
    return {"decided": True}


@router.get("/{flow}/runs/{run_id}/artifacts/{path:path}", response_class=PlainTextResponse)
async def read_artifact(flow: str, run_id: str, path: str) -> str:
    directory = controller.flow_or_404(flow)
    run, current = controller.run_or_404(directory, run_id)
    return controller.artifact(run, current, path, writing=False).read_text(errors="replace")


@router.get("/{flow}/runs/{run_id}/preview/{path:path}", response_model=FilePreview)
async def preview_artifact(flow: str, run_id: str, path: str) -> FilePreview:
    """One output's head and row count — what the Run page shows under each Phase, so a person
    sees what landed without opening the file."""
    directory = controller.flow_or_404(flow)
    run, current = controller.run_or_404(directory, run_id)
    return controller.artifact_preview(run, current, path)


@router.put("/{flow}/runs/{run_id}/artifacts/{path:path}", response_model=dto.Written)
async def write_artifact(flow: str, run_id: str, path: str, body: dto.FileBody) -> dict:
    """Edit a Phase's output at its **Gate**. This is what the Gate is for.

    Whatever is here when the next Phase runs is what it reads: `_gather` pushes declared Working
    inputs to the sandbox on every Phase, so an edit made now is the version that arrives.
    """
    directory = controller.flow_or_404(flow)
    run, current = controller.run_or_404(directory, run_id)
    target = controller.artifact(run, current, path, writing=True)
    target.write_text(body.text)
    # Anything downstream that had already run no longer follows from this. Nothing has, at a
    # Gate — but a re-run reaches this same code, and then it has.
    state.mark_stale_after(current, path.split("/")[0])
    state.write(run, current)
    return {"path": path, "ok": True}


@router.post("/{flow}/runs/{run_id}/phases/{dir_name}/rerun", response_model=dto.Started)
async def rerun_phase(flow: str, run_id: str, dir_name: str) -> dict:
    """Point the Run back at one Phase so `advance` runs it again.

    The Phase is not run here — repositioning and executing are two things, and a caller that
    wanted both calls `advance` next. What this does record is that everything downstream is now
    **Stale**: its output is still on disk and still readable, and what is gone is the claim that
    it follows from what is above it.
    """
    directory = controller.flow_or_404(flow)
    run, current = controller.run_or_404(directory, run_id)
    record = current.phase(dir_name)
    if record is None:
        raise HTTPException(404, f"{dir_name} is not a phase of this run")
    state.mark_stale_after(current, dir_name)
    record.status, record.error = "pending", ""
    current.current, current.status, current.waiting, current.error = dir_name, "running", "", ""
    current.ended_at = 0.0
    state.write(run, current)
    return {"run_id": run_id, "current": dir_name}


@router.delete("/{flow}/runs/{run_id}", response_model=dto.Removed)
async def delete_run(flow: str, run_id: str, request: Request) -> dict:
    """Delete one Run. Nothing prunes automatically — an audit trail should go when someone says
    so, not to save disk.

    Refused while the Run is streaming: its Phase would go on writing into a folder that no
    longer exists, and the next `run.json` write would bring half of it back."""
    import shutil

    directory = controller.flow_or_404(flow)
    run, _ = controller.run_or_404(directory, run_id)
    if f"{flow}/{run_id}" in request.app.state.cancels:
        raise HTTPException(409, "this run is going — stop it before deleting it")
    await runner.forget(flow, run_id)
    shutil.rmtree(run, ignore_errors=True)
    return {"removed": True}


# --- the flow chat -----------------------------------------------------------


@chats.post("")
async def open_chat(body: dict) -> dict:
    """The Flow's chat, made on first ask and the same one afterwards.

    One chat per Flow, bound in `flows/<flow>/chat.json`. A Flow's authoring conversation is about
    that Flow for its whole life, on the argument an edit chat is about one ref.
    """
    flow = str(body.get("flow", ""))
    if flow:
        controller.flow_or_404(flow)
        if existing := flow_chat.chat_of(flow):
            found = await transcript.get(existing)
            if found is not None:
                return {"chat_id": found.id, "flow": flow, "title": found.title}
    made = await transcript.create(title=flow or "New flow", kind="flow")
    if flow:
        flow_chat.bind_chat(flow, made.id)
    return {"chat_id": made.id, "flow": flow, "title": made.title}


@chats.get("/{chat_id}/events")
async def read_chat_events(chat_id: str) -> list[dict]:
    return await transcript.events(chat_id)


@chats.post("/{chat_id}/messages")
async def send_message(chat_id: str, body: dto.Question, request: Request):
    found = await transcript.get(chat_id)
    if found is None or found.kind != "flow":
        raise HTTPException(404, "no such flow chat")
    return turn_stream(
        request,
        chat_id,
        lambda cancel: flow_chat.stream_flow_turn(
            chat_id, body.question, request.app.state.saver, cancel
        ),
    )


@chats.post("/{chat_id}/stop", response_model=dto.Stopped)
async def stop_chat_turn(chat_id: str, request: Request) -> dict:
    cancel = request.app.state.cancels.get(chat_id)
    if cancel is not None:
        cancel.set()
    return {"stopped": cancel is not None}
