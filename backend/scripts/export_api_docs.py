"""Write the API reference (docs/API.md) and the raw schema (docs/openapi.json)
from the FastAPI app itself, so the docs always match the code.

    cd backend
    python -m scripts.export_api_docs

Re-run it after changing any endpoint or schema, and commit both files.
The live, clickable version is at http://127.0.0.1:8000/docs while the API runs.
"""

import json
from pathlib import Path

from app.main import app

DOCS = Path(__file__).resolve().parent.parent / "docs"

# Who may call what. Derived from the dependency each router uses
# (core/deps.py): CurrentUser, OnboardedUser, StaffUser, AdminUser.
ACCESS_BY_PREFIX = [
    ("/admin", "Admin"),
    ("/staff", "Guard or Admin"),
    ("/auth/request-code", "Anyone"),
    ("/auth/verify-code", "Anyone"),
    ("/health", "Anyone"),
]


def access_for(path: str) -> str:
    for prefix, who in ACCESS_BY_PREFIX:
        if path.startswith(prefix):
            return who
    return "Signed-in user"


def resolve(schema: dict, components: dict) -> dict:
    if "$ref" in schema:
        return components[schema["$ref"].split("/")[-1]]
    return schema


def type_of(prop: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"].split("/")[-1]
    if "anyOf" in prop:
        return " | ".join(type_of(p) for p in prop["anyOf"])
    if prop.get("type") == "array":
        return f"list[{type_of(prop.get('items', {}))}]"
    if "enum" in prop:
        return " / ".join(map(str, prop["enum"]))
    if "const" in prop:
        return json.dumps(prop["const"])
    return prop.get("type", "any")


def fields_table(schema: dict, components: dict) -> list[str]:
    schema = resolve(schema, components)
    props = schema.get("properties", {})
    if not props:
        return []
    required = set(schema.get("required", []))
    lines = ["| Field | Type | Required | Notes |", "|---|---|---|---|"]
    for name, prop in props.items():
        limits = [f"{k}={prop[k]}" for k in ("minLength", "maxLength", "minimum", "maximum", "maxItems") if k in prop]
        note = "; ".join(limits)
        lines.append(f"| `{name}` | {type_of(prop)} | {'yes' if name in required else 'no'} | {note} |")
    return lines


def main() -> None:
    spec = app.openapi()
    components = spec.get("components", {}).get("schemas", {})
    DOCS.mkdir(exist_ok=True)
    (DOCS / "openapi.json").write_text(json.dumps(spec, indent=2) + "\n")

    out = [
        "# API reference",
        "",
        "Generated from the code by `python -m scripts.export_api_docs` (do not edit by hand).",
        "Interactive version: run the API and open http://127.0.0.1:8000/docs.",
        "",
        "**Conventions**",
        "",
        "- Auth: a session cookie (`lf_session`) set by `POST /auth/verify-code`. No passwords.",
        "- Errors are JSON `{\"detail\": \"message\"}`; validation errors (422) list each field.",
        "- 401 not signed in, 403 not allowed (role or state), 404 doesn't exist *or you may not see it*,",
        "  409 conflicts with current state (e.g. another claim is already approved), 429 too many code requests.",
        "- State changes on an item or its claims lock the item row, so simultaneous requests are served",
        "  one after the other; the loser gets a clean 403/409 (see `app/services/locks.py`).",
        "",
    ]
    by_tag: dict[str, list] = {}
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((path, method, op))

    out.append("## Contents\n")
    out += [f"- [{tag}](#{tag})" for tag in by_tag]
    for tag, ops in by_tag.items():
        out += ["", f"## {tag}"]
        for path, method, op in ops:
            out += ["", f"### `{method.upper()} {path}`", ""]
            if op.get("description"):
                out += [op["description"].strip(), ""]
            elif op.get("summary"):
                out += [op["summary"], ""]
            out.append(f"**Who:** {access_for(path)}")
            params = [p for p in op.get("parameters", []) if p.get("in") in ("query", "path")]
            if params:
                out += ["", "**Parameters**", "", "| Name | In | Type | Required |", "|---|---|---|---|"]
                out += [f"| `{p['name']}` | {p['in']} | {type_of(p.get('schema', {}))} | "
                        f"{'yes' if p.get('required') else 'no'} |" for p in params]
            body = op.get("requestBody", {}).get("content", {})
            if "application/json" in body:
                schema = body["application/json"]["schema"]
                out += ["", f"**Body** (`{type_of(schema)}`)", ""] + fields_table(schema, components)
            elif "multipart/form-data" in body:
                out += ["", "**Body:** multipart form with one image file `file` (JPEG, PNG or WebP)."]
            codes = [c for c in op.get("responses", {}) if c != "422"]
            ok = next((c for c in codes if c.startswith("2")), None)
            if ok:
                content = op["responses"][ok].get("content", {}).get("application/json", {})
                shape = type_of(content["schema"]) if content.get("schema") else "no body"
                out += ["", f"**Returns:** `{ok}` {shape}"]
    out += ["", "## Response shapes", ""]
    for name in sorted(components):
        if name.endswith("In") or name.startswith(("HTTPValidationError", "ValidationError", "Body_")):
            continue
        table = fields_table(components[name], components)
        if table:
            out += [f"### {name}", ""] + table + [""]
    (DOCS / "API.md").write_text("\n".join(out) + "\n")
    print(f"Wrote {DOCS / 'API.md'} and {DOCS / 'openapi.json'} ({len(spec['paths'])} paths).")


if __name__ == "__main__":
    main()
