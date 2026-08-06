"""The pipeline, in order, in one place.

    read      the database catalog          -> Schema
    build     cube bodies + a menu          (pure, no LLM)
    install   write, restart, ask Cube
    decide    descriptions, measures, views (LLM, then guarded)
    install   again, now validating every measure
    decide    repair whatever Cube refused, then install once more

Every trip into the container goes through `publish.install`, so the
write-restart-validate cycle is written once. The caller gets a Report and decides
whether to keep the result — reverting is theirs to choose, not ours.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..registry import Source
from . import build, decide, publish, read
from .warehouse import Schema


@dataclass
class Report:
    schema: Schema
    """What the database said. The CLI renders its own table from this."""
    notes: list[str] = field(default_factory=list)
    enrichment: decide.Enrichment | None = None
    validation: publish.Validation = field(default_factory=publish.Validation)
    backup: Path | None = None


async def generate(
    source: Source,
    schema: str = "public",
    enrich: bool = False,
    say: Callable[[str], None] = lambda step: None,
) -> Report:
    """Generate and install a model for `source`.

    Raises RuntimeError if the database cannot be read at all. Everything after that
    is reported on the Report rather than raised, so the caller can decide.
    """
    say("introspecting — one throwaway Cube container")
    facts, notes = await read.introspect(source, schema)
    if not facts.tables:
        # Installing nothing would delete a working model and report success.
        raise RuntimeError(f"schema {schema!r} has no tables — nothing to model")
    report = Report(schema=facts, notes=notes)

    cubes = build.emit_cubes(facts)
    report.notes += cubes.notes

    report.backup = publish.snapshot(source)
    say(f"installing {len(cubes.bodies)} cubes")
    # Enrichment rewrites and revalidates this model, and needs it compiled only so
    # sample values can be read — so the per-member sweep waits until then.
    report.validation = await publish.install(
        source, publish.build_files(cubes.bodies), members=not enrich
    )
    if not enrich or not report.validation.compiled:
        return report

    say("reading sample values, then asking the model")
    menu = await build.build_menu(source, facts)
    enrichment = await decide.enrich(facts, menu)
    report.enrichment = enrichment

    say("installing the enriched model, validating every measure")
    report.validation = await publish.install(
        source,
        publish.build_files(cubes.bodies, enrichment),
        every_measure=True,
        enrichment=enrichment,
    )

    if report.validation.compiled and report.validation.member_errors:
        say(f"{len(report.validation.member_errors)} members failed — repairing")
        await decide.repair(enrichment, facts, report.validation.member_errors)
        report.validation = await publish.install(
            source,
            publish.build_files(cubes.bodies, enrichment),
            every_measure=True,
            enrichment=enrichment,
        )
    return report
