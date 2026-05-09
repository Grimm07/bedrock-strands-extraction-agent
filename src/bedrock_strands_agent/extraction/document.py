"""Document upload detection and pre-processing.

Used by ``POST /extract/document``. Inspects the upload's MIME type and
contents and decides which extraction path to take:

* PDFs with embedded text → text path (``ExtractionService.extract``).
* Images (PNG / JPEG / WebP / GIF) → vision path
  (``ExtractionService._extract_via_vision`` → :func:`invoke_multimodal`).
* PDFs without embedded text → :class:`UnsupportedDocumentError` with a
  message asking the caller to rasterise client-side. v0.2 deliberately
  does not depend on a PDF rasteriser to keep the install footprint small;
  v0.3 may add ``pypdfium2`` if demand justifies the dep weight.

See :doc:`docs/extraction-modes.md` for the full design rationale.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Final, Literal

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

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
            content cannot be parsed as the declared type, or a PDF lacks
            embedded text.
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
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if page_text.strip():
            text_parts.append(page_text)

    if not text_parts:
        msg = (
            "PDF has no embedded text. The service does not include a PDF "
            "rasteriser in v0.2; rasterise the page(s) to PNG/JPEG client-"
            "side and re-upload. v0.3 may add pypdfium2 if demand justifies "
            "the dep weight."
        )
        raise UnsupportedDocumentError(msg)

    return DocumentInput(mode="text", text="\n\n".join(text_parts))


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
