"""What the model files say — the cubes and views a file set defines, parsed and keyed by name.

Below both `modelgen` and `editing` because both need it, and so does the pipeline's rail and
the panel that lists what Cube serves. It sat in `editing/edits.py` and made a cycle out of
`modelgen.steps` → `evidence` → `edits` → `modelgen.decide`: reading the model is not an
editing concern, and putting it there meant nothing could read the model without importing
the gate that refuses writes to it.

Parsing only. What may *change* about a definition is `editing.edits`, which imports this.
"""

from dataclasses import dataclass

import yaml

TOP_LEVEL = ("cubes", "views", "view_groups")
"""What Cube accepts at the top of a model file. Anything else fails compilation for the
**whole model**, not just the file: a stray `grounding/certified_queries/probe.yml` took chinook
from 13 cubes to 0, and every query was down until the file went."""


@dataclass
class Definition:
    kind: str
    """`cube` or `view`."""
    name: str
    file: str
    body: dict

    def collection(self, key: str) -> dict[str, dict]:
        """One of `edits.COLLECTIONS`, as member name -> body."""
        return {
            item["name"]: item
            for item in self.body.get(key) or []
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }


def parse(text: str, path: str) -> tuple[dict, list[str]]:
    """One model file as a document, or why it is not one."""
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return {}, [f"{path}: not valid YAML — {str(exc).splitlines()[0]}"]
    if document is None:
        return {}, []
    if not isinstance(document, dict):
        return {}, [f"{path}: a model file is a mapping with a `cubes` or `views` key"]
    if stray := sorted(k for k in document if k not in TOP_LEVEL):
        return {}, [
            f"{path}: unexpected top-level key {', '.join(stray)} — Cube allows only "
            f"{', '.join(TOP_LEVEL)} here, and one file it refuses takes the whole source "
            "to zero cubes"
        ]
    return document, []


def read(files: dict[str, str]) -> tuple[dict[str, Definition], list[str]]:
    """Relative path -> YAML text, into entities keyed by name. Keyed by name and not by file
    because moving a cube between files is not a change to the model, and deleting the file it
    was in is."""
    entities: dict[str, Definition] = {}
    errors: list[str] = []
    for path in sorted(files):
        # The store hands over the whole model, grounding markdown included, and Cube compiles
        # only the `*.yml` — so anything else is not a model file this can be wrong about.
        if not path.endswith(".yml"):
            continue
        document, parsed = parse(files[path], path)
        errors += parsed
        for key, kind in (("cubes", "cube"), ("views", "view")):
            for body in document.get(key) or []:
                if not isinstance(body, dict) or not isinstance(body.get("name"), str):
                    errors.append(f"{path}: an entry under `{key}` has no name")
                    continue
                name = body["name"]
                if name in entities:
                    errors.append(
                        f"{name!r} is defined twice, in {entities[name].file} and {path} — "
                        "Cube refuses a model with two entities of one name"
                    )
                    continue
                entities[name] = Definition(kind=kind, name=name, file=path, body=body)
    return entities, errors
