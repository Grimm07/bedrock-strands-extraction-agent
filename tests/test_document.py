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


def test_pdf_without_embedded_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bedrock_strands_agent.extraction.document.PdfReader",
        _stub_reader_factory([_StubPage(""), _StubPage("   ")]),
    )
    with pytest.raises(UnsupportedDocumentError, match="no embedded text"):
        process_upload(b"%PDF-1.4 stub", "application/pdf")


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
