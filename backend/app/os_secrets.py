"""Windows current-user DPAPI protection for the existing AES master key."""

import ctypes
import os
import secrets
from ctypes import wintypes
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value: bytes, *, decrypt=False) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI is only available on Windows")
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, output = Blob(len(value), buffer), Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # UI_FORBIDDEN only. Never use LOCAL_MACHINE (which permits other users).
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(), "Windows could not protect/unprotect the application key")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def load_master_key(directory: Path) -> str:
    legacy = directory / "encryption.key"
    protected = directory / "encryption.key.dpapi"
    if protected.exists():
        value = dpapi(protected.read_bytes(), decrypt=True).decode("utf-8")
        if legacy.exists():
            if legacy.read_text(encoding="utf-8").strip() != value:
                raise ValueError("Protected and legacy encryption keys differ; manual recovery required")
            legacy.unlink()  # Verified duplicate; backup is retained by the migration workflow.
        return value
    value = legacy.read_text(encoding="utf-8").strip() if legacy.exists() else secrets.token_urlsafe(48)
    encrypted = dpapi(value.encode("utf-8"))
    if dpapi(encrypted, decrypt=True).decode("utf-8") != value:
        raise ValueError("DPAPI round-trip verification failed")
    # Exclusive creation prevents silently overwriting a different process's key.
    try:
        with protected.open("xb") as output:
            output.write(encrypted)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        return load_master_key(directory)
    if dpapi(protected.read_bytes(), decrypt=True).decode("utf-8") != value:
        raise ValueError("Stored DPAPI key verification failed")
    if legacy.exists():
        legacy.unlink()
    return value
