# -*- coding: utf-8 -*-
"""Windows shell helpers WITHOUT any interpreter in the loop (imported by server.py on win32 only).

Rule: a value a user or a file can influence (file / folder names, default directories ...) is never spliced into the
source code of an interpreter (PowerShell, cmd, AppleScript, sh, python -c). Everything here talks to Win32 directly
through ctypes, so such values only ever travel as API arguments / memory buffers.

  copy_image_to_clipboard(path)      CF_DIB (+ "PNG") via OpenClipboard / SetClipboardData
  clipboard_image_to_png(out_path)   reads "PNG" or CF_DIB back and writes a PNG
  pick_path(mode, default_dir, name) GetOpenFileNameW / GetSaveFileNameW / SHBrowseForFolderW
"""
import ctypes
import io
import os
import struct
import sys
import time
from ctypes import wintypes as wt

if sys.platform != "win32":      # pragma: no cover
    raise ImportError("winshell is Windows-only")

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_sh = ctypes.WinDLL("shell32", use_last_error=True)
_ole = ctypes.WinDLL("ole32", use_last_error=True)
_cd = ctypes.WinDLL("comdlg32", use_last_error=True)

_k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
_k32.GlobalAlloc.restype = ctypes.c_void_p
_k32.GlobalLock.argtypes = [ctypes.c_void_p]
_k32.GlobalLock.restype = ctypes.c_void_p
_k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_k32.GlobalUnlock.restype = wt.BOOL
_k32.GlobalFree.argtypes = [ctypes.c_void_p]
_k32.GlobalFree.restype = ctypes.c_void_p
_k32.GlobalSize.argtypes = [ctypes.c_void_p]
_k32.GlobalSize.restype = ctypes.c_size_t
_u32.OpenClipboard.argtypes = [wt.HWND]
_u32.OpenClipboard.restype = wt.BOOL
_u32.CloseClipboard.argtypes = []
_u32.CloseClipboard.restype = wt.BOOL
_u32.EmptyClipboard.argtypes = []
_u32.EmptyClipboard.restype = wt.BOOL
_u32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
_u32.SetClipboardData.restype = ctypes.c_void_p
_u32.GetClipboardData.argtypes = [wt.UINT]
_u32.GetClipboardData.restype = ctypes.c_void_p
_u32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
_u32.IsClipboardFormatAvailable.restype = wt.BOOL
_u32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
_u32.RegisterClipboardFormatW.restype = wt.UINT

CF_DIB = 8
GMEM_MOVEABLE = 0x0002


def _open_clipboard(retries=20):
    for _ in range(retries):
        if _u32.OpenClipboard(None):
            return True
        time.sleep(0.05)
    return False


def _set_clip(fmt, data):
    h = _k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not h:
        return False
    p = _k32.GlobalLock(h)
    if not p:
        _k32.GlobalFree(h)
        return False
    ctypes.memmove(p, data, len(data))
    _k32.GlobalUnlock(h)
    if not _u32.SetClipboardData(fmt, h):
        _k32.GlobalFree(h)
        return False
    return True


def make_clipboard_payloads(path):
    """(CF_DIB bytes, PNG bytes) for an image file - pure data, no clipboard involved."""
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        rgba = im.convert("RGBA")
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.split()[3])
    bmp = io.BytesIO()
    flat.save(bmp, format="BMP")
    dib = bmp.getvalue()[14:]                      # CF_DIB = BMP without the 14-byte file header
    png = io.BytesIO()
    rgba.save(png, format="PNG")
    return dib, png.getvalue()


def dib_to_image(dib):
    """Inverse of the above: CF_DIB bytes -> PIL image (adds the missing BMP file header)."""
    from PIL import Image
    hs, = struct.unpack_from("<I", dib, 0)
    bitcount, = struct.unpack_from("<H", dib, 14)
    comp, = struct.unpack_from("<I", dib, 16)
    clrused, = struct.unpack_from("<I", dib, 32)
    off = 14 + hs
    if comp == 3 and hs == 40:
        off += 12
    if bitcount <= 8:
        off += (clrused or (1 << bitcount)) * 4
    hdr = b"BM" + struct.pack("<IHHI", 14 + len(dib), 0, 0, off)
    im = Image.open(io.BytesIO(hdr + dib))
    im.load()
    return im


def copy_image_to_clipboard(path):
    """Put the image file on the clipboard (flattened CF_DIB for every app + PNG for alpha-aware apps)."""
    dib, png_bytes = make_clipboard_payloads(path)
    if not _open_clipboard():
        raise OSError("clipboard is busy")
    try:
        _u32.EmptyClipboard()
        ok = _set_clip(CF_DIB, dib)
        _set_clip(_u32.RegisterClipboardFormatW("PNG"), png_bytes)
        if not ok:
            raise OSError("SetClipboardData failed")
    finally:
        _u32.CloseClipboard()
    return True


def _get_clip_bytes(fmt):
    if not _u32.IsClipboardFormatAvailable(fmt):
        return None
    h = _u32.GetClipboardData(fmt)
    if not h:
        return None
    size = _k32.GlobalSize(h)
    p = _k32.GlobalLock(h)
    if not p:
        return None
    try:
        return ctypes.string_at(p, size)
    finally:
        _k32.GlobalUnlock(h)


def clipboard_image_to_png(out_path):
    """Write the clipboard image to out_path as PNG. Returns True when the clipboard held an image."""
    from PIL import Image
    if not _open_clipboard():
        return False
    try:
        data = _get_clip_bytes(_u32.RegisterClipboardFormatW("PNG"))
        if data:
            im = Image.open(io.BytesIO(data))
        else:
            dib = _get_clip_bytes(CF_DIB)
            if not dib or len(dib) < 40:
                return False
            im = dib_to_image(dib)
        im.load()
        im.save(out_path, format="PNG")
        return True
    except Exception:
        return False
    finally:
        _u32.CloseClipboard()


# ---------------------------------------------------------------------------------------------- pickers
class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [("lStructSize", wt.DWORD), ("hwndOwner", wt.HWND), ("hInstance", wt.HINSTANCE),
                ("lpstrFilter", wt.LPCWSTR), ("lpstrCustomFilter", wt.LPWSTR), ("nMaxCustFilter", wt.DWORD),
                ("nFilterIndex", wt.DWORD), ("lpstrFile", wt.LPWSTR), ("nMaxFile", wt.DWORD),
                ("lpstrFileTitle", wt.LPWSTR), ("nMaxFileTitle", wt.DWORD), ("lpstrInitialDir", wt.LPCWSTR),
                ("lpstrTitle", wt.LPCWSTR), ("Flags", wt.DWORD), ("nFileOffset", wt.WORD),
                ("nFileExtension", wt.WORD), ("lpstrDefExt", wt.LPCWSTR), ("lCustData", wt.LPARAM),
                ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wt.LPCWSTR), ("pvReserved", ctypes.c_void_p),
                ("dwReserved", wt.DWORD), ("FlagsEx", wt.DWORD)]


BFFCALLBACK = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HWND, wt.UINT, wt.LPARAM, wt.LPARAM)


class BROWSEINFOW(ctypes.Structure):
    _fields_ = [("hwndOwner", wt.HWND), ("pidlRoot", ctypes.c_void_p), ("pszDisplayName", wt.LPWSTR),
                ("lpszTitle", wt.LPCWSTR), ("ulFlags", wt.UINT), ("lpfn", BFFCALLBACK),
                ("lParam", wt.LPARAM), ("iImage", ctypes.c_int)]


_cd.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
_cd.GetOpenFileNameW.restype = wt.BOOL
_cd.GetSaveFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
_cd.GetSaveFileNameW.restype = wt.BOOL
_sh.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
_sh.SHBrowseForFolderW.restype = ctypes.c_void_p
_sh.SHGetPathFromIDListW.argtypes = [ctypes.c_void_p, wt.LPWSTR]
_sh.SHGetPathFromIDListW.restype = wt.BOOL
_ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
_ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wt.DWORD]
_ole.CoInitializeEx.restype = ctypes.c_long
_ole.CoUninitialize.argtypes = []
_u32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, ctypes.c_void_p]
_u32.SendMessageW.restype = ctypes.c_ssize_t


def _file_dialog(save, default_dir, default_name):
    """Thin wrapper around GetOpenFileNameW / GetSaveFileNameW (a seam the unit test replaces)."""
    buf = ctypes.create_unicode_buffer(32768)
    buf.value = (default_name or "")[:32000]           # always leaves room for the terminating NUL
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = "All files (*.*)\0*.*\0\0"
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(buf, wt.LPWSTR)
    ofn.nMaxFile = len(buf)
    ofn.lpstrInitialDir = default_dir
    ofn.lpstrTitle = "另存新檔" if save else "選擇要開啟的檔案"
    ofn.Flags = 0x80000 | 0x800 | 0x8 | (0x2 if save else 0x1000)    # EXPLORER | PATHMUSTEXIST | NOCHANGEDIR | OVERWRITEPROMPT / FILEMUSTEXIST
    ok = (_cd.GetSaveFileNameW if save else _cd.GetOpenFileNameW)(ctypes.byref(ofn))
    return buf.value if ok else None


def _folder_dialog(default_dir):
    """SHBrowseForFolderW with the start folder passed as API data (BFFM_SETSELECTIONW)."""
    path_buf = ctypes.create_unicode_buffer(default_dir or "")

    def _cb(hwnd, msg, lparam, data):
        if msg == 1:                                   # BFFM_INITIALIZED
            _u32.SendMessageW(hwnd, 0x467, 1, ctypes.cast(path_buf, ctypes.c_void_p))   # BFFM_SETSELECTIONW
        return 0

    cb = BFFCALLBACK(_cb)
    disp = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFOW()
    bi.pszDisplayName = ctypes.cast(disp, wt.LPWSTR)
    bi.lpszTitle = "選擇要瀏覽的資料夾"
    bi.ulFlags = 0x1 | 0x40                            # RETURNONLYFSDIRS | NEWDIALOGSTYLE
    bi.lpfn = cb
    hr = _ole.CoInitializeEx(None, 2)                  # COINIT_APARTMENTTHREADED (needed by the new-style dialog)
    com_inited = hr in (0, 1)                          # S_OK / S_FALSE need a CoUninitialize; RPC_E_CHANGED_MODE (negative) does not
    try:
        pidl = _sh.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            return None
        try:
            out = ctypes.create_unicode_buffer(32768)
            return out.value if _sh.SHGetPathFromIDListW(pidl, out) else None
        finally:
            _ole.CoTaskMemFree(pidl)
    finally:
        if com_inited:
            _ole.CoUninitialize()


def pick_path(mode, default_dir, default_name=None):
    if mode == "file":
        return _file_dialog(False, default_dir, "")
    if mode == "save":
        return _file_dialog(True, default_dir, default_name or "scratch.md")
    return _folder_dialog(default_dir)
