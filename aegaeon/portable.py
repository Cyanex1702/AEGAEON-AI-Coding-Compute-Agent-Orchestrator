from __future__ import annotations

import re
from pathlib import PurePath, PureWindowsPath

_INVALID_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class PortableFilenameError(ValueError):
    """Raised before a non-portable artifact name reaches the filesystem."""

    code = "INVALID_ARTIFACT_FILENAME"


def validate_portable_filename(value: str) -> str:
    if not isinstance(value, str):
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: filename must be text")
    if not value or value in {".", ".."} or value != value.strip():
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: empty or unsafe whitespace")
    if PurePath(value).name != value or PureWindowsPath(value).name != value:
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: filename must not contain a path")
    if PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: absolute paths are forbidden")
    if _INVALID_CHARACTERS.search(value) or value.endswith((" ", ".")):
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: filename is not portable")
    stem = value.split(".", 1)[0].upper()
    if stem in _RESERVED_WINDOWS_NAMES:
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: reserved Windows device name")
    if len(value) > 180:
        raise PortableFilenameError("INVALID_ARTIFACT_FILENAME: filename is too long")
    return value


def sanitize_filename(value: object, *, default: str = "artifact") -> str:
    candidate = str(value or "").strip()
    candidate = _INVALID_CHARACTERS.sub("_", candidate)
    candidate = re.sub(r"\s+", "_", candidate)
    candidate = re.sub(r"_+", "_", candidate).strip(" ._")
    if not candidate or candidate in {".", ".."}:
        candidate = default
    if candidate.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        candidate = f"_{candidate}"
    candidate = candidate[:180].rstrip(" .") or default
    return validate_portable_filename(candidate)


def artifact_filename(identifier: object, suffix: str = ".json") -> str:
    safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    base = sanitize_filename(identifier)
    if base.lower().endswith(safe_suffix.lower()):
        return base
    return validate_portable_filename(f"{base[: 180 - len(safe_suffix)]}{safe_suffix}")
