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


def test_retry_prompt_includes_errors() -> None:
    schema = get_schema("irs_w9")
    rendered = PromptRenderer().retry(
        schema=schema,
        document_text="doc",
        errors=["MISSING_REQUIRED:legal_name", "MISSING_REQUIRED:address"],
    )
    assert "MISSING_REQUIRED:legal_name" in rendered
    assert "MISSING_REQUIRED:address" in rendered
    assert "doc" in rendered


def test_strict_undefined() -> None:
    """A missing variable in a template surface should raise, not silently empty."""
    renderer = PromptRenderer()
    # PackageLoader caches at module level; reach into the env to add an inline
    # template that intentionally references a missing var.
    template = renderer._env.from_string("{{ does_not_exist }}")
    with pytest.raises(UndefinedError):
        template.render()
