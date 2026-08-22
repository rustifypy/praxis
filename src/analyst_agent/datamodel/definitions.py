from dataclasses import dataclass

import yaml

# Anything else fails compilation for the whole model, not just the file.
TOP_LEVEL = ("cubes", "views", "view_groups")


@dataclass
class Definition:
    # `cube` or `view`.
    kind: str
    name: str
    file: str
    body: dict

    def collection(self, key: str) -> dict[str, dict]:
        return {
            item["name"]: item
            for item in self.body.get(key) or []
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }


def parse(text: str, path: str) -> tuple[dict, list[str]]:
    try:
        document = yaml.safe_load(text)
        if document is None:
            return {}, []
        if not isinstance(document, dict):
            return {}, [f"{path}: a model file is a mapping with a `cubes` or `views` key"]
        if stray := sorted(key for key in document if key not in TOP_LEVEL):
            return {}, [
                f"{path}: unexpected top-level key {', '.join(stray)} — Cube allows only "
                f"{', '.join(TOP_LEVEL)} here, and one file it refuses takes the whole source "
                "to zero cubes"
            ]
        return document, []
    except yaml.YAMLError as exc:
        return {}, [f"{path}: not valid YAML — {str(exc).splitlines()[0]}"]


def read(files: dict[str, str]) -> tuple[dict[str, Definition], list[str]]:
    # Keyed by name and not by file: moving a cube between files is not a change to the model,
    # and deleting the file it was in is.
    entities: dict[str, Definition] = {}
    errors: list[str] = []
    for path in sorted(files):
        # The store hands over the whole model, grounding markdown included; Cube compiles
        # only the `*.yml`.
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
