# -*- coding: utf-8 -*-
"""Windows-only support code for the frozen PowerUp Toolbox build.

Pure stdlib. Must NOT import server.py (server imports this module).
Contains: paths, version, HKCU file-association registration, portable auto-update.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

APP_NAME = "PowerUpToolbox"
EXE_NAME = "PowerUpToolbox.exe"
PROGID = "PowerUpToolbox.File"
ASSET_NAME = "PowerUpToolbox-portable.zip"
TAG_PREFIX = "win-v"
GITHUB_REPO = "become/powerup-toolbox"
DEFAULT_API = "https://api.github.com/repos/%s/releases/latest" % GITHUB_REPO
# Primary update source: the official website. The manifest sits next to the zip:
#   {"version": "2.8.1", "release_date": "YYYY-MM-DD", "sha256": "<hex of the zip>", "size": 123, "changelog": ["..."]}
# GitHub Releases (above) stays only as a fallback until the repository goes private.
OFFICIAL_MANIFEST = "https://powerup.dancingarden.com/downloads/toolbox/win-version.json"
OFFICIAL_PAGE = "https://powerup.dancingarden.com/toolbox"

# Mirror of server.py *_EXTS (what detect_file_type can handle).
SUPPORTED_EXTS = sorted({
    ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".svg",
    ".md", ".markdown", ".mdx",
    ".json", ".jsonl", ".ndjson", ".geojson",
    ".csv", ".tsv",
    ".pptx", ".ppt",
    ".docx", ".doc", ".rtf",
    ".xlsx", ".xlsm", ".xls",
    ".xml", ".plist", ".rss", ".atom", ".wsdl", ".xsd", ".csproj", ".pom", ".jmx",
    ".pdf",
})


# ---------------------------------------------------------------- paths
def exe_in_temp(exe_path, temp_dir=None):
    """True when the executable lives under the temp folder (a test copy). Such a copy must never touch the real
    user data dir or HKCU: the launcher refuses to start and ensure_registered() refuses to register it."""
    import tempfile
    tmp = os.path.normcase(os.path.abspath(temp_dir or os.environ.get("TEMP") or tempfile.gettempdir()))
    d = os.path.normcase(os.path.dirname(os.path.abspath(exe_path)))
    return d == tmp or d.startswith(tmp + os.sep)


def data_dir():
    """LOCALAPPDATA/PowerUpToolbox, or $POWERUP_DATA_DIR (test isolation)."""
    override = os.environ.get("POWERUP_DATA_DIR")
    if override:
        return os.path.abspath(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def is_test_instance():
    return bool(os.environ.get("POWERUP_DATA_DIR"))


def log_dir():
    d = os.path.join(data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def resource_dir():
    return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))


def install_dir():
    """Folder holding PowerUpToolbox.exe (frozen) or None."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return None


def exe_path():
    return os.path.abspath(sys.executable) if getattr(sys, "frozen", False) else None


def current_version():
    for p in (os.path.join(resource_dir(), "VERSION"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")):
        try:
            with open(p, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except OSError:
            pass
    return "0.0.0"


def _parse_ver(v):
    nums = re.findall(r"\d+", re.sub(r"^[^\d]*", "", str(v)))[:3]
    nums = [int(x) for x in nums] + [0] * (3 - len(nums))
    return nums


def compare_versions(a, b):
    pa, pb = _parse_ver(a), _parse_ver(b)
    return (pa > pb) - (pa < pb)


# ---------------------------------------------------------------- registry
def _winreg():
    import winreg
    return winreg


def _command_for(exe):
    return '"%s" "%%1"' % exe


def _set_default(root, path, value):
    wr = _winreg()
    with wr.CreateKeyEx(root, path, 0, wr.KEY_WRITE) as k:
        wr.SetValueEx(k, "", 0, wr.REG_SZ, value)


VERB = "PowerUpToolbox"
REG_VERSION = 2
NO_AUTOREG_MARK = "no_autoregister"


def _write_verb(root, path, icon, command):
    wr = _winreg()
    with wr.CreateKeyEx(root, path, 0, wr.KEY_WRITE) as k:
        wr.SetValueEx(k, "MUIVerb", 0, wr.REG_SZ, "以 PowerUp Toolbox 開啟")
        wr.SetValueEx(k, "Icon", 0, wr.REG_SZ, icon)
    _set_default(root, path + r"\command", command)


def _prune_empty(root, path):
    """Delete key if it has no subkeys and no values (never removes pre-existing content)."""
    wr = _winreg()
    try:
        with wr.OpenKey(root, path) as k:
            n_sub, n_val, _ = wr.QueryInfoKey(k)
        if n_sub == 0 and n_val == 0:
            wr.DeleteKey(root, path)
            return True
    except OSError:
        pass
    return False


def _delete_tree(root, path):
    wr = _winreg()
    try:
        with wr.OpenKey(root, path, 0, wr.KEY_READ | wr.KEY_WRITE) as k:
            while True:
                try:
                    sub = wr.EnumKey(k, 0)
                except OSError:
                    break
                _delete_tree(root, path + "\\" + sub)
        wr.DeleteKey(root, path)
    except FileNotFoundError:
        pass


def _notify():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def registered_exe():
    """Exe path currently stored in the HKCU ProgID command, or None."""
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER,
                        r"Software\Classes\%s\shell\open\command" % PROGID) as k:
            cmd = wr.QueryValueEx(k, "")[0]
        m = re.match(r'^"([^"]+)"', cmd)
        return m.group(1) if m else None
    except OSError:
        return None


def register(exe=None):
    """Register under HKCU only. Adds to OpenWithProgids; never touches UserChoice/HKLM."""
    exe = exe or exe_path()
    if not exe:
        raise RuntimeError("register() requires the frozen exe")
    wr = _winreg()
    root = wr.HKEY_CURRENT_USER
    cls = r"Software\Classes"
    cmd = _command_for(exe)
    # ProgID
    _set_default(root, r"%s\%s" % (cls, PROGID), "PowerUp Toolbox")
    _set_default(root, r"%s\%s\DefaultIcon" % (cls, PROGID), '"%s",0' % exe)
    _set_default(root, r"%s\%s\shell\open" % (cls, PROGID), "用 PowerUp Toolbox 開啟")
    _set_default(root, r"%s\%s\shell\open\command" % (cls, PROGID), cmd)
    # Applications\PowerUpToolbox.exe (shows in "Open with" list)
    app = r"%s\Applications\%s" % (cls, EXE_NAME)
    _set_default(root, app + r"\shell\open\command", cmd)
    with wr.CreateKeyEx(root, app, 0, wr.KEY_WRITE) as k:
        wr.SetValueEx(k, "FriendlyAppName", 0, wr.REG_SZ, "PowerUp Toolbox")
    with wr.CreateKeyEx(root, app + r"\SupportedTypes", 0, wr.KEY_WRITE) as k:
        for ext in SUPPORTED_EXTS:
            wr.SetValueEx(k, ext, 0, wr.REG_SZ, "")
    # Direct context-menu entries (shown under "Show more options" on Win11)
    icon = '"%s",0' % exe
    for ext in SUPPORTED_EXTS:
        _write_verb(root, r"%s\SystemFileAssociations\%s\shell\%s" % (cls, ext, VERB), icon, '"%s" "%%1"' % exe)
    _write_verb(root, r"%s\Directory\shell\%s" % (cls, VERB), icon, '"%s" "%%1"' % exe)
    _write_verb(root, r"%s\Directory\Background\shell\%s" % (cls, VERB), icon, '"%s" "%%V"' % exe)
    with wr.CreateKeyEx(root, r"%s\%s" % (cls, PROGID), 0, wr.KEY_WRITE) as k:
        wr.SetValueEx(k, "RegVersion", 0, wr.REG_DWORD, REG_VERSION)
    # OpenWithProgids per extension
    for ext in SUPPORTED_EXTS:
        with wr.CreateKeyEx(root, r"%s\%s\OpenWithProgids" % (cls, ext), 0, wr.KEY_WRITE) as k:
            wr.SetValueEx(k, PROGID, 0, wr.REG_NONE, b"")
    _notify()
    return register_status()


def unregister():
    wr = _winreg()
    root = wr.HKEY_CURRENT_USER
    cls = r"Software\Classes"
    for ext in SUPPORTED_EXTS:
        sub = r"%s\%s\OpenWithProgids" % (cls, ext)
        try:
            with wr.OpenKey(root, sub, 0, wr.KEY_READ | wr.KEY_WRITE) as k:
                try:
                    wr.DeleteValue(k, PROGID)
                except FileNotFoundError:
                    pass
                empty = wr.QueryInfoKey(k)[1] == 0 and wr.QueryInfoKey(k)[0] == 0
            if empty:  # remove only keys we would have created
                wr.DeleteKey(root, sub)
                try:
                    with wr.OpenKey(root, r"%s\%s" % (cls, ext)) as ek:
                        ext_empty = wr.QueryInfoKey(ek)[0] == 0 and wr.QueryInfoKey(ek)[1] == 0
                    if ext_empty:
                        wr.DeleteKey(root, r"%s\%s" % (cls, ext))
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
    for ext in SUPPORTED_EXTS:
        base = r"%s\SystemFileAssociations\%s" % (cls, ext)
        _delete_tree(root, base + "\\shell\\" + VERB)
        for sub in (base + r"\shell", base):
            if not _prune_empty(root, sub):
                break
    for base in (r"%s\Directory\shell" % cls, r"%s\Directory\Background\shell" % cls):
        _delete_tree(root, base + "\\" + VERB)
        p = base
        while p.count("\\") >= 2 and _prune_empty(root, p):   # never prune Software\Classes itself
            p = p.rsplit("\\", 1)[0]
    # SystemFileAssociations parent
    _prune_empty(root, r"%s\SystemFileAssociations" % cls)
    _delete_tree(root, r"%s\%s" % (cls, PROGID))
    _delete_tree(root, r"%s\Applications\%s" % (cls, EXE_NAME))
    _notify()
    return register_status()


def _reg_version():
    wr = _winreg()
    try:
        with wr.OpenKey(wr.HKEY_CURRENT_USER, r"Software\Classes\%s" % PROGID) as k:
            return int(wr.QueryValueEx(k, "RegVersion")[0])
    except (OSError, ValueError):
        return 0


def set_autoreg_enabled(enabled):
    mark = os.path.join(data_dir(), NO_AUTOREG_MARK)
    try:
        if enabled:
            if os.path.exists(mark):
                os.remove(mark)
        else:
            os.makedirs(data_dir(), exist_ok=True)
            open(mark, "w").close()
    except OSError:
        pass


def ensure_registered():
    """Silent, UAC-free auto registration at startup. Skipped for test instances and after
    an explicit --unregister (marker file). Re-registers if exe moved or schema is old."""
    if is_test_instance() or not getattr(sys, "frozen", False):
        return False
    try:        # never register an exe that lives in the temp folder (test copies)
        if exe_in_temp(sys.executable):
            return False
    except Exception:
        pass
    if os.path.exists(os.path.join(data_dir(), NO_AUTOREG_MARK)):
        return False
    try:
        st = register_status()
        if st["registered"] and st["path_matches"] and _reg_version() >= REG_VERSION:
            return False
        register()
        return True
    except Exception as e:
        print("auto-register failed: %r" % (e,), flush=True)
        return False


def register_status():
    reg = registered_exe()
    cur = exe_path()
    return {
        "registered": bool(reg),
        "registered_exe": reg,
        "current_exe": cur,
        "path_matches": bool(reg and cur and os.path.normcase(reg) == os.path.normcase(cur)),
    }


def refresh_registration_if_moved():
    """If registered but pointing to another path (exe was moved), re-register."""
    try:
        st = register_status()
        if st["registered"] and st["current_exe"] and not st["path_matches"]:
            register()
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- update
def _http_json(url, timeout=6):
    req = urllib.request.Request(url, headers={
        "User-Agent": "PowerUpToolbox-Win/%s (AutoUpdater)" % current_version(),
        "Accept": "application/json, application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2 * 1024 * 1024).decode("utf-8-sig"))


def _official_manifest_url():
    """https only (plain http is accepted for a loopback test server set through POWERUP_WIN_UPDATE_URL)."""
    url = os.environ.get("POWERUP_WIN_UPDATE_URL") or OFFICIAL_MANIFEST
    u = urllib.parse.urlsplit(url)
    if u.scheme == "https" or (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost")):
        return url
    raise ValueError("update manifest URL must be https")


def _check_official(cur):
    """Read the website manifest. Raises on any problem so the caller can fall back to GitHub Releases.
    The download URL is never taken from the manifest: it is always ASSET_NAME in the manifest's own folder."""
    murl = _official_manifest_url()
    m = _http_json(murl)
    if not isinstance(m, dict):
        raise ValueError("bad manifest")
    latest = str(m.get("version", ""))
    sha = str(m.get("sha256", "")).strip().lower()
    if not re.fullmatch(r"\d+(\.\d+){0,3}", latest) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("bad manifest")
    log = m.get("changelog")
    changelog = [str(x)[:300] for x in log if isinstance(x, str) and x.strip()][:12] if isinstance(log, list) else []
    return {
        "ok": True, "has_update": compare_versions(latest, cur) > 0, "latest_version": latest,
        "release_date": str(m.get("release_date", ""))[:10],
        "changelog": changelog,
        "dmg_url": OFFICIAL_PAGE,                     # frontend's "open download page" button
        "pkg_url": urllib.parse.urljoin(murl, ASSET_NAME) + "?v=" + latest,
        "sha256": sha,
        "repo": "", "branch": "", "current_version": cur, "source": "official_website",
    }


def _find_release():
    api = os.environ.get("POWERUP_WIN_RELEASE_API") or DEFAULT_API
    rel = _http_json(api)
    if str(rel.get("tag_name", "")).startswith(TAG_PREFIX):
        return rel
    # latest is not a Windows release (e.g. a Mac tag): scan the release list
    lst = _http_json(re.sub(r"/latest/?$", "", api) + "?per_page=20")
    for r in lst if isinstance(lst, list) else []:
        if str(r.get("tag_name", "")).startswith(TAG_PREFIX) and not r.get("draft"):
            return r
    return None


def check_update():
    cur = current_version()
    try:
        return _check_official(cur)
    except Exception as e:
        print("official update check failed, falling back to GitHub Releases: %r" % (e,), flush=True)
    base = {"repo": GITHUB_REPO, "branch": "", "current_version": cur, "source": "github-releases"}
    try:
        rel = _find_release()
    except urllib.error.HTTPError as e:
        if e.code == 404:  # no release published yet
            return {"ok": True, "has_update": False, "latest_version": cur, "changelog": [], **base}
        return {"ok": False, "has_update": False, "error": str(e), **base}
    except Exception as e:
        return {"ok": False, "has_update": False, "error": str(e), **base}
    if not rel:
        return {"ok": True, "has_update": False, "latest_version": cur, "changelog": [], **base}
    latest = str(rel["tag_name"])[len(TAG_PREFIX):]
    asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
    has = compare_versions(latest, cur) > 0 and asset is not None
    body = [ln.strip("-* \t") for ln in (rel.get("body") or "").splitlines() if ln.strip()]
    return {
        "ok": True, "has_update": has, "latest_version": latest,
        "release_date": (rel.get("published_at") or "")[:10],
        "changelog": body[:12],
        "dmg_url": rel.get("html_url", ""),          # frontend's "open release page" button
        "pkg_url": asset.get("browser_download_url", "") if asset else "",
        **base,
    }


_UPDATE_LOCK = threading.Lock()
UPDATE_STATE = {"state": "idle", "message": "", "downloaded": 0, "total": 0}


def update_status():
    """Snapshot for GET /api/update-status. state: idle|downloading|verifying|installing|error."""
    return dict(UPDATE_STATE)


def _dir_writable(d):
    try:
        probe = os.path.join(d, ".write_probe_%d" % os.getpid())
        with open(probe, "w") as f:
            f.write("x")
        os.remove(probe)
        return True
    except OSError:
        return False


_UPDATER_PS1 = r'''param([int]$ProcId,[string]$InstallDir,[string]$NewDir,[string]$ExeName,[string]$LogFile)
$ErrorActionPreference = 'Stop'
function Log($m){ Add-Content -LiteralPath $LogFile -Value ("{0} {1}" -f (Get-Date -Format s),$m) }
Log "updater start pid=$ProcId install=$InstallDir new=$NewDir"
try { Wait-Process -Id $ProcId -Timeout 60 -ErrorAction SilentlyContinue } catch {}
$old = "$InstallDir.old-" + (Get-Date -Format yyyyMMddHHmmss)
$renamed = $false
for ($i=0; $i -lt 40 -and -not $renamed; $i++) {
  try { Rename-Item -LiteralPath $InstallDir -NewName (Split-Path $old -Leaf); $renamed = $true }
  catch { Start-Sleep -Milliseconds 500 }
}
if (-not $renamed) { Log "FAILED to rename install dir (still locked); abort, relaunching old"; Start-Process (Join-Path $InstallDir $ExeName); exit 1 }
try {
  Move-Item -LiteralPath $NewDir -Destination $InstallDir
  if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $ExeName))) { throw "new exe missing" }
  Log "swap ok, backup at $old"
} catch {
  Log "swap failed: $_ ; restoring"
  if (Test-Path -LiteralPath $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force -ErrorAction SilentlyContinue }
  Rename-Item -LiteralPath $old -NewName (Split-Path $InstallDir -Leaf)
  Start-Process (Join-Path $InstallDir $ExeName)
  exit 2
}
Start-Process (Join-Path $InstallDir $ExeName)
Log "relaunched"
'''


def _stage_update(url, sha256=None):
    upd = os.path.join(data_dir(), "updates")
    os.makedirs(upd, exist_ok=True)
    zpath = os.path.join(upd, ASSET_NAME)
    req = urllib.request.Request(url, headers={"User-Agent": "PowerUpToolbox-Win/%s" % current_version()})
    UPDATE_STATE.update(state="downloading", message="下載中", downloaded=0, total=0)
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as r, open(zpath, "wb") as out:
        UPDATE_STATE["total"] = int(r.headers.get("Content-Length") or 0)
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
            UPDATE_STATE["downloaded"] += len(chunk)
    UPDATE_STATE.update(state="verifying", message="驗證更新檔")
    if sha256 and digest.hexdigest().lower() != sha256.lower():
        try:
            os.remove(zpath)
        except OSError:
            pass
        raise RuntimeError("zip checksum mismatch")
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        if bad:
            raise RuntimeError("zip corrupted at " + bad)
        names = z.namelist()
        if not any(n.replace("\\", "/").split("/")[-1] == EXE_NAME for n in names):
            raise RuntimeError("zip does not contain " + EXE_NAME)
        ext = os.path.join(upd, "extract")
        shutil.rmtree(ext, ignore_errors=True)
        z.extractall(ext)
    # find folder holding the exe (zip root or one level down)
    new_dir = None
    for root, _dirs, files in os.walk(ext):
        if EXE_NAME in files:
            new_dir = root
            break
    if not new_dir:
        raise RuntimeError("extracted tree lacks " + EXE_NAME)
    return upd, new_dir


def friendly_update_error(e):
    """Traditional-Chinese message with the raw reason in parentheses."""
    import socket
    import errno
    raw = str(e) or e.__class__.__name__
    if isinstance(e, zipfile.BadZipFile):
        return "更新檔損毀或下載不完整（%s），請稍後再試" % raw
    if isinstance(e, urllib.error.HTTPError):
        return "更新伺服器回應錯誤（HTTP %s），請稍後再試" % e.code
    if isinstance(e, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError)):
        reason = getattr(e, "reason", None) or raw
        return "網路連線失敗，無法下載更新（%s），請檢查網路後再試" % reason
    if isinstance(e, PermissionError) or getattr(e, "errno", None) == errno.EACCES:
        return "沒有寫入權限，無法儲存或套用更新（%s），請改用有寫入權限的資料夾或手動更新" % raw
    if isinstance(e, OSError) and getattr(e, "errno", None) == errno.ENOSPC:
        return "磁碟空間不足，無法下載更新（%s）" % raw
    if isinstance(e, RuntimeError) and ("zip" in raw.lower() or "extracted" in raw.lower()):
        return "更新檔內容不正確（%s），請稍後再試或手動下載" % raw
    return "更新失敗（%s），請稍後再試" % raw


def _system_exe(rel):
    import winpath
    return winpath.system_exe(rel)


def _do_update(url, sha256=None):
    try:
        UPDATE_STATE.update(state="downloading", message="下載中")
        upd, new_dir = _stage_update(url, sha256)
        ps1 = os.path.join(upd, "updater.ps1")
        with open(ps1, "w", encoding="utf-8-sig") as f:
            f.write(_UPDATER_PS1)
        UPDATE_STATE.update(state="installing", message="下載完成，即將重新啟動")
        cmd = [_system_exe(r"System32\WindowsPowerShell\v1.0\powershell.exe"), "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
               "-File", ps1, "-ProcId", str(os.getpid()), "-InstallDir", install_dir(),
               "-NewDir", new_dir, "-ExeName", EXE_NAME,
               "-LogFile", os.path.join(log_dir(), "updater.log")]
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP; try to leave any parent job so the
        # updater survives our exit, fall back without breakaway if the job forbids it.
        base = 0x08000000 | 0x00000200
        for extra in (0x01000000, 0):
            try:
                subprocess.Popen(cmd, creationflags=base | extra, close_fds=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                print("updater launched (flags %#x)" % (base | extra), flush=True)
                break
            except OSError as e:
                print("updater launch failed with flags %#x: %r" % (base | extra, e), flush=True)
        else:
            raise RuntimeError("cannot launch updater")
        time.sleep(1.5)
        # parent (bootloader) must exit too so the folder can be renamed
        os._exit(0)
    except Exception as e:
        print("update failed: %r" % (e,), flush=True)
        UPDATE_STATE.update(state="error", message=friendly_update_error(e))
        try:
            _UPDATE_LOCK.release()
        except RuntimeError:
            pass


def perform_update():
    d = install_dir()
    if not d:
        return {"ok": False, "message": "僅免安裝版 exe 支援自動更新"}
    if not _dir_writable(d) or not _dir_writable(os.path.dirname(d)):
        return {"ok": False, "message": "程式目錄不可寫入，無法自動更新，請至官網手動下載"}
    info = check_update()   # re-resolve server-side; never trust a URL from the page
    if not info.get("ok") or not info.get("has_update") or not info.get("pkg_url"):
        return {"ok": False, "message": "目前沒有可用的更新"}
    if not _UPDATE_LOCK.acquire(blocking=False):
        return {"ok": True, "win": True, "message": "更新已在進行中"}
    UPDATE_STATE.update(state="downloading", message="準備下載", downloaded=0, total=0)
    threading.Thread(target=_do_update, args=(info["pkg_url"], info.get("sha256")), daemon=True).start()
    return {"ok": True, "win": True, "message": "已開始下載 v%s，完成後程式會自動關閉並以新版重新啟動。" % info["latest_version"]}


def cleanup_old_backups():
    d = install_dir()
    if not d:
        return
    parent, name = os.path.dirname(d), os.path.basename(d)
    try:
        for n in os.listdir(parent):
            if n.startswith(name + ".old-"):
                shutil.rmtree(os.path.join(parent, n), ignore_errors=True)
    except OSError:
        pass
