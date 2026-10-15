"""Write the API schemas the SDK types are checked against.

Reads nozle-openapi.yaml (a path or URL, default the published docs copy) and writes the
property names and required keys of the schemas listed in tests/contract/schemas.txt to
tests/contract/openapi_schemas.json. Run it after the API spec changes, then run the tests.

    python scripts/sync_openapi_contract.py [path-or-url]   # needs PyYAML
"""

import json
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCE = sys.argv[1] if len(sys.argv) > 1 else "https://docs.nozle.dev/nozle-openapi.yaml"
NAMES = (ROOT / "tests/contract/schemas.txt").read_text().split()

if SOURCE.startswith("http"):
    with urllib.request.urlopen(SOURCE) as response:  # noqa: S310 - fixed docs URL or caller's choice
        spec = yaml.safe_load(response.read())
else:
    spec = yaml.safe_load(Path(SOURCE).read_text())

schemas = spec["components"]["schemas"]
out = {}
for name in NAMES:
    schema = schemas[name]
    properties, required = {}, set()
    for part in schema.get("allOf", [schema]):
        part = schemas[part["$ref"].rsplit("/", 1)[1]] if "$ref" in part else part
        properties.update(part.get("properties", {}))
        required.update(part.get("required", []))
    out[name] = {"properties": sorted(properties), "required": sorted(required)}

target = ROOT / "tests/contract/openapi_schemas.json"
target.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
print(f"wrote {len(out)} schemas to {target.relative_to(ROOT)}")
