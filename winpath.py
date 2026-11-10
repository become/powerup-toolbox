# -*- coding: utf-8 -*-
"""Windows path WHITELIST (imported by server.py and windows/launcher.py; inert on other platforms).

Why a whitelist: on Windows any path that reaches a network location (\\\\host\\share, \\\\?\\UNC\\..., \\\\?\\GLOBALROOT\\Device\\Mup\\...,
\\??\\..., a symlink / junction whose target is one of those, ...) makes the OS open an SMB / WebDAV connection and leak the
user's NTLM hash as soon as it is touched. Black-listing spellings has been bypassed three times, so every path that came
from a request / argv / file content must now prove it is a LOCAL DRIVE-LETTER path:

  1. only  X:\\...  (after str.strip + abspath); NUL / control chars / ADS colon / wildcard chars / device names refused;
  2. the drive's GetDriveTypeW must be FIXED, REMOVABLE, CDROM, RAMDISK or REMOTE
     (REMOTE = a network drive letter the user mapped himself; a hostile file can only point at a server the user
     already chose to connect to, never at an attacker-chosen host);
  3. EVERY component is examined with GetFileAttributesW (never following the link); a reparse point is never followed
     by the OS - its raw target text is read with FSCTL_GET_REPARSE_POINT and re-validated by the same rules:
       - symlink (0xA000000C): relative flag -> plain relative text (no ':' / leading '\\'); otherwise the substitute name
         must be  \\??\\X:\\...  or  \\??\\Volume{GUID}\\...
       - junction (0xA0000003): substitute name must be \\??\\X:\\...  or  \\??\\Volume{GUID}\\...
       - Volume{GUID}: accepted only when GetVolumePathNamesForVolumeNameW maps it to a LOCAL drive letter that passes (2);
         an unknown GUID or a volume mounted only as a folder is refused;
       - anything else (UNC, GLOBALROOT, \\Device\\..., \\??\\UNC\\...) is refused;
  4. reparse tags other than symlink / junction: OneDrive & friends (IO_REPARSE_TAG_CLOUD*, 0x9000x01A: local placeholder
     files), WOF 0x80000017 and DEDUP 0x80000013 (local transparent compression) are accepted; EVERY other tag
     (AppExecLink, WSL, ProjFS, HSM, unknown) is refused.
Target resolution is textual: a link is replaced by its validated target text and the walk continues, so the OS never
resolves an unvalidated link on our behalf (TOCTOU by a concurrent local attacker is out of scope).
"""
import os
import re
import struct
import sys

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes as _wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetFileAttributesW.argtypes = [_wt.LPCWSTR]
    _k32.GetFileAttributesW.restype = _wt.DWORD
    _k32.GetDriveTypeW.argtypes = [_wt.LPCWSTR]
    _k32.GetDriveTypeW.restype = _wt.UINT
    _k32.CreateFileW.argtypes = [_wt.LPCWSTR, _wt.DWORD, _wt.DWORD, ctypes.c_void_p, _wt.DWORD, _wt.DWORD, ctypes.c_void_p]
    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.DeviceIoControl.argtypes = [ctypes.c_void_p, _wt.DWORD, ctypes.c_void_p, _wt.DWORD, ctypes.c_void_p, _wt.DWORD,
                                     ctypes.POINTER(_wt.DWORD), ctypes.c_void_p]
    _k32.DeviceIoControl.restype = _wt.BOOL
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.GetVolumePathNamesForVolumeNameW.argtypes = [_wt.LPCWSTR, _wt.LPWSTR, _wt.DWORD, ctypes.POINTER(_wt.DWORD)]
    _k32.GetVolumePathNamesForVolumeNameW.restype = _wt.BOOL

_INVALID_HANDLE = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1 if IS_WIN else 0
INVALID_ATTRS = 0xFFFFFFFF
ATTR_REPARSE = 0x400
TAG_SYMLINK = 0xA000000C
TAG_MOUNT_POINT = 0xA0000003
TAG_WOF = 0x80000017
TAG_DEDUP = 0x80000013
# DRIVE_REMOVABLE, FIXED, REMOTE, CDROM, RAMDISK   (0 UNKNOWN, 1 NO_ROOT_DIR are refused)
ALLOWED_DRIVE_TYPES = (2, 3, 4, 5, 6)
MAX_HOPS = 32

_DRIVE_RE = re.compile(r"^[A-Za-z]:\\")
_VOLUME_RE = re.compile(r"^Volume\{([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})\}(\\.*)?$")
_EXT_PREFIX_RE = re.compile(r"^\\\\\?\\[A-Za-z]:\\")
_NTDRIVE_RE =re.compile(r"^([A-Za-z]:)(\\.*)?$")
_BAD_COMP_CHARS = set('<>"|?*:')
_RESERVED = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
             | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)})


def is_cloud_tag(tag):
    return (tag & 0xFFFF0FFF) == 0x9000001A


def tag_allowed_plain(tag):
    return is_cloud_tag(tag) or tag in (TAG_WOF, TAG_DEDUP)


def _split_parts(rest):
    return [x for x in re.split(r"[\\/]+", rest) if x]


def _comp_ok(comp):
    if comp in (".", ".."):
        return True
    if any(ord(c) < 32 or c in _BAD_COMP_CHARS for c in comp):
        return False
    stem = comp.split(".")[0].rstrip(" ").upper()
    return stem not in _RESERVED


def _plain_comp(comp):
    """An ordinary name: not '.', '..', empty, no trailing dot / space, not a device name."""
    return bool(comp) and comp not in (".", "..") and comp == comp.rstrip(" .") and _comp_ok(comp)


def _attrs(path):
    """(attributes, error_code). Never follows a final reparse point."""
    a = _k32.GetFileAttributesW(path)
    if a == INVALID_ATTRS:
        return None, ctypes.get_last_error()
    return a, 0


def _drive_ok(root):
    """root = 'X:\\'"""
    return _k32.GetDriveTypeW(root[:3]) in ALLOWED_DRIVE_TYPES


def _form_ok(p):
    if not isinstance(p, str) or not _DRIVE_RE.match(p):
        return False
    if any(ord(c) < 32 for c in p):
        return False
    return _drive_ok(p)


def _read_reparse(path):
    """Open the reparse point itself (never its target) and return (tag, relative, substitute_name)."""
    h = _k32.CreateFileW(path, 0x80, 7, None, 3, 0x00200000 | 0x02000000, None)   # READ_ATTRIBUTES, OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    if h is None or h == _INVALID_HANDLE:
        return None
    try:
        buf = ctypes.create_string_buffer(16384)
        ret = _wt.DWORD(0)
        if not _k32.DeviceIoControl(h, 0x000900A8, None, 0, buf, 16384, ctypes.byref(ret), None):   # FSCTL_GET_REPARSE_POINT
            return None
    finally:
        _k32.CloseHandle(h)
    raw = buf.raw[:ret.value]
    if len(raw) < 8:
        return None
    tag = struct.unpack_from("<I", raw, 0)[0]
    if tag == TAG_SYMLINK:
        so, sl, _po, _pl, flags = struct.unpack_from("<HHHHI", raw, 8)
        start = 20
    elif tag == TAG_MOUNT_POINT:
        so, sl, _po, _pl = struct.unpack_from("<HHHH", raw, 8)
        flags, start = 0, 16
    else:
        return tag, False, None
    try:
        subst = raw[start + so:start + so + sl].decode("utf-16-le")
    except Exception:
        return None
    return tag, bool(flags & 1) if tag == TAG_SYMLINK else False, subst


def _volume_root(guid):
    buf = ctypes.create_unicode_buffer(1024)
    n = _wt.DWORD(0)
    if not _k32.GetVolumePathNamesForVolumeNameW("\\\\?\\Volume{%s}\\" % guid, buf, 1024, ctypes.byref(n)):
        return None
    for item in buf[:n.value].split("\0"):
        if re.match(r"^[A-Za-z]:\\$", item):
            return item
    return None


def _resolve_link(tag, relative, subst, linkdir):
    """Validated textual target of a symlink / junction -> (new_current_dir, remaining_parts) or None (= refuse)."""
    # RELATIVE symlinks (SYMLINK_FLAG_RELATIVE) are refused outright: their '..' semantics cannot be modelled safely
    # (the OS resolves them against the link's own location) and ordinary users do not create them.
    # An ABSOLUTE target must be canonical: \??\X:\ (or a local Volume GUID) + plain components, nothing simplified.
    if subst is None or relative:
        return None
    if not subst.startswith("\\??\\"):
        return None                    # an absolute link must be an NT path; anything else is not understood
    t = subst[4:]
    m = _VOLUME_RE.match(t)
    if m:
        root = _volume_root(m.group(1))
        if not root:
            return None                # unknown GUID / volume without a local drive letter
        rest = m.group(2) or ""
    else:
        m = _NTDRIVE_RE.match(t)
        if not m:
            return None                # \??\UNC\..., \??\GLOBALROOT\..., \Device\..., anything not X:
        root, rest = m.group(1) + "\\", m.group(2) or ""
    if not _form_ok(root):
        return None
    if "\\\\" in rest or "/" in rest:
        return None                    # empty component / forward slash
    parts = [x for x in rest.split("\\") if x]
    if any(not _plain_comp(p) for p in parts):
        return None                    # '.', '..', trailing dot / space, device names ...: refuse, never simplify
    return root, parts


def normalize(raw):
    """str.strip + NUL check + expanduser + abspath. Returns None for values that are not even a string path."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if raw == "" or "\x00" in raw:
        return None
    if _EXT_PREFIX_RE.match(raw):      # F1: a legitimate extended-length prefix is accepted ONLY when X:\ follows at once
        raw = raw[4:]
    try:
        return os.path.abspath(os.path.expanduser(raw))
    except Exception:
        return None


def is_allowed(path):
    """True when the (already normalised, absolute) path is a local / user-mapped drive-letter path whose whole chain of
    components is whitelisted. Nothing is stat'ed through an unvalidated link."""
    if not IS_WIN:
        return True
    if not _form_ok(path):
        return False
    cur = path[:3]
    parts = _split_parts(path[3:])
    hops = 0
    while parts:
        comp = parts.pop(0)
        if not _plain_comp(comp):
            return False               # includes "." and "..": the caller must have normalised the path first
        nxt = cur.rstrip("\\") + "\\" + comp
        a, err = _attrs(nxt)
        if a is None:
            if err in (2, 3):          # FILE_NOT_FOUND / PATH_NOT_FOUND
                return all(_plain_comp(p) for p in parts)    # only plain names may follow a missing component
            return False
        if a & ATTR_REPARSE:
            info = _read_reparse(nxt)
            if info is None:
                return False
            tag, relative, subst = info
            if tag in (TAG_SYMLINK, TAG_MOUNT_POINT):
                hops += 1
                if hops > MAX_HOPS:
                    return False
                res = _resolve_link(tag, relative, subst, cur)
                if res is None:
                    return False
                cur, newparts = res
                parts = newparts + parts
                continue
            if not tag_allowed_plain(tag):
                return False
        cur = nxt
    return True


def entry_ok(full):
    """Directory entry: parent chain was validated by the caller; a plain entry needs no more, a reparse entry is run
    through the full check (and is skipped / refused if it fails)."""
    if not IS_WIN:
        return True
    a, _err = _attrs(full)
    if a is None:
        return False
    if not (a & ATTR_REPARSE):
        return True
    return is_allowed(os.path.abspath(full))


def check_raw(raw):
    """normalize + is_allowed in one call (used for request fields). Empty / non-string values are 'allowed' here
    (the route reports its own 'missing path' error)."""
    if not IS_WIN:
        return not (isinstance(raw, str) and "\x00" in raw)
    if not isinstance(raw, str):
        return True
    if raw.strip() == "":
        return True
    n = normalize(raw)
    return n is not None and is_allowed(n)


if IS_WIN:
    _k32.GetLongPathNameW.argtypes = [_wt.LPCWSTR, _wt.LPWSTR, _wt.DWORD]
    _k32.GetLongPathNameW.restype = _wt.DWORD


if IS_WIN:
    _k32.GetSystemWindowsDirectoryW.argtypes = [_wt.LPWSTR, _wt.UINT]
    _k32.GetSystemWindowsDirectoryW.restype = _wt.UINT


def system_exe(rel):
    """Absolute path of a Windows system program (e.g. "explorer.exe", "System32\\icacls.exe"). The directory comes from
    GetSystemWindowsDirectoryW - never from PATH, the current directory or an environment variable that can be tampered
    with - so CreateProcess can not pick up a planted look-alike."""
    buf = ctypes.create_unicode_buffer(260)
    n = _k32.GetSystemWindowsDirectoryW(buf, 260)
    return os.path.join(buf.value if 0 < n < 260 else "C:\\Windows", rel)


def long_path(p):
    """8.3 short names -> long names (EVIL~1.PDF may really be evil.pdfx). Unchanged when the path does not exist."""
    if not IS_WIN:
        return p
    buf = ctypes.create_unicode_buffer(32768)
    n = _k32.GetLongPathNameW(p, buf, 32768)
    return buf.value if 0 < n < 32768 else p


def any_reparse(p):
    """True when the path itself or ANY component on the way is a reparse point (symlink, junction, cloud placeholder ...),
    or when anything cannot be examined (fail closed). Nothing is followed."""
    if not IS_WIN:
        return False
    if not _form_ok(p):
        return True
    cur = p[:3]
    for comp in _split_parts(p[3:]):
        cur = cur.rstrip("\\") + "\\" + comp
        a, _err = _attrs(cur)
        if a is None or (a & ATTR_REPARSE):
            return True
    return False


def drive_type(letter):
    return _k32.GetDriveTypeW(letter.upper() + ":\\") if IS_WIN else 3
