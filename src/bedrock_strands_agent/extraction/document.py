"""Document upload detection and pre-processing.

Used by ``POST /extract/document``. Inspects the upload's MIME type and
contents and decides which extraction path to take:

* PDFs with embedded text → text path (``ExtractionService.extract``).
* PDFs **without** embedded text (scans) → server-side rasterisation via
  ``pypdfium2`` at :data:`RASTER_DPI` DPI, capped at :data:`RASTER_MAX_PAGES`
  pages, then routed to the vision path as PNG images. ADR-0008 originally
  deferred this to v0.3; that follow-on ships here.
* Images (PNG / JPEG / WebP / GIF) → vision path
  (``ExtractionService._extract_via_vision`` → :func:`invoke_multimodal`).

See :doc:`docs/extraction-modes.md` for the full design rationale.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from typing import Final, Literal

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

LOGGER = logging.getLogger(__name__)

ImageFormat = Literal["png", "jpeg", "gif", "webp"]
DocumentMode = Literal["text", "image"]


SUPPORTED_IMAGE_TYPES: Final[dict[str, ImageFormat]] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}

# 5 MiB cap. Bedrock multimodal accepts smaller; oversized uploads are
# almost always scans at unnecessary DPI. Re-encode at 200-300 DPI before
# uploading.
MAX_UPLOAD_BYTES: Final = 5 * 1024 * 1024

# Server-side rasterisation settings for image-only PDFs. 200 DPI is the
# accepted sweet spot for OCR-grade vision input — 300 DPI roughly doubles
# the rendered byte size for a marginal accuracy gain on Claude vision.
RASTER_DPI: Final = 200
# Page cap. A multi-page Converse request stacks input tokens linearly per
# image; without a cap a 50-page scan would balloon the bill and likely hit
# the model context window. Callers needing every page should split client-
# side before upload.
RASTER_MAX_PAGES: Final = 5


class UnsupportedDocumentError(ValueError):
    """Raised when an uploaded document cannot be routed to either path."""


@dataclass(frozen=True)
class DocumentInput:
    """Result of inspecting an uploaded document.

    ``mode == "text"`` populates ``text``; ``mode == "image"`` populates
    ``images`` and ``image_format``. Exactly one of the two shapes is
    valid in any given instance.
    """

    mode: DocumentMode
    text: str | None = None
    images: tuple[bytes, ...] = field(default_factory=tuple)
    image_format: ImageFormat | None = None


def process_upload(content: bytes, content_type: str) -> DocumentInput:
    """Detect the upload type and return a :class:`DocumentInput`.

    Args:
        content: Raw bytes of the upload.
        content_type: HTTP ``Content-Type`` (parameters such as ``charset=``
            are stripped automatically).

    Returns:
        A :class:`DocumentInput` ready to be handed to
        :class:`ExtractionService`.

    Raises:
        UnsupportedDocumentError: When the upload exceeds
            :data:`MAX_UPLOAD_BYTES`, the MIME type is unsupported, the
            content cannot be parsed as the declared type, or a PDF that
            has neither extractable text nor renders cleanly via PDFium.
    """
    if len(content) > MAX_UPLOAD_BYTES:
        msg = (
            f"upload size {len(content)} bytes exceeds limit of "
            f"{MAX_UPLOAD_BYTES} bytes; re-encode at lower DPI"
        )
        raise UnsupportedDocumentError(msg)

    ct = content_type.split(";", 1)[0].strip().lower()

    if ct == "application/pdf":
        return _process_pdf(content)

    image_format = SUPPORTED_IMAGE_TYPES.get(ct)
    if image_format is not None:
        return _process_image(content, image_format)

    msg = f"unsupported Content-Type: {ct!r}. Supported: application/pdf, " + ", ".join(
        sorted(SUPPORTED_IMAGE_TYPES)
    )
    raise UnsupportedDocumentError(msg)


def _process_pdf(content: bytes) -> DocumentInput:
    try:
        reader = PdfReader(io.BytesIO(content))
    except (PdfReadError, ValueError) as exc:
        msg = f"could not read PDF: {exc}"
        raise UnsupportedDocumentError(msg) from exc

    text_parts: list[str] = []
    empty_pages = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            text_parts.append(page_text)
        else:
            empty_pages += 1

    if text_parts:
        if empty_pages:
            # Mixed-content PDF (some text pages, some blank/scanned pages):
            # we keep the existing text-mode behaviour but warn the operator
            # so silent data loss on the scanned pages is at least visible.
            LOGGER.warning(
                "Mixed-content PDF: %d page(s) with text used, %d page(s) "
                "with no extractable text dropped (mixed mode not supported)",
                len(text_parts),
                empty_pages,
            )
        return DocumentInput(mode="text", text="\n\n".join(text_parts))

    # No embedded text on any page — treat the upload as a scanned PDF and
    # render up to RASTER_MAX_PAGES pages to PNG for the vision path.
    images = _rasterize_pdf(content)
    return DocumentInput(mode="image", images=images, image_format="png")


def _rasterize_pdf(content: bytes) -> tuple[bytes, ...]:
    """Render the first :data:`RASTER_MAX_PAGES` pages to PNG bytes.

    Uses ``pypdfium2`` (statically-linked PDFium, no system deps). Pages
    beyond the cap are dropped and a WARNING is logged; see the constant's
    docstring for the rationale. ``PdfBitmap`` instances are closed
    eagerly because they are not children of the document for the sake of
    cascading cleanup, only ``PdfPage`` is — leaking bitmaps would leak
    native PDFium memory across requests.

    Raises:
        UnsupportedDocumentError: If PDFium cannot open the file or any
            page fails to render (encrypted PDF, malformed XObject, OOM
            on a pathological page, etc.). All-or-nothing: a mid-loop
            failure discards any pages already rendered to keep the
            upload contract atomic.
    """
    scale = RASTER_DPI / 72  # PDFium's native unit is 72 DPI.
    rendered: list[bytes] = []
    try:
        doc = pdfium.PdfDocument(content)
    except pdfium.PdfiumError as exc:
        msg = f"could not rasterise PDF: {exc}"
        raise UnsupportedDocumentError(msg) from exc

    total_pages = len(doc)
    failing_index = -1
    try:
        try:
            for index, page in enumerate(doc):
                if index >= RASTER_MAX_PAGES:
                    break
                failing_index = index
                bitmap = page.render(scale=scale)
                try:
                    pil_image = bitmap.to_pil()
                    buf = io.BytesIO()
                    pil_image.save(buf, format="PNG")
                    rendered.append(buf.getvalue())
                finally:
                    bitmap.close()
        except pdfium.PdfiumError as exc:
            msg = f"could not rasterise PDF page {failing_index}: {exc}"
            raise UnsupportedDocumentError(msg) from exc
    finally:
        doc.close()

    if total_pages > RASTER_MAX_PAGES:
        LOGGER.warning(
            "Truncated scanned PDF rasterisation: rendered %d of %d pages "
            "(RASTER_MAX_PAGES=%d); caller should split client-side if all "
            "pages matter",
            RASTER_MAX_PAGES,
            total_pages,
            RASTER_MAX_PAGES,
        )
    return tuple(rendered)


def _process_image(content: bytes, image_format: ImageFormat) -> DocumentInput:
    # Defence-in-depth: a malformed file masquerading as a PNG should fail
    # here, before being shipped to Bedrock.
    try:
        with Image.open(io.BytesIO(content)) as im:
            im.verify()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        msg = f"invalid image: {exc}"
        raise UnsupportedDocumentError(msg) from exc

    return DocumentInput(
        mode="image",
        images=(content,),
        image_format=image_format,
    )
