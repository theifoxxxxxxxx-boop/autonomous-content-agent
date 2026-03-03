from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("{") and raw.endswith("}"):
        return json.loads(raw)

    pattern = re.compile(r"\{.*\}", flags=re.DOTALL)
    match = pattern.search(raw)
    if not match:
        raise ValueError("No JSON object found in model output")
    return json.loads(match.group(0))
