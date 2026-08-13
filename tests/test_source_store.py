import json
from dataclasses import MISSING, fields

import pytest

from analyst_agent.sources import registry
from analyst_agent.sources.model.decide import (
    CubeDecision,
    Enrichment,
    MeasureDecision,
    MemberDoc,
    ViewCube,
    ViewDecision,
)
from analyst_agent.sources.model.state import dump_schema, load_schema
from analyst_agent.sources.model.warehouse import Column, ForeignKey, Schema, Table
from analyst_agent.sources.registry import Source
from analyst_agent.sources.store import (
    STEPS,
    SourceStore,
    blocked_by,
    invalidated_by,
    merge_credentials,
    public,
    stages,
)

CREDENTIALS = {
    "CUBEJS_DB_TYPE": "postgres",
    "CUBEJS_DB_HOST": "host.docker.internal",
    "CUBEJS_DB_PORT": "5432",
    "CUBEJS_DB_NAME": "chinook",
    "CUBEJS_DB_USER": "cube",
    "CUBEJS_DB_PASS": "secret",
}

SHOP = Source(
    name="shop",
    description="Orders.",
    driver="postgres",
    port=4001,
    credentials=CREDENTIALS,
)


@pytest.fixture
async def store(tmp_path) -> SourceStore:
    s = SourceStore(tmp_path / "sources.db")
    await s.init()
    return s


async def test_round_trip_keeps_credentials_and_mounts(store):
    await store.add(
        Source(
            name="ads",
            description="Ad spend.",
            driver="bigquery",
            port=4003,
            mounts={"/cube/conf/bq-key.json": "/home/me/key.json"},
            credentials={"CUBEJS_DB_BQ_PROJECT_ID": "acme"},
        )
    )
    loaded = await store.get("ads")
    assert loaded.mounts == {"/cube/conf/bq-key.json": "/home/me/key.json"}
    assert loaded.credentials == {"CUBEJS_DB_BQ_PROJECT_ID": "acme"}
    assert loaded.port == 4003
    assert loaded.service == "cube-ads"


async def test_the_database_is_owner_only(store):
    """It holds database passwords in plaintext, so the file mode is the only thing guarding
    them."""
    assert store.path.stat().st_mode & 0o777 == 0o600


async def test_two_sources_cannot_share_a_port(store):
    await store.add(SHOP)
    with pytest.raises(Exception, match="UNIQUE"):
        await store.add(Source(name="other", description="", driver="mysql", port=4001))


async def test_update_leaves_omitted_columns_alone(store):
    await store.add(SHOP)
    await store.update("shop", description="Orders and returns.")
    loaded = await store.get("shop")
    assert loaded.description == "Orders and returns."
    assert loaded.credentials == CREDENTIALS


async def test_saving_a_step_does_not_touch_the_others(store):
    await store.add(SHOP)
    await store.save_step("shop", "connect", {"status": "done"})
    await store.save_step("shop", "cubes", {"status": "running"})
    assert await store.steps("shop") == {
        "connect": {"status": "done"},
        "cubes": {"status": "running"},
    }


async def test_saving_a_step_does_not_touch_credentials(store):
    """A job finishing must not rewrite what the user typed, and vice versa."""
    await store.add(SHOP)
    await store.save_step("shop", "connect", {"status": "done"})
    await store.save_state("shop", {"selection": {"tables": ["invoice"]}})
    assert (await store.get("shop")).credentials == CREDENTIALS


async def test_unknown_step_is_refused(store):
    """By the `Step` annotation, not by a membership test in the body. `ValidationError` is a
    `ValueError`, so what a caller may catch is unchanged."""
    await store.add(SHOP)
    with pytest.raises(ValueError, match="'connect', 'cubes', 'describe' or 'views'"):
        await store.save_step("shop", "enrich", {})


async def test_rerunning_a_step_forgets_the_ones_after_it(store):
    """`publish.write` unlinks every YAML, so the later output is gone on disk too."""
    await store.add(SHOP)
    for step in ("connect", "cubes", "describe", "views"):
        await store.save_step("shop", step, {"status": "done"})

    dropped = await store.clear_steps_after("shop", "cubes")
    assert dropped == ["describe", "views"]
    assert sorted(await store.steps("shop")) == ["connect", "cubes"]


async def test_clearing_after_the_last_step_drops_nothing(store):
    await store.add(SHOP)
    await store.save_step("shop", "views", {"status": "done"})
    assert await store.clear_steps_after("shop", "views") == []


async def test_catalog_read_is_the_cached_catalog_one_source_or_all(store):
    """`read` records no step, so this is the only evidence it ran — and the per-source and
    batch answers have to agree, since one feeds the detail page and the other the list."""
    await store.add(SHOP)
    assert await store.catalog_read("shop") is False
    await store.save_state("shop", {"catalog": {"public": {"tables": []}}})
    assert await store.catalog_read("shop") is True
    assert await store.all_catalog_read() == {"shop": True}


async def test_state_merges_at_the_top_level(store):
    await store.add(SHOP)
    await store.save_state("shop", {"catalog": {"public": ["invoice"]}})
    await store.save_state("shop", {"selection": {"tables": ["invoice"]}})
    state = await store.state("shop")
    assert state["catalog"] == {"public": ["invoice"]}
    assert state["selection"] == {"tables": ["invoice"]}


async def test_remove_takes_the_row(store):
    await store.add(SHOP)
    await store.remove("shop")
    assert await store.get("shop") is None
    assert await store.all() == []


# --- what leaves the process -------------------------------------------------


def test_public_never_carries_a_credential_value():
    body = public(SHOP, steps={}, running=True, health={"healthy": True, "status": "HEALTH"})
    assert "secret" not in json.dumps(body)
    assert body["credential_keys"] == sorted(CREDENTIALS)
    assert body["container"] == "cube-shop"
    assert body["url"] == "http://localhost:4001"


def test_public_ships_what_each_step_would_discard():
    """So the browser confirms from the same rule `clear_steps_after` performs."""
    steps = {step: {"status": "done"} for step in ("connect", "cubes", "describe", "views")}
    body = public(SHOP, steps=steps, running=True, health={})
    assert body["invalidates"]["cubes"] == ["describe", "views"]
    assert body["invalidates"]["views"] == []


def test_invalidated_by_ignores_a_step_that_only_failed():
    """A step with no status recorded has no output to lose."""
    steps = {"cubes": {"status": "done"}, "describe": {}, "views": {"status": "error"}}
    assert invalidated_by(steps, "cubes") == ["views"]


def test_blank_supplied_credential_keeps_the_stored_one():
    """The edit form is never sent the password, so blank has to mean unchanged."""
    merged = merge_credentials(CREDENTIALS, {"CUBEJS_DB_PASS": "", "CUBEJS_DB_USER": "analyst"})
    assert merged["CUBEJS_DB_PASS"] == "secret"
    assert merged["CUBEJS_DB_USER"] == "analyst"


# --- names and ports ---------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("", "required"),
        ("Shop", "lowercase"),
        ("9shop", "lowercase letters"),
        ("my_shop", "lowercase letters"),
        ("my-shop", None),
    ],
)
def test_validate_name(name, expected):
    error = registry.validate_name(name, taken=["other"])
    assert error is None if expected is None else expected in error


def test_a_name_is_ascii_only():
    """It becomes a container name and a directory name, and `str.isalpha`/`isalnum` accept
    Greek, accented Latin and Arabic-Indic digits — which the form's own rule rejects."""
    for name in ("\u03c9mega", "caf\u00e9", "shop\u0663", "\u01c4shop"):
        assert registry.validate_name(name, taken=[]) is not None, name


def test_validate_name_rejects_duplicates():
    assert "already exists" in registry.validate_name("shop", taken=["shop"])


def test_next_port_skips_assigned_ports(monkeypatch):
    monkeypatch.setattr(registry, "port_in_use", lambda port: False)
    assert registry.next_port([4001, 4002, 4003]) == 4004
    assert registry.next_port([]) == registry.FIRST_PORT


def test_next_port_skips_a_port_something_else_is_listening_on(monkeypatch):
    monkeypatch.setattr(registry, "port_in_use", lambda port: port == 4001)
    assert registry.next_port([]) == 4002


# --- the state a step leaves behind ------------------------------------------

SCHEMA = Schema(
    tables=[
        Table(
            name="invoice_line",
            # Not "public": a field left at its default is invisible to a round-trip test,
            # which `test_the_fixture_sets_every_warehouse_field` below is what enforces.
            schema="sales",
            columns=[
                Column(name="invoice_line_id", data_type="integer", distinct=-1.0),
                Column(name="invoice_id", data_type="integer"),
                Column(name="unit_price", data_type="numeric", null_fraction=0.0),
            ],
            primary_key=["invoice_line_id"],
            foreign_keys=[
                ForeignKey(columns=["invoice_id"], ref_table="invoice", ref_columns=["invoice_id"])
            ],
        ),
        Table(name="invoice", primary_key=["invoice_id"]),
    ]
)

ENRICHMENT = Enrichment(
    cubes={
        "invoice_line": CubeDecision(
            description="One line of an invoice.",
            members=[MemberDoc(name="unit_price", description="Price per unit.")],
            measures=[
                MeasureDecision(
                    column="unit_price",
                    name="revenue",
                    aggregation="sum",
                    expression="{CUBE}.unit_price * {CUBE}.quantity",
                    description="Line revenue.",
                    ai_context="Prefer this over unit_price.",
                )
            ],
        )
    },
    views=[
        ViewDecision(
            name="sales",
            description="Line-level sales.",
            ai_context="Use for revenue questions.",
            cubes=[ViewCube(join_path="invoice_line", includes=["unit_price"])],
        )
    ],
    rejected=["invoice_line.bad_measure: not additive"],
    repaired=["invoice_line.revenue"],
    dropped=["invoice.total"],
)


def test_schema_survives_the_round_trip():
    """A lost field here means a re-run silently builds a different model."""
    back = load_schema(json.loads(json.dumps(dump_schema(SCHEMA))))
    assert back == SCHEMA


def test_schema_round_trip_keeps_the_derived_types():
    back = load_schema(dump_schema(SCHEMA))
    line = back.table("invoice_line")
    assert line.column("unit_price").type == "number"
    assert line.key_columns == {"invoice_line_id", "invoice_id"}
    assert back.table("invoice").primary_key == ["invoice_id"]


def test_the_fixture_sets_every_warehouse_field():
    """What makes the round trip above mean anything. A field the fixture never sets is equal on
    both sides whatever the loader does with it, so an enumerating loader could drop a new field
    with every test still passing. No pydantic model needs this: `model_dump` walks
    `model_fields`, so its symmetry does not depend on a fixture."""
    instances = {Schema: [SCHEMA], Table: SCHEMA.tables, Column: [], ForeignKey: []}
    for table in SCHEMA.tables:
        instances[Column] += table.columns
        instances[ForeignKey] += table.foreign_keys

    for cls, seen in instances.items():
        for f in fields(cls):
            default = f.default_factory() if f.default_factory is not MISSING else f.default
            assert any(getattr(i, f.name) != default for i in seen), (
                f"{cls.__name__}.{f.name} is left at its default everywhere in SCHEMA, so no "
                "round-trip assertion can see it disappear"
            )


def test_a_key_the_dataclass_does_not_have_is_refused_rather_than_dropped():
    """`load_schema` builds with `**raw` instead of picking fields, which is what makes a newly
    added field survive without anyone editing the loader. The cost is the right one: a key that
    is not a field fails here rather than vanishing days later during `views`."""
    raw = dump_schema(SCHEMA)
    raw["tables"][0]["columns"][0]["comment"] = "not a field"
    with pytest.raises(TypeError, match="comment"):
        load_schema(raw)


def test_enrichment_survives_the_round_trip():
    back = Enrichment.model_validate(json.loads(json.dumps(ENRICHMENT.model_dump())))
    assert back == ENRICHMENT
    assert back.counts == ENRICHMENT.counts


async def test_state_survives_sqlite(store):
    """The whole point: views can read what cubes decided, days later."""
    await store.add(SHOP)
    await store.save_state(
        "shop", {"schema": dump_schema(SCHEMA), "enrichment": ENRICHMENT.model_dump()}
    )
    state = await store.state("shop")
    assert load_schema(state["schema"]) == SCHEMA
    assert Enrichment.model_validate(state["enrichment"]) == ENRICHMENT


# --- the stage rail, decided once and shipped -------------------------------


def test_cubes_is_blocked_by_connect_not_by_read():
    """`cubes` reads the catalog itself when none is cached, so `connect` is the real
    prerequisite — gating it on `read` would refuse a source that is ready to build."""
    steps = {"connect": {"status": "done"}}
    assert blocked_by(steps, "cubes") == ""
    assert blocked_by({}, "cubes") == "connect"


def test_read_is_gated_by_nothing():
    """It records no step, and `run.action` does not gate it — it is reachable on its own so
    the picker has something to show."""
    assert blocked_by({}, "read") == ""


def test_the_first_step_is_never_blocked():
    assert blocked_by({}, "connect") == ""


def test_the_rail_reports_read_from_the_cached_catalog():
    steps = {"connect": {"status": "done"}}
    by_name = {s["name"]: s for s in stages(steps, catalog_read=True)}
    assert by_name["read"]["state"] == "done"
    assert {s["name"]: s["state"] for s in stages(steps, catalog_read=False)}["read"] == "todo"


def test_a_failed_step_is_reported_as_an_error_not_as_todo():
    rail = {s["name"]: s["state"] for s in stages({"connect": {"status": "error"}}, False)}
    assert rail["connect"] == "error"


def test_next_stage_is_the_first_one_not_done():
    """One answer, so a sidebar and a page cannot name different next steps."""
    body = public(
        SHOP,
        steps={"connect": {"status": "done"}},
        running=True,
        health={},
        catalog_read=False,
    )
    assert body["next_stage"] == "read"
    done = public(
        SHOP,
        steps={s: {"status": "done"} for s in STEPS},
        running=True,
        health={},
        catalog_read=True,
    )
    assert done["next_stage"] is None
