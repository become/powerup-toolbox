# -*- coding: utf-8 -*-
"""PowerUp Toolbox - Windows launcher (PyInstaller entry point).

Starts server.py in a background thread on a free port, opens a WebView2 window
(pywebview), enforces single instance and hands
file arguments to an already running instance.

CLI:  PowerUpToolbox.exe [file]   open a file
      PowerUpToolbox.exe --register / --unregister   HKCU "Open with" registration
"""
import ctypes
import json
import os

# ---- earliest hardening (before anything is imported, started or loaded) -------------------------------------------
# a bare program name must never resolve through the current directory, and DLLs must not be searched there either
os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
try:
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _k.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    _k.SetDllDirectoryW("")                                   # drop the current directory from the DLL search path
    _k.SetDefaultDllDirectories.argtypes = [ctypes.c_ulong]
    _k.SetDefaultDllDirectories(0x00001000)                   # LOAD_LIBRARY_SEARCH_DEFAULT_DIRS (app dir, System32, AddDllDirectory dirs)
except Exception:
    pass
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import hashlib
import secrets
import win_support as _early_ws
import winpath

# Safety net for test builds: an exe that lives under %TEMP% must never run against the real user data dir /
# registry. Without POWERUP_DATA_DIR it refuses to start (before anything is created or registered).
if getattr(sys, "frozen", False) and not os.environ.get("POWERUP_DATA_DIR") and _early_ws.exe_in_temp(sys.executable):
    try:   # e.g. double-clicked inside a zip viewer: Explorer extracted it to %TEMP%
        ctypes.windll.user32.MessageBoxW(None, "請先把 PowerUp Toolbox 解壓縮到固定的資料夾（例如 文件 或 桌面），再執行 PowerUpToolbox.exe。\n"
                                         "Please extract the folder to a permanent location first.", "PowerUp Toolbox", 0x30)
    except Exception:
        pass
    sys.exit(3)

# POWERUP_DATA_DIR = isolated test instance (own data dir, own mutex, own window title, no auto-register)
IS_TEST = bool(os.environ.get("POWERUP_DATA_DIR"))
APP_TITLE = "PowerUp Toolbox" + (" [test]" if IS_TEST else "")
PREFERRED_PORT = int(os.environ.get("MACTOOLBOX_PORT", "18765"))

DATA_DIR = os.path.abspath(os.environ.get("POWERUP_DATA_DIR") or
                           os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PowerUpToolbox"))
LOG_DIR = os.path.join(DATA_DIR, "logs")
INSTANCE_FILE = os.path.join(DATA_DIR, "instance.json")
os.makedirs(LOG_DIR, exist_ok=True)

# The working directory is whatever folder Explorer started us in (often the folder of the opened file). Make file
# arguments absolute against THAT directory first, then leave it: nothing we start may ever see a planted program there.
_sys_argv = [sys.argv[0]]
for _a in sys.argv[1:]:
    try:
        _sys_argv.append(_a if _a.startswith("--") else os.path.abspath(_a))
    except Exception:
        _sys_argv.append(_a)
sys.argv = _sys_argv
try:
    os.chdir(DATA_DIR)
except OSError:
    pass


def _setup_logging():
    """windowed exe has sys.stdout/stderr == None; route everything to a log file."""
    path = os.path.join(LOG_DIR, "launcher.log")
    try:
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".1")
    except OSError:
        pass
    f = open(path, "a", encoding="utf-8", buffering=1)
    sys.stdout = f
    sys.stderr = f
    print("\n==== start %s pid=%d argv=%r" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), sys.argv), flush=True)


_setup_logging()

import win_support  # noqa: E402  (bundled next to launcher)


def msgbox(text, title=APP_TITLE):
    try:
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x40)
    except Exception:
        pass


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ------------------------------------------------------------ args
def parse_args(argv):
    flags, files = set(), []
    for a in argv[1:]:
        if a.startswith("--"):
            flags.add(a.lower())
        else:
            n = winpath.normalize(a)
            if n is None or not winpath.is_allowed(n):
                log("argument refused (not a local drive-letter path):", repr(a)[:60])
                continue                      # whitelist: nothing else is ever touched by the launcher (no SMB connection)
            if os.path.lexists(n):            # lexists: does not follow a symlink / junction
                files.append(n)
    return flags, (files[0] if files else None)


# ------------------------------------------------------------ single instance
def _http(port, path, data=None, timeout=2.0, token=None):
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["X-PowerUp-Token"] = token   # per-run secret from instance.json (user-private data dir)
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def restrict_to_current_user(path):
    """icacls: drop inheritance, grant only the current user full control."""
    try:
        user = os.environ.get("USERNAME") or ""
        dom = os.environ.get("USERDOMAIN") or ""
        who = (dom + "\\" + user) if dom and user else user
        r = subprocess.run([winpath.system_exe(r"System32\icacls.exe"), path, "/inheritance:r", "/grant:r", who + ":F"],
                           capture_output=True, text=True, creationflags=0x08000000, timeout=15)
        log("icacls", os.path.basename(path), "rc=%s" % r.returncode)
    except Exception as e:
        log("icacls failed:", repr(e))


def acquire_mutex():
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    tag = hashlib.md5(os.path.normcase(DATA_DIR).encode("utf-8")).hexdigest()[:12]  # one mutex per data dir
    h = k32.CreateMutexW(None, False, "Local\\PowerUpToolbox.SingleInstance." + tag)
    already = ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    return h, already


def bring_to_front():
    try:
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, APP_TITLE)
        if hwnd:
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
            u.SetForegroundWindow(hwnd)
    except Exception:
        pass


def hand_off_to_running(file_path):
    """Second instance: pass file to the live instance, raise its window. Returns True if handled."""
    deadline = time.time() + 15
    while time.time() < deadline:
        try:
            with open(INSTANCE_FILE, "r", encoding="utf-8") as f:
                inst = json.load(f)
            port, token = inst["port"], inst.get("token")
            _http(port, "/api/pending-open", token=token)  # liveness check
            if file_path:
                _http(port, "/api/open-target", {"path": file_path}, token=token)
                log("handed file to running instance on port", port, file_path)
            bring_to_front()
            return True
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 415):
                # the running instance understood us and refused (e.g. an unsafe / UNC path): do not retry and do
                # NOT start a second instance
                log("running instance refused the hand-off, HTTP", e.code)
                return True
            time.sleep(0.4)
        except Exception:
            time.sleep(0.4)  # first instance may still be starting
    return False


# ------------------------------------------------------------ server
def start_server(server_mod):
    class ExclusiveServer(server_mod.ReusableThreadingHTTPServer):
        # On Windows SO_REUSEADDR lets a second socket steal a bound port; turn it off.
        allow_reuse_address = False

    srv = None
    for port in [PREFERRED_PORT] + list(range(PREFERRED_PORT + 1, PREFERRED_PORT + 40)) + [0]:
        try:
            srv = ExclusiveServer(("127.0.0.1", port), server_mod.ToolboxHandler)
            break
        except OSError as e:
            log("port", port, "unavailable:", e)
    if srv is None:
        raise RuntimeError("no free port")
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, name="toolbox-http", daemon=True).start()
    return srv, port


def wait_ready(port, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


# ------------------------------------------------------------ WebView2 navigation lock
# pywebview only intercepts new windows; it does NOT restrict top-level navigation. A link inside the built-in PDF
# viewer (or a script) could therefore navigate the app window to an external site, whose JS could then try
# window.open("search-ms:...") etc. The lock lives here in the shell: the main window and every frame may only
# show our own loopback origin; external http(s) links are handed to the system browser after strict validation;
# every other scheme is refused and never passed to ShellExecute.
ALLOWED_PORT = [0]
TEST_EXTERNAL_LOG = os.environ.get("POWERUP_TEST_EXTERNAL_LOG") if IS_TEST else None   # test only: record, do not open


def _loopback_parts(uri):
    import urllib.parse
    try:
        p = urllib.parse.urlsplit(uri)
        return p if (p.scheme == "http" and p.hostname == "127.0.0.1" and p.port == ALLOWED_PORT[0]
                     and not p.username and not any(ord(c) < 33 for c in uri)) else None
    except ValueError:
        return None


def nav_allowed(uri):
    """TOP-LEVEL navigation: only the app page itself ("/" on our loopback port, query / fragment allowed) or about:blank.
    API URLs (e.g. a PDF link that resolved against the page as /api///host/x) are not pages and are refused too."""
    u = str(uri or "")
    if u == "about:blank":
        return True
    p = _loopback_parts(u) if ALLOWED_PORT[0] else None
    return bool(p and p.path == "/")


def frame_allowed(uri):
    """Sub-frames: our own origin (any path: /api/pdf-raw ...), blob:/about:srcdoc created by the page itself."""
    u = str(uri or "")
    if u == "about:blank" or u.startswith("about:srcdoc"):
        return True
    if ALLOWED_PORT[0] and u.startswith("blob:http://127.0.0.1:%d/" % ALLOWED_PORT[0]):
        return True
    return bool(ALLOWED_PORT[0] and _loopback_parts(u))


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]


REAL_INPUT_WINDOW_MS = 3000


def real_input_recent():
    """True when the USER touched the mouse / keyboard in the last few seconds (OS-level GetLastInputInfo). WebView2's
    IsUserInitiated flag is not enough: it is also set for the first window.open after a page load, and page script can
    obtain it by itself. A web page cannot produce OS-level input, so it can never satisfy this check alone."""
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return False
        age = (ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
        return age < REAL_INPUT_WINDOW_MS
    except Exception:
        return False


_last_ext = [0.0]


def _throttle_ok():
    now = time.time()
    if now - _last_ext[0] < 8:
        return False
    _last_ext[0] = now
    return True


def _all_headers(args):
    try:
        return {str(kv.Key): str(kv.Value)[:60] for kv in args.RequestHeaders}
    except Exception as e:
        return {"error": repr(e)}


def _request_header(args, name):
    try:
        h = args.RequestHeaders
        return str(h.GetHeader(name)) if h.Contains(name) else ""
    except Exception:
        return ""


def external_http_url(uri):
    """Return the URL when it is a plain http(s) URL safe to hand to the default browser, else None."""
    import urllib.parse
    u = str(uri or "")
    if not u or len(u) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in u):
        return None
    try:
        p = urllib.parse.urlsplit(u)
    except ValueError:
        return None
    if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password:
        return None
    if p.hostname in ("127.0.0.1", "localhost", "::1"):
        return None          # the app's own origin / local services are never opened through the "external link" path
    return u


def open_external(uri):
    url = external_http_url(uri)
    if url is None:
        log("external link REFUSED (scheme/format):", repr(str(uri))[:80])
        return False
    if TEST_EXTERNAL_LOG:
        with open(TEST_EXTERNAL_LOG, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        log("external link recorded (test mode):", url[:80])
        return True
    import webbrowser
    log("external link -> system browser:", url[:80])
    webbrowser.open(url)
    return True


def install_navigation_lock():
    from webview.platforms import edgechromium as ec
    orig_ready = ec.EdgeChrome.on_webview_ready

    def on_nav(sender, args):
        try:
            uri = str(args.Uri)
            if not nav_allowed(uri):
                args.Cancel = True
                log("navigation BLOCKED (top):", uri[:100])
                # Only a user gesture, or a link click inside the built-in PDF viewer (WebView2 does not flag those as
                # user-initiated; recognised by the page's own "user clicked inside the PDF frame" signal, throttled) may
                # hand an http(s) URL to the system browser. Any other script-driven top navigation is cancelled and dropped.
                srv_mod = sys.modules.get("server")
                from_pdf = bool(srv_mod) and (time.time() - srv_mod.PDF_INTERACT["t"]) < 3.0
                log("  nav details: user_initiated=%s from_pdf_viewer=%s" % (getattr(args, "IsUserInitiated", None), from_pdf))
                if external_http_url(uri) is None:
                    log("external link REFUSED (scheme/format):", repr(uri)[:80])
                elif not real_input_recent():
                    log("external hand-off DROPPED (no real mouse / keyboard input in the last %d ms): %s" % (REAL_INPUT_WINDOW_MS, uri[:80]))
                elif getattr(args, "IsUserInitiated", False) or (from_pdf and _throttle_ok()):
                    open_external(uri)
                else:
                    log("external hand-off DROPPED (no user gesture, not from the PDF viewer):", uri[:80])
        except Exception as e:
            args.Cancel = True
            log("navigation handler error, cancelled:", repr(e))

    def on_frame_nav(sender, args):
        try:
            uri = str(args.Uri)
            if not frame_allowed(uri):
                args.Cancel = True
                log("navigation BLOCKED (frame):", uri[:100])
        except Exception as e:
            args.Cancel = True
            log("frame navigation handler error, cancelled:", repr(e))

    def on_ext_scheme(sender, args):
        try:
            args.Cancel = True
            log("external URI scheme launch CANCELLED:", str(args.Uri)[:100])
        except Exception as e:
            log("ext scheme handler error:", repr(e))

    def on_new_window(self_, sender, args):
        args.set_Handled(True)                   # never let WebView2 create a window of its own
        uri = str(args.get_Uri())
        if getattr(args, "IsUserInitiated", False) and real_input_recent():
            open_external(uri)
        else:
            log("window.open dropped (no user gesture, or no real mouse / keyboard input in the last %d ms): %s" % (REAL_INPUT_WINDOW_MS, uri[:80]))

    def ready(self, sender, args):
        if not args.IsSuccess:
            die("WebView2 initialisation failed: %s" % str(args.InitializationException)[:120])
        if args.IsSuccess:
            core = sender.CoreWebView2
            # prove (before anything is loaded) that the proxy sandbox really reached the browser process
            cmd = process_cmdline(int(core.BrowserProcessId))
            if cmd is None or WEBVIEW_PROXY_ARG not in cmd:
                die("proxy sandbox is not active in the WebView2 process (cmdline %s)" % ("unreadable" if cmd is None else "lacks the switch"))
            core.NavigationStarting += on_nav
            core.FrameNavigationStarting += on_frame_nav
            try:      # custom-scheme links (search-ms:, ms-msdt:, ...): never launch an external handler, never prompt
                core.LaunchingExternalUriScheme += on_ext_scheme
            except Exception as e:
                log("LaunchingExternalUriScheme not available:", repr(e))
        orig_ready(self, sender, args)
        log("renderer verified: edgechromium, navigation lock registered, proxy switch present in the browser command line")
        RENDERER_READY.set()

    ec.EdgeChrome.on_webview_ready = ready
    ec.EdgeChrome.on_new_window_request = on_new_window
    log("navigation lock installed")


# ------------------------------------------------------------ window
WEBVIEW2_HELP = ("PowerUp Toolbox 需要 Microsoft Edge WebView2 Runtime 才能安全地顯示畫面。\n"
                 "請先安裝（免費）：https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                 "PowerUp Toolbox requires the Microsoft Edge WebView2 Runtime. Install it from the address above.")
RENDERER_READY = threading.Event()


def die(reason, show=WEBVIEW2_HELP):
    """Fail closed: there is NO fallback renderer (neither MSHTML nor a plain msedge --app window): those would run
    without the navigation lock and the proxy sandbox."""
    log("FAIL CLOSED:", reason)
    try:
        os.remove(INSTANCE_FILE)
    except OSError:
        pass
    if IS_TEST:
        log("(test instance: message box suppressed) " + show.split(chr(10))[0])     # a modal box would block the automated test
    else:
        msgbox(show + "\n\n(" + reason + ")")
    sys.stdout.flush()
    os._exit(5)


def process_cmdline(pid):
    """Command line of another process of this user (PEB walk, x64): used to prove the proxy switch really reached the
    WebView2 browser process."""
    k32, nt = ctypes.windll.kernel32, ctypes.windll.ntdll
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    h = k32.OpenProcess(0x0400 | 0x0010, 0, pid)       # QUERY_INFORMATION | VM_READ
    if not h:
        return None
    try:
        class PBI(ctypes.Structure):
            _fields_ = [("Exit", ctypes.c_void_p), ("Peb", ctypes.c_void_p), ("Aff", ctypes.c_void_p),
                        ("Prio", ctypes.c_void_p), ("Pid", ctypes.c_void_p), ("Parent", ctypes.c_void_p)]
        pbi = PBI()
        n = ctypes.c_ulong(0)
        nt.NtQueryInformationProcess.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        if nt.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(n)) != 0 or not pbi.Peb:
            return None

        def rd(addr, size):
            buf = ctypes.create_string_buffer(size)
            got = ctypes.c_size_t(0)
            if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(got)) or got.value != size:
                raise OSError("ReadProcessMemory")
            return buf.raw
        params = int.from_bytes(rd(pbi.Peb + 0x20, 8), "little")           # PEB.ProcessParameters
        length = int.from_bytes(rd(params + 0x70, 2), "little")            # CommandLine.Length
        bufp = int.from_bytes(rd(params + 0x78, 8), "little")              # CommandLine.Buffer
        return rd(bufp, length).decode("utf-16-le", "replace")
    except Exception:
        return None
    finally:
        k32.CloseHandle(h)


WEBVIEW_PROXY_ARG = "--proxy-server=http://127.0.0.1:1"


class _BrowserArgs(object):
    """Formats into pywebview's ' --remote-debugging-port={value}' so extra Chromium switches can follow it.
    Without a test port the first switch is an empty '--remote-debugging-port=' (no debugging endpoint is opened)."""

    def __init__(self, text):
        self.text = text

    def __format__(self, spec):
        return self.text

    def __str__(self):
        return self.text


def run_window(url):
    # never let the environment pick another renderer (PYWEBVIEW_GUI=mshtml/qt/cef, KDE_FULL_SESSION -> qt)
    for var in ("PYWEBVIEW_GUI", "KDE_FULL_SESSION"):
        if os.environ.pop(var, None) is not None:
            log("ignored environment variable", var)
    gui = "edgechromium"
    if IS_TEST and os.environ.get("POWERUP_TEST_GUI"):      # test hook only: prove that a non-Chromium renderer is refused
        gui = os.environ["POWERUP_TEST_GUI"]
    try:
        import webview
        install_navigation_lock()
        # Network sandbox for the WebView: every non-loopback request is sent to a dead local proxy, so the app window
        # can never open a socket to a remote host (not even Chromium's speculative preconnect for a link the navigation
        # lock then cancels). Loopback (our own server) bypasses the proxy implicitly. pywebview exposes no switch for
        # extra browser arguments (and WebView2 ignores WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS when the app sets its own),
        # but it formats settings["REMOTE_DEBUGGING_PORT"] into the argument string: _BrowserArgs rides on that.
        webview.settings["ALLOW_FILE_URLS"] = False    # the app never needs file:// pages (no --allow-file-access-from-files)
        dbg = ""
        if IS_TEST and os.environ.get("POWERUP_DEBUG_PORT"):  # test-only hook (needs POWERUP_DATA_DIR too)
            dbg = str(int(os.environ["POWERUP_DEBUG_PORT"]))
        webview.settings["REMOTE_DEBUGGING_PORT"] = _BrowserArgs(dbg + " " + WEBVIEW_PROXY_ARG)
        webview.create_window(APP_TITLE, url, width=1360, height=860, min_size=(900, 600))

        def verify_renderer():
            # runs on a helper thread once the GUI loop is up
            import webview.platforms.winforms as wf
            if getattr(wf, "renderer", None) != "edgechromium":
                die("renderer is %r, not edgechromium" % getattr(wf, "renderer", None))
            if not RENDERER_READY.wait(30):
                die("WebView2 did not become ready (navigation lock / proxy not verified within 30 s)")

        webview.start(verify_renderer, gui=gui, storage_path=os.path.join(DATA_DIR, "webview"), private_mode=False)
        log("webview closed")
    except Exception as e:
        import traceback
        log("pywebview failed:", repr(e))
        traceback.print_exc()
        die("pywebview failed: %r" % (e,))


# ------------------------------------------------------------ main
def main():
    flags, file_path = parse_args(sys.argv)

    if "--register" in flags or "--unregister" in flags:
        quiet = "--quiet" in flags
        try:
            if "--register" in flags:
                st = win_support.register()
                win_support.set_autoreg_enabled(True)
                msg = "已註冊為「開啟檔案」程式（僅目前使用者）。\n路徑：%s" % st["registered_exe"]
            else:
                win_support.unregister()
                win_support.set_autoreg_enabled(False)  # don't silently re-register at next start
                msg = "已取消註冊。"
            log(msg)
            if not quiet:
                msgbox(msg)
            return 0
        except Exception as e:
            log("register error", repr(e))
            if not quiet:
                msgbox("操作失敗：%s" % e)
            return 1

    mutex, already = acquire_mutex()
    if already:
        log("second instance")
        if hand_off_to_running(file_path):
            return 0
        log("could not reach first instance; starting anyway")

    os.environ.setdefault("MACTOOLBOX_PORT", str(PREFERRED_PORT))
    import server  # frozen: bundled; sets up WIN_DATA_DIR paths and creates dirs

    if file_path:
        server.LAST_OPEN_REQUEST.update(id=1, path=file_path,
                                        type=server.detect_file_type(file_path),
                                        timestamp=time.time())

    win_support.cleanup_old_backups()
    win_support.ensure_registered()

    srv, port = start_server(server)
    if not wait_ready(port):
        msgbox("伺服器啟動失敗，請查看 %s" % LOG_DIR)
        return 1
    # instance.json carries the per-run API token: create it empty, restrict it to the current user only
    # (remove inherited ACEs), and only then write the secret into it.
    open(INSTANCE_FILE, "w").close()
    restrict_to_current_user(INSTANCE_FILE)
    with open(INSTANCE_FILE, "w", encoding="utf-8") as f:
        json.dump({"port": port, "pid": os.getpid(), "token": server.SERVER_TOKEN}, f)
    log("server up on port", port)

    # one-time nonce: only a "/" request carrying it receives the API token cookie (see server.py)
    nonce = secrets.token_urlsafe(16)
    server.register_index_nonce(nonce)
    extra = {}
    if IS_TEST:        # test instances only: let the test harness open the page in a normal browser as well
        test_nonce = secrets.token_urlsafe(16)
        server.register_index_nonce(test_nonce)
        extra["test_nonce"] = test_nonce
    if extra:
        try:
            inst = json.load(open(INSTANCE_FILE, encoding="utf-8"))
            inst.update(extra)
            json.dump(inst, open(INSTANCE_FILE, "w", encoding="utf-8"))
        except Exception as e:
            log("could not store test nonce:", repr(e))
    ALLOWED_PORT[0] = port
    run_window("http://127.0.0.1:%d/?k=%s" % (port, nonce))

    try:
        srv.shutdown()
        os.remove(INSTANCE_FILE)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        msgbox("發生未預期錯誤，詳見 %s" % LOG_DIR)
        code = 1
    sys.stdout.flush()
    os._exit(code or 0)
