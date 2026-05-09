"""A2A AgentExecutor that wraps `ExtractionService` directly.

Rather than letting the model's free-text Bedrock response flow back to
the A2A caller (which would discard the schema validation, citation
verification, retry loop, and vision-mode grounding from ADR-0011), we
require A2A messages to carry a structured `DataPart` payload, route it
through `ExtractionService.extract`, and return the validated
`ExtractionResult` as a `DataPart` artifact.

Cancellation is unsupported: extractions are short-lived (single-digit
seconds in the happy path; tens of seconds with grounding + retries) and
the integration does not expose a cancellable streaming API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from a2a.server.agent_execution import AgentExecutor
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    DataPart,
    Part,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import new_task
from a2a.utils.errors import ServerError
from pydantic import ValidationError

from bedrock_strands_agent.api.schemas import ExtractRequestBody
from bedrock_strands_agent.extraction import ExtractionError

if TYPE_CHECKING:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.events import EventQueue
    from a2a.types import Message

    from bedrock_strands_agent.extraction import ExtractionService

LOGGER = logging.getLogger(__name__)

_REQUIRED_PAYLOAD_FIELDS: tuple[str, ...] = ("schema_name", "document_text")


class ExtractionAgentExecutor(AgentExecutor):
    """A2A executor that delegates to `ExtractionService.extract`.

    The A2A message must contain a `DataPart` whose `data` is a JSON
    object with at least `schema_name` and `document_text`. Optional
    fields: `schema_version` (pin), `document_id` (correlation id).
    Anything else in the payload is ignored.
    """

    def __init__(self, extraction_service: ExtractionService) -> None:
        """Bind the executor to a pre-built `ExtractionService` instance."""
        self._service = extraction_service

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Process an A2A request and emit task events."""
        task = context.current_task
        if task is None:
            task = new_task(context.message)  # type: ignore[arg-type]
            await event_queue.enqueue_event(task)

        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            payload = self._extract_payload(context.message)
        except ValueError as exc:
            await self._fail(updater, str(exc))
            return

        try:
            # ExtractionService.extract is synchronous and may take seconds
            # (Bedrock latency + retry loop). Offload to a worker thread so
            # we do not block the A2A event loop. Mirrors the same pattern
            # `/extract` and `/extract/document` already use in api/routes.py.
            result = await asyncio.to_thread(
                self._service.extract,
                document_text=payload.document_text,
                schema_name=payload.schema_name,
                schema_version=payload.schema_version,
                document_id=payload.document_id,
            )
        except KeyError as exc:
            # Schema not registered (or schema_version pin mismatch).
            await self._fail(updater, f"unknown schema: {exc}")
            return
        except ExtractionError as exc:
            LOGGER.exception("a2a extraction failed")
            await self._fail(updater, f"extraction failed: {exc}")
            return

        result_dict = result.model_dump(by_alias=True, mode="json")
        await updater.add_artifact(
            [Part(root=DataPart(data=result_dict))],
            name="extraction_result",
        )
        await updater.complete()

    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        """Reject cancellation requests.

        Extractions are short-lived; the executor does not expose a
        cancellation hook. Returning ``UnsupportedOperationError`` is
        the spec-conformant way for an A2A executor to decline.
        """
        task = context.current_task
        if task is not None:
            LOGGER.info("a2a cancel requested for task %s; rejecting", task.id)
        raise ServerError(error=UnsupportedOperationError())

    @staticmethod
    def _extract_payload(message: Message | None) -> ExtractRequestBody:
        """Find the DataPart payload and validate it through Pydantic.

        Routes through the same ``ExtractRequestBody`` model the HTTP
        ``/extract`` endpoint uses.

        ``ExtractRequestBody`` enforces ``extra='forbid'``,
        ``document_text`` ``min_length=1`` / ``max_length=200_000``, and the
        ``schema_version`` shape. Routing the A2A payload through it gives
        every transport the same input bounds (otherwise the A2A path would
        be a second untrusted ingress without the DoS / type-confusion
        protections that ADR-0011 documents).

        Raises ``ValueError`` on missing-DataPart, FilePart-only requests,
        or any Pydantic validation failure. The HTTP error path
        translates these to A2A task-failed messages with the same
        guidance.
        """
        if message is None or not message.parts:
            msg = "A2A message has no parts"
            raise ValueError(msg)

        for part in message.parts:
            root = part.root
            if isinstance(root, DataPart) and isinstance(root.data, dict):
                try:
                    return ExtractRequestBody.model_validate(root.data)
                except ValidationError as exc:
                    # Surface field-level errors so the A2A caller can fix
                    # the payload without round-tripping through Bedrock.
                    detail = "; ".join(
                        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                    )
                    raise ValueError(f"A2A DataPart payload failed validation: {detail}") from exc

        msg = (
            "A2A extraction request must include a DataPart with at least "
            f"{', '.join(_REQUIRED_PAYLOAD_FIELDS)} fields. Send your payload as "
            'a `DataPart` (parts: [{"kind": "data", "data": {...}}]), not a '
            "TextPart or FilePart. Vision-mode extractions (image / PDF "
            "uploads) are not yet supported over A2A — use the HTTP "
            "POST /extract/document endpoint. See ADR-0012."
        )
        raise ValueError(msg)

    @staticmethod
    async def _fail(updater: TaskUpdater, detail: str) -> None:
        """Mark the current task as failed with a human-readable message."""
        try:
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(text=detail))],
                ),
            )
        except RuntimeError:  # pragma: no cover - already-terminal task
            LOGGER.debug("a2a task already in terminal state, cannot mark failed")
