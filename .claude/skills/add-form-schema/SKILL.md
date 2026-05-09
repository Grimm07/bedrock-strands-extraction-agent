---
name: add-form-schema
description: Scaffold a new FormSchema (FieldDefinitions + registration + test + README update) for the bedrock-strands-agent project
disable-model-invocation: true
---

# add-form-schema

Scaffold a new `FormSchema` end-to-end: definition, registration, test, README
entry, and CHANGELOG bump. This skill has side effects (writes new code), so it
is **user-invoke only** (`disable-model-invocation: true`).

## When to use

The user typed `/add-form-schema` (or asked you to "add a schema"). They want a
new entry in `bedrock_strands_agent.extraction.schemas` so it shows up in
`/schemas`, can be requested via `/extract`, and is exercised by the test suite.

## Inputs to gather (ask the user, don't guess)

Before writing anything, collect:

1. **`name`** — snake_case, unique within the registry. Becomes the
   `schema_name` callers send to `/extract`. Examples: `irs_w9`, `invoice`,
   `purchase_order`, `irs_1099_misc`.
2. **`version`** — semver string. Default `1.0.0` for a new schema.
3. **`description`** — one-paragraph human description. Shows up in `/schemas`
   and helps the model understand what document type this is.
4. **`fields`** — an ordered list. For each field, get:
   - `name` (snake_case, unique)
   - `type` — one of `STRING`, `INTEGER`, `NUMBER`, `BOOLEAN`, `DATE`, `EMAIL`,
     `SSN`, `EIN`, `CURRENCY` (these are members of `FieldType`, a `StrEnum`)
   - `required` — `True`/`False`
   - `description` — what the model should look for
   - `pattern` — optional regex string the value must match (e.g. for IDs)

If the user says "use X form" and X is one of the bundled templates under
`.claude/skills/add-form-schema/templates/`, just paste that template into
`examples.py` and skip the field-by-field interview.

## Files to touch (in this order)

### 1. Append to `src/bedrock_strands_agent/extraction/schemas/examples.py`

Add the `FormSchema` literal, then add a matching `register_schema(...)` call
at the bottom of the file (next to the existing `register_schema(IRS_W9)` and
`register_schema(INVOICE)` calls).

> Type-only note: `FieldType` is a `StrEnum` (Python 3.11+). Do **not** rewrite
> it as `class FieldType(str, Enum)` — ruff's UP042 will reject that.

### 2. Add a test

Either extend `tests/test_extraction_service.py` or create
`tests/test_<schema_name>_extraction.py`. Mirror the existing pattern: build a
canned-JSON payload, push it through `_service(settings, payload)`, assert on
the parsed `result.fields`.

The minimum useful test is one that supplies **all required fields** and
asserts the round-trip succeeds with no `MISSING_REQUIRED:` warnings. If the
schema has a `pattern` field, also add a test that mismatches the pattern and
asserts the warning fires (mirror `test_pattern_mismatch_warns`).

If you create a new test file, copy the `_service` helper from
`test_extraction_service.py` or import it; do **not** monkey-patch
`ExtractionService` directly.

### 3. Update `README.md`

The `## Adding a schema` section (around line 141) shows a `purchase_order`
example. If the schema you're adding is *the* canonical example
(`purchase_order`, generic and well-known), update that snippet to be the full
definition you just wrote — otherwise add the new schema name to any
"registered schemas" list and leave the worked example alone.

If there is no explicit "currently registered" list, the README's `/schemas`
example output (around line 39) implicitly enumerates the registry — leaving
that unchanged is fine since it's a `curl` line, not a hard-coded list.

### 4. Update `CHANGELOG.md`

Under `## [Unreleased]` → `### Added`, append a bullet such as:

```markdown
- `<schema_name>` schema for <one-line description>.
```

## Worked example: `purchase_order`

Paste this block at the bottom of `examples.py`, **above** the
`register_schema(IRS_W9)` line:

```python
PURCHASE_ORDER = FormSchema(
    name="purchase_order",
    version="1.0.0",
    description=(
        "Standard purchase order: PO number, vendor, ship-to, line-item "
        "totals, and currency. Line-item arrays are out of scope; this "
        "schema captures only the header and totals."
    ),
    fields=(
        FieldDefinition(
            name="po_number",
            description="Unique purchase order identifier, formatted PO-NNNNNN.",
            type=FieldType.STRING,
            required=True,
            pattern=r"^PO-\d{6}$",
        ),
        FieldDefinition(
            name="po_date",
            description="Date the PO was issued, ISO-8601 (YYYY-MM-DD).",
            type=FieldType.DATE,
            required=True,
        ),
        FieldDefinition(
            name="vendor_name",
            description="Name of the vendor / supplier the PO is issued to.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="ship_to",
            description="Ship-to name and address (single line).",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="total",
            description="Grand total of the PO as a decimal number.",
            type=FieldType.CURRENCY,
            required=True,
        ),
        FieldDefinition(
            name="currency",
            description="ISO-4217 currency code (e.g. USD, EUR).",
            type=FieldType.STRING,
        ),
    ),
)
```

Then add at the bottom (next to the other `register_schema` lines):

```python
register_schema(PURCHASE_ORDER)
```

A matching minimal test (drop in `tests/test_extraction_service.py`):

```python
def test_purchase_order_round_trip(settings: Settings) -> None:
    payload = (
        '{"fields": ['
        '{"name": "po_number", "value": "PO-000123", "confidence": 0.95},'
        '{"name": "po_date", "value": "2026-04-15", "confidence": 0.95},'
        '{"name": "vendor_name", "value": "Acme Co", "confidence": 0.95},'
        '{"name": "ship_to", "value": "1 Main St, Springfield", "confidence": 0.9},'
        '{"name": "total", "value": 1234.56, "confidence": 0.95}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="d", schema_name="purchase_order")
    assert not [w for w in result.warnings if w.startswith("MISSING_REQUIRED:")]
    by_name = {f.name: f.value for f in result.fields}
    assert by_name["po_number"] == "PO-000123"


def test_purchase_order_po_pattern_mismatch_warns(settings: Settings) -> None:
    payload = (
        '{"fields": ['
        '{"name": "po_number", "value": "BAD", "confidence": 1.0},'
        '{"name": "po_date", "value": "2026-04-15", "confidence": 1.0},'
        '{"name": "vendor_name", "value": "Acme", "confidence": 1.0},'
        '{"name": "ship_to", "value": "1 Main St", "confidence": 1.0},'
        '{"name": "total", "value": 1, "confidence": 1.0}'
        "]}"
    )
    svc = _service(settings, payload)
    result = svc.extract(document_text="d", schema_name="purchase_order")
    assert any("pattern mismatch" in w and "po_number" in w for w in result.warnings)
```

CHANGELOG entry:

```markdown
- `purchase_order` schema for header + totals on standard purchase orders.
```

## Bundled templates

Two ready-to-use IRS schemas live under `templates/`. Both are valid Python
snippets — paste them into `examples.py` and add a matching `register_schema`
call.

- `templates/1099_misc.py.tmpl` — IRS Form 1099-MISC (miscellaneous income).
- `templates/k1.py.tmpl` — IRS Schedule K-1 (Form 1065) partner share of
  income.

To use one: open the file, copy the constant definition into `examples.py`
above the `register_schema(IRS_W9)` line, then append e.g.
`register_schema(IRS_1099_MISC)` next to the existing registrations.

## Verification

After making changes, all of these MUST pass:

```bash
# 1. The new schema is in the registry
uv run python -c "from bedrock_strands_agent.extraction.schemas import list_schema_names; print(list_schema_names())"
# expect: [..., '<your_schema_name>', ...]

# 2. Full quality gate (ruff + mypy + pytest >=80% cov + bandit)
make check

# 3. Sanity-check the new endpoint listing
uv run python -m bedrock_strands_agent serve  # then in another shell:
# curl http://localhost:8000/schemas
```

If `make check` complains about coverage on a new schema-only test file,
either (a) move the test into the existing `test_extraction_service.py`, or
(b) skip the new file and just append the new test functions to
`test_extraction_service.py`. The existing file is the path of least
resistance.

## Things to avoid

- Do not import `Enum` from `enum` to redefine `FieldType` — it already exists
  as a `StrEnum`.
- Do not register the schema from `__init__.py`; the documented entry point is
  the bottom of `examples.py`.
- Do not make `FormSchema.fields` a `list` — it is a `tuple` and the model is
  frozen. Use `(...)` not `[...]`.
- Do not skip the `register_schema(MY_SCHEMA)` call. Defining the constant is
  not enough; without registration `get_schema(...)` raises `KeyError`.
