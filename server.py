#!/usr/bin/env python3
"""
MacToolbox — General-Purpose macOS Power Toolbox
Tool 1: Ultra-Fast Photo Viewer + Fit-to-Window + Crop/Clipboard + Face Detection + Apple Vision + AI
Tool 2: Markdown Browser & Live Reader/Editor (Finder integration, TOC, Mermaid, GFM Alerts, Live Reload)
"""
import os
import sys
import re
import json
import time
import base64
import shutil
import hashlib
import hmac
import io
import secrets
import urllib.parse
import subprocess
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
if sys.platform == "win32":
    import winshell     # Windows shell helpers: clipboard / pickers through ctypes, never through a PowerShell script
try:
    import winpath      # Windows path whitelist (only used on win32; a Mac bundle without the file still works)
except ImportError:
    if sys.platform == "win32":
        raise
    winpath = None

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

TOOLBOX_DIR = os.path.dirname(os.path.abspath(__file__))
IS_WINDOWS = sys.platform == "win32"
# Windows PyInstaller frozen build: read-only resources live in sys._MEIPASS,
# writable data always goes to %LOCALAPPDATA%\PowerUpToolbox
IS_WIN_FROZEN = IS_WINDOWS and bool(getattr(sys, "frozen", False))
if IS_WIN_FROZEN:
    TOOLBOX_DIR = getattr(sys, "_MEIPASS", TOOLBOX_DIR)
    WIN_DATA_DIR = os.environ.get("POWERUP_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PowerUpToolbox")
    import win_support
IS_APP_BUNDLE = (not IS_WINDOWS) and (".app/Contents/Resources" in TOOLBOX_DIR or not os.access(TOOLBOX_DIR, os.W_OK))

APP_VERSION = "2.7.0"
DEFAULT_GITHUB_REPO = os.environ.get("POWERUP_GITHUB_REPO", "become/powerup-toolbox")
DEFAULT_GITHUB_BRANCH = os.environ.get("POWERUP_GITHUB_BRANCH", "main")

def _mac_run(*a, **k):
    """Every helper started through here is a macOS tool (osascript, sips, qlmanage, screencapture, textutil, the Swift helper,
    open, codex ...). On any other platform nothing is started at all - a bare program name on Windows would be resolved
    through the current directory / PATH, so 'not found' is not good enough."""
    if sys.platform != "darwin":
        return subprocess.CompletedProcess(a[0] if a else k.get("args"), 127, "" if k.get("text") else b"", "" if k.get("text") else b"")
    return subprocess.run(*a, **k)


def _mac_popen(*a, **k):
    if sys.platform != "darwin":
        return None
    return subprocess.Popen(*a, **k)


def reveal_in_file_manager(path):
    """Cross-platform reveal in Finder or Windows Explorer."""
    if not path or not os.path.exists(path):
        return False
    norm = os.path.normpath(path)
    if IS_WINDOWS:
        if '"' in norm or any(ord(c) < 32 for c in norm) or path_is_unsafe(norm):
            return False                    # Windows file names cannot contain a double quote; never build a split argument
        if os.path.isdir(norm):
            subprocess.Popen([winpath.system_exe("explorer.exe"), norm])                  # argv list, the path is data
        else:
            subprocess.Popen([winpath.system_exe("explorer.exe"), "/select," + norm])     # one argv element (list2cmdline quotes it if needed)
    else:
        _mac_popen(["/usr/bin/open", "-R", "--", os.path.abspath(path)])
    return True

def windows_move_to_recycle_bin(path):
    """Move a file/folder to the Windows Recycle Bin (undoable). Raises OSError on failure."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort),
                    ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = 3  # FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.pTo = None
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted:
        raise OSError("無法移至資源回收筒（錯誤碼 %s）" % rc)


# Only these types may be handed to the user's default program. Deliberately small: no XML family (Edge runs XHTML
# script from file:), no legacy binary Office / RTF (macros, OLE), no project / script / shortcut / web / SVG types.
QUICKLOOK_EXT = {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".json",
                 ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic"}
QUICKLOOK_REFUSED = "為安全起見，此類型不支援以預設程式開啟"


def open_with_default_app(path):
    """Cross-platform open file with default system handler."""
    if not path or not os.path.exists(path):
        return False
    if IS_WINDOWS:
        # os.startfile() runs the default handler: for programs / scripts / shortcuts / active documents that is code
        # execution. Only the explicit QUICKLOOK_EXT list is handed to the shell.
        if path_is_unsafe(path) or winpath.any_reparse(path):
            return False                       # a symlink / junction / placeholder anywhere on the path: refuse (fake.pdf -> evil.exe)
        path = winpath.long_path(path)         # 8.3 short names are expanded BEFORE the extension is judged (EVIL~1.PDF = evil.pdfx)
        if path_is_unsafe(path) or winpath.any_reparse(path) or not os.path.isfile(path) or os.path.splitext(path)[1].lower() not in QUICKLOOK_EXT:
            return False
        os.startfile(path)
    else:
        _mac_popen(["/usr/bin/open", "--", os.path.abspath(path)])
    return True

def pick_native_path(mode="folder", default_dir=None, default_name=None):
    """Cross-platform native GUI file/folder picker. User-influenced values (default folder / file name) are only ever
    passed as DATA: Win32 API arguments on Windows, osascript argv on macOS - never spliced into script source."""
    if not default_dir or not os.path.isdir(default_dir):
        default_dir = os.path.expanduser("~/Desktop")
    if IS_WINDOWS:
        clean_dir = os.path.normpath(default_dir)
        if path_is_unsafe(clean_dir):
            clean_dir = os.path.expanduser("~")
        out = winshell.pick_path(mode, clean_dir, default_name)
        return out if out else None
    else:
        # macOS: the script text is a FIXED string; the values arrive in argv ("on run argv")
        if mode == "file":
            script = ('on run argv\n'
                      'POSIX path of (choose file with prompt "選擇要開啟的檔案：" default location (POSIX file (item 1 of argv)))\n'
                      'end run')
            args = [default_dir]
        elif mode == "save":
            script = ('on run argv\n'
                      'POSIX path of (choose file name with prompt "另存新檔為：" default location (POSIX file (item 1 of argv)) default name (item 2 of argv))\n'
                      'end run')
            args = [default_dir, default_name or "scratch.md"]
        else:
            script = ('on run argv\n'
                      'POSIX path of (choose folder with prompt "選擇要瀏覽的資料夾：" default location (POSIX file (item 1 of argv)))\n'
                      'end run')
            args = [default_dir]
        res = _mac_run(["/usr/bin/osascript", "-e", script, "--"] + args, capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            return os.path.abspath(res.stdout.strip())
        return None

def compare_versions(v1, v2):
    """Return 1 if v1 > v2, -1 if v1 < v2, 0 if equal."""
    def parse_parts(v):
        cleaned = re.sub(r'^[^\d]*', '', str(v))
        parts = []
        for x in cleaned.split('.'):
            try:
                parts.append(int(re.sub(r'\D', '', x)))
            except Exception:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        return parts[:3]
    p1 = parse_parts(v1)
    p2 = parse_parts(v2)
    if p1 > p2:
        return 1
    elif p1 < p2:
        return -1
    return 0

def check_github_update(repo=None, branch=None):
    """Check GitHub main branch dist/version.json for available updates."""
    import urllib.request
    repo = repo or DEFAULT_GITHUB_REPO
    branch = branch or DEFAULT_GITHUB_BRANCH
    raw_url = f"https://raw.githubusercontent.com/{repo}/{branch}/dist/version.json"
    req = urllib.request.Request(
        raw_url,
        headers={"User-Agent": f"MacToolbox/{APP_VERSION} (AutoUpdater)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3.5) as resp:
            if resp.status == 200:
                remote_data = json.loads(resp.read().decode("utf-8"))
                remote_ver = remote_data.get("version", "0.0.0")
                has_update = compare_versions(remote_ver, APP_VERSION) > 0
                return {
                    "ok": True,
                    "repo": repo,
                    "branch": branch,
                    "current_version": APP_VERSION,
                    "latest_version": remote_ver,
                    "has_update": has_update,
                    "release_date": remote_data.get("release_date", ""),
                    "changelog": remote_data.get("changelog", []),
                    "dmg_url": remote_data.get("dmg_url", f"https://raw.githubusercontent.com/{repo}/{branch}/dist/MacToolbox-latest.dmg"),
                    "pkg_url": remote_data.get("pkg_url", f"https://raw.githubusercontent.com/{repo}/{branch}/dist/MacToolbox-latest.pkg"),
                    "source": "github"
                }
    except Exception as e:
        # Fallback to local dist/version.json if offline or before remote repo is public
        local_vfile = os.path.join(TOOLBOX_DIR, "dist", "version.json")
        if os.path.isfile(local_vfile):
            try:
                with open(local_vfile, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                remote_ver = local_data.get("version", APP_VERSION)
                return {
                    "ok": True,
                    "repo": repo,
                    "branch": branch,
                    "current_version": APP_VERSION,
                    "latest_version": remote_ver,
                    "has_update": compare_versions(remote_ver, APP_VERSION) > 0,
                    "release_date": local_data.get("release_date", ""),
                    "changelog": local_data.get("changelog", []),
                    "dmg_url": local_data.get("dmg_url", ""),
                    "pkg_url": local_data.get("pkg_url", ""),
                    "source": "local_fallback",
                    "note": f"GitHub check fallback: {str(e)}"
                }
            except Exception:
                pass
        return {
            "ok": False,
            "repo": repo,
            "branch": branch,
            "current_version": APP_VERSION,
            "has_update": False,
            "error": str(e)
        }

if IS_APP_BUNDLE:
    USER_SUPPORT_DIR = os.path.expanduser("~/Library/Application Support/MacToolbox")
    CACHE_DIR = os.path.expanduser("~/Library/Caches/MacToolbox/thumbs")
    SCRATCH_DIR = os.path.expanduser("~/Library/Caches/MacToolbox/scratch")
    CAPTURES_DIR = os.path.expanduser("~/Pictures/MacToolbox_Captures")
    MODELS_DIR = os.path.join(USER_SUPPORT_DIR, "models")
    if not os.path.isdir(MODELS_DIR) and os.path.isdir("/Users/become/Claude/Projects/toolbox/models"):
        MODELS_DIR = "/Users/become/Claude/Projects/toolbox/models"
elif IS_WIN_FROZEN:
    MODELS_DIR = os.path.join(WIN_DATA_DIR, "models")
    CACHE_DIR = os.path.join(WIN_DATA_DIR, "cache", "thumbs")
    SCRATCH_DIR = os.path.join(WIN_DATA_DIR, "cache", "scratch")
    CAPTURES_DIR = os.path.join(WIN_DATA_DIR, "captures")
else:
    MODELS_DIR = os.path.join(TOOLBOX_DIR, "models")
    CACHE_DIR = os.path.join(TOOLBOX_DIR, ".cache", "thumbs")
    SCRATCH_DIR = os.path.join(TOOLBOX_DIR, ".cache", "scratch")
    CAPTURES_DIR = os.path.join(TOOLBOX_DIR, "captures")

HELPER_BIN = os.path.join(TOOLBOX_DIR, "mac_vision_helper")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(SCRATCH_DIR, exist_ok=True)
os.makedirs(CAPTURES_DIR, exist_ok=True)

IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".svg"}
MD_EXTS = {".md", ".markdown", ".mdx"}
JSON_EXTS = {".json", ".jsonl", ".ndjson", ".geojson"}
CSV_EXTS = {".csv", ".tsv"}
PPTX_EXTS = {".pptx", ".ppt"}
DOCX_EXTS = {".docx", ".doc", ".rtf"}
XLSX_EXTS = {".xlsx", ".xlsm", ".xls"}
XML_EXTS = {".xml", ".plist", ".rss", ".atom", ".wsdl", ".xsd", ".csproj", ".pom", ".jmx"}
PDF_EXTS = {".pdf"}

# Ensure user site-packages and bundled vendor packages are importable inside .app bundle
for _extra_site in [
    TOOLBOX_DIR,
    os.path.expanduser("~/Library/Python/3.9/lib/python/site-packages"),
    os.path.expanduser("~/Library/Python/3.10/lib/python/site-packages"),
    os.path.expanduser("~/Library/Python/3.11/lib/python/site-packages"),
    os.path.expanduser("~/Library/Python/3.12/lib/python/site-packages"),
]:
    if os.path.isdir(_extra_site) and _extra_site not in sys.path:
        sys.path.insert(0, _extra_site)

# State for Finder "Open With" navigation
LAST_OPEN_REQUEST = {"id": 0, "path": None, "type": None, "timestamp": 0}
MODEL_CACHE = {}
MODEL_LOCK = threading.Lock()


def natural_key(s):
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]


def detect_file_type(path):
    if not path:
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in MD_EXTS:
        return "markdown"
    if ext in JSON_EXTS:
        return "json"
    if ext in CSV_EXTS:
        return "csv"
    if ext in PPTX_EXTS:
        return "pptx"
    if ext in DOCX_EXTS:
        return "docx"
    if ext in XLSX_EXTS:
        return "xlsx"
    if ext in XML_EXTS:
        return "xml"
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    return None


def imread_unicode(path, flags=None):
    import cv2
    import numpy as np
    if flags is None:
        flags = cv2.IMREAD_COLOR
    data = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(data, flags)
    if img is None and path.lower().endswith((".heic", ".tif", ".tiff")):
        tmp = os.path.join(CACHE_DIR, "heic_tmp.png")
        _mac_run(["/usr/bin/sips", "-s", "format", "png", path, "--out", tmp],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(tmp):
            img = cv2.imdecode(np.fromfile(tmp, dtype=np.uint8), flags)
    return img


def imwrite_unicode(path, img, ext=None):
    import cv2
    if ext is None:
        ext = os.path.splitext(path)[1] or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, 95] if ext.lower() in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if ok:
        buf.tofile(path)
    return ok


def get_face_detector():
    with MODEL_LOCK:
        if "face_det" not in MODEL_CACHE:
            import torch
            from facexlib.detection import init_detection_model
            det = init_detection_model("retinaface_resnet50", half=False, device=torch.device("cpu"))
            MODEL_CACHE["face_det"] = det
        return MODEL_CACHE["face_det"]


def get_ai_model(name):
    with MODEL_LOCK:
        if name not in MODEL_CACHE:
            import torch
            import spandrel_extra_arches
            from spandrel import ModelLoader
            spandrel_extra_arches.install()
            dev = torch.device("mps" if (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()) else ("cuda" if torch.cuda.is_available() else "cpu"))
            m = ModelLoader(device=dev).load_from_file(os.path.join(MODELS_DIR, name))
            m.eval()
            MODEL_CACHE[name] = (m, dev)
        return MODEL_CACHE[name]


def find_codex_bin():
    for candidate in [
        shutil.which("codex"),
        "/opt/homebrew/bin/codex",
        "/usr/local/bin/codex",
        os.path.expanduser("~/.npm-global/bin/codex"),
    ]:
        if candidate and os.path.exists(candidate):
            return candidate
    res = _mac_run(["/bin/zsh", "-l", "-c", "which codex"], capture_output=True, text=True)
    if res.returncode == 0 and res.stdout.strip():
        return res.stdout.strip().splitlines()[-1]
    return "codex"


def crop_image_array(img, crop):
    if not crop:
        return img, (0, 0, img.shape[1], img.shape[0])
    h, w = img.shape[:2]
    cx = max(0, min(w - 1, int(round(float(crop.get("x", 0)) * w))))
    cy = max(0, min(h - 1, int(round(float(crop.get("y", 0)) * h))))
    cw = max(1, min(w - cx, int(round(float(crop.get("w", 1)) * w))))
    ch = max(1, min(h - cy, int(round(float(crop.get("h", 1)) * h))))
    return img[cy:cy + ch, cx:cx + cw].copy(), (cx, cy, cw, ch)


def descreen_fft_gray(g, r0=0.16, k=2.2):
    import cv2
    import numpy as np
    pad = 32
    x = cv2.copyMakeBorder(g, pad, pad, pad, pad, cv2.BORDER_REFLECT).astype(np.float32)
    h, w = x.shape
    F = np.fft.fftshift(np.fft.fft2(x))
    L = np.log1p(np.abs(F)).astype(np.float32)
    bg = cv2.GaussianBlur(cv2.morphologyEx(L, cv2.MORPH_OPEN, np.ones((9, 9))), (0, 0), 6)
    yy, xx = np.indices((h, w))
    r = np.hypot((yy - h / 2) / (h / 2), (xx - w / 2) / (w / 2))
    peak = ((L - bg) > k) & (r > r0)
    mask = cv2.dilate(peak.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(np.float32)
    mask = cv2.GaussianBlur(mask, (0, 0), 1.5)
    mask = np.clip(mask * 1.4, 0, 1)
    mask = np.maximum(mask, mask[::-1, ::-1])
    F2 = F * (1 - mask) + F / np.maximum(np.abs(F), 1e-3) * np.exp(bg) * mask
    y = np.real(np.fft.ifft2(np.fft.ifftshift(F2)))[pad:-pad, pad:-pad]
    return cv2.bilateralFilter(np.clip(y, 0, 255).astype(np.uint8), 5, 18, 2)


def discover_variants(file_path):
    """Find related images in the same folder or captures folder for A/B comparison."""
    if not file_path or not os.path.exists(file_path):
        return []
    fname = os.path.basename(file_path)
    stem, _ = os.path.splitext(fname)
    base_key = re.sub(r'_(ORIG|GA-S2|GA-S3|GM-S2|GM-S3|G-M-S1|SIX|選項一覽|crop.*|ai.*|nobg.*|codex.*)$', '', stem)

    candidates = []
    seen = set()

    def add_cand(label, p):
        ap = os.path.abspath(p)
        if entry_is_unsafe_link(ap):
            return
        if os.path.exists(ap) and ap not in seen:
            seen.add(ap)
            candidates.append({"label": label, "path": ap, "name": os.path.basename(ap)})

    add_cand("目前圖片", file_path)

    parent = os.path.dirname(file_path)
    # 1. Check files with similar prefix in same folder & captures folder
    for folder in (parent, CAPTURES_DIR):
        if os.path.isdir(folder):
            for entry in sorted(os.listdir(folder), key=natural_key):
                if entry.startswith(base_key) and os.path.splitext(entry)[1].lower() in IMAGE_EXTS:
                    add_cand(entry, os.path.join(folder, entry))

    # 2. Also include up to 30 other images in the same folder so user can compare ANY two images easily
    if os.path.isdir(parent):
        for entry in sorted(os.listdir(parent), key=natural_key)[:40]:
            if os.path.splitext(entry)[1].lower() in IMAGE_EXTS:
                add_cand(entry, os.path.join(parent, entry))

    return candidates


def scan_recent_files():
    """Find recently modified supported files across captures, toolbox, ~/Claude/Projects, ~/Desktop, ~/Downloads, ~/Documents."""
    roots = [
        CAPTURES_DIR,
        TOOLBOX_DIR,
        os.path.expanduser("~/Claude/Projects"),
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Downloads"),
        os.path.expanduser("~/Documents"),
    ]
    found = []
    seen = set()
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", ".next", "dist", "build", ".cache"}
    skip_names = {"package-lock.json", "tsconfig.tsbuildinfo"}

    for r in roots:
        if path_is_unsafe(r) or not os.path.isdir(r):
            continue
        for root_dir, subdirs, files in safe_walk(r):
            rel_depth = root_dir[len(r):].count(os.sep)
            if rel_depth > 3:
                subdirs[:] = []
                continue
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in skip_dirs]
            for f in files:
                if f.startswith(".") or f in skip_names:
                    continue
                ftype = detect_file_type(f)
                if ftype:
                    full = os.path.abspath(os.path.join(root_dir, f))
                    if full not in seen:
                        seen.add(full)
                        try:
                            st = os.stat(full)
                            found.append({
                                "name": f,
                                "folder": os.path.basename(root_dir),
                                "dir": root_dir,
                                "path": full,
                                "type": ftype,
                                "size": st.st_size,
                                "mtime": int(st.st_mtime),
                            })
                        except Exception:
                            pass
    found.sort(key=lambda x: x["mtime"], reverse=True)
    return found[:120]


def scan_recent_markdowns():
    return [f for f in scan_recent_files() if f.get("type") == "markdown"][:60]


class EncryptedOfficeError(Exception):
    """Password-protected Office file (cannot be previewed)."""


# ---- In-memory unlock cache -------------------------------------------------------------------------
# Decrypted bytes only (never the password), keyed by (path, mtime_ns, size). Lives in this process's RAM:
# never written to disk / log / cache dir. Limits: 5 entries, 200 MB total, 10 minutes idle.
UNLOCK_MAX_ENTRIES = 5
UNLOCK_MAX_BYTES = 200 * 1024 * 1024
UNLOCK_IDLE_SECONDS = 600
_UNLOCK_CACHE = {}          # key -> {"data": bytes, "last": float}
_UNLOCK_LOCK = threading.Lock()
_PDFIUM_LOCK = threading.Lock()   # pdfium is not thread-safe


def _unlock_key(path):
    st = os.stat(path)
    return (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)


def _unlock_purge_locked(now=None):
    now = now or time.time()
    for k in [k for k, v in _UNLOCK_CACHE.items() if now - v["last"] > UNLOCK_IDLE_SECONDS]:
        del _UNLOCK_CACHE[k]
    while _UNLOCK_CACHE and (len(_UNLOCK_CACHE) > UNLOCK_MAX_ENTRIES or
                             sum(len(v["data"]) for v in _UNLOCK_CACHE.values()) > UNLOCK_MAX_BYTES):
        oldest = min(_UNLOCK_CACHE, key=lambda k: _UNLOCK_CACHE[k]["last"])
        del _UNLOCK_CACHE[oldest]


def unlock_cache_put(path, data):
    with _UNLOCK_LOCK:
        _UNLOCK_CACHE[_unlock_key(path)] = {"data": data, "last": time.time()}
        _unlock_purge_locked()


def unlock_cache_get(path):
    try:
        key = _unlock_key(path)
    except OSError:
        return None
    with _UNLOCK_LOCK:
        _unlock_purge_locked()
        ent = _UNLOCK_CACHE.get(key)
        if ent:
            ent["last"] = time.time()
            return ent["data"]
    return None


def _unlock_sweeper():
    while True:
        time.sleep(60)
        with _UNLOCK_LOCK:
            _unlock_purge_locked()


threading.Thread(target=_unlock_sweeper, name="unlock-sweeper", daemon=True).start()


class WrongPasswordError(Exception):
    pass


def can_unlock(path):
    """Whether this server can offer a password prompt for the file."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            if not IS_WINDOWS:
                return False
            import pypdfium2  # noqa: F401
            return True
        import msoffcrypto  # noqa: F401
        return True
    except Exception:
        return False


def decrypt_to_bytes(path, password):
    """Decrypt a password-protected file fully in memory. Returns plain bytes; raises WrongPasswordError."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium
        with _PDFIUM_LOCK:
            try:
                pdf = pdfium.PdfDocument(path, password=password)
            except pdfium.PdfiumError as e:
                if "password" in str(e).lower():
                    raise WrongPasswordError()
                raise RuntimeError("PDF 解密失敗")
            try:
                buf = io.BytesIO()
                pdf.save(buf, flags=pdfium.raw.FPDF_REMOVE_SECURITY)
                return buf.getvalue()
            finally:
                pdf.close()
    import msoffcrypto
    from msoffcrypto.exceptions import InvalidKeyError
    with open(path, "rb") as f:
        of = msoffcrypto.OfficeFile(f)
        if not of.is_encrypted():
            raise RuntimeError("此檔案沒有加密")
        try:
            of.load_key(password=password, verify_password=True)
            out = io.BytesIO()
            of.decrypt(out)
        except InvalidKeyError:
            raise WrongPasswordError()
        except Exception:
            # never include the underlying message (could echo input) in what we return
            raise RuntimeError("解密失敗（檔案格式不支援或已損毀）")
        return out.getvalue()


def encrypted_response(path):
    return {"ok": False, "error": "此檔案有密碼保護，目前無法預覽", "encrypted": True, "can_unlock": can_unlock(path)}


ENCRYPTED_OFFICE_RESPONSE = {"ok": False, "error": "此檔案有密碼保護，目前無法預覽", "encrypted": True}  # kept for compatibility


def _ole_stream_names(f, max_dir_sectors=4096, max_fat_sectors=4096):
    """Read an OLE2 compound file's directory (stream/storage names only) without loading the file.
    Reads the 512-byte header, the FAT sectors and the directory sector chain. Returns a set or None."""
    import struct
    f.seek(0)
    hdr = f.read(512)
    if len(hdr) < 512 or hdr[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
        return None
    ssz = 1 << struct.unpack_from("<H", hdr, 30)[0]
    if ssz not in (512, 4096):
        return None
    dir_start = struct.unpack_from("<I", hdr, 48)[0]
    difat_start, n_difat = struct.unpack_from("<II", hdr, 68)
    fat_secs = [x for x in struct.unpack_from("<109I", hdr, 76) if x < 0xFFFFFFFA]
    ENDMARK = 0xFFFFFFFA
    sec, n = difat_start, 0
    while sec < ENDMARK and n < min(n_difat, 1000):
        f.seek((sec + 1) * ssz)
        blk = f.read(ssz)
        if len(blk) < ssz:
            break
        vals = struct.unpack("<%dI" % (ssz // 4), blk)
        fat_secs += [x for x in vals[:-1] if x < ENDMARK]
        sec, n = vals[-1], n + 1
    fat = []
    for fs in fat_secs[:max_fat_sectors]:
        f.seek((fs + 1) * ssz)
        blk = f.read(ssz)
        if len(blk) < ssz:
            break
        fat += struct.unpack("<%dI" % (ssz // 4), blk)
    names, sec, seen = set(), dir_start, 0
    while sec < ENDMARK and sec < len(fat) + 1 and seen < max_dir_sectors:
        f.seek((sec + 1) * ssz)
        blk = f.read(ssz)
        for off in range(0, len(blk) - 127, 128):
            nlen = struct.unpack_from("<H", blk, off + 64)[0]
            if 2 <= nlen <= 64:
                names.add(blk[off:off + nlen - 2].decode("utf-16-le", "ignore"))
        seen += 1
        sec = fat[sec] if sec < len(fat) else ENDMARK
    return names


def check_office_encrypted(target):
    """Raise EncryptedOfficeError for password-protected OOXML (OLE container holding an
    'EncryptedPackage' stream). Only the OLE directory is read (no whole-file read).
    Legacy encrypted .xls is detected from xlrd's own error."""
    try:
        with open(target, "rb") as f:
            names = _ole_stream_names(f)
    except Exception:
        return
    if names and "EncryptedPackage" in names:
        raise EncryptedOfficeError()


def parse_pptx_file(target, data=None):
    """Extract slides, 2D canvas items, text blocks, tables, speaker notes, and all embedded/SVG images from a .pptx file.
    `data` = decrypted bytes (memory-only mode: nothing derived from them is written to disk)."""
    import pptx
    mem = data is not None
    if not mem:
        check_office_encrypted(target)
    st = os.stat(target)
    ext = os.path.splitext(target)[1].lower()
    if ext == ".ppt":
        return {
            "ok": True,
            "path": target,
            "name": os.path.basename(target),
            "dir": os.path.dirname(target),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "aspect": 1.777,
            "slides": [{
                "index": 1,
                "title": f"舊版二進位簡報 ({os.path.basename(target)})",
                "blocks": [{"text": "此檔案為舊版 .ppt 格式，請點擊上方「👁️ 原版預覽」直接檢視完整版面。", "level": 0, "is_title": False}],
                "tables": [],
                "images": [],
                "canvas_items": [],
                "notes": ""
            }],
            "all_images": [],
            "markdown": f"# {os.path.basename(target)}\n\n（舊版 .ppt 格式，支援系統 QuickLook 預覽）"
        }

    prs = pptx.Presentation(io.BytesIO(data) if mem else target)
    sw = float(prs.slide_width or 12192000)
    sh = float(prs.slide_height or 6858000)
    aspect = round(sw / max(1.0, sh), 4)

    slides_out = []
    all_images = []
    md_lines = [f"# 📽️ {os.path.basename(target)}", ""]

    def save_blob_to_cache(blob, ext_hint, shape_name, slide_idx, box):
        img_ext = (ext_hint or "png").lower().lstrip(".")
        if img_ext in ("jfif", "jpe"):
            img_ext = "jpg"
        if img_ext not in ("jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "tif", "tiff"):
            img_ext = "png"
        if mem:   # decrypted content: embed as data URI, never touch the disk
            mime = {"jpg": "image/jpeg", "svg": "image/svg+xml", "tif": "image/tiff"}.get(img_ext, "image/" + img_ext)
            return {
                "path": "",
                "url": "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode("ascii")),
                "name": shape_name or f"Slide_{slide_idx}_Image.{img_ext}",
                "slide": slide_idx,
                "box": box,
            }
        h_md5 = hashlib.md5(blob).hexdigest()[:16]
        img_fname = f"pptx_{h_md5}.{img_ext}"
        img_path = os.path.join(CACHE_DIR, img_fname)
        if not os.path.exists(img_path):
            with open(img_path, "wb") as img_f:
                img_f.write(blob)
        if img_ext in ("tif", "tiff"):
            png_path = os.path.join(CACHE_DIR, f"pptx_{h_md5}.png")
            if not os.path.exists(png_path):
                _mac_run(["/usr/bin/sips", "-s", "format", "png", img_path, "--out", png_path],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(png_path):
                img_path = png_path
        url = f"/api/image?path={urllib.parse.quote(img_path)}"
        return {
            "path": img_path,
            "url": url,
            "name": shape_name or f"Slide_{slide_idx}_Image.{img_ext}",
            "slide": slide_idx,
            "box": box,
        }

    for s_idx, slide in enumerate(prs.slides, start=1):
        title_text = ""
        try:
            if slide.shapes.title and slide.shapes.title.text:
                title_text = slide.shapes.title.text.strip()
        except Exception:
            pass

        blocks = []
        tables = []
        charts = []
        images = []
        canvas_items = []
        extracted_rids = set()

        def compute_transformed_box(shape, tf):
            raw_x = float(getattr(shape, "left", 0) or 0)
            raw_y = float(getattr(shape, "top", 0) or 0)
            raw_w = float(getattr(shape, "width", 0) or 0)
            raw_h = float(getattr(shape, "height", 0) or 0)
            if tf is not None:
                gx, gy, gw, gh, chx, chy, chw, chh = tf
                if chw > 0 and chh > 0:
                    raw_x = gx + (raw_x - chx) * (gw / chw)
                    raw_y = gy + (raw_y - chy) * (gh / chh)
                    raw_w = raw_w * (gw / chw)
                    raw_h = raw_h * (gh / chh)
            return {
                "x": round(max(0.0, min(98.0, (raw_x / sw) * 100.0)), 2),
                "y": round(max(0.0, min(98.0, (raw_y / sh) * 100.0)), 2),
                "w": round(max(2.0, min(100.0, (raw_w / sw) * 100.0)), 2),
                "h": round(max(2.0, min(100.0, (raw_h / sh) * 100.0)), 2),
            }

        def format_chart_num(val, series_name=""):
            if val is None or val == "":
                return ""
            try:
                fval = float(val)
                if "yoy" in series_name.lower() or "率" in series_name or "占比" in series_name or (-1.5 <= fval <= 1.5 and fval != 0 and not fval.is_integer()):
                    if fval == -1.0:
                        return "—"
                    return f"{fval * 100:.1f}%"
                if abs(fval) >= 1_000_000:
                    return f"{fval / 1_000_000:.2f}M"
                if fval.is_integer():
                    return f"{int(fval):,}"
                return f"{fval:,.1f}"
            except Exception:
                return str(val)

        def process_shape(shape, tf=None):
            nonlocal title_text
            # Handle Group Shape with proper coordinate transformation (chOff / chExt -> off / ext)
            try:
                if hasattr(shape, "shapes"):
                    xml_str = shape._element.xml
                    gx = float(getattr(shape, "left", 0) or 0)
                    gy = float(getattr(shape, "top", 0) or 0)
                    gw = float(getattr(shape, "width", 0) or 0)
                    gh = float(getattr(shape, "height", 0) or 0)
                    if tf is not None:
                        pgx, pgy, pgw, pgh, pchx, pchy, pchw, pchh = tf
                        if pchw > 0 and pchh > 0:
                            gx = pgx + (gx - pchx) * (pgw / pchw)
                            gy = pgy + (gy - pchy) * (pgh / pchh)
                            gw = gw * (pgw / pchw)
                            gh = gh * (pgh / pchh)
                    m_choff = re.search(r'<a:chOff\s+x="(-?\d+)"\s+y="(-?\d+)"', xml_str)
                    m_chext = re.search(r'<a:chExt\s+cx="(\d+)"\s+cy="(\d+)"', xml_str)
                    if m_choff and m_chext:
                        new_tf = (
                            gx, gy, gw, gh,
                            float(m_choff.group(1)), float(m_choff.group(2)),
                            float(m_chext.group(1)), float(m_chext.group(2))
                        )
                    else:
                        new_tf = tf
                    for sub in shape.shapes:
                        process_shape(sub, new_tf)
                    return
            except Exception:
                pass

            box = compute_transformed_box(shape, tf)
            shape_name = getattr(shape, "name", "") or ""
            lower_name = shape_name.lower()

            # Skip slide number placeholders
            if "投影片編號" in shape_name or "slide number" in lower_name:
                return

            # 1. Image shape (standard shape.image OR SVG blip / picture fill via r:embed)
            got_image = False
            try:
                if hasattr(shape, "image") and shape.image is not None:
                    blob = shape.image.blob
                    img_ext = (shape.image.ext or "png").lower()
                    img_obj = save_blob_to_cache(blob, img_ext, shape_name, s_idx, box)
                    images.append(img_obj)
                    canvas_items.append({"type": "image", "image": img_obj, "url": img_obj["url"], "path": img_obj["path"], "name": img_obj["name"], "box": box})
                    if not any(x["path"] == img_obj["path"] for x in all_images):
                        all_images.append(img_obj)
                    got_image = True
                    for rid in re.findall(r'r:embed="([^"]+)"', shape._element.xml):
                        extracted_rids.add(rid)
            except Exception:
                pass

            if not got_image:
                try:
                    xml_s = shape._element.xml
                    rids = re.findall(r'r:embed="([^"]+)"', xml_s)
                    for rid in reversed(rids):
                        rel = slide.part.rels.get(rid)
                        if rel and not rel.is_external and "image" in rel.reltype:
                            part = rel.target_part
                            ext_hint = os.path.splitext(str(rel.target_partname))[1].lstrip(".") or "png"
                            img_obj = save_blob_to_cache(part.blob, ext_hint, shape_name, s_idx, box)
                            images.append(img_obj)
                            canvas_items.append({"type": "image", "image": img_obj, "url": img_obj["url"], "path": img_obj["path"], "name": img_obj["name"], "box": box})
                            if not any(x["path"] == img_obj["path"] for x in all_images):
                                all_images.append(img_obj)
                            extracted_rids.update(rids)
                            got_image = True
                            break
                except Exception:
                    pass

            # 2. Native PowerPoint Chart shape (has_chart)
            try:
                if getattr(shape, "has_chart", False):
                    ch = shape.chart
                    ch_title = ""
                    try:
                        if ch.has_title and ch.chart_title.has_text_frame:
                            ch_title = ch.chart_title.text_frame.text.strip()
                    except Exception:
                        pass
                    ch_type_str = str(getattr(ch, "chart_type", "")).upper()
                    kind = "line" if "LINE" in ch_type_str else ("pie" if "PIE" in ch_type_str else "bar")
                    categories = []
                    series_list = []
                    seen_series = set()
                    for plot in ch.plots:
                        try:
                            cats = [str(c) for c in plot.categories]
                            if cats and not categories:
                                categories = cats
                        except Exception:
                            pass
                        for ser in plot.series:
                            s_name = str(getattr(ser, "name", "") or f"Series {len(series_list)+1}").strip()
                            if s_name in seen_series:
                                continue
                            seen_series.add(s_name)
                            try:
                                raw_vals = [ (float(v) if v is not None else None) for v in ser.values ]
                            except Exception:
                                raw_vals = []
                            # Skip all-zero placeholder series if another plot has real data
                            if raw_vals and all((v is None or v == 0.0 or v == -1.0) for v in raw_vals):
                                continue
                            is_pct = ("yoy" in s_name.lower() or "率" in s_name or "占比" in s_name)
                            series_list.append({
                                "name": s_name,
                                "values": raw_vals,
                                "is_pct": is_pct,
                                "formatted": [format_chart_num(v, s_name) for v in raw_vals],
                            })
                    if series_list:
                        if not categories:
                            max_len = max(len(s["values"]) for s in series_list)
                            categories = [f"#{i+1}" for i in range(max_len)]
                        chart_obj = {
                            "title": ch_title or shape_name or "圖表數據",
                            "kind": kind,
                            "categories": categories,
                            "series": series_list,
                            "box": box,
                        }
                        charts.append(chart_obj)
                        canvas_items.append({"type": "chart", "chart": chart_obj, "box": box})
                        # Also build a clean table representation of the chart data
                        t_rows = [["數列 / 期間"] + categories]
                        for s_item in series_list:
                            row_cells = [s_item["name"]] + s_item["formatted"][:len(categories)]
                            t_rows.append(row_cells)
                        tables.append(t_rows)
                    return
            except Exception:
                pass

            # 3. Table shape
            try:
                if getattr(shape, "has_table", False):
                    t_rows = []
                    for row in shape.table.rows:
                        t_rows.append([cell.text.replace("\x0b", "\n").strip() for cell in row.cells])
                    if t_rows:
                        tables.append(t_rows)
                        canvas_items.append({"type": "table", "rows": t_rows, "box": box})
                    return
            except Exception:
                pass

            # 4. Text frame / AutoShape
            try:
                xml_full = shape._element.xml
                m_sp = re.search(r"<p:spPr>.*?</p:spPr>", xml_full, re.S)
                sp_xml = m_sp.group(0) if m_sp else ""
                sp_no_ln = re.sub(r"<a:ln[^>]*>.*?</a:ln>", "", sp_xml, flags=re.S)
                shape_bg = ""
                if '<a:alpha val="0"/>' not in sp_no_ln:
                    m_clr = re.search(r'<a:solidFill>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', sp_no_ln)
                    if m_clr:
                        c_hex = m_clr.group(1).upper()
                        if c_hex not in ("FFFFFF", "000000"):
                            shape_bg = f"#{c_hex}"

                if getattr(shape, "has_text_frame", False):
                    is_title_shape = (
                        "標題" in shape_name
                        or "title" in lower_name
                        or (shape_name.startswith("TextBox ") and box["x"] < 15.0 and box["y"] < 12.0)
                    )
                    try:
                        if slide.shapes.title == shape:
                            is_title_shape = True
                    except Exception:
                        pass
                    shape_lines = []
                    shape_paras = []
                    for p in shape.text_frame.paragraphs:
                        txt = p.text.replace("\x0b", " ").strip()
                        if not txt:
                            continue
                        # Ignore bottom-corner page number digits
                        if box["y"] > 80.0 and txt.isdigit() and len(txt) <= 3:
                            continue
                        lvl = int(getattr(p, "level", 0) or 0)
                        p_xml = p._p.xml if hasattr(p, "_p") else ""
                        hl_color = shape_bg
                        if not hl_color and p_xml:
                            m_hl = re.search(r'<a:highlight>\s*<a:srgbClr val="([0-9A-Fa-f]{6})"', p_xml)
                            if m_hl:
                                hl_color = f"#{m_hl.group(1).upper()}"
                            else:
                                m_fg = re.search(r'<a:rPr[^>]*>.*?<a:solidFill>\s*<a:srgbClr val="(FF0000|FFFF00|00FF00)"', p_xml, re.S | re.I)
                                if m_fg:
                                    hl_color = f"#{m_fg.group(1).upper()}"
                        shape_lines.append(txt)
                        shape_paras.append({"text": txt, "level": lvl, "hl_color": hl_color})
                        if is_title_shape and not title_text:
                            title_text = txt
                        blocks.append({
                            "text": txt,
                            "level": lvl,
                            "is_title": is_title_shape,
                            "bg_color": shape_bg,
                            "hl_color": hl_color,
                            "box": box,
                        })
                    if shape_lines:
                        canvas_items.append({
                            "type": "text",
                            "text": "\n".join(shape_lines),
                            "paragraphs": shape_paras,
                            "is_title": is_title_shape,
                            "bg_color": shape_bg,
                            "box": box,
                        })
                    elif not got_image and 'txBox="1"' not in xml_full and shape_name != "BG" and not (box["w"] >= 95.0 and box["h"] >= 95.0):
                        if '<a:alpha val="0"/>' not in sp_xml:
                            has_no_fill = "<a:noFill/>" in sp_no_ln
                            has_no_ln = bool(re.search(r"<a:ln[^>]*>\s*<a:noFill/>", sp_xml))
                            if not (has_no_fill and has_no_ln):
                                canvas_items.append({
                                    "type": "shape",
                                    "bg_color": shape_bg,
                                    "box": box,
                                })
            except Exception:
                pass

        for sh_item in slide.shapes:
            process_shape(sh_item, None)

        # Also check if slide has any remaining image rels (e.g. background images) not yet extracted
        try:
            for rid, rel in slide.part.rels.items():
                if rid not in extracted_rids and not rel.is_external and "image" in rel.reltype:
                    part = rel.target_part
                    ext_hint = os.path.splitext(str(rel.target_partname))[1].lstrip(".") or "png"
                    bg_box = {"x": 0.0, "y": 0.0, "w": 100.0, "h": 100.0}
                    img_obj = save_blob_to_cache(part.blob, ext_hint, f"Slide_{s_idx}_BG", s_idx, bg_box)
                    images.insert(0, img_obj)
                    canvas_items.insert(0, {"type": "image", "image": img_obj, "url": img_obj["url"], "path": img_obj["path"], "name": img_obj["name"], "box": bg_box})
                    if not any(x["path"] == img_obj["path"] for x in all_images):
                        all_images.append(img_obj)
        except Exception:
            pass

        # Sort blocks top-to-bottom, left-to-right, and strip the chosen slide title from body blocks
        blocks.sort(key=lambda b: (0 if b.get("is_title") else 1, round(b["box"]["y"], 1), round(b["box"]["x"], 1)))
        if not title_text and blocks:
            top_candidates = [b for b in blocks if b["box"]["y"] < 35.0 and b["box"]["x"] < 60.0]
            hero_candidates = [b for b in blocks if b["box"]["w"] >= 50.0]
            if top_candidates:
                title_text = top_candidates[0]["text"][:80]
            elif hero_candidates:
                title_text = hero_candidates[0]["text"][:80]
        blocks = [b for b in blocks if b["text"] != title_text]

        notes_text = ""
        try:
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes_text = slide.notes_slide.notes_text_frame.text.strip()
        except Exception:
            pass

        slides_out.append({
            "index": s_idx,
            "title": title_text or f"Slide {s_idx}",
            "blocks": blocks,
            "tables": tables,
            "charts": charts,
            "images": images,
            "canvas_items": canvas_items,
            "notes": notes_text,
        })

    # High-fidelity 1:1 Retina vector slide rasterization via native Keynote / PDFKit bridge
    rendered_slides_map = {}
    try:
        if mem:
            raise RuntimeError("memory-only mode: no on-disk slide rasterization")
        h_cache = hashlib.md5(f"{target}_{st.st_mtime}_{st.st_size}".encode("utf-8")).hexdigest()[:16]
        cache_pdf = os.path.join(CACHE_DIR, f"pptx_vec_{h_cache}.pdf")
        cache_slides_dir = os.path.join(CACHE_DIR, f"pptx_slides_{h_cache}")

        # Check if high-res rendered slides already exist in cache
        if not (os.path.exists(cache_slides_dir) and os.listdir(cache_slides_dir)):
            # If PDF doesn't exist, use Keynote in headless mode to export pixel-perfect PDF
            if not os.path.exists(cache_pdf):
                osa_script = ('on run argv\n'
                              'tell application "Keynote"\n'
                              'set doc to open POSIX file (item 1 of argv)\n'
                              'export doc to POSIX file (item 2 of argv) as PDF\n'
                              'close doc saving no\n'
                              'end tell\n'
                              'end run')                       # fixed script text, file names travel in argv only
                _mac_run(["osascript", "-e", osa_script, "--", target, cache_pdf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)

            # If PDF generated successfully, use mac_vision_helper to render all 1080p slides
            if os.path.exists(cache_pdf) and os.path.getsize(cache_pdf) > 1000:
                os.makedirs(cache_slides_dir, exist_ok=True)
                _mac_run([HELPER_BIN, "export-pdf-slides", cache_pdf, cache_slides_dir, "1920", "1080"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)

        # Map rendered slides into slides_out
        if os.path.exists(cache_slides_dir):
            for s in slides_out:
                s_num = s["index"]
                png_path = os.path.join(cache_slides_dir, f"slide_{s_num}.png")
                if os.path.exists(png_path):
                    s["rendered_image"] = {
                        "path": png_path,
                        "url": f"/api/image?path={urllib.parse.quote(png_path)}",
                        "width": 1920,
                        "height": 1080,
                    }
                    rendered_slides_map[s_num] = s["rendered_image"]
    except Exception as e:
        pass

    # Build Markdown outline for this slide
    for s_item in slides_out:
        s_idx = s_item["index"]
        t_text = s_item["title"]
        md_lines.append(f"## Slide {s_idx}: {t_text}")
        if s_item.get("rendered_image"):
            md_lines.append(f"\n![Slide {s_idx}]({s_item['rendered_image']['url']})\n")
        for blk in s_item["blocks"]:
            indent = "  " * blk["level"]
            md_lines.append(f"{indent}- {blk['text']}")
        for r_list in s_item["tables"]:
            if r_list:
                md_lines.append("")
                md_lines.append("| " + " | ".join(c.replace("\n", " ") for c in r_list[0]) + " |")
                md_lines.append("| " + " | ".join("---" for _ in r_list[0]) + " |")
                for r in r_list[1:]:
                    md_lines.append("| " + " | ".join(c.replace("\n", " ") for c in r) + " |")
                md_lines.append("")
        for im in s_item["images"]:
            md_lines.append(f"\n![{im['name']}]({im['url']})\n")
        if s_item["notes"]:
            md_lines.append(f"\n> 💡 **講者備忘稿**：{s_item['notes'].replace(chr(10), ' ')}")
        md_lines.append("")

    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target),
        "dir": os.path.dirname(target),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "aspect": aspect,
        "total_slides": len(slides_out),
        "has_rendered_slides": len(rendered_slides_map) > 0,
        "slides": slides_out,
        "all_images": all_images,
        "markdown": "\n".join(md_lines),
    }


def parse_docx_file(target, data=None):
    """Convert .docx / .doc / .rtf into structured Markdown & HTML with embedded images and tables.
    `data` = decrypted bytes (memory-only mode)."""
    mem = data is not None
    if not mem:
        check_office_encrypted(target)
    st = os.stat(target)
    ext = os.path.splitext(target)[1].lower()

    if ext in (".doc", ".rtf"):
        res_txt = _mac_run(["/usr/bin/textutil", "-convert", "txt", "-stdout", target], capture_output=True, text=True)
        txt = res_txt.stdout if res_txt.returncode == 0 else "（無法解析舊版文件內容）"
        return {
            "ok": True,
            "path": target,
            "name": os.path.basename(target),
            "dir": os.path.dirname(target),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "paragraphs_count": txt.count("\n") + 1,
            "tables_count": 0,
            "images_count": 0,
            "chars": len(txt),
            "markdown": f"# 📘 {os.path.basename(target)}\n\n" + txt,
        }

    import docx
    from docx.oxml.text.paragraph import CT_P
    from docx.oxml.table import CT_Tbl
    from docx.text.paragraph import Paragraph
    from docx.table import Table

    doc = docx.Document(io.BytesIO(data) if mem else target)
    md_out = []
    para_count = 0
    table_count = 0
    img_count = 0
    total_chars = 0

    # Map relationship IDs for embedded images
    rel_images = {}
    try:
        for r_id, rel in doc.part.rels.items():
            if "image" in rel.target_ref:
                if getattr(rel, "is_external", False):
                    continue                      # linked (not embedded) image: skip it, keep going
                try:
                    blob = rel.target_part.blob
                except Exception:
                    continue
                i_ext = os.path.splitext(rel.target_ref)[1].lower().lstrip(".") or "png"
                if mem:   # decrypted content: data URI only, nothing written to disk
                    mime = {"jpg": "image/jpeg", "svg": "image/svg+xml", "tif": "image/tiff"}.get(i_ext, "image/" + i_ext)
                    rel_images[r_id] = "data:%s;base64,%s" % (mime, base64.b64encode(blob).decode("ascii"))
                    continue
                h_md5 = hashlib.md5(blob).hexdigest()[:16]
                img_path = os.path.join(CACHE_DIR, f"docx_{h_md5}.{i_ext}")
                if not os.path.exists(img_path):
                    with open(img_path, "wb") as f:
                        f.write(blob)
                rel_images[r_id] = img_path
    except Exception:
        pass

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            p = Paragraph(child, doc)
            txt = p.text.strip()
            total_chars += len(txt)

            # Check for inline images in this paragraph
            embedded_rids = re.findall(r'r:embed="([^"]+)"', child.xml)
            for rid in embedded_rids:
                if rid in rel_images:
                    img_count += 1
                    _img_ref = rel_images[rid]
                    if not _img_ref.startswith("data:"):
                        _img_ref = "/api/image?path=" + urllib.parse.quote(_img_ref, safe="")
                    md_out.append(f"\n![Embedded Image]({_img_ref})\n")

            if not txt:
                continue
            para_count += 1
            style_name = (p.style.name if p.style else "").lower()
            if "title" in style_name and "subtitle" not in style_name:
                md_out.append(f"# {txt}\n")
            elif "heading 1" in style_name:
                md_out.append(f"# {txt}\n")
            elif "heading 2" in style_name:
                md_out.append(f"## {txt}\n")
            elif "heading 3" in style_name:
                md_out.append(f"### {txt}\n")
            elif "heading 4" in style_name or "subtitle" in style_name:
                md_out.append(f"#### {txt}\n")
            elif "list" in style_name or "bullet" in style_name:
                md_out.append(f"- {txt}")
            else:
                md_out.append(f"{txt}\n")

        elif isinstance(child, CT_Tbl):
            tbl = Table(child, doc)
            rows_data = []
            for r in tbl.rows:
                row_cells = [c.text.strip().replace("\n", " ") for c in r.cells]
                rows_data.append(row_cells)
                total_chars += sum(len(x) for x in row_cells)
            if rows_data:
                table_count += 1
                md_out.append("")
                md_out.append("| " + " | ".join(rows_data[0]) + " |")
                md_out.append("| " + " | ".join("---" for _ in rows_data[0]) + " |")
                for r in rows_data[1:]:
                    md_out.append("| " + " | ".join(r) + " |")
                md_out.append("")

    markdown_str = "\n".join(md_out).strip()
    if not markdown_str:
        markdown_str = f"# 📘 {os.path.basename(target)}\n\n*（空白 Word 文件）*"

    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target),
        "dir": os.path.dirname(target),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "paragraphs_count": para_count,
        "tables_count": table_count,
        "images_count": img_count,
        "chars": total_chars,
        "markdown": markdown_str,
    }


def _build_sheet_dict(sheet_name, raw_rows):
    """Normalize worksheet rows and automatically detect report metadata header vs true table header."""
    if not raw_rows:
        return {
            "name": sheet_name,
            "meta_rows": [],
            "headers": [],
            "rows": [],
            "total_rows": 0,
        }

    # Smart detection for bank statements & enterprise reports where top rows are metadata (1~4 cells)
    # and a subsequent row within the first 15 rows has all column headers (e.g. 6~20 cells).
    sample_n = min(15, len(raw_rows))
    filled_counts = [sum(1 for c in r if str(c).strip() != "") for r in raw_rows[:sample_n]]
    max_filled = max(filled_counts) if filled_counts else 0
    hdr_idx = 0
    if max_filled >= 5 and filled_counts[0] <= max(3, int(max_filled * 0.55)):
        for i in range(1, sample_n):
            if filled_counts[i] >= max(4, int(max_filled * 0.8)):
                hdr_idx = i
                break

    meta_rows = []
    for mr in raw_rows[:hdr_idx]:
        non_empty = [str(c).strip() for c in mr if str(c).strip() != ""]
        if non_empty:
            meta_rows.append(non_empty)

    headers = [str(c).strip() for c in raw_rows[hdr_idx]] if hdr_idx < len(raw_rows) else []
    data_rows = raw_rows[hdr_idx + 1:] if hdr_idx + 1 < len(raw_rows) else []

    num_cols = max([len(headers)] + [len(r) for r in data_rows[:200]] + [0])
    # Trim trailing completely empty columns
    while num_cols > 1:
        col_i = num_cols - 1
        h_val = headers[col_i] if col_i < len(headers) else ""
        has_any = (h_val != "") or any((col_i < len(r) and str(r[col_i]).strip() != "") for r in data_rows[:200])
        if has_any:
            break
        num_cols -= 1

    headers = headers[:num_cols]
    for i in range(len(headers)):
        if not headers[i]:
            headers[i] = f"Col_{i+1}"
    if len(headers) < num_cols:
        headers = headers + [f"Col_{i+1}" for i in range(len(headers), num_cols)]

    norm_rows = [
        (r + [""] * (num_cols - len(r)))[:num_cols]
        for r in data_rows[:10000]
    ]
    return {
        "name": sheet_name,
        "meta_rows": meta_rows,
        "headers": headers,
        "rows": norm_rows,
        "total_rows": len(data_rows),
    }


def parse_xlsx_file(target, data=None):
    """Parse all worksheets of an Excel (.xlsx / .xlsm / legacy BIFF8 .xls / HTML-table .xls) workbook.
    `data` = decrypted bytes (memory-only mode)."""
    mem = data is not None
    if not mem:
        check_office_encrypted(target)
    st = os.stat(target)
    ext = os.path.splitext(target)[1].lower()
    if mem:
        head = data[:64]
    else:
        with open(target, "rb") as bf:
            head = bf.read(64)

    sheets_out = []

    # 1. Binary BIFF8 .xls (OLE2 Compound Document magic b'\xd0\xcf\x11\xe0')
    if head.startswith(b"\xd0\xcf\x11\xe0") or (ext == ".xls" and not head.startswith(b"PK")):
        try:
            import xlrd
            wb = xlrd.open_workbook(file_contents=data) if mem else xlrd.open_workbook(target)
            for sheet_name in wb.sheet_names():
                sh = wb.sheet_by_name(sheet_name)
                raw_rows = []
                for r_idx in range(min(sh.nrows, 10015)):
                    row_vals = []
                    for c_idx in range(sh.ncols):
                        ctype = sh.cell_type(r_idx, c_idx)
                        val = sh.cell_value(r_idx, c_idx)
                        if ctype == xlrd.XL_CELL_EMPTY or val is None:
                            row_vals.append("")
                        elif ctype == xlrd.XL_CELL_DATE:
                            try:
                                dt = xlrd.xldate.xldate_as_datetime(val, wb.datemode)
                                if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                                    row_vals.append(dt.strftime("%Y-%m-%d"))
                                else:
                                    row_vals.append(dt.strftime("%Y-%m-%d %H:%M:%S"))
                            except Exception:
                                row_vals.append(str(val).strip())
                        elif ctype == xlrd.XL_CELL_NUMBER:
                            if isinstance(val, float) and val.is_integer():
                                row_vals.append(str(int(val)))
                            else:
                                row_vals.append(str(val).strip())
                        elif ctype == xlrd.XL_CELL_BOOLEAN:
                            row_vals.append("TRUE" if val else "FALSE")
                        else:
                            row_vals.append(str(val).strip())
                    if any(c != "" for c in row_vals):
                        raw_rows.append(row_vals)
                sheets_out.append(_build_sheet_dict(sheet_name, raw_rows))

            return {
                "ok": True,
                "path": target,
                "name": os.path.basename(target),
                "dir": os.path.dirname(target),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "format": "XLS (BIFF8)",
                "sheets": sheets_out,
            }
        except Exception as xls_err:
            if "encrypt" in str(xls_err).lower():
                raise EncryptedOfficeError()
            # Fallback if .xls is actually HTML or TSV text saved with .xls extension
            try:
                if mem:
                    raw_b = data[:10 * 1024 * 1024]
                else:
                    with open(target, "rb") as f:
                        raw_b = f.read(10 * 1024 * 1024)
                txt = None
                for enc in ("utf-8-sig", "utf-8", "cp950", "big5", "utf-16", "latin1"):
                    try:
                        txt = raw_b.decode(enc)
                        break
                    except Exception:
                        continue
                if txt and ("<table" in txt.lower() or "<tr" in txt.lower()):
                    tr_matches = re.findall(r"<tr[^>]*>(.*?)</tr>", txt, flags=re.I | re.S)
                    raw_rows = []
                    for tr_html in tr_matches:
                        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr_html, flags=re.I | re.S)
                        clean_cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
                        if any(clean_cells):
                            raw_rows.append(clean_cells)
                    sheets_out.append(_build_sheet_dict("Sheet1", raw_rows))
                    return {
                        "ok": True,
                        "path": target,
                        "name": os.path.basename(target),
                        "dir": os.path.dirname(target),
                        "size": st.st_size,
                        "mtime": int(st.st_mtime),
                        "format": "XLS (HTML Table)",
                        "sheets": sheets_out,
                    }
            except Exception:
                pass
            raise xls_err

    # 2. Standard OOXML .xlsx / .xlsm
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data) if mem else target, data_only=True)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        raw_rows = []
        for row in ws.iter_rows(values_only=True):
            str_row = []
            for v in row:
                if v is None:
                    str_row.append("")
                elif isinstance(v, float) and v.is_integer():
                    str_row.append(str(int(v)))
                else:
                    str_row.append(str(v).strip())
            if any(c != "" for c in str_row):
                raw_rows.append(str_row)
            if len(raw_rows) >= 10015:
                break
        sheets_out.append(_build_sheet_dict(sheet_name, raw_rows))

    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target),
        "dir": os.path.dirname(target),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "format": "XLSX",
        "sheets": sheets_out,
    }


def parse_xml_file(target):
    """Parse XML / Plist / RSS / Atom / SVG into a structured JSON tree and formatted XML text."""
    import xml.etree.ElementTree as ET
    import xml.dom.minidom as minidom
    import plistlib

    st = os.stat(target)
    with open(target, "rb") as bf:
        raw_bytes = bf.read(15 * 1024 * 1024)

    # Check if binary Apple Plist (b"bplist00")
    if raw_bytes.startswith(b"bplist"):
        pl_obj = plistlib.loads(raw_bytes)
        xml_bytes = plistlib.dumps(pl_obj, fmt=plistlib.FMT_XML)
        xml_text = xml_bytes.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "path": target,
            "name": os.path.basename(target),
            "dir": os.path.dirname(target),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "mtime_ns": st.st_mtime_ns,
            "lines": xml_text.count("\n") + 1,
            "root_tag": "plist (Binary Plist)",
            "content": xml_text,
            "tree": pl_obj,
        }

    text = None
    for enc in ("utf-8-sig", "utf-8", "utf-16", "cp950", "big5", "latin1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw_bytes.decode("utf-8", errors="replace")

    def clean_tag(t):
        return t.split("}", 1)[-1] if "}" in t else t

    def elem_to_obj(el):
        obj = {}
        if el.attrib:
            for k, v in el.attrib.items():
                obj[f"@{clean_tag(k)}"] = v
        children = list(el)
        if not children:
            txt = (el.text or "").strip()
            if not obj:
                return txt
            if txt:
                obj["#text"] = txt
            return obj

        grouped = {}
        for ch in children:
            ctag = clean_tag(ch.tag)
            cval = elem_to_obj(ch)
            if ctag in grouped:
                if not isinstance(grouped[ctag], list):
                    grouped[ctag] = [grouped[ctag]]
                grouped[ctag].append(cval)
            else:
                grouped[ctag] = cval
        obj.update(grouped)
        txt = (el.text or "").strip()
        if txt:
            obj["#text"] = txt
        return obj

    root = ET.fromstring(text.encode("utf-8") if text.lstrip().startswith("<?xml") else text)
    root_tag = clean_tag(root.tag)
    tree_data = {root_tag: elem_to_obj(root)}

    # Prettify XML if single-line or unformatted
    pretty_xml = text
    try:
        dom = minidom.parseString(text.encode("utf-8"))
        pretty_lines = [line for line in dom.toprettyxml(indent="  ").splitlines() if line.strip()]
        pretty_xml = "\n".join(pretty_lines)
    except Exception:
        pass

    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target),
        "dir": os.path.dirname(target),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "mtime_ns": st.st_mtime_ns,
        "lines": pretty_xml.count("\n") + 1,
        "root_tag": root_tag,
        "content": pretty_xml,
        "tree": tree_data,
    }


class EncryptedPdfError(Exception):
    """Password-protected PDF (cannot be previewed)."""


def pdfium_pdf_info(target, max_pages=1500, data=None):
    """Windows replacement for the macOS PDFKit helper: page count, size, metadata and per-page text.
    `data` = decrypted PDF bytes (memory-only mode)."""
    import pypdfium2 as pdfium
    try:
        pdf = pdfium.PdfDocument(data if data is not None else target)
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise EncryptedPdfError()
        raise
    try:
        meta = {}
        try:
            meta = pdf.get_metadata_dict() or {}
        except Exception:
            pass
        pages = []
        n = len(pdf)
        for i in range(min(n, max_pages)):
            page = pdf[i]
            try:
                w, h = page.get_size()
                tp = page.get_textpage()
                try:
                    txt = tp.get_text_range()
                finally:
                    tp.close()
            finally:
                page.close()
            pages.append({"index": i + 1, "text": txt.replace("\r\n", "\n"), "width": round(w), "height": round(h)})
        return {"pages": pages, "page_count": n, "title": (meta.get("Title") or "").strip(),
                "author": (meta.get("Author") or "").strip()}
    finally:
        pdf.close()


def pdfium_render_page(target, page_num, out_png, scale=2.0, data=None):
    """Render one page to PNG: to a file (out_png) or, when out_png is None, return the PNG bytes."""
    import pypdfium2 as pdfium
    with _PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(data if data is not None else target)
        try:
            if page_num < 1 or page_num > len(pdf):
                return False if out_png else None
            page = pdf[page_num - 1]
            try:
                img = page.render(scale=scale).to_pil().convert("RGB")
            finally:
                page.close()
            if out_png:
                img.save(out_png, "PNG")
                return True
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return buf.getvalue()
        finally:
            pdf.close()


def parse_pdf_file(target, data=None):
    """Extract PDF metadata, page count, dimensions, and per-page text using macOS PDFKit
    (Windows: pypdfium2). `data` = decrypted PDF bytes (memory-only, Windows)."""
    st = os.stat(target)
    info = {}
    if IS_WINDOWS:
        with _PDFIUM_LOCK:
            info = pdfium_pdf_info(target, data=data)
    else:
        res = _mac_run([HELPER_BIN, "pdf-info", target], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            try:
                info = json.loads(res.stdout.strip().splitlines()[-1])
            except Exception:
                info = {}

    pages = info.get("pages") or []
    page_count = info.get("page_count") or len(pages) or 1
    title = info.get("title") or os.path.basename(target)
    author = info.get("author") or ""

    md_lines = [f"# 📕 {title}", ""]
    if author:
        md_lines.append(f"> **作者**：{author} ｜ **總頁數**：{page_count} 頁\n")
    total_chars = 0
    for p in pages:
        txt = (p.get("text") or "").strip()
        total_chars += len(txt)
        md_lines.append(f"## 第 {p.get('index')} 頁")
        md_lines.append(txt if txt else "*（此頁為圖片或無內嵌文字，可切換至「🖼️ 頁面截圖 / AI OCR」進行辨識）*")
        md_lines.append("")

    return {
        "ok": True,
        "path": target,
        "name": os.path.basename(target),
        "dir": os.path.dirname(target),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
        "page_count": page_count,
        "title": title,
        "author": author,
        "total_chars": total_chars,
        "pages": pages,
        "raw_url": f"/api/pdf-raw?path={urllib.parse.quote(target)}",
        "markdown": "\n".join(md_lines),
    }


# Per-process secret. The page receives it as an HttpOnly, SameSite=Strict cookie when "/" is served; every
# /api/* request must present it (cookie, or X-PowerUp-Token header for the native launchers).
SERVER_TOKEN = secrets.token_urlsafe(32)
# Inline handlers / styles are still used by the page, so script-src keeps 'unsafe-inline'; the rest closes
# exfiltration and injection routes (no plugins, no <base>, no foreign forms/frames, connect only to ourselves).
PAGE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
COOKIE_PREFIX = "ptk_"
# Windows frozen build only: the token cookie is handed out solely to a "/" request carrying a one-time nonce that the
# launcher created and passed to the WebView (e.g. "/?k=<nonce>"). A nonce works once; later page reloads rely on the
# cookie already in the WebView. Plain-Python / macOS mode keeps the old behaviour (the Mac shell cannot be changed
# in lock-step), and ?path= stays available there.
INDEX_NONCES = set()
_NONCE_LOCK = threading.Lock()


def register_index_nonce(nonce):
    with _NONCE_LOCK:
        INDEX_NONCES.add(nonce)


def _consume_index_nonce(nonce):
    if not nonce:
        return False
    with _NONCE_LOCK:
        if nonce in INDEX_NONCES:
            INDEX_NONCES.discard(nonce)
            return True
    return False


def cookie_name(port):
    return COOKIE_PREFIX + str(port)
# unlock brute-force limiter (thread-safe): per-file {fails, until, inflight} keyed by file identity + a global ceiling
_UNLOCK_RL = {}
_UNLOCK_RL_LOCK = threading.Lock()
_UNLOCK_GLOBAL = []
UNLOCK_MAX_FAILS = 5
UNLOCK_COOLDOWN_SECONDS = 30
UNLOCK_GLOBAL_PER_MINUTE = 30


def _file_identity(path):
    """(st_dev, st_ino) survives renames / hard links / case variants; fall back to the real path."""
    try:
        st = os.stat(path)
        if st.st_ino:
            return ("id", st.st_dev, st.st_ino)
    except OSError:
        pass
    return ("path", os.path.normcase(os.path.realpath(path)))


def unlock_reserve(path):
    """Reserve one attempt BEFORE doing any work, so parallel requests cannot all slip past the check.
    Returns (key, None) when allowed or (None, message) when limited."""
    now = time.time()
    key = _file_identity(path)
    with _UNLOCK_RL_LOCK:
        while _UNLOCK_GLOBAL and now - _UNLOCK_GLOBAL[0] > 60:
            _UNLOCK_GLOBAL.pop(0)
        if len(_UNLOCK_GLOBAL) >= UNLOCK_GLOBAL_PER_MINUTE:
            return None, "嘗試次數過多，請稍後再試"
        rec = _UNLOCK_RL.setdefault(key, {"fails": 0, "until": 0.0, "inflight": 0})
        if rec["until"] > now:
            return None, "嘗試次數過多，請 %d 秒後再試" % (int(rec["until"] - now) + 1)
        if rec["fails"] + rec["inflight"] >= UNLOCK_MAX_FAILS:
            return None, "嘗試次數過多，請稍後再試"
        rec["inflight"] += 1
        _UNLOCK_GLOBAL.append(now)
        return key, None


def unlock_release(key, success):
    with _UNLOCK_RL_LOCK:
        rec = _UNLOCK_RL.get(key)
        if rec is None:
            return False
        rec["inflight"] = max(0, rec["inflight"] - 1)
        if success:
            _UNLOCK_RL.pop(key, None)
            return False
        rec["fails"] += 1
        if rec["fails"] >= UNLOCK_MAX_FAILS:
            rec["fails"] = 0
            rec["until"] = time.time() + UNLOCK_COOLDOWN_SECONDS
            return True      # cooldown just started
        return False


def _tokens_from_cookie(cookie_header, port):
    """All values of our cookie. A page on another port could plant a same-named cookie with a narrower Path;
    accepting any matching value means a planted junk cookie cannot lock the real app out."""
    name = cookie_name(port)
    vals = []
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            vals.append(v)
    return vals


# ---- Path validation --------------------------------------------------------------------------------
# On Windows any path that begins with two separators (UNC "\\\\host\\share", "//host/share", "\\\\?\\UNC\\...",
# "\\\\.\\pipe", "/\\host", ...) or the NT namespace prefix "\\??\\" makes the OS open an SMB / WebDAV connection
# (leaking an NTLMv2 hash) as soon as it is touched with isfile()/open()/listdir(). Every path-like request field
# goes through path_is_unsafe() in ONE place (the request entry points below) so no route can forget it.
PATH_PARAM_KEYS = ("path", "base_dir", "default_dir", "source_path", "target_path", "dir", "target")
NAME_PARAM_KEYS = ("new_name", "name", "default_name")
_WIN_RESERVED = ({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "COM\u00b9", "COM\u00b2", "COM\u00b3", "LPT\u00b9", "LPT\u00b2", "LPT\u00b3"}
                 | {"COM%d" % i for i in range(1, 10)} | {"LPT%d" % i for i in range(1, 10)})
# bidi controls and look-alike separators / dots (visual spoofing of names)
_BIDI_CHARS = set("\u200e\u200f\u061c\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_LOOKALIKE_SEPARATORS = set("\u2044\u2215\u2216\u29f5\u29f8\u29f9\uff0f\uff3c\ufe68")
_LOOKALIKE_DOTS = set("\uff0e\u2024\u2025\u2026\u3002\ufe52")


PDF_INTERACT = {"t": 0.0}


class PathRejected(Exception):
    """A request path is a network / device path, or reaches one through a symlink / junction."""


def _legacy_path_is_unsafe(raw):
    """True for network / device / NT-namespace paths (Windows) and NUL bytes (everywhere).
    The value is stripped with str.strip() first (every Unicode white-space char) because the routes do the same."""
    if not isinstance(raw, str):
        return False
    raw = raw.strip()
    if "\x00" in raw:
        return True
    if sys.platform == "win32":
        t = raw.replace("/", "\\")
        if t.startswith("\\\\") or t.startswith("\\??\\"):
            return True
        try:
            n = os.path.normpath(os.path.expanduser(raw)).replace("/", "\\")
        except Exception:
            return True
        if n.startswith("\\\\") or os.path.splitdrive(n)[0].startswith("\\\\"):
            return True
    return False


def path_is_unsafe(raw):
    """Windows: WHITELIST (winpath.check_raw: only local / user-mapped drive-letter paths whose every component, including
    symlink / junction targets, passes). Elsewhere: NUL bytes only. Empty / non-string values are not 'unsafe'."""
    if sys.platform == "win32":
        return not winpath.check_raw(raw)
    return _legacy_path_is_unsafe(raw)


def normalize_posix_path(raw):
    """POSIX: a leading '//' is the same directory as '/', so normalise instead of rejecting."""
    if sys.platform != "win32" and isinstance(raw, str) and raw.startswith("//"):
        return "/" + raw.lstrip("/")
    return raw


_REPARSE_LINK_TAGS = (0xA000000C, 0xA0000003)      # IO_REPARSE_TAG_SYMLINK, IO_REPARSE_TAG_MOUNT_POINT (junction)


def _is_link_stat(st):
    import stat as _st
    return _st.S_ISLNK(st.st_mode) or getattr(st, "st_reparse_tag", 0) in _REPARSE_LINK_TAGS


def _lstat_is_link(full):
    try:
        return _is_link_stat(os.lstat(full))
    except OSError:
        return False


def _link_target(tgt):
    """Interpret an os.readlink() result. Returns (unsafe, usable_target). A leading extended-length prefix is
    fine for local paths (\\\\?\\C:\\x, \\??\\C:\\x) but \\\\?\\UNC\\... / \\??\\UNC\\... / \\\\host\\s are network paths."""
    t = tgt.replace("/", "\\")
    up = t.upper()
    if up.startswith("\\\\?\\UNC\\") or up.startswith("\\??\\UNC\\"):
        return True, tgt
    if t.startswith("\\\\?\\") or t.startswith("\\??\\"):
        t = t[4:]
    if path_is_unsafe(t):
        return True, tgt
    return False, t


def _split_parts(rest):
    return [x for x in re.split(r"[\\/]+", rest) if x]


def _legacy_chain_has_unsafe_link(p, max_hops=40):
    """Resolve symlinks / junctions component by component with lstat + readlink ONLY (never stat'ing through a link),
    so a link that points to a UNC / device path is detected before the OS would open an SMB connection."""
    p = os.path.abspath(p)
    drive, rest = os.path.splitdrive(p)
    parts = _split_parts(rest)
    cur = drive + os.sep
    hops = 0
    while parts:
        comp = parts.pop(0)
        if comp == ".":
            continue
        if comp == "..":
            cur = os.path.dirname(cur.rstrip("\\/")) or cur
            continue
        nxt = os.path.join(cur, comp)
        try:
            st = os.lstat(nxt)
        except OSError:
            return False                       # does not exist: nothing deeper can be touched
        if _is_link_stat(st):
            hops += 1
            if hops > max_hops:
                return True
            try:
                tgt = os.readlink(nxt)
            except OSError:
                return True                    # cannot tell where it goes: refuse
            unsafe, tgt = _link_target(tgt)
            if unsafe:
                return True
            d2, r2 = os.path.splitdrive(tgt)
            if d2 or tgt.startswith(("\\", "/")):
                cur = (d2 or drive) + os.sep
                parts = _split_parts(r2) + parts
            else:
                parts = _split_parts(tgt) + parts   # relative to the folder holding the link
            continue
        cur = nxt
    return False


def chain_has_unsafe_link(p, max_hops=40):
    if sys.platform == "win32":
        n = winpath.normalize(p)
        return n is None or not winpath.is_allowed(n)
    return _legacy_chain_has_unsafe_link(p, max_hops)


def entry_is_unsafe_link(full):
    """For directory entries: the parent chain was verified already, so only the entry itself needs checking.
    Windows: True (= skip the entry) unless winpath.entry_ok() - reparse entries are validated, never followed blindly."""
    if sys.platform == "win32":
        return not winpath.entry_ok(full)
    try:
        st = os.lstat(full)
    except OSError:
        return False
    if not _is_link_stat(st):
        return False
    return chain_has_unsafe_link(full)


def safe_fs_path(raw):
    """abspath(expanduser(raw)) after validation: the single door to the file system for request-derived paths."""
    if sys.platform == "win32":
        n = winpath.normalize(raw)
        if n is None or not winpath.is_allowed(n):
            raise PathRejected(str(raw)[:40])
        return n
    if isinstance(raw, str):
        raw = raw.strip()
        if _legacy_path_is_unsafe(raw):
            raise PathRejected(raw[:40])
    out = os.path.abspath(os.path.expanduser(raw))
    if _legacy_path_is_unsafe(out) or _legacy_chain_has_unsafe_link(out):
        raise PathRejected(out[:40])
    return out


def safe_or_none(raw):
    """safe_fs_path that returns None instead of raising (for optional locations such as ~/Desktop)."""
    try:
        return safe_fs_path(raw)
    except (PathRejected, OSError, ValueError):
        return None


def safe_walk(top):
    """os.walk replacement: skips symlinks / junctions that lead to UNC / device paths and never descends links."""
    try:
        entries = list(os.scandir(top))
    except OSError:
        return
    dirs, files = [], []
    for e in entries:
        if entry_is_unsafe_link(e.path):
            continue
        try:
            (dirs if e.is_dir() else files).append(e.name)
        except OSError:
            files.append(e.name)
    yield top, dirs, files
    for d in dirs:
        full = os.path.join(top, d)
        if _lstat_is_link(full):
            continue
        yield from safe_walk(full)


def filename_is_invalid(name):
    """Reject names that could escape the folder, are reserved / unusable on Windows, or are visual spoofs.
    Checked on the raw value AND on the str.strip()ped value (what the routes actually use)."""
    if not isinstance(name, str) or name == "":
        return False                       # empty handled by each route
    for v in (name, name.strip()):
        if v == "":
            return True
        if v != v.rstrip():
            return True                    # trailing white-space of any kind
        if v != v.rstrip(" ."):
            return True                    # trailing space / dot
        if any(ord(c) < 32 for c in v) or any(c in v for c in ':/\\<>"|?*'):
            return True
        if v in (".", ".."):
            return True
        stem = v.split(".")[0].rstrip(" ").upper()
        if stem in _WIN_RESERVED:
            return True
        if any(c in _BIDI_CHARS or c in _LOOKALIKE_SEPARATORS for c in v):
            return True
        if all(c == "." or c in _LOOKALIKE_DOTS for c in v):
            return True
    return False


class ToolboxHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        """Every response carries nosniff; every response that does not set its own CSP (index page, PDF frame) gets a
        locked-down one, so even an error page or a mis-typed payload can never run script or load anything."""
        buf = b"".join(getattr(self, "_headers_buffer", [])).lower()
        if b"x-content-type-options" not in buf:
            self.send_header("X-Content-Type-Options", "nosniff")
        if b"content-security-policy" not in buf and b"content-type: application/pdf" not in buf:
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'; sandbox")
        super().end_headers()

    def _reject_bad_inputs(self, qs, body):
        """Returns True (after replying 400) when any path-like / name-like field is unsafe. Values are stripped with
        str.strip() (as every route does) BEFORE validation and the stripped value is written back, so routes only ever
        see the value that was validated. Symlink / junction chains are checked here too."""
        for src in (qs, body):
            if not isinstance(src, dict):
                continue
            for k in PATH_PARAM_KEYS:
                if k not in src:
                    continue
                v = src[k]
                vals = v if isinstance(v, list) else [v]
                out = []
                for x in vals:
                    if isinstance(x, str):
                        x = x.strip()
                        try:
                            bad = path_is_unsafe(x)
                            if not bad and x and sys.platform != "win32":
                                bad = chain_has_unsafe_link(os.path.expanduser(x))
                        except Exception:
                            bad = True
                        if bad:
                            self.send_json({"ok": False, "error": "不允許的路徑（網路路徑或裝置路徑）"}, 400)
                            return True
                        if x and sys.platform == "win32":
                            # write back exactly what was validated (abspath / expanduser / extended-prefix applied), so a
                            # route can never use a raw string that differs from the checked one. A relative "path" that is
                            # joined to a base_dir by the route (/api/image) is validated there after the join instead.
                            joined = (k == "path" and not os.path.isabs(x) and bool(src.get("base_dir")))
                            if not joined:
                                x = winpath.normalize(x) or x
                    out.append(x)
                src[k] = out if isinstance(v, list) else out[0]
            for k in NAME_PARAM_KEYS:
                if k not in src:
                    continue
                v = src[k]
                vals = v if isinstance(v, list) else [v]
                out = []
                for x in vals:
                    if isinstance(x, str):
                        if filename_is_invalid(x):
                            self.send_json({"ok": False, "error": "檔名不合法"}, 400)
                            return True
                        x = x.strip()
                    out.append(x)
                src[k] = out if isinstance(v, list) else out[0]
        return False

    # ---------------------------------------------------------------- single security entry point
    def _deny(self, code, msg):
        try:   # drain (a bounded amount of) the unread request body so the client reliably receives the 4xx
            n = int(self.headers.get("Content-Length") or 0)
            if 0 < n <= (1 << 20):
                self.rfile.read(n)
        except Exception:
            pass
        self.close_connection = True
        body = json.dumps({"ok": False, "error": msg}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        return False

    def _gate(self):
        """Applied to EVERY request (any method, any path) before routing. Returns True when allowed.
        Layers: Host allow-list (anti DNS-rebinding) -> Origin -> Sec-Fetch-Site -> JSON content type (POST)
        -> per-run token for /api/*."""
        port = self.server.server_address[1]
        hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
        if (self.headers.get("Host") or "").strip().lower() not in hosts:
            return self._deny(403, "forbidden host")
        origin = self.headers.get("Origin")
        if origin is not None and origin.strip().lower() not in {"http://" + h for h in hosts}:
            return self._deny(403, "forbidden origin")
        path = urllib.parse.urlparse(self.path).path
        sfs = self.headers.get("Sec-Fetch-Site")
        # Pages and static files too: a cross-site / same-site (other port) top-level navigation to "/?path=..."
        # must not receive the token cookie nor auto-open a file. Typed URLs (none), the app shell and its own
        # sub-requests (same-origin) are unaffected.
        if sfs is not None and sfs.strip().lower() not in ("same-origin", "none"):
            return self._deny(403, "cross-site request blocked")
        if not path.startswith("/api/"):
            return True
        if self.command == "POST":
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype != "application/json":
                return self._deny(415, "application/json required")
        candidates = _tokens_from_cookie(self.headers.get("Cookie"), port)
        if self.headers.get("X-PowerUp-Token"):
            candidates.append(self.headers.get("X-PowerUp-Token"))
        for supplied in candidates:
            if supplied and hmac.compare_digest(supplied.encode("utf-8"), SERVER_TOKEN.encode("utf-8")):
                return True
        # Narrow exception for the macOS app shell (Swift URLSession, cannot hold a browser cookie): a liveness
        # probe and the "open this path" hand-off, accepted only from non-browser clients (browsers always send
        # Origin and/or Sec-Fetch-Site on cross-site requests, and cannot send application/json without preflight).
        if sys.platform == "darwin" and origin is None and sfs is None and (
                (self.command == "GET" and path == "/api/pending-open") or
                (self.command == "POST" and path == "/api/open-target")):
            return True
        return self._deny(403, "missing or invalid token")

    def _guarded(self, fn):
        try:
            fn()
        except PathRejected:
            try:
                self.send_json({"ok": False, "error": "不允許的路徑（網路路徑或裝置路徑）"}, 400)
            except Exception:
                pass
        except (BrokenPipeError, ConnectionError):
            pass
        except Exception as e:      # never let a route bug kill the connection without a JSON answer
            try:
                self.send_json({"ok": False, "error": "伺服器錯誤：%s" % e.__class__.__name__}, 500)
            except Exception:
                pass

    def do_GET(self):
        if self._gate():
            self._guarded(self._handle_get)

    def do_POST(self):
        if self._gate():
            self._guarded(self._handle_post)

    def _method_not_allowed(self):
        if self._gate():
            self._deny(405, "method not allowed")

    do_OPTIONS = do_PUT = do_DELETE = do_PATCH = do_HEAD = _method_not_allowed

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _handle_get(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        if sys.platform != "win32":
            for _k in PATH_PARAM_KEYS:
                if _k in qs:
                    qs[_k] = [normalize_posix_path(x) for x in qs[_k]]
        if route.startswith("/api/") and self._reject_bad_inputs(qs, {}):
            return

        if route in ("/", "/index.html"):
            index_path = os.path.join(TOOLBOX_DIR, "index.html")
            with open(index_path, "rb") as f:
                content = f.read()
            port_ = self.server.server_address[1]
            issue_cookie = True
            if IS_WIN_FROZEN:
                have_cookie = any(hmac.compare_digest(v.encode("utf-8"), SERVER_TOKEN.encode("utf-8"))
                                  for v in _tokens_from_cookie(self.headers.get("Cookie"), port_))
                if _consume_index_nonce((qs.get("k") or [""])[0]):
                    issue_cookie = True
                elif have_cookie:
                    issue_cookie = False        # page reload: the WebView already holds the cookie
                else:
                    self._deny(403, "open PowerUp Toolbox from the application")
                    return
                # no ?path= auto-open in the frozen build: files arrive via the launcher hand-off (/api/pending-open)
                content = content.replace(b'name="powerup-allow-url-path" content="1"', b'name="powerup-allow-url-path" content="0"')
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            if issue_cookie:
                self.send_header("Set-Cookie", "%s=%s; HttpOnly; SameSite=Strict; Path=/" % (cookie_name(port_), SERVER_TOKEN))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "SAMEORIGIN")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", PAGE_CSP)
            self.end_headers()
            self.wfile.write(content)
            return

        if route.startswith("/assets/"):
            rel_sub = route[len("/assets/"):].lstrip("/")
            asset_file = os.path.abspath(os.path.join(TOOLBOX_DIR, "assets", rel_sub))
            asset_root = os.path.abspath(os.path.join(TOOLBOX_DIR, "assets"))
            try:
                _inside = os.path.commonpath([asset_file, asset_root]) == asset_root
            except ValueError:      # different drives
                _inside = False
            if _inside and os.path.isfile(asset_file):
                mime_map = {
                    ".html": "text/html; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".js": "application/javascript; charset=utf-8",
                    ".json": "application/json; charset=utf-8",
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".jpeg": "image/jpeg",
                    ".webp": "image/webp",
                    ".svg": "image/svg+xml",
                    ".ico": "image/x-icon",
                }
                _, fext = os.path.splitext(asset_file)
                ctype = mime_map.get(fext.lower(), "application/octet-stream")
                try:
                    with open(asset_file, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception:
                    pass

        if route == "/api/win-register-status" and IS_WIN_FROZEN:
            self.send_json({"ok": True, **win_support.register_status()})
            return

        if route == "/api/update-status" and IS_WIN_FROZEN:
            self.send_json({"ok": True, **win_support.update_status()})
            return

        if route == "/api/check-update" and IS_WIN_FROZEN:
            self.send_json(win_support.check_update())
            return

        if route == "/api/check-update" and IS_WINDOWS:
            self.send_json({"ok": False, "error": "Windows 非打包版不支援自動更新"})
            return

        if route == "/api/check-update":
            repo_param = qs.get("repo", [None])[0]
            branch_param = qs.get("branch", [None])[0]
            self.send_json(check_github_update(repo=repo_param, branch=branch_param))
            return

        if route == "/api/pending-open":
            self.send_json({"ok": True, **LAST_OPEN_REQUEST})
            return

        if route == "/api/bookmarks":
            bookmarks = [
                {"name": "📸 截圖匣", "full_name": "📸 截圖與剪貼簿匣", "path": os.path.abspath(CAPTURES_DIR), "icon": "📸"},
                {"name": "📝 暫存匣", "full_name": "📝 暫存草稿工作匣 (Scratchpad)", "path": os.path.abspath(SCRATCH_DIR), "icon": "📝"},
                {"name": "🧰 Toolbox", "full_name": "🧰 Toolbox 專案目錄", "path": os.path.abspath(TOOLBOX_DIR), "icon": "🧰"},
                {"name": "🖥️ 桌面", "full_name": "🖥️ 桌面 (Desktop)", "path": safe_or_none("~/Desktop"), "icon": "🖥️"},
                {"name": "📂 下載", "full_name": "📂 下載項目 (Downloads)", "path": safe_or_none("~/Downloads"), "icon": "📂"},
                {"name": "🖼️ 圖片", "full_name": "🖼️ 圖片 (Pictures)", "path": safe_or_none("~/Pictures"), "icon": "🖼️"},
                {"name": "📄 文件", "full_name": "📄 文件 (Documents)", "path": safe_or_none("~/Documents"), "icon": "📄"},
            ]
            if IS_WINDOWS:
                import string
                for drive_letter in string.ascii_uppercase:
                    dpath = f"{drive_letter}:\\"
                    if winpath.drive_type(drive_letter) in winpath.ALLOWED_DRIVE_TYPES and (
                            winpath.drive_type(drive_letter) == 4 or os.path.exists(dpath)):   # mapped network drives are listed, not probed
                        bookmarks.append({
                            "name": f"💾 {drive_letter}: 槽",
                            "full_name": f"💾 本機磁碟 ({drive_letter}:)",
                            "path": dpath,
                            "icon": "💾"
                        })
            else:
                bookmarks.append({"name": "💻 專案區", "full_name": "💻 Claude 專案目錄", "path": safe_or_none("~/Claude/Projects"), "icon": "💻"})

            valid = [b for b in bookmarks if b["path"] and not path_is_unsafe(b["path"])
                     and (len(b["path"]) == 3 and b["path"][1:] == ":\\" and winpath.drive_type(b["path"][0]) == 4
                          or os.path.exists(b["path"]))]
            self.send_json({"ok": True, "bookmarks": valid, "home": safe_or_none("~") or ""})
            return

        if route == "/api/recent-files":
            self.send_json({"ok": True, "files": scan_recent_files()})
            return

        if route == "/api/md-recent":
            self.send_json({"ok": True, "files": scan_recent_markdowns()})
            return

        if route == "/api/md":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定檔案路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                st = os.stat(target)
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                self.send_json({
                    "ok": True,
                    "path": target,
                    "name": os.path.basename(target),
                    "dir": os.path.dirname(target),
                    "content": content,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "mtime_ns": st.st_mtime_ns,
                    "lines": content.count("\n") + 1,
                    "chars": len(content),
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/json":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 JSON 檔案路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                st = os.stat(target)
                ext = os.path.splitext(target)[1].lower()
                is_jsonl = ext in (".jsonl", ".ndjson")
                # Limit reading up to 15MB so huge files don't freeze browser
                max_bytes = 15 * 1024 * 1024
                truncated = st.st_size > max_bytes
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(max_bytes)

                self.send_json({
                    "ok": True,
                    "path": target,
                    "name": os.path.basename(target),
                    "dir": os.path.dirname(target),
                    "content": content,
                    "is_jsonl": is_jsonl,
                    "truncated": truncated,
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "mtime_ns": st.st_mtime_ns,
                    "lines": content.count("\n") + 1,
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/pptx":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定簡報路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                self.send_json(parse_pptx_file(target, data=unlock_cache_get(target)))
            except EncryptedOfficeError:
                self.send_json(encrypted_response(target))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/docx":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 Word 路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                self.send_json(parse_docx_file(target, data=unlock_cache_get(target)))
            except EncryptedOfficeError:
                self.send_json(encrypted_response(target))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/xlsx":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 Excel 路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                self.send_json(parse_xlsx_file(target, data=unlock_cache_get(target)))
            except EncryptedOfficeError:
                self.send_json(encrypted_response(target))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/xml":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 XML 路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                self.send_json(parse_xml_file(target))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/pdf":
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 PDF 路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                self.send_json(parse_pdf_file(target, data=unlock_cache_get(target)))
            except EncryptedPdfError:
                self.send_json(encrypted_response(target))
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/pdf-raw":
            path = qs.get("path", [""])[0].strip()
            target = safe_fs_path(path) if path else ""
            if not target or not os.path.isfile(target):
                self.send_error(404, "PDF not found")
                return
            try:
                unlocked = unlock_cache_get(target)
                if unlocked is not None:
                    data = unlocked
                else:
                    with open(target, "rb") as f:
                        data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition", f'inline; filename="{urllib.parse.quote(os.path.basename(target))}"')
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store" if unlocked is not None else "private, no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(data)
            except Exception:
                self.send_error(500, "Failed to read PDF")
            return

        if route == "/api/pdf-page":
            path = qs.get("path", [""])[0].strip()
            page_num = int(qs.get("page", ["1"])[0] or "1")
            target = safe_fs_path(path) if path else ""
            if not target or not os.path.isfile(target):
                self.send_json({"ok": False, "error": "找不到 PDF 檔案"}, 404)
                return
            try:
                unlocked = unlock_cache_get(target) if IS_WINDOWS else None
                if unlocked is not None:
                    png = pdfium_render_page(target, page_num, None, 2.0, data=unlocked)
                    if not png:
                        self.send_json({"ok": False, "error": "無法渲染該頁 PDF"}, 500)
                        return
                    uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                    self.send_json({"ok": True, "path": "", "url": uri, "image_url": uri, "image_path": "", "page": page_num,
                                    "in_memory": True, "error": "加密文件的頁面不會寫入磁碟，無法送到圖片工坊"})
                    return
                st = os.stat(target)
                h_key = hashlib.md5(f"{target}:{st.st_mtime_ns}:{page_num}".encode()).hexdigest()[:16]
                out_png = os.path.join(CACHE_DIR, f"pdf_p{page_num}_{h_key}.png")
                if not os.path.exists(out_png):
                    if IS_WINDOWS:
                        try:
                            pdfium_render_page(target, page_num, out_png, 2.0)
                        except Exception:
                            pass
                    else:
                        _mac_run([HELPER_BIN, "pdf-page", target, str(page_num), out_png, "2.0"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(out_png):
                    self.send_json({
                        "ok": True,
                        "path": out_png,
                        "url": f"/api/image?path={urllib.parse.quote(out_png)}",
                        # the front end reads image_url / image_path (was never returned -> broken page view)
                        "image_url": f"/api/image?path={urllib.parse.quote(out_png)}",
                        "image_path": out_png,
                        "page": page_num,
                    })
                else:
                    self.send_json({"ok": False, "error": "無法渲染該頁 PDF"}, 500)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/csv":
            import csv
            import io
            path = qs.get("path", [""])[0].strip()
            if not path:
                self.send_json({"ok": False, "error": "未指定 CSV 檔案路徑"}, 400)
                return
            target = safe_fs_path(path)
            if not os.path.isfile(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {target}"}, 404)
                return
            try:
                st = os.stat(target)
                with open(target, "rb") as bf:
                    raw_bytes = bf.read(15 * 1024 * 1024)

                # Smart encoding detection (UTF-8-SIG, UTF-8, CP950/Big5, UTF-16)
                detected_enc = "utf-8"
                text = None
                for enc in ("utf-8-sig", "utf-8", "cp950", "big5", "utf-16", "gb18030", "latin1"):
                    try:
                        text = raw_bytes.decode(enc)
                        detected_enc = enc
                        break
                    except UnicodeDecodeError:
                        continue
                if text is None:
                    text = raw_bytes.decode("utf-8", errors="replace")

                ext = os.path.splitext(target)[1].lower()
                delim = "\t" if ext == ".tsv" else ","
                sample = text[:8192]
                if ext != ".tsv":
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
                        delim = dialect.delimiter
                    except Exception:
                        pass

                reader = csv.reader(io.StringIO(text), delimiter=delim)
                all_rows = [r for r in reader if any(cell.strip() for cell in r)]
                headers = all_rows[0] if len(all_rows) > 0 else []
                data_rows = all_rows[1:] if len(all_rows) > 1 else []

                # Normalize row lengths to match headers
                num_cols = max([len(headers)] + [len(r) for r in data_rows[:200]] + [0])
                if len(headers) < num_cols:
                    headers = headers + [f"Col_{i+1}" for i in range(len(headers), num_cols)]
                norm_rows = [
                    (r + [""] * (num_cols - len(r)))[:num_cols]
                    for r in data_rows[:10000]
                ]

                self.send_json({
                    "ok": True,
                    "path": target,
                    "name": os.path.basename(target),
                    "dir": os.path.dirname(target),
                    "encoding": detected_enc.upper(),
                    "delimiter": "TAB" if delim == "\t" else delim,
                    "headers": headers,
                    "rows": norm_rows,
                    "total_rows": len(data_rows),
                    "truncated": len(data_rows) > 10000,
                    "raw": text[:500000],
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                    "mtime_ns": st.st_mtime_ns,
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/list":
            raw_path = qs.get("path", [""])[0].strip()
            recursive = qs.get("recursive", ["0"])[0] == "1"
            if not raw_path:
                desktop = os.path.expanduser("~/Desktop")
                has_desktop_supported = False
                if not path_is_unsafe(desktop) and os.path.isdir(desktop):
                    try:
                        has_desktop_supported = any(
                            not e.startswith(".") and detect_file_type(e) is not None
                            for e in os.listdir(desktop)
                        )
                    except Exception:
                        pass
                raw_path = desktop if has_desktop_supported else TOOLBOX_DIR

            target = safe_fs_path(raw_path)
            focus_file = None
            focus_type = None
            if os.path.isfile(target):
                focus_file = target
                focus_type = detect_file_type(target)
                target = os.path.dirname(target)

            if not os.path.isdir(target):
                self.send_json({"ok": False, "error": f"找不到目錄: {target}"}, 404)
                return

            dirs = []
            items = []
            images = []
            markdowns = []
            jsons = []
            csvs = []
            skip_dirs = {".git", ".venv", "__pycache__", ".cache", "node_modules", ".next", "dist"}
            skip_json_names = {"package-lock.json", "tsconfig.tsbuildinfo"}

            try:
                for entry in sorted(os.listdir(target), key=natural_key):
                    if entry.startswith("."):
                        continue
                    full = os.path.join(target, entry)
                    if entry_is_unsafe_link(full):
                        continue                      # symlink / junction to a UNC or device path: never touch it
                    if os.path.isdir(full):
                        if entry not in skip_dirs:
                            try:
                                dst = os.stat(full)
                                dmtime = int(dst.st_mtime)
                            except Exception:
                                dmtime = 0
                            dirs.append({"name": entry, "path": full, "mtime": dmtime})
                    elif not recursive:
                        if entry in skip_json_names:
                            continue
                        ftype = detect_file_type(entry)
                        if not ftype:
                            continue
                        try:
                            st = os.stat(full)
                        except Exception:
                            continue
                        obj = {
                            "name": entry,
                            "path": full,
                            "dir": target,
                            "type": ftype,
                            "size": st.st_size,
                            "mtime": int(st.st_mtime),
                        }
                        items.append(obj)
                        if ftype == "image":
                            images.append(obj)
                        elif ftype == "markdown":
                            markdowns.append(obj)
                        elif ftype == "json":
                            jsons.append(obj)
                        elif ftype == "csv":
                            csvs.append(obj)

                # Only walk subdirectories when recursive=1 is explicitly enabled
                if recursive:
                    for root_dir, subdirs, files in safe_walk(target):
                        subdirs[:] = sorted([d for d in subdirs if not d.startswith(".") and d not in skip_dirs], key=natural_key)
                        for f in sorted(files, key=natural_key):
                            if f.startswith(".") or f in skip_json_names or len(items) >= 3000:
                                continue
                            ftype = detect_file_type(f)
                            if not ftype:
                                continue
                            full = os.path.join(root_dir, f)
                            rel = os.path.relpath(full, target)
                            try:
                                st = os.stat(full)
                                obj = {
                                    "name": rel,
                                    "path": full,
                                    "dir": root_dir,
                                    "type": ftype,
                                    "size": st.st_size,
                                    "mtime": int(st.st_mtime),
                                }
                                items.append(obj)
                                if ftype == "image":
                                    images.append(obj)
                                elif ftype == "markdown":
                                    markdowns.append(obj)
                                elif ftype == "json":
                                    jsons.append(obj)
                                elif ftype == "csv":
                                    csvs.append(obj)
                            except Exception:
                                pass
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
                return

            self.send_json({
                "ok": True,
                "path": target,
                "home": safe_or_none("~") or "",
                "parent": os.path.dirname(target) if target != "/" else "/",
                "recursive": recursive,
                "focus_file": focus_file,
                "focus_type": focus_type,
                "dirs": dirs,
                "items": items,
                "images": images,
                "markdowns": markdowns,
                "jsons": jsons,
                "csvs": csvs,
            })
            return

        if route == "/api/variants":
            path = qs.get("path", [""])[0]
            self.send_json({"ok": True, "variants": discover_variants(path)})
            return

        if route == "/api/image":
            path = qs.get("path", [""])[0]
            base_dir = qs.get("base_dir", [""])[0]
            is_thumb = qs.get("thumb", ["0"])[0] == "1"

            if path and not os.path.isabs(path) and base_dir:
                path = os.path.join(base_dir, path)
            if path:
                try:
                    path = safe_fs_path(path)          # the joined path is validated as a whole (whitelist on Windows)
                except PathRejected:
                    self.send_json({"ok": False, "error": "不允許的路徑（網路路徑或裝置路徑）"}, 400)
                    return

            if not path or not os.path.isfile(path):
                self.send_error(404, "Image not found")
                return

            st = os.stat(path)
            etag = hashlib.md5(f"{path}:{st.st_mtime_ns}:{st.st_size}:{is_thumb}".encode()).hexdigest()
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.end_headers()
                return

            ext = os.path.splitext(path)[1].lower()
            if ext == ".svg":
                with open(path, "rb") as f:
                    data = f.read()
                ctype = "image/svg+xml"
            elif is_thumb:
                thumb_file = os.path.join(CACHE_DIR, f"{etag}.jpg")
                thumb_file_png = os.path.join(CACHE_DIR, f"{etag}.png")
                
                if os.path.exists(thumb_file):
                    with open(thumb_file, "rb") as f:
                        data = f.read()
                    ctype = "image/jpeg"
                elif os.path.exists(thumb_file_png):
                    with open(thumb_file_png, "rb") as f:
                        data = f.read()
                    ctype = "image/png"
                else:
                    # 1. Try PIL (Pillow) first (cross-platform, fast)
                    pil_done = False
                    try:
                        from PIL import Image
                        with Image.open(path) as im:
                            im.thumbnail((360, 360))
                            if im.mode in ("RGBA", "P"):
                                im = im.convert("RGB")
                            im.save(thumb_file, "JPEG", quality=85)
                            pil_done = True
                    except Exception:
                        pil_done = False

                    if not pil_done and not IS_WINDOWS:
                        # 2. Try macOS sips for standard image files
                        _mac_run(
                            ["/usr/bin/sips", "-Z", "360", "-s", "format", "jpeg", path, "--out", thumb_file],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                        )

                    if os.path.exists(thumb_file):
                        with open(thumb_file, "rb") as f:
                            data = f.read()
                        ctype = "image/jpeg"
                    else:
                        # 3. Try macOS QuickLook for PPTX, PDF, Office, Keynote, etc.
                        if not IS_WINDOWS:
                            _mac_run(
                                ["/usr/bin/qlmanage", "-t", "-s", "360", "-o", CACHE_DIR, path],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5
                            )
                        ql_out = os.path.join(CACHE_DIR, os.path.basename(path) + ".png")
                        if os.path.exists(ql_out):
                            try:
                                os.rename(ql_out, thumb_file_png)
                            except Exception:
                                thumb_file_png = ql_out
                            with open(thumb_file_png, "rb") as f:
                                data = f.read()
                            ctype = "image/png"
                        else:
                            try:
                                with open(path, "rb") as f:
                                    data = f.read()
                                ctype = "application/octet-stream"
                            except Exception:
                                self.send_error(404, "Thumbnail not found")
                                return
            elif ext in (".heic", ".tif", ".tiff"):
                conv_file = os.path.join(CACHE_DIR, f"{etag}_full.jpg")
                if not os.path.exists(conv_file):
                    _mac_run(["/usr/bin/sips", "-s", "format", "jpeg", path, "--out", conv_file],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with open(conv_file if os.path.exists(conv_file) else path, "rb") as f:
                    data = f.read()
                ctype = "image/jpeg"
            else:
                with open(path, "rb") as f:
                    data = f.read()
                mime_map = {
                    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
                    ".png": "image/png", ".webp": "image/webp",
                    ".gif": "image/gif", ".bmp": "image/bmp",
                }
                ctype = mime_map.get(ext, "application/octet-stream")

            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "private, no-store")
            # A user-supplied .svg opened as a top-level document must never run script in the app origin.
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404)

    def _handle_post(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        try:
            body = self.read_json()
        except (ValueError, UnicodeDecodeError):
            self.send_json({"ok": False, "error": "invalid JSON body"}, 400)
            return
        if not isinstance(body, dict):
            self.send_json({"ok": False, "error": "JSON object expected"}, 400)
            return
        if sys.platform != "win32":
            for _k in PATH_PARAM_KEYS:
                if isinstance(body.get(_k), str):
                    body[_k] = normalize_posix_path(body[_k])
        if self._reject_bad_inputs({}, body):
            return

        if route == "/api/win-register" and IS_WIN_FROZEN:
            if body.get("action") == "unregister":
                win_support.unregister()
            else:
                win_support.register()
            self.send_json({"ok": True, **win_support.register_status()})
            return

        if route == "/api/unlock":
            # Password arrives ONLY in the POST body; it is used once and never stored, logged or echoed.
            path = (body.get("path") or "").strip()
            password = body.get("password")
            target = safe_fs_path(path) if path else ""
            if not target or not os.path.isfile(target):
                self.send_json({"ok": False, "error": "找不到檔案"}, 404)
                return
            if not isinstance(password, str) or password == "":
                self.send_json({"ok": False, "wrong_password": True, "error": "請輸入密碼"})
                return
            if not can_unlock(target):
                self.send_json(encrypted_response(target))
                return
            rl_key, rl_msg = unlock_reserve(target)
            if rl_key is None:
                self.send_json({"ok": False, "rate_limited": True, "error": rl_msg})
                return
            ok_flag = False
            try:
                plain = decrypt_to_bytes(target, password)
                unlock_cache_put(target, plain)
                ok_flag = True
                self.send_json({"ok": True, "unlocked": True})
            except WrongPasswordError:
                cooled = unlock_release(rl_key, False)
                rl_key = None
                if cooled:
                    self.send_json({"ok": False, "rate_limited": True,
                                    "error": "密碼錯誤次數過多，請 %d 秒後再試" % UNLOCK_COOLDOWN_SECONDS})
                else:
                    self.send_json({"ok": False, "wrong_password": True, "error": "密碼錯誤"})
            except Exception as e:
                self.send_json({"ok": False, "error": e.__class__.__name__ if not isinstance(e, RuntimeError) else str(e)})
            finally:
                if rl_key is not None:
                    unlock_release(rl_key, ok_flag)
                password = None
                body.pop("password", None)
            return

        if route == "/api/perform-update" and IS_WIN_FROZEN:
            self.send_json(win_support.perform_update())
            return

        if route == "/api/perform-update" and IS_WINDOWS:
            self.send_json({"ok": False, "error": "Windows 非打包版不支援自動更新"}, 400)
            return

        if route == "/api/perform-update":
            pkg_url = body.get("pkg_url")
            if not pkg_url:
                repo = body.get("repo") or DEFAULT_GITHUB_REPO
                branch = body.get("branch") or DEFAULT_GITHUB_BRANCH
                pkg_url = f"https://raw.githubusercontent.com/{repo}/{branch}/dist/MacToolbox-latest.pkg"

            def do_download_and_install():
                import urllib.request
                dest = "/tmp/MacToolbox-Update.pkg"
                try:
                    req = urllib.request.Request(pkg_url, headers={"User-Agent": f"MacToolbox/{APP_VERSION}"})
                    with urllib.request.urlopen(req, timeout=45.0) as resp, open(dest, "wb") as out:
                        shutil.copyfileobj(resp, out)
                    _mac_popen(["/usr/bin/open", dest])
                except Exception as err:
                    print(f"Auto-update download error: {err}")

            threading.Thread(target=do_download_and_install, daemon=True).start()
            self.send_json({"ok": True, "message": "已開始背景下載最新安裝包，完成後將自動啟動 macOS 安裝引導。"})
            return

        if route == "/api/pdf-interact":
            # the page reports that the user clicked inside the built-in PDF viewer (window blur with the PDF frame active):
            # the shell uses it to tell a PDF link click from a script-driven navigation (see windows/launcher.py)
            PDF_INTERACT["t"] = time.time()
            self.send_json({"ok": True})
            return

        if route == "/api/open-target":
            target = body.get("path", "")
            if target:
                abs_p = safe_fs_path(target)
                LAST_OPEN_REQUEST["id"] += 1
                LAST_OPEN_REQUEST["path"] = abs_p
                LAST_OPEN_REQUEST["type"] = detect_file_type(abs_p)
                LAST_OPEN_REQUEST["timestamp"] = time.time()
            self.send_json({"ok": True, **LAST_OPEN_REQUEST})
            return

        if route == "/api/xlsx":
            import openpyxl
            path = body.get("path", "").strip()
            sheet_name = (body.get("sheet_name") or body.get("sheet") or "").strip()
            headers = body.get("headers") or []
            rows = body.get("rows")
            if not path:
                self.send_json({"ok": False, "error": "缺少檔案路徑"}, 400)
                return
            if not isinstance(rows, list) or not isinstance(headers, list):
                self.send_json({"ok": False, "error": "缺少必要欄位 rows（資料列陣列）"}, 400)     # never clear a sheet by accident
                return
            target = safe_fs_path(path)
            try:
                ext = os.path.splitext(target)[1].lower()
                if ext == ".xls":
                    # Save modified legacy .xls as modern .xlsx alongside original
                    wb = openpyxl.Workbook()
                    ws = wb.active
                    ws.title = sheet_name or "Sheet1"
                    target = os.path.splitext(target)[0] + "_edited.xlsx"
                else:
                    wb = openpyxl.load_workbook(target)
                    ws = wb[sheet_name] if sheet_name and sheet_name in wb.sheetnames else wb.active
                    ws.delete_rows(1, ws.max_row + 1)
                if headers:
                    ws.append(headers)
                for r in rows:
                    conv_r = []
                    for cell in r:
                        s = str(cell if cell is not None else "").strip()
                        if re.match(r'^-?\d+$', s) and not (len(s) > 1 and s.startswith("0")):
                            conv_r.append(int(s))
                        elif re.match(r'^-?\d+\.\d+$', s):
                            conv_r.append(float(s))
                        else:
                            conv_r.append(cell)
                    ws.append(conv_r)
                wb.save(target)
                st = os.stat(target)
                self.send_json({
                    "ok": True,
                    "path": target,
                    "mtime": int(st.st_mtime),
                    "message": f"已儲存 Excel 工作表「{ws.title}」({os.path.basename(target)})"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/xml":
            path = body.get("path", "").strip()
            content = body.get("content")
            if not path or content is None:
                self.send_json({"ok": False, "error": "缺少路徑或內容"}, 400)
                return
            target = safe_fs_path(path)
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
                st = os.stat(target)
                self.send_json({
                    "ok": True,
                    "path": target,
                    "mtime": int(st.st_mtime),
                    "message": f"已儲存 XML ({os.path.basename(target)})"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/quicklook":
            path = body.get("path", "").strip()
            if path and os.path.exists(path):
                if IS_WINDOWS:
                    path = safe_fs_path(path)          # the validated, normalised path - never the raw string
                    if not open_with_default_app(path):
                        self.send_json({"ok": False, "error": QUICKLOOK_REFUSED}, 400)
                        return
                else:
                    _mac_popen(["/usr/bin/qlmanage", "-p", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "找不到檔案"}, 404)
            return

        if route == "/api/csv":
            import csv
            path = body.get("path", "").strip()
            headers = body.get("headers")
            rows = body.get("rows")
            if not path:
                self.send_json({"ok": False, "error": "缺少檔案路徑"}, 400)
                return
            target = safe_fs_path(path)
            try:
                ext = os.path.splitext(target)[1].lower()
                delim = "\t" if ext == ".tsv" else ","
                with open(target, "w", encoding="utf-8-sig", newline="") as f:
                    writer = csv.writer(f, delimiter=delim)
                    if headers:
                        writer.writerow(headers)
                    if rows:
                        writer.writerows(rows)
                st = os.stat(target)
                self.send_json({
                    "ok": True,
                    "path": target,
                    "mtime": int(st.st_mtime),
                    "mtime_ns": st.st_mtime_ns,
                    "message": f"已儲存 CSV ({os.path.basename(target)})"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/md" or route == "/api/json":
            path = body.get("path", "").strip()
            content = body.get("content", "")
            if not path:
                self.send_json({"ok": False, "error": "缺少檔案路徑"}, 400)
                return
            target = safe_fs_path(path)
            try:
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
                st = os.stat(target)
                self.send_json({
                    "ok": True,
                    "path": target,
                    "mtime": int(st.st_mtime),
                    "mtime_ns": st.st_mtime_ns,
                    "message": f"已儲存 {os.path.basename(target)}"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/reveal-finder":
            path = body.get("path", "")
            if path and os.path.exists(path):
                reveal_in_file_manager(path)
                self.send_json({"ok": True})
            else:
                self.send_json({"ok": False, "error": "Path not found"}, 404)
            return

        if route == "/api/pick-path":
            mode = body.get("mode", "folder")
            default_dir = body.get("default_dir", "").strip()
            default_name = body.get("default_name", "").strip()
            chosen = pick_native_path(mode=mode, default_dir=default_dir, default_name=default_name)
            if chosen:
                self.send_json({"ok": True, "path": chosen})
            else:
                self.send_json({"ok": False, "cancelled": True})
            return

        if route == "/api/scratch":
            content = body.get("content", "")
            req_format = body.get("format", "auto").lower()
            custom_name = body.get("name", "").strip()

            raw = content.strip()
            detected = "markdown"
            ext = ".md"

            if req_format in ("json", "markdown", "csv", "xml"):
                detected = req_format
                ext = {
                    "json": ".json",
                    "markdown": ".md",
                    "csv": ".csv",
                    "xml": ".xml"
                }.get(detected, ".md")
            else:
                # Intelligent Auto Format Detection
                if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
                    try:
                        json.loads(raw)
                        detected = "json"
                        ext = ".json"
                    except Exception:
                        pass
                if detected == "markdown" and raw.startswith("<") and raw.endswith(">"):
                    try:
                        import xml.etree.ElementTree as ET
                        ET.fromstring(raw)
                        detected = "xml"
                        ext = ".xml"
                    except Exception:
                        pass
                if detected == "markdown":
                    lines = [ln for ln in raw.split("\n") if ln.strip()]
                    if len(lines) >= 2:
                        first = lines[0]
                        second = lines[1]
                        if "," in first and first.count(",") >= 1 and second.count(",") == first.count(","):
                            detected = "csv"
                            ext = ".csv"
                        elif "\t" in first and first.count("\t") >= 1 and second.count("\t") == first.count("\t"):
                            detected = "csv"
                            ext = ".tsv"

            time_str = time.strftime("%Y%m%d_%H%M%S")
            fname = custom_name if custom_name else f"scratch_{time_str}{ext}"
            scratch_path = os.path.join(SCRATCH_DIR, fname)
            with open(scratch_path, "w", encoding="utf-8") as f:
                f.write(content)

            st = os.stat(scratch_path)
            self.send_json({
                "ok": True,
                "path": scratch_path,
                "scratch_dir": SCRATCH_DIR,
                "format": detected,
                "name": fname,
                "is_scratch": True,
                "size": st.st_size,
                "mtime": int(st.st_mtime)
            })
            return

        if route == "/api/scratch-save":
            source_path = body.get("source_path", "").strip()
            target_path = body.get("target_path", "").strip()
            content = body.get("content", None)

            if not target_path:
                self.send_json({"ok": False, "error": "未指定儲存目標路徑"}, 400)
                return

            target = safe_fs_path(target_path)
            os.makedirs(os.path.dirname(target), exist_ok=True)

            try:
                if content is not None:
                    with open(target, "w", encoding="utf-8") as f:
                        f.write(content)
                elif source_path and os.path.exists(source_path):
                    shutil.copy2(source_path, target)
                else:
                    self.send_json({"ok": False, "error": "缺少內容或暫存來源檔案"}, 400)
                    return

                st = os.stat(target)
                self.send_json({
                    "ok": True,
                    "path": target,
                    "name": os.path.basename(target),
                    "dir": os.path.dirname(target),
                    "mtime": int(st.st_mtime),
                    "message": f"已正式儲存為 {os.path.basename(target)}"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/file-action":
            action = body.get("action", "")
            raw_path = body.get("path", "").strip()
            if not raw_path:
                self.send_json({"ok": False, "error": "未指定檔案路徑"}, 400)
                return
            target = safe_fs_path(raw_path)
            if not os.path.exists(target):
                self.send_json({"ok": False, "error": f"找不到檔案: {os.path.basename(target)}"}, 404)
                return

            if action == "rename":
                new_name = body.get("new_name", "").strip()
                if not new_name or filename_is_invalid(new_name):
                    self.send_json({"ok": False, "error": "新檔名不合法"}, 400)
                    return
                parent_dir = os.path.dirname(target)
                new_path = os.path.abspath(os.path.join(parent_dir, new_name))
                if new_path == target:
                    self.send_json({"ok": True, "path": target, "name": new_name, "message": "檔名未變更"})
                    return
                if os.path.exists(new_path):
                    self.send_json({"ok": False, "error": f"目標檔案已存在: {new_name}"}, 409)
                    return
                try:
                    os.rename(target, new_path)
                    self.send_json({
                        "ok": True,
                        "old_path": target,
                        "path": new_path,
                        "name": new_name,
                        "message": f"已重新命名為 {new_name}"
                    })
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif action == "trash":
                try:
                    if IS_WINDOWS and os.environ.get("POWERUP_DATA_DIR"):
                        # test instance: never use the real Recycle Bin; use a trash folder in the test data dir
                        tdir = os.path.join(os.environ["POWERUP_DATA_DIR"], "trash")
                        os.makedirs(tdir, exist_ok=True)
                        dest = os.path.join(tdir, "%d_%s" % (int(time.time() * 1000), os.path.basename(target)))
                        if os.path.splitdrive(os.path.abspath(target))[0].upper() != os.path.splitdrive(os.path.abspath(tdir))[0].upper():
                            self.send_json({"ok": False, "error": "測試模式的垃圾桶只支援同一磁碟機"}, 400)
                            return
                        os.rename(target, dest)        # same-volume rename: moves a junction / symlink itself, never copies through it
                        self.send_json({"ok": True, "deleted_path": target, "trash_path": dest,
                                        "message": f"已移至資源回收筒：{os.path.basename(target)}"})
                        return
                    if IS_WINDOWS:
                        windows_move_to_recycle_bin(target)
                        self.send_json({
                            "ok": True,
                            "deleted_path": target,
                            "message": f"已移至資源回收筒：{os.path.basename(target)}"
                        })
                        return
                    trash_dir = os.path.expanduser("~/.Trash")
                    os.makedirs(trash_dir, exist_ok=True)
                    base_name = os.path.basename(target)
                    stem, ext = os.path.splitext(base_name)
                    dest = os.path.join(trash_dir, base_name)
                    if os.path.exists(dest):
                        dest = os.path.join(trash_dir, f"{stem}_{int(time.time())}{ext}")
                    shutil.move(target, dest)
                    self.send_json({
                        "ok": True,
                        "deleted_path": target,
                        "trash_path": dest,
                        "message": f"已移至 Mac 垃圾桶：{base_name}"
                    })
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            elif action == "duplicate":
                try:
                    parent_dir = os.path.dirname(target)
                    base_name = os.path.basename(target)
                    stem, ext = os.path.splitext(base_name)
                    idx = 1
                    while True:
                        cand_name = f"{stem}_copy{'' if idx == 1 else idx}{ext}"
                        cand_path = os.path.join(parent_dir, cand_name)
                        if not os.path.exists(cand_path):
                            break
                        idx += 1
                    shutil.copy2(target, cand_path)
                    self.send_json({
                        "ok": True,
                        "path": cand_path,
                        "name": cand_name,
                        "message": f"已建立副本：{cand_name}"
                    })
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
                return

            self.send_json({"ok": False, "error": f"未知操作: {action}"}, 400)
            return

        if route == "/api/copy-crop":
            path = body.get("path", "")
            crop = body.get("crop") or {}
            rotate = int(body.get("rotate", 0))
            action = body.get("action", "copy")

            if not path or not os.path.isfile(path):
                self.send_json({"ok": False, "error": "找不到來源圖片"}, 404)
                return

            stem = os.path.splitext(os.path.basename(path))[0]
            ts = time.strftime("%H%M%S")
            if action in ("save", "both"):
                out_dir = os.path.dirname(path)
                out_path = os.path.join(out_dir, f"{stem}_crop_{ts}.png")
            else:
                out_path = os.path.join(CAPTURES_DIR, f"{stem}_clip_{ts}.png")

            try:
                rx = str(float(crop.get("x", 0.0)))
                ry = str(float(crop.get("y", 0.0)))
                rw = str(float(crop.get("w", 1.0)))
                rh = str(float(crop.get("h", 1.0)))
            except (TypeError, ValueError):
                self.send_json({"ok": False, "error": "裁切參數必須是數字"}, 400)
                return

            if os.path.exists(HELPER_BIN):
                cmd = [HELPER_BIN, "crop-rotate", path, out_path, rx, ry, rw, rh, str(rotate)]
                if action in ("copy", "both"):
                    cmd.append("copy")

                res = _mac_run(cmd, capture_output=True, text=True)
                try:
                    info = json.loads(res.stdout.strip())
                except Exception:
                    info = {"ok": False, "error": res.stderr or "圖片裁切失敗"}
            else:
                try:
                    from PIL import Image
                    with Image.open(path) as im:
                        orig_w, orig_h = im.size
                        fx, fy, fw, fh = float(rx), float(ry), float(rw), float(rh)
                        left = max(0, int(round(fx * orig_w)))
                        top = max(0, int(round(fy * orig_h)))
                        right = min(orig_w, int(round((fx + fw) * orig_w)))
                        bottom = min(orig_h, int(round((fy + fh) * orig_h)))
                        cropped = im.crop((left, top, right, bottom))
                        if rotate != 0:
                            cropped = cropped.rotate(-rotate, expand=True)
                        cropped.save(out_path, format="PNG")
                        cw, ch = cropped.size
                        info = {"ok": True, "width": cw, "height": ch, "copied": False}
                except Exception as e:
                    info = {"ok": False, "error": f"圖片裁切失敗: {e}"}

            if not info.get("ok"):
                self.send_json(info, 500)
                return

            # On Windows, copy image to clipboard (Win32 clipboard API through ctypes) if requested
            if IS_WINDOWS and action in ("copy", "both") and os.path.exists(out_path):
                try:
                    winshell.copy_image_to_clipboard(out_path)       # ctypes clipboard API, no PowerShell, no script text
                    info["copied"] = True
                except Exception:
                    pass

            cw = info.get("width", 0)
            ch = info.get("height", 0)
            copied = bool(info.get("copied", False))
            self.send_json({
                "ok": True,
                "copied": copied,
                "saved_path": out_path,
                "width": cw,
                "height": ch,
                "message": f"已處理 ({cw}×{ch} px)" + ((" 並複製到剪貼簿！可直接 Ctrl+V 貼上" if IS_WINDOWS else " 並複製到剪貼簿！可直接 Ctrl+V / Cmd+V 貼上") if copied else "")
            })
            return

        if route == "/api/screenshot":
            mode = body.get("mode", "interactive")
            ts = time.strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join(CAPTURES_DIR, f"screenshot_{ts}.png")

            if IS_WINDOWS:
                if mode == "clipboard":
                    winshell.clipboard_image_to_png(out_path)         # ctypes clipboard API, no PowerShell
                    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                        self.send_json({"ok": True, "path": out_path, "folder": CAPTURES_DIR})
                    else:
                        self.send_json({"ok": False, "error": "剪貼簿中沒有圖片"})
                    return
                else:
                    subprocess.Popen([winpath.system_exe("explorer.exe"), "ms-screenclip:"])
                    self.send_json({"ok": True, "message": "已啟動 Windows 截圖工具，截圖完成後請點擊「貼上剪貼簿」載入。"})
                    return

            if mode == "interactive":
                _mac_run(["/usr/sbin/screencapture", "-i", out_path])
                if not os.path.exists(out_path):
                    self.send_json({"ok": False, "cancelled": True, "error": "已取消截圖"})
                    return
                if os.path.exists(HELPER_BIN):
                    _mac_run([HELPER_BIN, "copy-image", out_path], capture_output=True)
                self.send_json({"ok": True, "path": out_path, "folder": CAPTURES_DIR})
                return
            elif mode == "clipboard":
                out_path = os.path.join(CAPTURES_DIR, f"clipboard_{ts}.png")
                if os.path.exists(HELPER_BIN):
                    res = _mac_run([HELPER_BIN, "paste-image", out_path], capture_output=True, text=True)
                    try:
                        info = json.loads(res.stdout.strip())
                    except Exception:
                        info = {"ok": False, "error": res.stderr or "無法讀取剪貼簿"}
                else:
                    info = {"ok": False, "error": "無法讀取剪貼簿"}
                if info.get("ok") and os.path.exists(out_path):
                    self.send_json({"ok": True, "path": out_path, "folder": CAPTURES_DIR})
                else:
                    self.send_json({"ok": False, "error": info.get("error", "剪貼簿中沒有圖片")})
                return

        if route == "/api/faces":
            path = body.get("path", "")
            if not path or not os.path.isfile(path):
                self.send_json({"ok": False, "error": "找不到圖片"}, 404)
                return

            if os.path.exists(HELPER_BIN):
                res = _mac_run([HELPER_BIN, "faces", path], capture_output=True, text=True)
                try:
                    data = json.loads(res.stdout.strip())
                except Exception:
                    data = {"ok": False, "error": res.stderr or "人臉偵測失敗"}
                self.send_json(data)
                return
            else:
                self.send_json({
                    "ok": False,
                    "error": "Windows 平台人臉偵測模組開發中（即將支援 OpenCV YuNet / Windows.Media.FaceAnalysis）"
                })
                return

        if route == "/api/ocr":
            path = body.get("path", "")
            crop = body.get("crop")
            if not path or not os.path.isfile(path):
                self.send_json({"ok": False, "error": "找不到圖片"}, 404)
                return

            if not os.path.exists(HELPER_BIN):
                self.send_json({
                    "ok": False,
                    "error": "Windows 平台 OCR 模組開發中（即將支援 Windows.Media.Ocr.OcrEngine）"
                })
                return

            target_path = path
            if crop:
                target_path = os.path.join(CACHE_DIR, "ocr_crop_tmp.png")
                _mac_run([
                    HELPER_BIN, "crop-rotate", path, target_path,
                    str(float(crop.get("x", 0))), str(float(crop.get("y", 0))),
                    str(float(crop.get("w", 1))), str(float(crop.get("h", 1))), "0"
                ], capture_output=True)

            res = _mac_run([HELPER_BIN, "ocr", target_path], capture_output=True, text=True)
            try:
                data = json.loads(res.stdout.strip())
            except Exception:
                data = {"ok": False, "error": res.stderr or "OCR 執行失敗"}
            self.send_json(data)
            return

        if route == "/api/remove-bg":
            path = body.get("path", "")
            crop = body.get("crop")
            if not path or not os.path.isfile(path):
                self.send_json({"ok": False, "error": "找不到圖片"}, 404)
                return

            if not os.path.exists(HELPER_BIN):
                self.send_json({
                    "ok": False,
                    "error": "Windows 平台去背模組開發中（即將支援 RMBG ONNX DirectML）"
                })
                return

            stem = os.path.splitext(os.path.basename(path))[0]
            ts = time.strftime("%H%M%S")
            in_path = path
            if crop:
                in_path = os.path.join(CACHE_DIR, "nobg_in_tmp.png")
                _mac_run([
                    HELPER_BIN, "crop-rotate", path, in_path,
                    str(float(crop.get("x", 0))), str(float(crop.get("y", 0))),
                    str(float(crop.get("w", 1))), str(float(crop.get("h", 1))), "0"
                ], capture_output=True)

            out_path = os.path.join(CAPTURES_DIR, f"{stem}_nobg_{ts}.png")
            res = _mac_run([HELPER_BIN, "remove-bg", in_path, out_path, "copy"], capture_output=True, text=True)
            try:
                data = json.loads(res.stdout.strip())
            except Exception:
                data = {"ok": False, "error": res.stderr or "去背失敗"}
            self.send_json(data)
            return

        if route == "/api/ai-enhance" and IS_WINDOWS:
            self.send_json({"ok": False, "error": "Windows 版尚未支援本地 AI 修復強化"})
            return

        if route == "/api/codex" and IS_WINDOWS:
            self.send_json({"ok": False, "error": "Windows 版尚未支援 Codex CLI 整合"})
            return

        if route == "/api/ai-enhance":
            import cv2
            import numpy as np
            import torch
            from facexlib.utils.face_restoration_helper import FaceRestoreHelper

            path = body.get("path", "")
            mode = body.get("mode", "gfpgan")
            crop = body.get("crop")
            paste_back = bool(body.get("paste_back", True))
            weight = float(body.get("weight", 0.6))

            if not path or not os.path.isfile(path):
                self.send_json({"ok": False, "error": "找不到圖片"}, 404)
                return

            full_img = imread_unicode(path, cv2.IMREAD_COLOR)
            if full_img is None:
                self.send_json({"ok": False, "error": "無法讀取圖片"}, 500)
                return

            work_img, (cx, cy, cw, ch) = crop_image_array(full_img, crop)

            t0 = time.time()
            try:
                if mode == "descreen":
                    g = cv2.cvtColor(work_img, cv2.COLOR_BGR2GRAY)
                    den = descreen_fft_gray(g)
                    res_img = cv2.cvtColor(den, cv2.COLOR_GRAY2BGR)

                elif mode in ("gfpgan", "codeformer"):
                    ckpt = "GFPGANv1.4.pth" if mode == "gfpgan" else "codeformer.pth"
                    model_wrapper, dev = get_ai_model(ckpt)
                    net = model_wrapper.model
                    helper = FaceRestoreHelper(
                        1, face_size=512, det_model="retinaface_resnet50",
                        use_parse=True, device=torch.device("cpu")
                    )
                    helper.read_image(work_img)
                    n_faces = helper.get_face_landmarks_5(only_center_face=False, resize=640, eye_dist_threshold=5)
                    if n_faces == 0:
                        self.send_json({"ok": False, "error": "選取區域內未偵測到人臉，請擴大框選範圍或改用 RealESRGAN"})
                        return
                    helper.align_warp_face()
                    with torch.inference_mode():
                        for face in helper.cropped_faces:
                            rgb = cv2.cvtColor(face, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                            t = torch.from_numpy(rgb).permute(2, 0, 1)[None].to(dev) * 2.0 - 1.0
                            if mode == "gfpgan":
                                out_t = (net(t, return_rgb=False)[0] + 1.0) / 2.0
                            else:
                                out_t = (net(t, weight=weight, adain=True)[0] + 1.0) / 2.0
                            out_rgb = out_t[0].clamp(0, 1).permute(1, 2, 0).float().cpu().numpy()
                            restored_face = cv2.cvtColor((out_rgb * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
                            helper.add_restored_face(restored_face)
                    helper.get_inverse_affine(None)
                    res_img = helper.paste_faces_to_input_image(upsample_img=work_img)

                elif mode == "realesrgan":
                    esr, dev = get_ai_model("RealESRGAN_x4plus.pth")
                    mh, mw = work_img.shape[:2]
                    if max(mh, mw) > 800:
                        scale = 800.0 / max(mh, mw)
                        work_in = cv2.resize(work_img, (int(mw * scale), int(mh * scale)))
                    else:
                        work_in = work_img
                    with torch.inference_mode():
                        rgb = cv2.cvtColor(work_in, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
                        t = torch.from_numpy(rgb).permute(2, 0, 1)[None].to(dev)
                        out_t = esr(t)
                        out_rgb = out_t[0].clamp(0, 1).permute(1, 2, 0).float().cpu().numpy()
                        res_img = cv2.cvtColor((out_rgb * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
                    if crop and paste_back:
                        res_img = cv2.resize(res_img, (cw, ch), interpolation=cv2.INTER_LANCZOS4)

                elif mode == "ddcolor":
                    col, dev = get_ai_model("ddcolor_artistic.pth")
                    g = cv2.cvtColor(work_img, cv2.COLOR_BGR2GRAY)
                    with torch.inference_mode():
                        x = torch.from_numpy(g.astype(np.float32) / 255.0)[None, None].to(dev)
                        out_t = col(x)
                        out_rgb = out_t[0].clamp(0, 1).permute(1, 2, 0).float().cpu().numpy()
                        res_img = cv2.cvtColor((out_rgb * 255).round().astype(np.uint8), cv2.COLOR_RGB2BGR)
                else:
                    self.send_json({"ok": False, "error": f"未知 AI 模式: {mode}"}, 400)
                    return

                if crop and paste_back and res_img.shape[:2] == (ch, cw):
                    blended = full_img.copy()
                    mask = np.ones((ch, cw), dtype=np.float32)
                    border = max(4, min(cw, ch) // 16)
                    mask[:border, :] *= np.linspace(0, 1, border)[:, None]
                    mask[-border:, :] *= np.linspace(1, 0, border)[:, None]
                    mask[:, :border] *= np.linspace(0, 1, border)[None, :]
                    mask[:, -border:] *= np.linspace(1, 0, border)[None, :]
                    mask3 = mask[:, :, None]
                    roi = blended[cy:cy + ch, cx:cx + cw].astype(np.float32)
                    blended[cy:cy + ch, cx:cx + cw] = (res_img.astype(np.float32) * mask3 + roi * (1.0 - mask3)).round().astype(np.uint8)
                    final_img = blended
                else:
                    final_img = res_img

                stem = os.path.splitext(os.path.basename(path))[0]
                ts = time.strftime("%H%M%S")
                out_path = os.path.join(CAPTURES_DIR, f"{stem}_ai_{mode}_{ts}.png")
                imwrite_unicode(out_path, final_img, ".png")
                _mac_run([HELPER_BIN, "copy-image", out_path], capture_output=True)

                elapsed = round(time.time() - t0, 2)
                self.send_json({
                    "ok": True,
                    "path": out_path,
                    "mode": mode,
                    "elapsed": elapsed,
                    "message": f"AI {mode.upper()} 完成（耗時 {elapsed}s），已存入截圖匣並複製到剪貼簿！"
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        if route == "/api/codex":
            path = body.get("path", "")
            prompt = body.get("prompt", "").strip()
            crop = body.get("crop")

            if not prompt:
                self.send_json({"ok": False, "error": "請輸入給 Codex CLI 的指令"}, 400)
                return

            ts = time.strftime("%Y%m%d_%H%M%S")
            out_img_path = os.path.join(CAPTURES_DIR, f"codex_{ts}.png")
            in_img_path = path
            if path and os.path.isfile(path) and crop:
                in_img_path = os.path.join(CAPTURES_DIR, f"codex_input_{ts}.png")
                _mac_run([
                    HELPER_BIN, "crop-rotate", path, in_img_path,
                    str(float(crop.get("x", 0))), str(float(crop.get("y", 0))),
                    str(float(crop.get("w", 1))), str(float(crop.get("h", 1))), "0"
                ], capture_output=True)

            codex_bin = find_codex_bin()
            python_bin = "/usr/bin/python3"
            full_prompt = (
                f"You are an image processing assistant running inside {TOOLBOX_DIR}.\n"
                f"Input image path: {in_img_path if in_img_path else 'None (generate from scratch)'}\n"
                f"Required output image path: {out_img_path}\n"
                f"User instruction: {prompt}\n"
                f"Produce or save the requested image directly to {out_img_path}, and briefly explain what you did."
            )

            try:
                proc = _mac_run(
                    [codex_bin, "exec", "--skip-git-repo-check", "--full-auto", full_prompt],
                    cwd=TOOLBOX_DIR,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                output_log = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                has_new_image = os.path.exists(out_img_path)
                if has_new_image:
                    _mac_run([HELPER_BIN, "copy-image", out_img_path], capture_output=True)
                self.send_json({
                    "ok": proc.returncode == 0 or has_new_image,
                    "has_image": has_new_image,
                    "output_image": out_img_path if has_new_image else None,
                    "log": output_log.strip()[-4000:],
                })
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return

        self.send_error(404)


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = sys.platform != "win32"


def main():
    port = int(os.environ.get("MACTOOLBOX_PORT", "18765"))
    abs_p = safe_or_none(sys.argv[1]) if len(sys.argv) > 1 else None
    if abs_p and os.path.exists(abs_p):
        LAST_OPEN_REQUEST["id"] = 1
        LAST_OPEN_REQUEST["path"] = abs_p
        LAST_OPEN_REQUEST["type"] = detect_file_type(abs_p)
        LAST_OPEN_REQUEST["timestamp"] = time.time()

    server = ReusableThreadingHTTPServer(("127.0.0.1", port), ToolboxHandler)
    print(f"MacToolbox Server running on http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
