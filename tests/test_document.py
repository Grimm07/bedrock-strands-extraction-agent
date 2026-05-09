"""Tests for :mod:`bedrock_strands_agent.extraction.document`."""

from __future__ import annotations

import io

import pytest
from PIL import Image
from pypdf.errors import PdfReadError

from bedrock_strands_agent.extraction.document import (
    MAX_UPLOAD_BYTES,
    UnsupportedDocumentError,
    process_upload,
)


def _png_bytes(width: int = 20, height: int = 20) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), color="blue").save(buf, format="JPEG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# image path
# --------------------------------------------------------------------------- #
def test_png_routes_to_image_mode() -> None:
    out = process_upload(_png_bytes(), "image/png")
    assert out.mode == "image"
    assert out.image_format == "png"
    assert len(out.images) == 1
    assert out.text is None


def test_jpeg_routes_to_image_mode() -> None:
    out = process_upload(_jpeg_bytes(), "image/jpeg")
    assert out.mode == "image"
    assert out.image_format == "jpeg"


def test_alternate_jpeg_mime_aliases_to_jpeg() -> None:
    out = process_upload(_jpeg_bytes(), "image/jpg")
    assert out.image_format == "jpeg"


def test_content_type_with_charset_param_is_normalised() -> None:
    out = process_upload(_png_bytes(), "image/png; charset=binary")
    assert out.mode == "image"


def test_invalid_image_bytes_raise() -> None:
    with pytest.raises(UnsupportedDocumentError, match="invalid image"):
        process_upload(b"not an image", "image/png")


def test_unsupported_mime_raises() -> None:
    with pytest.raises(UnsupportedDocumentError, match="unsupported"):
        process_upload(b"<html/>", "text/html")


def test_oversized_upload_raises() -> None:
    huge = b"\x00" * (MAX_UPLOAD_BYTES + 1)
    with pytest.raises(UnsupportedDocumentError, match="exceeds"):
        process_upload(huge, "image/png")


# --------------------------------------------------------------------------- #
# pdf path (mocking pypdf)
# --------------------------------------------------------------------------- #
class _StubPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_text(self) -> str:
        return self._text


def _stub_reader_factory(pages: list[_StubPage]):
    class _Reader:
        def __init__(self, *_a: object, **_kw: object) -> None:
            return

        @property
        def pages(self) -> list[_StubPage]:
            return pages

    return _Reader


def test_pdf_with_embedded_text_routes_to_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.document.PdfReader",
        _stub_reader_factory([_StubPage("Hello world")]),
    )
    out = process_upload(b"%PDF-1.4 stub", "application/pdf")
    assert out.mode == "text"
    assert out.text is not None
    assert "Hello world" in out.text


def test_pdf_concatenates_multiple_pages_with_blank_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.document.PdfReader",
        _stub_reader_factory([_StubPage("page one"), _StubPage("page two")]),
    )
    out = process_upload(b"%PDF-1.4 stub", "application/pdf")
    assert out.text == "page one\n\npage two"


def _image_only_pdf_bytes(num_pages: int = 1) -> bytes:
    """Build an image-only PDF (no text layer) — a scanned-PDF surrogate.

    PIL's ``save(format="PDF")`` writes each page as a single image XObject
    with no text layer, so ``pypdf.PdfPage.extract_text()`` returns ``""``
    for every page. This is structurally simpler than a real scanner output
    (which uses CCITT G4 or DCTDecode streams); good enough to exercise the
    rasterisation path but not a substitute for fixture-based tests against
    real scans, which a future change should add.
    """
    palette = ("red", "green", "blue", "yellow", "purple", "orange")
    pages = [
        Image.new("RGB", (120, 160), color=palette[i % len(palette)]) for i in range(num_pages)
    ]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()


def test_pdf_without_embedded_text_renders_to_images() -> None:
    """A scanned-style (image-only) PDF is rasterised server-side and routed
    to the vision path. Validates the v0.3 follow-on to ADR-0008.
    """
    out = process_upload(_image_only_pdf_bytes(num_pages=2), "application/pdf")
    assert out.mode == "image"
    assert out.image_format == "png"
    assert out.text is None
    assert len(out.images) == 2
    # PNG signature must appear at the start of every rendered page so the
    # downstream Bedrock Converse call sees valid image bytes.
    for png in out.images:
        assert png.startswith(b"\x89PNG\r\n\x1a\n"), "rendered page is not a valid PNG"


def test_rasterized_pdf_caps_pages_at_module_constant() -> None:
    """A scanned PDF longer than ``RASTER_MAX_PAGES`` is truncated, not rejected.

    The cap exists so a 50-page scan can't fan out into a 50-image Converse
    request. Callers that need every page should split client-side.
    """
    from bedrock_strands_agent.extraction.document import RASTER_MAX_PAGES

    over_cap = RASTER_MAX_PAGES + 2
    out = process_upload(_image_only_pdf_bytes(num_pages=over_cap), "application/pdf")
    assert out.mode == "image"
    assert len(out.images) == RASTER_MAX_PAGES


def test_unparseable_pdf_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _bad(*_a: object, **_kw: object) -> object:
        raise PdfReadError("malformed")

    monkeypatch.setattr("bedrock_strands_agent.extraction.document.PdfReader", _bad)
    with pytest.raises(UnsupportedDocumentError, match="could not read PDF"):
        process_upload(b"<garbage>", "application/pdf")


def test_pdf_with_one_failing_page_does_not_break_overall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that raises during extract_text should be skipped, not abort."""

    class _BadPage:
        def extract_text(self) -> str:
            raise RuntimeError("page corrupted")

    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.document.PdfReader",
        _stub_reader_factory([_BadPage(), _StubPage("good content")]),  # type: ignore[list-item]
    )
    out = process_upload(b"%PDF-1.4 stub", "application/pdf")
    assert out.text == "good content"


def test_pdf_text_extraction_caps_at_max_document_text_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF text extraction past the document-text cap must reject at the
    upload layer.

    The JSON `/extract` endpoint enforces `max_length=200_000` on
    `document_text` via Pydantic; without this defence the upload path
    could send a much larger string straight into the prompt by abusing
    PDF compression. ADR-0011 promises this parity.
    """
    from bedrock_strands_agent.extraction.document import MAX_DOCUMENT_TEXT_CHARS

    # One char past the cap when joined with "\n\n" between two pages.
    half = "x" * (MAX_DOCUMENT_TEXT_CHARS // 2)
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.document.PdfReader",
        _stub_reader_factory([_StubPage(half), _StubPage(half)]),
    )
    with pytest.raises(UnsupportedDocumentError, match=r"exceeding the .* cap"):
        process_upload(b"%PDF-1.4 stub", "application/pdf")
