"""Live-Bedrock integration tests.

These are skipped by default. Set `RUN_INTEGRATION_TESTS=1` and provide AWS
credentials with `bedrock:InvokeModel` access to run them. They are slow and
incur model-invocation cost.
"""

from __future__ import annotations

import os

import pytest

from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionService

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_INTEGRATION_TESTS") != "1",
        reason="Set RUN_INTEGRATION_TESTS=1 to run live Bedrock tests.",
    ),
]


def test_extract_invoice_against_live_bedrock() -> None:
    """Smoke test that real Bedrock returns parseable JSON for the invoice schema."""
    settings = Settings()
    service = ExtractionService.from_settings(settings)
    result = service.extract(
        document_text=(
            "INVOICE #INV-9001\n"
            "Date: 2026-04-15\n"
            "Vendor: Acme Widgets, Inc.\n"
            "Bill to: Wile E. Coyote\n"
            "Total: USD 1234.56\n"
        ),
        schema_name="invoice",
    )
    by_name = {f.name: f.value for f in result.fields}
    assert by_name["invoice_number"] == "INV-9001"
    assert by_name["total"] in {1234.56, "1234.56"}
