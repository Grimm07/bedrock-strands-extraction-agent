"""Built-in `FormSchema` definitions: `irs_w9` and `invoice`."""

from __future__ import annotations

from bedrock_strands_agent.extraction.models import (
    FieldDefinition,
    FieldType,
    FormSchema,
)
from bedrock_strands_agent.extraction.schemas import register_schema

IRS_W9 = FormSchema(
    name="irs_w9",
    version="1.0.0",
    description=(
        "IRS Form W-9: Request for Taxpayer Identification Number "
        "and Certification (US). Captures the legal name, business "
        "name, federal tax classification, address, taxpayer "
        "identification numbers, and signature date."
    ),
    fields=(
        FieldDefinition(
            name="legal_name",
            description="Name of the entity exactly as shown on its income tax return.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="business_name",
            description="Business name / disregarded entity name, if different from above.",
            type=FieldType.STRING,
        ),
        FieldDefinition(
            name="federal_tax_classification",
            description=(
                "One of: Individual/sole proprietor, C Corporation, S Corporation, "
                "Partnership, Trust/estate, Limited liability company, or Other."
            ),
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="address",
            description="Street address (number, street, and apt or suite no.).",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="city_state_zip",
            description="City, state, and ZIP code on a single line.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="ssn",
            description="Social Security Number formatted as 999-99-9999.",
            type=FieldType.SSN,
            pattern=r"^\d{3}-\d{2}-\d{4}$",
        ),
        FieldDefinition(
            name="ein",
            description="Employer Identification Number formatted as 99-9999999.",
            type=FieldType.EIN,
            pattern=r"^\d{2}-\d{7}$",
        ),
        FieldDefinition(
            name="signature_date",
            description="Date the form was signed, in ISO-8601 (YYYY-MM-DD).",
            type=FieldType.DATE,
        ),
    ),
)


INVOICE = FormSchema(
    name="invoice",
    version="1.0.0",
    description=(
        "Generic invoice schema with header, totals, and currency. "
        "Line items are captured at the top level; nested item arrays "
        "are out of scope for this version."
    ),
    fields=(
        FieldDefinition(
            name="invoice_number",
            description="Unique invoice identifier as printed on the document.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="invoice_date",
            description="The date the invoice was issued, ISO-8601 (YYYY-MM-DD).",
            type=FieldType.DATE,
            required=True,
        ),
        FieldDefinition(
            name="due_date",
            description="Payment due date, ISO-8601 (YYYY-MM-DD).",
            type=FieldType.DATE,
        ),
        FieldDefinition(
            name="vendor_name",
            description="Name of the vendor / supplier issuing the invoice.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="bill_to",
            description="Name (and optionally address) of the party being billed.",
            type=FieldType.STRING,
            required=True,
        ),
        FieldDefinition(
            name="subtotal",
            description="Pre-tax subtotal as a decimal number.",
            type=FieldType.CURRENCY,
        ),
        FieldDefinition(
            name="tax",
            description="Total tax amount as a decimal number.",
            type=FieldType.CURRENCY,
        ),
        FieldDefinition(
            name="total",
            description="Grand total as a decimal number.",
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

register_schema(IRS_W9)
register_schema(INVOICE)
