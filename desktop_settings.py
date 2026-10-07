"""Windows desktop location and per-user encrypted preferences."""

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


def desktop_directory() -> Path:
    buffer = ctypes.create_unicode_buffer(32768)
    result = ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buffer)
    if result != 0:
        raise OSError("无法读取 Windows 桌面路径")
    return Path(buffer.value)


def settings_path() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "ZhijianTranslator" / "settings.json"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    """DPAPI defaults to the current Windows user; no key is stored alongside it."""
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                         wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


def load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("api_key_encrypted"):
        result["api_key"] = _crypt(base64.b64decode(result["api_key_encrypted"]), True).decode("utf-8")
    return result


def save_settings(path: Path, base_url: str, model: str, api_key: str) -> None:
    data = {"base_url": base_url, "model": model,
            "api_key_encrypted": base64.b64encode(_crypt(api_key.encode("utf-8"))).decode("ascii") if api_key else ""}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)
