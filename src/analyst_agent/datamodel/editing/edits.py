"""Tiers 1-2 of the edit gate — everything checkable without a container.

Tier 3 is Cube itself (write, restart, `publish.check`), and it runs against the **live**
container, because a branch is served rather than deployed. That is what makes these two tiers
load-bearing rather than a convenience: what they refuse never reaches disk, so it never takes
the model down.

The base for every comparison is **main**, not the branch's previous state. A member main
carries is frozen because a dashboard or a certified query may name it; a member the branch
added itself is the branch's own, and may be reshaped or dropped freely until it merges.
"""

import re
from dataclasses import dataclass, field

from ..definitions import Definition, read
from ..modelgen.decide import IDENTIFIER, SQL_WORDS

DOC_FIELDS = frozenset({"description", "title", "meta", "format"})
"""What may change on something main already has. `public` is deliberately absent — hiding a
member is not a rename, but it leaves the queryable surface exactly as a deletion does, and a
tile naming it breaks the same way."""

COLLECTIONS = ("measures", "dimensions", "segments", "joins", "hierarchies", "pre_aggregations")
"""Cube body keys holding named things. Everything else in a body is compared whole."""

KEYWORDS = SQL_WORDS | frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "NULL",
        "IS",
        "IN",
        "LIKE",
        "BETWEEN",
        "DISTINCT",
        "INTERVAL",
        "TRUE",
        "FALSE",
        "ON",
        "OVER",
        "FILTER",
    }
)
"""A superset of `decide.SQL_WORDS`: that set gates what the model may *emit* and is
deliberately tight, while this one only suppresses false warnings on hand-written SQL."""

QUALIFIED = re.compile(r"\{CUBE\}\s*\.\s*" + IDENTIFIER)
CALL = re.compile(IDENTIFIER + r"\s*\(")


@dataclass
class EditResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_message(self) -> str:
        """The same shape `GateResult` returns, for the same reason: an editing agent acts on
        text, and an exception is not something it can fix."""
        parts = []
        if self.errors:
            parts.append("EDIT REJECTED:\n" + "\n".join(f"- {e}" for e in self.errors))
        if self.warnings:
            parts.append("Warnings:\n" + "\n".join(f"- {w}" for w in self.warnings))
        return "\n\n".join(parts)


def shape_errors(entity: Definition, entities: dict[str, Definition]) -> list[str]:
    """What Cube would refuse, checked here so it is refused before the write instead."""
    if entity.kind != "view":
        return []
    out = []
    cubes = entity.body.get("cubes")
    if not isinstance(cubes, list) or not cubes:
        return [f"view {entity.name!r}: needs a `cubes:` list saying what it exposes"]
    for entry in cubes:
        if not isinstance(entry, dict) or not isinstance(entry.get("join_path"), str):
            out.append(f"view {entity.name!r}: every `cubes:` entry needs a `join_path`")
            continue
        if "includes" not in entry:
            out.append(f"view {entity.name!r}: `{entry['join_path']}` has no `includes`")
        root = entry["join_path"].split(".")[0]
        if root not in entities:
            out.append(
                f"view {entity.name!r}: join_path starts at {root!r}, which is not a cube "
                "in this model"
            )
        elif entities[root].kind != "cube":
            out.append(f"view {entity.name!r}: join_path starts at {root!r}, which is a view")
    return out


# --- tier 2: what this edit is allowed to have changed ------------------------


def frozen_violations(was: Definition, now: Definition) -> list[str]:
    """Cubes are append-only. What main carries keeps its name and its structure, documentation
    does not count as structure, and anything genuinely new is welcome.

    Keys are compared as a **union** rather than by walking what main had: a member that gains
    `public: false` is neither a rename nor a removal, and leaves the queryable surface exactly
    as a deletion would.
    """
    editable = ", ".join(sorted(DOC_FIELDS))
    out = []
    for key in sorted(set(was.body) | set(now.body)):
        if key in DOC_FIELDS:
            continue
        if key not in COLLECTIONS:
            if key not in now.body:
                out.append(f"{was.name}: `{key}` was removed — a cube's structure is fixed")
            elif key not in was.body:
                out.append(
                    f"{was.name}: `{key}` was added — a cube's structure is fixed, and only "
                    f"{editable} may be set on one that already exists"
                )
            elif now.body[key] != was.body[key]:
                out.append(
                    f"{was.name}: `{key}` changed from {was.body[key]!r} to "
                    f"{now.body[key]!r} — a cube's structure is fixed, so add rather than alter"
                )
            continue

        current = now.collection(key)
        for member, body in was.collection(key).items():
            target = current.get(member)
            if target is None:
                out.append(
                    f"{was.name}: the join to {member!r} was removed — every view built on "
                    "that path depends on it"
                    if key == "joins"
                    else f"{was.name}.{member} was removed — a member main carries is frozen, "
                    "because a dashboard tile or a certified query may name it"
                )
                continue
            for name in sorted(set(body) | set(target)):
                if name in DOC_FIELDS or body.get(name) == target.get(name):
                    continue
                verb = "was added" if name not in body else "changed"
                out.append(
                    f"{was.name}.{member}: `{name}` {verb} — only {editable} may be edited "
                    "on a member that already exists"
                )
    return out


def unqualified_columns(expression: str) -> set[str]:
    """Column references Cube will hand to the database exactly as written.

    Cube qualifies a `sql:` that is *precisely one* identifier and passes anything longer
    through verbatim, so every column inside an expression needs `{CUBE}.` — `quantity *
    unit_price` arrives bare and dies with `column reference "unit_price" is ambiguous` the
    moment a join brings in another table carrying that name.
    """
    text = expression.strip()
    if re.fullmatch(IDENTIFIER, text):
        return set()
    # Blank out everything that is not a bare column — string literals, already-qualified
    # references, function names — so what is left to find is columns.
    masked = re.sub(r"'[^']*'", " ", text)
    masked = QUALIFIED.sub(" ", masked)
    masked = CALL.sub("(", masked)
    return {word for word in re.findall(IDENTIFIER, masked) if word.upper() not in KEYWORDS}


def check(base: dict[str, str], proposed: dict[str, str]) -> EditResult:
    """Tiers 1-2. `base` is main's model files; `proposed` is the whole model as it would be
    written. Both are relative path -> YAML text.
    """
    entities, errors = read(proposed)
    if errors:
        # Comparing a model that does not parse produces noise, not findings.
        return EditResult(ok=False, errors=errors)

    warnings: list[str] = []
    before, base_errors = read(base)
    if base_errors:
        # A `cubes` step Cube rejected is committed to main on purpose, so an unreadable main
        # is a real state rather than an impossible one. Check what parsed and say so.
        warnings.append(
            f"main does not fully parse ({base_errors[0]}), so some members could not be "
            "checked for removal"
        )

    for name, was in before.items():
        if was.kind != "cube":
            continue
        now = entities.get(name)
        if now is None:
            errors.append(
                f"cube {name!r} was removed — cubes are append-only, and everything built on "
                "this one would break with it"
            )
        elif now.kind != was.kind:
            errors.append(f"{name!r} was a cube on main and is a view here")
        else:
            errors += frozen_violations(was, now)

    # After the diff, not before it. Deleting a cube also orphans every view built on it, and
    # leading with the view's broken join_path sends someone to fix the wrong file.
    for entity in entities.values():
        errors += shape_errors(entity, entities)

    for name, now in entities.items():
        if now.kind != "cube":
            continue
        was = before.get(name)
        for key in ("measures", "dimensions"):
            existing = was.collection(key) if was else {}
            for member, body in now.collection(key).items():
                sql = body.get("sql")
                if member in existing or not isinstance(sql, str):
                    continue
                if bare := unqualified_columns(sql):
                    warnings.append(
                        f"{name}.{member}: {', '.join(sorted(bare))} "
                        f"{'are' if len(bare) > 1 else 'is'} unqualified in `sql` — write "
                        "{CUBE}.column, or the database sees an ambiguous reference as soon "
                        "as a join brings in another table with that name"
                    )
    return EditResult(ok=not errors, errors=errors, warnings=warnings)
