"""The user's own AI key, protected with Windows DPAPI (CryptProtectData).

Only the current Windows user on this PC can decrypt the blob, so a copied
config.json is useless elsewhere. The plain key is never written to disk,
logged, or put in an error message.
"""

from __future__ import annotations

import base64
import sys

_ENTROPY = b"Keyware.AIKey.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretError(Exception):
    """Short, user-facing. Never contains the key."""


def available() -> bool:
    return sys.platform == "win32"


def _api():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    for name in ("CryptProtectData", "CryptUnprotectData"):
        fn = getattr(crypt32, name)
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
                       ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, Blob, crypt32, kernel32


def _run(name: str, data: bytes) -> bytes:
    ctypes, Blob, crypt32, kernel32 = _api()
    source = ctypes.create_string_buffer(data, len(data))
    entropy = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    blob_in = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_char)))
    blob_entropy = Blob(len(_ENTROPY), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    ok = getattr(crypt32, name)(ctypes.byref(blob_in), None, ctypes.byref(blob_entropy), None, None,
                                _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise SecretError("Windows could not protect the key." if name == "CryptProtectData"
                          else "The saved key can't be read on this PC. Add it again.")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)
        ctypes.memset(source, 0, len(data))


def protect(secret: str) -> str:
    """Plain key -> base64 DPAPI blob for config.json."""
    if not available():
        raise SecretError("Saving your own key needs Windows.")
    secret = (secret or "").strip()
    if not secret:
        raise SecretError("Paste a key first.")
    return base64.b64encode(_run("CryptProtectData", secret.encode("utf-8"))).decode("ascii")


def unprotect(blob: str) -> str:
    """base64 DPAPI blob -> plain key, in memory only."""
    if not blob:
        return ""
    if not available():
        raise SecretError("Your own key needs Windows.")
    try:
        raw = base64.b64decode(blob.encode("ascii"), validate=True)
    except (ValueError, UnicodeError):
        raise SecretError("The saved key can't be read on this PC. Add it again.") from None
    return _run("CryptUnprotectData", raw).decode("utf-8")
