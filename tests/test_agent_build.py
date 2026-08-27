"""Both agents are constructible.

Nothing here calls a model — `build_model` is replaced with a fake, because what these assert is
the wiring `create_agent` is handed, not an answer. The editor agent shipped for one commit with
`AuditLog(user="local")` in its middleware list, and `AgentMiddleware.__init__` is
`object.__init__`, so every edit turn died at construction with "AuditLog() takes no arguments"
while the query agent — the same middleware, called with no arguments — was fine. Constructing
both is the only thing that catches an argument one of them cannot take.
"""

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

from analyst_agent.agent import build as agent_build
from analyst_agent.agent.session import Session
from analyst_agent.agent.tools.dashboards import DashboardContext
from analyst_agent.agent.tools.editor import EditContext


@pytest.fixture(autouse=True)
def fake_model(monkeypatch):
    monkeypatch.setattr(
        agent_build, "build_model", lambda **_: GenericFakeChatModel(messages=iter([]))
    )


def test_editor_agent_builds():
    assert agent_build.build_editor_agent(EditContext(store=None, ref="branch"), "branch")


async def test_query_agent_builds(catalog):
    assert await agent_build.build_agent(
        Session(catalog=catalog, client=None),
        DashboardContext(catalog=catalog),
    )
