"""Write the request-body schemas of every API operation, for the request contract test.

Reads nozle-openapi.yaml (a path or URL, default the published docs copy) and writes, for each
operation that takes a JSON body, its full path (server path plus path template) and its body
schema with every $ref resolved, to the file given as the second argument.

    python scripts/sync_openapi_requests.py [path-or-url] [output.json]   # needs PyYAML
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

SOURCE = sys.argv[1] if len(sys.argv) > 1 else "https://docs.nozle.dev/nozle-openapi.yaml"
TARGET = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("openapi_requests.json")
KEEP = ("type", "enum", "properties", "required", "items", "allOf", "oneOf", "anyOf", "additionalProperties")

if SOURCE.startswith("http"):
    with urllib.request.urlopen(SOURCE) as response:  # noqa: S310 - fixed docs URL or caller's choice
        spec = yaml.safe_load(response.read())
else:
    spec = yaml.safe_load(Path(SOURCE).read_text())
schemas = spec["components"]["schemas"]


def resolve(node, depth=0):
    if depth > 12 or not isinstance(node, dict):
        return {}
    if "$ref" in node:
        return resolve(schemas[node["$ref"].rsplit("/", 1)[1]], depth + 1)
    out = {}
    for key in KEEP:
        if key not in node:
            continue
        value = node[key]
        if key == "properties":
            out[key] = {name: resolve(prop, depth + 1) for name, prop in value.items()}
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = resolve(value, depth + 1)
        elif key in ("allOf", "oneOf", "anyOf"):
            out[key] = [resolve(item, depth + 1) for item in value]
        else:
            out[key] = value
    return out


default_server = urllib.parse.urlparse(spec["servers"][0]["url"]).path
operations = {}
for path, item in spec["paths"].items():
    for method in ("post", "put", "patch", "delete"):
        operation = item.get(method)
        if not operation:
            continue
        server = (operation.get("servers") or item.get("servers") or [{"url": default_server}])[0]["url"]
        full = urllib.parse.urlparse(server).path.rstrip("/") + path
        body = operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
        operations[f"{method.upper()} {full}"] = resolve(body) if body else None

TARGET.write_text(json.dumps(operations, indent=1, sort_keys=True) + "\n")
print(f"wrote {len(operations)} operations to {TARGET}")
