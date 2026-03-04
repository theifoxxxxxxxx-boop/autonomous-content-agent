from __future__ import annotations

import json
import re
from typing import Any


def extract_json_object(text: str) -> dict[str, Any]:
    if text is None:
        raise ValueError("No model output to parse")

    raw = _normalize_text(text)
    if not raw:
        raise ValueError("Model output is empty")

    candidates = _build_candidates(raw)
    errors: list[str] = []
    for candidate in candidates:
        parsed, error = _try_parse_object(candidate)
        if parsed is not None:
            return parsed
        if error:
            errors.append(error)

        repaired = _repair_common_json_issues(candidate)
        if repaired != candidate:
            parsed, error = _try_parse_object(repaired)
            if parsed is not None:
                return parsed
            if error:
                errors.append(error)

    preview = _compact_preview(raw, 240)
    joined_errors = " | ".join(errors[:3]) if errors else "unknown parse failure"
    raise ValueError(f"Unable to parse JSON object ({joined_errors}). raw={preview}")


def _normalize_text(text: str) -> str:
    normalized = text.replace("\ufeff", "").strip()
    normalized = normalized.replace("“", '"').replace("”", '"')
    normalized = normalized.replace("‘", "'").replace("’", "'")
    return normalized


def _build_candidates(raw: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        if candidate is None:
            return
        value = candidate.strip()
        if not value:
            return
        if value in seen:
            return
        seen.add(value)
        candidates.append(value)

    add(raw)
    fence_content = _extract_markdown_fence_content(raw)
    add(fence_content)
    add(_extract_first_balanced_json_object(raw))
    if fence_content:
        add(_extract_first_balanced_json_object(fence_content))
    return candidates


def _extract_markdown_fence_content(text: str) -> str | None:
    pattern = re.compile(r"```(?:json)?\s*([\s\S]*?)```", flags=re.IGNORECASE)
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _extract_first_balanced_json_object(text: str) -> str | None:
    start_idx: int | None = None
    depth = 0
    in_string = False
    escaped = False

    for idx, char in enumerate(text):
        if start_idx is None:
            if char == "{":
                start_idx = idx
                depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_idx : idx + 1]

    return None


def _try_parse_object(candidate: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, _format_decode_error(candidate, exc)

    if not isinstance(parsed, dict):
        return None, f"JSON root must be object, got {type(parsed).__name__}"
    return parsed, None


def _format_decode_error(candidate: str, exc: json.JSONDecodeError) -> str:
    start = max(exc.pos - 35, 0)
    end = min(exc.pos + 35, len(candidate))
    snippet = candidate[start:end].replace("\n", "\\n").replace("\r", "\\r")
    return f"{exc.msg} at pos {exc.pos} near `{snippet}`"


def _repair_common_json_issues(candidate: str) -> str:
    repaired = candidate.strip()
    repaired = _strip_wrapping_code_fence_markers(repaired)
    repaired = _escape_problematic_chars_in_strings(repaired)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    repaired = _insert_missing_commas_between_pairs(repaired)
    repaired = repaired.replace("\r\n", "\n").replace("\r", "\n")
    return repaired


def _strip_wrapping_code_fence_markers(text: str) -> str:
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    if text.endswith("```"):
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _escape_problematic_chars_in_strings(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    length = len(text)
    terminators = {",", "}", "]", ":"}

    while index < length:
        char = text[index]

        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
            index += 1
            continue

        if escaped:
            result.append(char)
            escaped = False
            index += 1
            continue

        if char == "\\":
            result.append(char)
            escaped = True
            index += 1
            continue

        if char == '"':
            next_non_space_index = _next_non_space_index(text, index + 1)
            if next_non_space_index is None:
                in_string = False
                result.append(char)
                index += 1
                continue

            next_non_space = text[next_non_space_index]
            if next_non_space in terminators:
                in_string = False
                result.append(char)
            elif next_non_space == '"' and _looks_like_json_key_start(text, next_non_space_index):
                in_string = False
                result.append(char)
            else:
                result.append('\\"')
            index += 1
            continue

        if char == "\n":
            result.append("\\n")
            index += 1
            continue
        if char == "\r":
            result.append("\\r")
            index += 1
            continue
        if char == "\t":
            result.append("\\t")
            index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _next_non_space_char(text: str, start: int) -> str | None:
    idx = _next_non_space_index(text, start)
    return text[idx] if idx is not None else None


def _next_non_space_index(text: str, start: int) -> int | None:
    for idx in range(start, len(text)):
        if not text[idx].isspace():
            return idx
    return None


def _insert_missing_commas_between_pairs(text: str) -> str:
    patterns = [
        (r'("(?:(?:\\.)|[^"\\])*")\s*(?="(?:(?:\\.)|[^"\\])*"\s*:)', r"\1, "),
        (r"([}\]])\s*(?=\"(?:(?:\\.)|[^\"\\])*\"\s*:)", r"\1, "),
        (r"(\b(?:true|false|null)\b)\s*(?=\"(?:(?:\\.)|[^\"\\])*\"\s*:)", r"\1, "),
        (
            r"(-?\d+(?:\.\d+)?(?:[eE][+\-]?\d+)?)\s*(?=\"(?:(?:\\.)|[^\"\\])*\"\s*:)",
            r"\1, ",
        ),
    ]
    repaired = text
    for _ in range(3):
        previous = repaired
        for pattern, replacement in patterns:
            repaired = re.sub(pattern, replacement, repaired)
        if repaired == previous:
            break
    return repaired


def _looks_like_json_key_start(text: str, quote_start: int) -> bool:
    if quote_start < 0 or quote_start >= len(text) or text[quote_start] != '"':
        return False

    escaped = False
    cursor = quote_start + 1
    while cursor < len(text):
        char = text[cursor]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            after_quote = _next_non_space_index(text, cursor + 1)
            return after_quote is not None and text[after_quote] == ":"
        cursor += 1
    return False


def _compact_preview(text: str, limit: int) -> str:
    compact = text.replace("\n", "\\n").replace("\r", "\\r")
    if len(compact) <= limit:
        return compact
    return compact[:limit] + "..."
