"""Command-line entry point: `python -m bedrock_strands_agent <subcommand>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bedrock_strands_agent.config import get_settings


def _serve(args: argparse.Namespace) -> int:
    """Run the FastAPI app under uvicorn."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "bedrock_strands_agent.api.app:create_app",
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
        factory=True,
        log_config=None,
        reload=args.reload,
    )
    return 0


def _extract(args: argparse.Namespace) -> int:
    """One-shot extract: read a file (or stdin), print the result as JSON."""
    from bedrock_strands_agent.extraction import ExtractionService

    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")

    settings = get_settings()
    service = ExtractionService.from_settings(settings)
    result = service.extract(document_text=text, schema_name=args.schema)
    sys.stdout.write(result.model_dump_json(by_alias=True, indent=2))
    sys.stdout.write("\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bedrock-strands-agent",
        description="Form-extraction agent on Strands + Bedrock.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the HTTP server.")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_serve)

    extract = sub.add_parser("extract", help="Run a one-shot extraction.")
    extract.add_argument("--schema", required=True, help="Registered schema name.")
    extract.add_argument("--file", required=True, help="Path to a text file, or '-' for stdin.")
    extract.set_defaults(func=_extract)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
