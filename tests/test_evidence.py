"""Where the pipeline stands, read off the model files rather than off `steps`.

The case that matters is a model this portal did not build — a clone, a colleague's branch, a
deployment already serving thirteen cubes. `steps` is empty for all of them, and a rail keyed on
it reports a working model as unstarted.
"""

import json

from analyst_agent.core.deployment import Deployment
from analyst_agent.datamodel import evidence
from analyst_agent.datamodel.definitions import Definition
from analyst_agent.datamodel.evidence import STEPS, invalidated_by, public, stages
from analyst_agent.datamodel.registry import label_of

CREDENTIALS = {"CUBEJS_DB_NAME": "chinook", "CUBEJS_DB_PASS": "secret"}
SHOP = Deployment(
    store_url="http://localhost:8500",
    store_token="w" * 40,
    api_secret="s" * 40,
    description="Orders.",
    driver="postgres",
    credentials=CREDENTIALS,
)

BUILT = {"bodies": {"invoice": {}}, "enrichment": {}, "catalog": {"public": {}}}
"""What the steps leave behind, so `blocked` is not the thing under test."""


def model(cubes: dict[str, bool], views: tuple[str, ...] = ()) -> dict[str, Definition]:
    """`cubes` is name -> whether it carries a description."""
    out = {
        name: Definition(
            kind="cube",
            name=name,
            file=f"cubes/{name}.yml",
            body={"name": name} | ({"description": "A cube."} if described else {}),
        )
        for name, described in cubes.items()
    }
    for name in views:
        out[name] = Definition(kind="view", name=name, file=f"views/{name}.yml", body={})
    return out


# --- what the artefacts say --------------------------------------------------


def test_the_model_files_are_what_says_a_step_ran():
    found = evidence.of(model({"invoice": True, "track": False}, views=("sales",)), BUILT)
    assert found.done("read") and found.done("cubes")
    assert found.done("describe") and found.done("views")
    assert found.cubes == ("invoice", "track")
    assert found.described == ("invoice",)


def test_an_empty_model_directory_has_nothing_done():
    found = evidence.of({}, {})
    assert not any(found.done(step) for step in STEPS)


def test_read_is_the_cached_catalog_and_not_a_recorded_run():
    """It caches a catalog rather than writing model files, so that catalog is its artefact."""
    assert not evidence.of({}, {}).done("read")
    assert evidence.of({}, {"catalog": {"public": {}}}).done("read")


def test_describing_is_a_count_rather_than_a_tick():
    """Nobody re-describes a whole model to finish the last two, so "9 of 11" is the honest
    thing to say — and it still counts as run."""
    found = evidence.of(model({"a": True, "b": False, "c": False}), BUILT)
    assert found.done("describe")
    assert found.note("describe") == "1 of 3 described"
    assert evidence.of(model({"a": True}), BUILT).note("describe") == "all 1 described"


def test_a_model_nobody_here_built_says_so_rather_than_reading_as_unstarted():
    """The empty rail over a Cube serving thirteen cubes is the whole reason for this module."""
    adopted = evidence.of({}, {}, served=13)
    assert adopted.adopted
    assert not evidence.of({}, {}, served=0).adopted
    assert not evidence.of(model({"invoice": True}), BUILT, served=13).adopted


# --- the rail ----------------------------------------------------------------


def test_the_rail_comes_from_the_files_with_an_empty_steps_blob():
    """A cloned model directory: nothing recorded, everything built."""
    rail = {
        s["name"]: s["state"] for s in stages({}, evidence.of(model({"a": True}, ("v",)), BUILT))
    }
    assert rail == {"read": "done", "cubes": "done", "describe": "done", "views": "done"}


def test_a_failed_run_wins_over_the_artefacts_it_left_behind():
    """It is the newer information, and a half-written model must not hide the error."""
    steps = {"describe": {"status": "error"}}
    rail = {s["name"]: s["state"] for s in stages(steps, evidence.of(model({"a": True}), BUILT))}
    assert rail["describe"] == "error"
    assert rail["cubes"] == "done"


def test_a_step_is_held_up_by_the_state_it_reads_not_by_a_recorded_tick():
    """Cubes on disk whose bodies are gone cannot be described — `steps.describe` reads those
    bodies, so offering it would fail on its inputs however complete the rail looks."""
    without = evidence.of(model({"a": False}), {"catalog": {"public": {}}})
    assert without.done("cubes")
    assert without.blocked("describe") == "cubes"
    assert evidence.of(model({"a": False}), BUILT).blocked("describe") == ""


def test_read_and_cubes_are_gated_by_nothing():
    """`read` is reachable on its own so the picker has something to show, and `cubes` reads the
    catalog itself when none is cached."""
    bare = evidence.of({}, {})
    assert bare.blocked("read") == "" and bare.blocked("cubes") == ""


def test_views_needs_the_decisions_describe_makes():
    assert evidence.of({}, {"bodies": {"a": {}}}).blocked("views") == "describe"
    assert evidence.of({}, BUILT).blocked("views") == ""


# --- what a rebuild destroys -------------------------------------------------


def test_the_warning_names_what_is_on_disk_and_not_what_was_recorded():
    """For an adopted model `steps` is empty while the views a rebuild deletes are there, so a
    warning read off `steps` would promise nothing is lost."""
    found = evidence.of(model({"a": True}, views=("sales",)), BUILT)
    assert invalidated_by(found, "cubes") == ["describe", "views"]
    assert invalidated_by(found, "views") == []


def test_nothing_is_lost_when_the_later_steps_produced_nothing():
    found = evidence.of(model({"a": False}), BUILT)
    assert invalidated_by(found, "cubes") == []


# --- what leaves the process -------------------------------------------------


def test_public_never_carries_a_credential_value():
    body = public(
        SHOP,
        steps={},
        health={"healthy": True, "status": "HEALTH"},
        found=evidence.of({}, {}),
    )
    assert "secret" not in json.dumps(body)
    assert body["credential_keys"] == sorted(CREDENTIALS)
    assert body["store_url"] == "http://localhost:8500"


def test_the_secret_never_leaves_either():
    """It signs the JWT, and the browser has no use for it."""
    body = public(SHOP, steps={}, health={}, found=evidence.of({}, {}))
    assert "s" * 40 not in json.dumps(body)


def test_the_label_is_the_database_the_credentials_open():
    """There is nothing to name, so the connection is called after what it opens."""
    body = public(SHOP, steps={}, health={}, found=evidence.of({}, {}))
    assert body["label"] == "chinook"
    assert label_of(Deployment(driver="postgres")) == "PostgreSQL"


def test_public_ships_what_each_step_would_discard():
    """So the browser confirms from the same rule the rebuild performs."""
    found = evidence.of(model({"a": True}, views=("sales",)), BUILT)
    body = public(SHOP, steps={}, health={}, found=found)
    assert body["invalidates"]["cubes"] == ["describe", "views"]
    assert body["invalidates"]["views"] == []


def test_next_stage_is_the_first_one_not_done():
    """One answer, so a sidebar and a page cannot name different next steps."""
    partial = evidence.of({}, {"catalog": {"public": {}}})
    assert public(SHOP, steps={}, health={}, found=partial)["next_stage"] == "cubes"
    whole = evidence.of(model({"a": True}, views=("v",)), BUILT)
    assert public(SHOP, steps={}, health={}, found=whole)["next_stage"] is None
