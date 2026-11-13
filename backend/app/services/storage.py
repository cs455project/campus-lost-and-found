"""Saving and reading uploaded images (item photos and claim proofs).

For now files live in folders on the server: backend/uploads/items for item
photos, backend/uploads/proofs for claim proof images.
When the app is deployed, only this file needs to change to use cloud
storage instead; the rest of the code just calls save / path / delete.

Security rules for uploads:
- We decide the file name (a random id), never the uploader. A name like
  "../../app/main.py" could otherwise overwrite our own code.
- We check the file's first bytes ("magic number") to confirm it really
  is a JPEG, PNG or WebP image. Renaming virus.exe to photo.jpg won't pass.
- Size is checked while reading, so a huge upload is stopped early.
- Metadata is removed before the file is saved: phone photos carry EXIF data
  such as the GPS location where they were taken, and item photos are shown
  to every student. Only the "rotate" flag is kept (see strip_metadata).
"""

import io
import os
import secrets
from pathlib import Path

from fastapi import UploadFile
from PIL import Image, ImageOps

from app.core.config import get_settings
from app.core.errors import bad_request

# The first bytes of each allowed image type, and the extension we store it with.
SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff", ".jpg"),
    "image/png": (b"\x89PNG\r\n\x1a\n", ".png"),
}


def detect_image_type(head: bytes) -> tuple[str, str] | None:
    """Return (content_type, extension) if the bytes start like an image we accept."""
    for content_type, (signature, extension) in SIGNATURES.items():
        if head.startswith(signature):
            return content_type, extension
    # WebP files start with "RIFF", 4 size bytes, then "WEBP".
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


# Item photos and claim proofs are kept apart: proofs are private and are
# deleted soon after a claim closes (Phase 7).
FOLDERS = ("items", "proofs", "thumbs")


def _folder(name: str) -> Path:
    if name not in FOLDERS:
        raise ValueError(f"Unknown upload folder: {name}")
    folder = get_settings().upload_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def read_image(upload: UploadFile) -> tuple[bytes, str, str]:
    """Read an uploaded file, enforcing the size limit and image type.
    Returns (data, content_type, extension).

    Not async on purpose: the endpoints that call this also use the database,
    which blocks. Plain `def` endpoints run in a worker thread, so a slow
    upload never holds up every other request (an `async def` endpoint runs
    on the one shared event loop)."""
    max_bytes = get_settings().max_photo_mb * 1024 * 1024
    # Read one byte more than allowed: if we get it, the file is too big.
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise bad_request(f"Each photo must be {get_settings().max_photo_mb} MB or smaller.")
    if not data:
        raise bad_request("The photo file is empty.")
    detected = detect_image_type(data[:12])
    if detected is None:
        raise bad_request("Photos must be JPEG, PNG or WebP images.")
    content_type, extension = detected
    _check_pixels(data)
    return strip_metadata(data, content_type), content_type, extension


def _check_pixels(data: bytes) -> None:
    """Refuse images with a huge number of pixels. A small file can claim to
    be, say, 20,000 x 20,000 pixels ("decompression bomb"): opening it would
    take gigabytes of memory. Only the header is read here, not the pixels."""
    limit = get_settings().max_photo_megapixels
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        width = height = limit * 1_000_000  # far too many pixels: refused below
    except Exception:
        return  # unreadable header: accepted as before (it simply gets no thumbnail)
    if width * height > limit * 1_000_000:
        raise bad_request(f"Photos can be at most {limit} megapixels.")


# ---------------- Removing metadata (EXIF, GPS, XMP, IPTC) ----------------

ORIENTATION = 0x0112  # the EXIF tag phones use to say "show this rotated"
# JPEG segments that are kept: APP0 (JFIF header), APP14 (Adobe colour
# transform, needed to decode some files) and APP2 only when it holds the ICC
# colour profile. Every other APPn segment (EXIF, XMP, IPTC, maker notes,
# multi-picture index) and comments are dropped.
_KEEP_APP = {0xE0, 0xEE}


def strip_metadata(data: bytes, content_type: str) -> bytes:
    """Return the image without metadata. JPEG (nearly every phone photo) is
    cleaned without re-encoding, so the picture is unchanged byte for byte;
    PNG and WebP are re-saved without their metadata. A file Pillow can't
    read is returned as it is (it has no metadata Pillow could find either)."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            orientation = image.getexif().get(ORIENTATION, 1)
    except Exception:
        return data
    if content_type == "image/jpeg":
        cleaned = _strip_jpeg(data, orientation)
        if cleaned is not None:
            return cleaned
    cleaned = _resave(data, content_type)
    if cleaned is None:
        # Pillow found the header (and maybe a location in it) but can't read
        # the picture: refuse it rather than share its metadata.
        raise bad_request("This photo seems to be damaged. Please take or choose it again.")
    return cleaned


def _strip_jpeg(data: bytes, orientation: int) -> bytes | None:
    """Copy the JPEG's segments, leaving out the metadata ones. None if the
    file doesn't have the usual structure (it is then re-saved instead)."""
    if data[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    if orientation != 1:
        exif = Image.Exif()
        exif[ORIENTATION] = orientation
        block = exif.tobytes()  # starts with "Exif\0\0"
        out += b"\xff\xe1" + (len(block) + 2).to_bytes(2, "big") + block
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # padding byte before a marker
            i += 1
            continue
        if marker == 0xDA:  # start of the image data: copy up to the end-of-image marker
            end = data.find(b"\xff\xd9", i)
            if end < 0:
                return None
            # Anything after it (e.g. a second picture with its own EXIF, as
            # some phones add) is left out.
            out += data[i:end + 2]
            return bytes(out)
        length = int.from_bytes(data[i + 2:i + 4], "big")
        segment = data[i:i + 2 + length]
        if len(segment) < 4:
            return None
        is_app = 0xE0 <= marker <= 0xEF
        keep = (not is_app and marker != 0xFE) or marker in _KEEP_APP or (
            marker == 0xE2 and segment[4:16] == b"ICC_PROFILE\x00")
        if keep:
            out += segment
        i += 2 + length
    return None


def _resave(data: bytes, content_type: str) -> bytes | None:
    """Re-save a PNG or WebP (or an unusual JPEG) without metadata, turned
    the right way up."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = ImageOps.exif_transpose(image)
            buffer = io.BytesIO()
            if content_type == "image/png":
                image.save(buffer, "PNG", optimize=False)
            elif content_type == "image/webp":
                image.save(buffer, "WEBP", quality=90)
            else:
                if image.mode not in ("RGB", "L", "CMYK"):
                    image = image.convert("RGB")
                image.save(buffer, "JPEG", quality=92)
            return buffer.getvalue()
    except Exception:
        return None


def save(data: bytes, extension: str, folder: str = "items") -> str:
    """Write the file and return the random name it was stored under."""
    stored_name = secrets.token_hex(16) + extension
    (_folder(folder) / stored_name).write_bytes(data)
    return stored_name


def path_for(stored_name: str, folder: str = "items") -> Path:
    return _folder(folder) / stored_name


def delete(stored_name: str, folder: str = "items") -> None:
    path_for(stored_name, folder).unlink(missing_ok=True)
    if folder == "items":
        thumbnail_path(stored_name).unlink(missing_ok=True)


# ---------------- Thumbnails ----------------
# Lists (Found, Lost, My claims) show each item's first photo in a small card.
# A phone photo is 2-5 MB; the card needs about 640 pixels. So every item
# photo also gets a small WebP copy (about 30-60 KB) for the lists. It is made
# again from the original, so it carries no EXIF data (no GPS location).

THUMB_SIDE = 640


def thumbnail_path(stored_name: str) -> Path:
    """Thumbnails of item photos live in uploads/thumbs, same name, .webp."""
    return _folder("thumbs") / (Path(stored_name).stem + ".webp")


def make_thumbnail(stored_name: str) -> Path | None:
    """Return the photo's thumbnail, making it first if needed (photos
    uploaded before thumbnails existed get one the first time they are
    shown). None if the photo can't be read: the caller then serves the
    original."""
    target = thumbnail_path(stored_name)
    if target.exists():
        return target
    try:
        with Image.open(path_for(stored_name)) as image:
            if image.width * image.height > get_settings().max_photo_megapixels * 1_000_000:
                return None
            # For JPEG, decode at 1/2, 1/4 or 1/8 size straight away: far
            # faster and lighter than decoding all 12 megapixels.
            image.draft("RGB", (THUMB_SIDE, THUMB_SIDE))
            image = ImageOps.exif_transpose(image)  # phones store "rotate 90°" in EXIF
            image.thumbnail((THUMB_SIDE, THUMB_SIDE))
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            # Write to a temporary name, then rename: two requests making the
            # same thumbnail at once can't leave a half-written file.
            temporary = target.with_name(f"{target.stem}.{secrets.token_hex(4)}.tmp")
            image.save(temporary, "WEBP", quality=75)
            os.replace(temporary, target)
            return target
    except Exception:
        return None
