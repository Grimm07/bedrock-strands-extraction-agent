"""Jinja2 prompt rendering.

`StrictUndefined` ensures any missing variable is a loud `UndefinedError`
rather than a silent empty string. Templates live in
`bedrock_strands_agent/templates/` and ship inside the wheel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

if TYPE_CHECKING:
    from bedrock_strands_agent.config import Settings
    from bedrock_strands_agent.extraction.models import FormSchema


class PromptRenderer:
    """Render the system, extract, and retry prompts from package templates."""

    def __init__(self) -> None:
        """Build the Jinja environment with package-bound template loading."""
        # Prompts are not HTML; HTML autoescape would mangle code fences.
        self._env = Environment(
            loader=PackageLoader("bedrock_strands_agent", "templates"),
            autoescape=select_autoescape(default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )

    def system(self, *, settings: Settings) -> str:
        """Render the system prompt for a fresh agent."""
        return self._env.get_template("system.j2").render(
            service_name=settings.service_name,
            service_env=settings.service_env,
            model_id=settings.bedrock_model_id,
        )

    def extract(self, *, schema: FormSchema, document_text: str) -> str:
        """Render the per-request extraction prompt."""
        return self._env.get_template("extract.j2").render(
            schema=schema, document_text=document_text
        )

    def retry(self, *, schema: FormSchema, document_text: str, errors: list[str]) -> str:
        """Render a retry prompt that includes the validation errors."""
        return self._env.get_template("retry.j2").render(
            schema=schema, document_text=document_text, errors=errors
        )
