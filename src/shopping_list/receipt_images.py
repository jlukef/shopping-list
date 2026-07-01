"""Transient receipt-image validation and normalisation.

Receipt photos are never persisted (Jamie's 2026-07-01 decision — see
PHASE5_RECEIPT_OCR_PLAN.md §5). Every function here works on bytes already
held in memory and returns processed bytes/metadata; nothing here writes to
disk, and callers are responsible for discarding both the raw upload and the
``ProcessedReceiptImage.jpeg_bytes`` once they're done with them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io

from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pillow-heif is required in production; tests may stub it out
    pillow_heif = None


class InvalidReceiptImage(ValueError):
    """The uploaded bytes are not a decodable, reasonably-sized image."""


# Independent of the caller's byte-size limit — guards against a small file
# that decompresses to an enormous pixel buffer (decompression bomb).
MAX_DECODED_PIXELS = 40_000_000  # ~40MP
LONG_EDGE_TARGET = 1600  # receipts don't need more for OCR; also caps image-token cost
JPEG_QUALITY = 85

ALLOWED_FORMATS = {"JPEG", "PNG", "HEIF"}


@dataclass(frozen=True)
class ProcessedReceiptImage:
    """Result of validating and normalising one upload. In-memory only."""

    jpeg_bytes: bytes
    content_sha256: str
    width: int
    height: int


def process_upload(raw_bytes: bytes) -> ProcessedReceiptImage:
    """Validate, EXIF-orient, resize, and strip metadata from an uploaded image.

    Raises ``InvalidReceiptImage`` for anything that isn't a decodable
    JPEG/PNG/HEIC or that exceeds the decoded-pixel guard. Dimensions are
    checked from the header before the pixel data is decoded, so an oversize
    image is rejected before the expensive/bomb-prone decode step runs.
    """
    if not raw_bytes:
        raise InvalidReceiptImage("Empty upload")

    try:
        with Image.open(io.BytesIO(raw_bytes)) as probe:
            probe.verify()
    except Exception as exc:
        raise InvalidReceiptImage("Not a readable image") from exc

    try:
        image = Image.open(io.BytesIO(raw_bytes))
    except Exception as exc:
        raise InvalidReceiptImage("Not a readable image") from exc

    try:
        if image.format not in ALLOWED_FORMATS:
            raise InvalidReceiptImage(f"Unsupported image format: {image.format}")

        if image.width * image.height > MAX_DECODED_PIXELS:
            raise InvalidReceiptImage("Image resolution is too large")

        try:
            image.load()
        except Exception as exc:
            raise InvalidReceiptImage("Could not decode image") from exc

        # exif_transpose applies (and then discards) the orientation tag.
        oriented = ImageOps.exif_transpose(image) or image
        if oriented.mode not in ("RGB", "L"):
            oriented = oriented.convert("RGB")

        long_edge = max(oriented.width, oriented.height)
        if long_edge > LONG_EDGE_TARGET:
            scale = LONG_EDGE_TARGET / long_edge
            new_size = (max(1, round(oriented.width * scale)), max(1, round(oriented.height * scale)))
            oriented = oriented.resize(new_size, Image.LANCZOS)

        # A brand-new Image has an empty .info dict, so pasting pixel data
        # into it (rather than re-saving `oriented` directly) drops EXIF,
        # GPS, and every other metadata block that JPEG saving would
        # otherwise silently carry over from image.info.
        clean = Image.new(oriented.mode, oriented.size)
        clean.paste(oriented, (0, 0))

        buffer = io.BytesIO()
        clean.save(buffer, format="JPEG", quality=JPEG_QUALITY, exif=b"")
        jpeg_bytes = buffer.getvalue()

        return ProcessedReceiptImage(
            jpeg_bytes=jpeg_bytes,
            content_sha256=hashlib.sha256(jpeg_bytes).hexdigest(),
            width=clean.width,
            height=clean.height,
        )
    finally:
        image.close()
