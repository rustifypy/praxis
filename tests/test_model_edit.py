"""The path handling behind the file routes. The routes themselves restart containers, so
they are verified against a live source instead."""

import pytest
from fastapi import HTTPException

from analyst_agent.sources import registry
from analyst_agent.sources.registry import Source
from analyst_agent.web.model_edit import _target, model_files

SOURCE = Source(name="shop", description="", driver="postgres", port=4001)


@pytest.fixture
def rooted(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "project_root", lambda: tmp_path)
    base = registry.scaffold_source_dirs(SOURCE, root=tmp_path)
    (base / "cubes" / "album.yml").write_text("cubes:\n- name: album\n")
    (base / "grounding" / "rules" / "tone.md").write_text("# tone\n")
    return base


def test_a_model_path_resolves_inside_the_source(rooted):
    assert _target(SOURCE, "cubes/album.yml") == (rooted / "cubes" / "album.yml").resolve()


@pytest.mark.parametrize(
    "path",
    ["../../../../etc/passwd.yml", "/etc/passwd.yml", "cubes/../../escape.yml", ".git/config.yml"],
)
def test_a_path_leaving_the_model_directory_is_refused(rooted, path):
    with pytest.raises(HTTPException) as caught:
        _target(SOURCE, path)
    assert caught.value.status_code == 400


def test_only_yml_is_editable(rooted):
    """Cube ignores everything else under the mount, so writing it would be a silent no-op."""
    with pytest.raises(HTTPException):
        _target(SOURCE, "grounding/rules/tone.md")


def test_model_files_is_every_yml_and_nothing_else(rooted):
    (rooted / "views" / "sales.yml").write_text("views:\n- name: sales\n")
    assert sorted(model_files(SOURCE)) == ["cubes/album.yml", "views/sales.yml"]
