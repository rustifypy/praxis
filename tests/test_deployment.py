import json
from dataclasses import MISSING, fields

import pytest

from analyst_agent.core.deployment import Deployment, DeploymentStore
from analyst_agent.datamodel.drivers import merge_credentials
from analyst_agent.datamodel.modelgen.decide import (
    CubeDecision,
    Enrichment,
    MeasureDecision,
    MemberDescription,
    ViewCube,
    ViewDecision,
)
from analyst_agent.datamodel.modelgen.state import dump_schema, load_schema
from analyst_agent.datamodel.modelgen.warehouse import Column, ForeignKey, Schema, Table

CREDENTIALS = {
    "CUBEJS_DB_TYPE": "postgres",
    "CUBEJS_DB_HOST": "host.docker.internal",
    "CUBEJS_DB_PORT": "5432",
    "CUBEJS_DB_NAME": "chinook",
    "CUBEJS_DB_USER": "cube",
    "CUBEJS_DB_PASS": "secret",
}

SHOP = Deployment(
    store_url="http://localhost:8500",
    description="Orders.",
    driver="postgres",
    credentials=CREDENTIALS,
)


@pytest.fixture
async def store(tmp_path) -> DeploymentStore:
    s = DeploymentStore(tmp_path / "analyst.db")
    await s.init()
    return s


async def test_round_trip_keeps_credentials_and_mounts(store):
    await store.save(
        Deployment(
            description="Ad spend.",
            driver="bigquery",
            mounts={"/cube/conf/bq-key.json": "/home/me/key.json"},
            credentials={"CUBEJS_DB_BQ_PROJECT_ID": "acme"},
        )
    )
    loaded = await store.get()
    assert loaded.mounts == {"/cube/conf/bq-key.json": "/home/me/key.json"}
    assert loaded.credentials == {"CUBEJS_DB_BQ_PROJECT_ID": "acme"}
    assert loaded.driver == "bigquery"


async def test_the_database_is_owner_only(store):
    """It holds database passwords in plaintext, so the file mode is the only thing guarding
    them."""
    assert store.path.stat().st_mode & 0o777 == 0o600


async def test_connecting_again_replaces_the_one_row(store):
    """One Cube deployment, so a second connection is a replacement rather than an addition —
    and its steps start empty, because they describe the database that just went."""
    await store.save(SHOP)
    await store.save_step("connect", {"status": "done"})
    await store.save(Deployment(driver="mysql", credentials={"CUBEJS_DB_NAME": "other"}))
    loaded = await store.get()
    assert loaded.driver == "mysql"
    assert await store.steps() == {}


async def test_update_leaves_omitted_columns_alone(store):
    await store.save(SHOP)
    await store.update(description="Orders and returns.")
    loaded = await store.get()
    assert loaded.description == "Orders and returns."
    assert loaded.credentials == CREDENTIALS


async def test_saving_a_step_does_not_touch_the_others(store):
    await store.save(SHOP)
    await store.save_step("connect", {"status": "done"})
    await store.save_step("cubes", {"status": "running"})
    assert await store.steps() == {
        "connect": {"status": "done"},
        "cubes": {"status": "running"},
    }


async def test_saving_a_step_does_not_touch_credentials(store):
    """A job finishing must not rewrite what the user typed, and vice versa."""
    await store.save(SHOP)
    await store.save_step("connect", {"status": "done"})
    await store.save_state({"selection": {"tables": ["invoice"]}})
    assert (await store.get()).credentials == CREDENTIALS


async def test_state_merges_at_the_top_level(store):
    await store.save(SHOP)
    await store.save_state({"catalog": {"public": ["invoice"]}})
    await store.save_state({"selection": {"tables": ["invoice"]}})
    state = await store.state()
    assert state["catalog"] == {"public": ["invoice"]}
    assert state["selection"] == {"tables": ["invoice"]}


async def test_remove_takes_the_row(store):
    await store.save(SHOP)
    await store.remove()
    assert await store.get() is None


# --- what leaves the process -------------------------------------------------


def test_blank_supplied_credential_keeps_the_stored_one():
    """The edit form is never sent the password, so blank has to mean unchanged."""
    merged = merge_credentials(CREDENTIALS, {"CUBEJS_DB_PASS": "", "CUBEJS_DB_USER": "analyst"})
    assert merged["CUBEJS_DB_PASS"] == "secret"
    assert merged["CUBEJS_DB_USER"] == "analyst"


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
            members=[MemberDescription(name="unit_price", description="Price per unit.")],
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
    await store.save(SHOP)
    await store.save_state({"schema": dump_schema(SCHEMA), "enrichment": ENRICHMENT.model_dump()})
    state = await store.state()
    assert load_schema(state["schema"]) == SCHEMA
    assert Enrichment.model_validate(state["enrichment"]) == ENRICHMENT


# --- the stage rail, decided once and shipped -------------------------------
