"""The reference pages under `docs/` cannot fall behind the code they describe.

Prose drifts silently: a field added to `phase.yml` works, passes every other test, and is simply
absent from the page someone writes a Flow from. So each reference page is checked against the
thing it lists — the contract's fields, the settings, the route table — and a name missing from
the page fails here, where the change that added it is still in front of whoever made it.

Only presence is checked. Whether the sentence beside a name is still true is a reviewer's job.
"""

import json
import pathlib
import re

from praxis.core.config import Settings
from praxis.flows.contract import BUNDLES, FlowSpec, PhaseSpec

ROOT = pathlib.Path(__file__).parent.parent
DOCS = ROOT / "docs"


def test_every_contract_field_has_a_section():
    page = (DOCS / "flows" / "reference.md").read_text()
    headed = set(re.findall(r"^### `(\w+)`$", page, re.M))
    fields = set(PhaseSpec.model_fields) | set(FlowSpec.model_fields)
    assert fields - headed == set(), "add a '### `field`' section to docs/flows/reference.md"


def test_every_bundle_is_documented():
    page = (DOCS / "flows" / "reference.md").read_text()
    assert [name for name in BUNDLES if f"| `{name}` |" not in page] == []


def test_every_setting_is_documented():
    page = (DOCS / "operations" / "configuration.md").read_text()
    missing = [name.upper() for name in Settings.model_fields if f"`{name.upper()}`" not in page]
    assert missing == [], "add these to docs/operations/configuration.md"


def test_every_route_is_documented():
    page = (DOCS / "api" / "http.md").read_text()
    routes = json.loads((ROOT / "tests" / "surface.json").read_text())["routes"]
    # The page writes `{path}` where the route registers `{path:path}`.
    missing = [
        f"{route['method']} {route['path']}"
        for route in routes
        if f"| {route['method']} | `{re.sub(r':path}', '}', route['path'])}` |" not in page
    ]
    assert missing == [], "add these to docs/api/http.md"
