"""Jinja2 prompt rendering.

`StrictUndefined` ensures any missing variable is a loud `UndefinedError`
rather than a silent empty string. Templates live in
`bedrock_strands_agent/templates/` and ship inside the wheel.

Each template carries a leading ``{# template_version: X.Y.Z #}`` comment
(Phase C7) so spans (Phase D1) and the promptfoo eval (Phase D7) can A/B
template versions without re-reading the file at every call site.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape

if TYPE_CHECKING:
    from bedrock_strands_agent.config import Settings
    from bedrock_strands_agent.extraction.models import FormSchema

_VERSION_RE: Final = re.compile(r"\{#\s*template_version:\s*(?P<v>[\w.\-]+)\s*#\}")
_TEMPLATE_NAMES: Final = (
    "system.j2",
    "extract.j2",
    "retry.j2",
    "extract_image.j2",
    "verify_grounding.j2",
)
_UNVERSIONED: Final = "0.0.0"


class PromptRenderer:
    """Render the system, extract, and retry prompts from package templates."""

    def __init__(self) -> None:
        """Build the Jinja environment and parse template versions."""
        # Prompts are not HTML; HTML autoescape would mangle code fences.
        self._env = Environment(
            loader=PackageLoader("bedrock_strands_agent", "templates"),
            autoescape=select_autoescape(default=False),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._versions: dict[str, str] = self._collect_versions()

    def _collect_versions(self) -> dict[str, str]:
        """Parse the ``template_version`` comment from each known template."""
        loader = self._env.loader
        if loader is None:  # pragma: no cover — defensive
            return dict.fromkeys(_TEMPLATE_NAMES, _UNVERSIONED)
        out: dict[str, str] = {}
        for name in _TEMPLATE_NAMES:
            source, _, _ = loader.get_source(self._env, name)  # type: ignore[no-untyped-call]
            match = _VERSION_RE.search(source)
            out[name] = match.group("v") if match else _UNVERSIONED
        return out

    def template_version(self, name: str) -> str:
        """Return the parsed ``template_version`` for ``name``.

        Returns ``"0.0.0"`` for known templates that lack a version comment
        and raises ``KeyError`` for unknown template names.
        """
        return self._versions[name]

    def system(self, *, settings: Settings) -> str:
        """Render the system prompt for a fresh agent."""
        return self._env.get_template("system.j2").render(
            service_name=settings.service_name,
            service_env=settings.service_env,
            model_id=settings.bedrock_model_id,
        )

    def extract(self, *, schema: FormSchema, document_text: str) -> str:
        """Render the per-request text-mode extraction prompt."""
        return self._env.get_template("extract.j2").render(
            schema=schema, document_text=document_text
        )

    def extract_image(self, *, schema: FormSchema) -> str:
        """Render the per-request vision-mode extraction prompt.

        No ``document_text`` parameter — the document is delivered as the
        attached image content blocks in :func:`invoke_multimodal`.
        """
        return self._env.get_template("extract_image.j2").render(schema=schema)

    def verify_grounding(
        self,
        *,
        schema: FormSchema,
        candidates: list[dict[str, Any]],
    ) -> str:
        """Render the second-pass grounding-verification prompt for vision mode.

        Used by :meth:`ExtractionService._verify_vision_grounding` after the
        first vision extraction completes. The model is asked to verify each
        candidate's ``value`` is genuinely present in the attached image(s);
        ungrounded fields surface as ``vision_grounding_failures`` and trigger
        a retry. See ADR-0011 ("Vision-mode grounding") for the rationale.
        """
        return self._env.get_template("verify_grounding.j2").render(
            schema=schema, candidates=candidates
        )

    def retry(
        self,
        *,
        schema: FormSchema,
        document_text: str,
        validation_errors: list[str] | None = None,
        citation_failures: list[dict[str, Any]] | None = None,
        validator_failures: list[dict[str, Any]] | None = None,
        value_anchor_failures: list[dict[str, Any]] | None = None,
        vision_grounding_failures: list[dict[str, Any]] | None = None,
    ) -> str:
        """Render a retry prompt with up to five sections of error context.

        Args:
            schema: The schema being extracted.
            document_text: The full document, repeated below the error sections.
            validation_errors: Free-text strings (typically ``MISSING_REQUIRED:*``).
            citation_failures: Dicts ``{"name", "excerpt", "overlap_pct"}`` for
                excerpts that don't appear verbatim in the document.
            validator_failures: Dicts ``{"name", "value", "reason"}``.
            value_anchor_failures: Dicts ``{"name", "excerpt", "value"}`` for
                fields where the excerpt IS verbatim in the document but the
                emitted value is not contained inside that excerpt — the
                schema-confusion case from ADR-0011.
            vision_grounding_failures: Dicts ``{"name", "value"}`` for vision-
                mode fields the second-pass grounding verifier flagged as
                NOT present in the attached image. Vision-only.
        """
        return self._env.get_template("retry.j2").render(
            schema=schema,
            document_text=document_text,
            validation_errors=validation_errors or [],
            citation_failures=citation_failures or [],
            validator_failures=validator_failures or [],
            value_anchor_failures=value_anchor_failures or [],
            vision_grounding_failures=vision_grounding_failures or [],
        )
