"""OpenTelemetry tracing setup.

Three modes, in order of preference:

1. `OTEL_EXPORTER_OTLP_ENDPOINT` set → ship spans via OTLP/gRPC.
2. `STRANDS_OTEL_ENABLE_CONSOLE_EXPORT=true` → console exporter.
3. Neither → no exporter (the trace API stays safe to call).

`BotocoreInstrumentor` is always enabled so Bedrock client calls produce
spans regardless of the active exporter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from bedrock_strands_agent import __version__
from bedrock_strands_agent.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

_initialized: bool = False


def configure_tracing(settings: Settings) -> None:
    """Configure the global TracerProvider exactly once per process."""
    global _initialized
    if _initialized:
        return

    resource = Resource.create(
        {
            "service.name": settings.effective_otel_service_name,
            "service.version": __version__,
            "deployment.environment": settings.service_env,
        }
    )
    provider = TracerProvider(resource=resource)

    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=settings.otel_exporter_otlp_endpoint,
                    insecure=settings.otel_exporter_otlp_insecure,
                )
            )
        )
    elif settings.strands_otel_enable_console_export:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    # opentelemetry-instrumentation-botocore lacks type stubs.
    BotocoreInstrumentor().instrument()  # type: ignore[no-untyped-call]
    _initialized = True


def instrument_fastapi(app: FastAPI) -> None:
    """Instrument a FastAPI app for OTel. Imported lazily to keep CLI fast."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def reset_for_tests() -> None:
    """Clear the singleton flag so a test can re-configure tracing."""
    global _initialized
    _initialized = False
