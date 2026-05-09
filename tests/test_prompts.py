from __future__ import annotations

import pytest
from jinja2.exceptions import UndefinedError

from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction.schemas import get_schema


def test_system_prompt_contains_settings_context() -> None:
    s = Settings(service_name="my-svc", service_env="dev", bedrock_model_id="m1")
    rendered = PromptRenderer().system(settings=s)
    assert "my-svc" in rendered
    assert "dev" in rendered
    assert "m1" in rendered


def test_extract_prompt_lists_every_field() -> None:
    schema = get_schema("invoice")
    rendered = PromptRenderer().extract(schema=schema, document_text="hi")
    for f in schema.fields:
        assert f.name in rendered
    assert "hi" in rendered


def test_retry_prompt_includes_validation_errors() -> None:
    schema = get_schema("irs_w9")
    rendered = PromptRenderer().retry(
        schema=schema,
        document_text="doc",
        validation_errors=["MISSING_REQUIRED:legal_name", "MISSING_REQUIRED:address"],
    )
    assert "MISSING_REQUIRED:legal_name" in rendered
    assert "MISSING_REQUIRED:address" in rendered
    assert "doc" in rendered


def test_retry_prompt_includes_citation_failures() -> None:
    schema = get_schema("irs_w9")
    rendered = PromptRenderer().retry(
        schema=schema,
        document_text="doc",
        citation_failures=[{"name": "ssn", "excerpt": "123-45-6789", "overlap_pct": 25}],
    )
    assert "ssn" in rendered
    assert "123-45-6789" in rendered
    assert "25%" in rendered


def test_retry_prompt_includes_validator_failures() -> None:
    schema = get_schema("irs_w9")
    rendered = PromptRenderer().retry(
        schema=schema,
        document_text="doc",
        validator_failures=[{"name": "ssn", "value": "1234-56-789", "reason": "not a valid SSN"}],
    )
    assert "1234-56-789" in rendered
    assert "not a valid SSN" in rendered


def test_retry_prompt_omits_empty_sections() -> None:
    schema = get_schema("irs_w9")
    rendered = PromptRenderer().retry(
        schema=schema,
        document_text="doc",
    )
    # No section markers should appear when all lists are empty.
    assert "Structural / required-field issues:" not in rendered
    assert "do NOT appear verbatim" not in rendered
    assert "failed semantic validation" not in rendered


def test_template_versions_are_parsed() -> None:
    renderer = PromptRenderer()
    for name in ("system.j2", "extract.j2", "retry.j2", "extract_image.j2"):
        v = renderer.template_version(name)
        # Each shipped template must carry a non-default version.
        assert v != "0.0.0", f"{name} is missing its template_version comment"
        assert v.count(".") >= 2, f"{name} version {v!r} should be SemVer-shaped"


def test_extract_image_prompt_lists_every_field() -> None:
    schema = get_schema("invoice")
    rendered = PromptRenderer().extract_image(schema=schema)
    for f in schema.fields:
        assert f.name in rendered
    # Vision template must NOT include the text-mode 'Document:' section.
    assert "Document:\n" not in rendered
    # And it must instruct the model to read the attached image(s).
    assert "image" in rendered.lower()


def test_template_version_unknown_template_raises() -> None:
    with pytest.raises(KeyError):
        PromptRenderer().template_version("does-not-exist.j2")


def test_strict_undefined() -> None:
    """A missing variable in a template surface should raise, not silently empty."""
    renderer = PromptRenderer()
    # PackageLoader caches at module level; reach into the env to add an inline
    # template that intentionally references a missing var.
    template = renderer._env.from_string("{{ does_not_exist }}")
    with pytest.raises(UndefinedError):
        template.render()
