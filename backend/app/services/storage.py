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
"""

import secrets
from pathlib import Path

from fastapi import UploadFile

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
FOLDERS = ("items", "proofs")


def _folder(name: str) -> Path:
    if name not in FOLDERS:
        raise ValueError(f"Unknown upload folder: {name}")
    folder = get_settings().upload_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


async def read_image(upload: UploadFile) -> tuple[bytes, str, str]:
    """Read an uploaded file, enforcing the size limit and image type.
    Returns (data, content_type, extension)."""
    max_bytes = get_settings().max_photo_mb * 1024 * 1024
    # Read one byte more than allowed: if we get it, the file is too big.
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise bad_request(f"Each photo must be {get_settings().max_photo_mb} MB or smaller.")
    if not data:
        raise bad_request("The photo file is empty.")
    detected = detect_image_type(data[:12])
    if detected is None:
        raise bad_request("Photos must be JPEG, PNG or WebP images.")
    content_type, extension = detected
    return data, content_type, extension


def save(data: bytes, extension: str, folder: str = "items") -> str:
    """Write the file and return the random name it was stored under."""
    stored_name = secrets.token_hex(16) + extension
    (_folder(folder) / stored_name).write_bytes(data)
    return stored_name


def path_for(stored_name: str, folder: str = "items") -> Path:
    return _folder(folder) / stored_name


def delete(stored_name: str, folder: str = "items") -> None:
    path_for(stored_name, folder).unlink(missing_ok=True)
