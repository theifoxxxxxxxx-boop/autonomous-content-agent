from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


def encode_image_to_base64(path: str) -> tuple[str, str]:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(file_path.read_bytes()).decode("utf-8")
    return encoded, mime_type
