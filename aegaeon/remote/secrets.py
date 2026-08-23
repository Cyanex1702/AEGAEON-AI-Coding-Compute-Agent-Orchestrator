from __future__ import annotations

import base64
import ctypes
import json
import os
from contextlib import suppress
from ctypes import wintypes
from pathlib import Path


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class RemoteSecretStore:
    """Small controller-only secret store backed by Windows user-scoped DPAPI."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def configured(self, name: str) -> bool:
        return name in self._read()

    def set(self, name: str, value: str) -> None:
        data = self._read()
        data[name] = self._protect(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with suppress(OSError):
            os.chmod(self.path, 0o600)

    def get(self, name: str) -> str | None:
        encrypted = self._read().get(name)
        return self._unprotect(encrypted) if encrypted else None

    def remove(self, name: str) -> None:
        data = self._read()
        if name not in data:
            return
        data.pop(name)
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _protect(value: str) -> str:
        raw = value.encode("utf-8")
        if os.name != "nt":
            return "plain:" + base64.b64encode(raw).decode()
        return "dpapi:" + base64.b64encode(_crypt_protect(raw)).decode()

    @staticmethod
    def _unprotect(value: str) -> str:
        scheme, _, encoded = value.partition(":")
        raw = base64.b64decode(encoded)
        if scheme == "dpapi" and os.name == "nt":
            raw = _crypt_unprotect(raw)
        return raw.decode("utf-8")


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _crypt_protect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise OSError("Windows could not protect the remote connectivity credential")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def _crypt_unprotect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    ):
        raise OSError("Windows could not unlock the remote connectivity credential")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer
