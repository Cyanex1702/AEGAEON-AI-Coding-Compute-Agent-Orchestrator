from __future__ import annotations

import ast
import json
import re
from typing import Any

_LIST_FIELDS = (
    "test_commands",
    "notes",
    "contract_proposals",
    "risks",
    "blocked_by",
    "acceptance_evidence",
)


def _escape_string_controls(candidate: str) -> str:
    """Escape literal controls only while inside a quoted JSON string."""
    escaped: list[str] = []
    in_string = False
    after_backslash = False
    named = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r"}
    for character in candidate:
        if not in_string:
            escaped.append(character)
            if character == '"':
                in_string = True
            continue
        if after_backslash:
            escaped.append(character)
            after_backslash = False
        elif character == "\\":
            escaped.append(character)
            after_backslash = True
        elif character == '"':
            escaped.append(character)
            in_string = False
        elif ord(character) < 32:
            escaped.append(named.get(ord(character), f"\\u{ord(character):04x}"))
        else:
            escaped.append(character)
    return "".join(escaped)


def _object_candidates(text: str) -> list[str]:
    stripped = text.strip()
    fence = chr(96) * 3
    candidates = [
        match.group(1).strip()
        for match in re.finditer(
            re.escape(fence) + r"(?:json)?\s*(\{.*?\})\s*" + re.escape(fence),
            stripped,
            re.DOTALL,
        )
    ]
    start: int | None = None
    depth = 0
    in_string = False
    after_backslash = False
    for index, character in enumerate(stripped):
        if in_string:
            if after_backslash:
                after_backslash = False
            elif character == "\\":
                after_backslash = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(stripped[start : index + 1])
                start = None
    if stripped.startswith("{"):
        candidates.append(stripped)
    return list(dict.fromkeys(reversed(candidates)))


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse one bounded object from model output with conservative syntax recovery."""
    candidates = _object_candidates(text)
    if not candidates:
        raise ValueError("response does not contain a JSON object")
    last_error: Exception | None = None
    for candidate in candidates:
        repaired = _escape_string_controls(candidate)
        for parser in (json.loads, ast.literal_eval):
            try:
                value = parser(repaired)
            except (json.JSONDecodeError, SyntaxError, ValueError) as exc:
                last_error = exc
                continue
            if isinstance(value, dict):
                return value
            last_error = ValueError("model response must be a JSON object")
    raise ValueError(str(last_error or "response is not valid structured JSON"))


def _normalize_response(value: dict[str, Any]) -> dict[str, Any]:
    raw_files = value.get("files", [])
    if isinstance(raw_files, dict):
        raw_files = [{"path": str(path), "content": content} for path, content in raw_files.items()]
    if not isinstance(raw_files, list):
        raise ValueError("files must be an array or a path-to-content object")
    files: list[dict[str, str]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("every file must be an object")
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("every file needs a string path and content")
        files.append({"path": path.strip(), "content": content})
    changes = value.get("changes", [])
    if not isinstance(changes, list):
        raise ValueError("changes must be an array")
    summary = str(value.get("summary") or "Generated application files").strip()
    normalized: dict[str, Any] = {
        "summary": summary or "Generated application files",
        "files": files,
        "changes": changes,
    }
    for field in _LIST_FIELDS:
        item = value.get(field, [])
        if not isinstance(item, list):
            raise ValueError(f"{field} must be an array")
        normalized[field] = item
    return normalized


def _parse_file_envelope(text: str) -> dict[str, Any]:
    file_headers = len(re.findall(r"(?m)^\s*<<<FILE:", text))
    file_ends = len(re.findall(r"(?m)^\s*<<<END_FILE>>>\s*$", text))
    if not file_headers:
        raise ValueError("response does not contain an AEGAEON file envelope")
    if file_headers != file_ends:
        raise ValueError("AEGAEON file envelope is incomplete")
    pattern = re.compile(
        r"(?ms)^\s*<<<FILE:\s*([^>\r\n]+?)\s*>>>[ \t]*\r?\n"
        r"(.*?)^\s*<<<END_FILE>>>[ \t]*(?:\r?\n|$)"
    )
    files = [
        {"path": match.group(1).strip(), "content": match.group(2)}
        for match in pattern.finditer(text)
    ]
    if len(files) != file_headers:
        raise ValueError("AEGAEON file envelope markers are malformed")
    summary_match = re.search(r"(?mi)^\s*SUMMARY:\s*(.+?)\s*$", text)
    return _normalize_response(
        {
            "summary": summary_match.group(1) if summary_match else "Generated application files",
            "files": files,
        }
    )


def _parse_markdown_files(text: str) -> dict[str, Any]:
    """Salvage common filename-plus-code-fence responses without guessing paths."""
    fence = re.escape(chr(96) * 3)
    pattern = re.compile(
        r"(?ms)^(?:#{1,6}\s*)?(?:File:\s*)?`?"
        r"([A-Za-z0-9][A-Za-z0-9_./ -]*\.[A-Za-z0-9]+)`?[ \t]*\r?\n"
        + fence
        + r"[^\r\n]*\r?\n(.*?)^"
        + fence
        + r"[ \t]*$"
    )
    files = [
        {"path": match.group(1).strip(), "content": match.group(2)}
        for match in pattern.finditer(text)
    ]
    if not files:
        raise ValueError("response does not contain explicit filename code blocks")
    return _normalize_response({"summary": "Recovered generated files", "files": files})


def parse_model_response(text: str) -> dict[str, Any]:
    """Parse the code-safe envelope, with JSON and explicit Markdown compatibility."""
    errors: list[str] = []
    parsers = (
        (_parse_file_envelope, "file envelope"),
        (lambda value: _normalize_response(parse_json_object(value)), "JSON"),
        (_parse_markdown_files, "filename code blocks"),
    )
    for parser, name in parsers:
        try:
            return parser(text)
        except ValueError as exc:
            errors.append(f"{name}: {exc}")
    raise ValueError("; ".join(errors))


def recover_completed_envelope(text: str) -> str | None:
    """Keep only fully closed file blocks from a response cut off at its token cap."""
    start = text.find("AEGAEON_RESPONSE_V1")
    end_marker = "<<<END_FILE>>>"
    last_complete_file = text.rfind(end_marker)
    if start < 0 or last_complete_file < start:
        return None
    candidate = text[start : last_complete_file + len(end_marker)].rstrip()
    try:
        parse_model_response(candidate)
    except ValueError:
        return None
    return candidate + "\n<<<END_RESPONSE>>>"


PORTABLE_MODEL_RESPONSE_PARSER_SOURCE = r"""import ast

_LIST_FIELDS = (
    "test_commands", "notes", "contract_proposals", "risks", "blocked_by",
    "acceptance_evidence",
)

def _escape_string_controls(candidate):
    escaped = []
    in_string = False
    after_backslash = False
    named = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r"}
    for character in candidate:
        if not in_string:
            escaped.append(character)
            if character == '"':
                in_string = True
            continue
        if after_backslash:
            escaped.append(character)
            after_backslash = False
        elif character == "\\":
            escaped.append(character)
            after_backslash = True
        elif character == '"':
            escaped.append(character)
            in_string = False
        elif ord(character) < 32:
            escaped.append(named.get(ord(character), f"\\u{ord(character):04x}"))
        else:
            escaped.append(character)
    return "".join(escaped)

def _object_candidates(text):
    stripped = text.strip()
    fence = chr(96) * 3
    candidates = [
        match.group(1).strip()
        for match in re.finditer(
            re.escape(fence) + r"(?:json)?\s*(\{.*?\})\s*" + re.escape(fence),
            stripped, re.S,
        )
    ]
    start = None
    depth = 0
    in_string = False
    after_backslash = False
    for index, character in enumerate(stripped):
        if in_string:
            if after_backslash:
                after_backslash = False
            elif character == "\\":
                after_backslash = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(stripped[start:index + 1])
                start = None
    if stripped.startswith("{"):
        candidates.append(stripped)
    return list(dict.fromkeys(reversed(candidates)))

def parse_json_object(text):
    candidates = _object_candidates(text)
    if not candidates:
        raise ValueError("response does not contain a JSON object")
    last_error = None
    for candidate in candidates:
        repaired = _escape_string_controls(candidate)
        for parser in (json.loads, ast.literal_eval):
            try:
                value = parser(repaired)
            except (json.JSONDecodeError, SyntaxError, ValueError) as error:
                last_error = error
                continue
            if isinstance(value, dict):
                return value
            last_error = ValueError("model response must be a JSON object")
    raise ValueError(str(last_error or "response is not valid structured JSON"))

def _normalize_response(value):
    raw_files = value.get("files", [])
    if isinstance(raw_files, dict):
        raw_files = [{"path": str(path), "content": content} for path, content in raw_files.items()]
    if not isinstance(raw_files, list):
        raise ValueError("files must be an array or a path-to-content object")
    files = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("every file must be an object")
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("every file needs a string path and content")
        files.append({"path": path.strip(), "content": content})
    changes = value.get("changes", [])
    if not isinstance(changes, list):
        raise ValueError("changes must be an array")
    normalized = {
        "summary": str(value.get("summary") or "Generated application files").strip(),
        "files": files, "changes": changes,
    }
    for field in _LIST_FIELDS:
        item = value.get(field, [])
        if not isinstance(item, list):
            raise ValueError(f"{field} must be an array")
        normalized[field] = item
    return normalized

def _parse_file_envelope(text):
    file_headers = len(re.findall(r"(?m)^\s*<<<FILE:", text))
    file_ends = len(re.findall(r"(?m)^\s*<<<END_FILE>>>\s*$", text))
    if not file_headers:
        raise ValueError("response does not contain an AEGAEON file envelope")
    if file_headers != file_ends:
        raise ValueError("AEGAEON file envelope is incomplete")
    pattern = re.compile(
        r"(?ms)^\s*<<<FILE:\s*([^>\r\n]+?)\s*>>>[ \t]*\r?\n"
        r"(.*?)^\s*<<<END_FILE>>>[ \t]*(?:\r?\n|$)"
    )
    files = [
        {"path": match.group(1).strip(), "content": match.group(2)}
        for match in pattern.finditer(text)
    ]
    if len(files) != file_headers:
        raise ValueError("AEGAEON file envelope markers are malformed")
    summary_match = re.search(r"(?mi)^\s*SUMMARY:\s*(.+?)\s*$", text)
    return _normalize_response({
        "summary": summary_match.group(1) if summary_match else "Generated application files",
        "files": files,
    })

def _parse_markdown_files(text):
    fence = re.escape(chr(96) * 3)
    pattern = re.compile(
        r"(?ms)^(?:#{1,6}\s*)?(?:File:\s*)?`?"
        r"([A-Za-z0-9][A-Za-z0-9_./ -]*\.[A-Za-z0-9]+)`?[ \t]*\r?\n"
        + fence + r"[^\r\n]*\r?\n(.*?)^" + fence + r"[ \t]*$"
    )
    files = [
        {"path": match.group(1).strip(), "content": match.group(2)}
        for match in pattern.finditer(text)
    ]
    if not files:
        raise ValueError("response does not contain explicit filename code blocks")
    return _normalize_response({"summary": "Recovered generated files", "files": files})

def parse_model_response(text):
    errors = []
    for parser, name in (
        (_parse_file_envelope, "file envelope"),
        (lambda value: _normalize_response(parse_json_object(value)), "JSON"),
        (_parse_markdown_files, "filename code blocks"),
    ):
        try:
            return parser(text)
        except ValueError as error:
            errors.append(f"{name}: {error}")
    raise ValueError("; ".join(errors))

def recover_completed_envelope(text):
    start = text.find("AEGAEON_RESPONSE_V1")
    end_marker = "<<<END_FILE>>>"
    last_complete_file = text.rfind(end_marker)
    if start < 0 or last_complete_file < start:
        return None
    candidate = text[start:last_complete_file + len(end_marker)].rstrip()
    try:
        parse_model_response(candidate)
    except ValueError:
        return None
    return candidate + "\n<<<END_RESPONSE>>>"
"""

# Kept for generated notebooks and third-party workers that import the old name.
PORTABLE_JSON_PARSER_SOURCE = PORTABLE_MODEL_RESPONSE_PARSER_SOURCE
