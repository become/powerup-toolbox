#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerUp Toolbox - Real Local Photo Albums Module
Scans real local photos from Downloads, Desktop, Pictures, macOS Photos Library,
and manages custom albums inside ~/Pictures/PowerUp_Albums.
Pure real files on disk — zero mock/dummy data.
"""

import os
import sys
import time
import shutil
import subprocess
import urllib.parse

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".svg"
}

IS_WINDOWS = sys.platform == "win32"

# Set by server.py after import (this module must not import server):
#   CAPTURES_DIR  - the app's real captures folder (differs per platform / data dir)
#   ENTRY_FILTER  - callable(full_path) -> True when a directory entry must be skipped
#                   (Windows: symlinks / junctions that leave the path whitelist, so they are never stat'ed)
CAPTURES_DIR = None
ENTRY_FILTER = None


def _entry_skipped(full_p):
    if ENTRY_FILTER is None:
        return False
    try:
        return bool(ENTRY_FILTER(full_p))
    except Exception:
        return True


def _user_dir(*names):
    """First existing ~/<name>; on Windows also looks in OneDrive (known-folder redirection). Returns a native path."""
    home = os.path.expanduser("~")
    bases = [home]
    if IS_WINDOWS:
        for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
            v = os.environ.get(var)
            if v and v not in bases:
                bases.append(v)
    for base in bases:
        for name in names:
            p = os.path.normpath(os.path.join(base, name))
            if os.path.isdir(p):
                return p
    return os.path.normpath(os.path.join(home, names[0]))


def get_albums_root():
    """Base directory for user custom albums.
    Windows with POWERUP_DATA_DIR set (portable data dir / tests): <data dir>/albums, never the real Pictures folder."""
    data_dir = os.environ.get("POWERUP_DATA_DIR") if IS_WINDOWS else None
    if data_dir:
        d = os.path.join(data_dir, "albums")
    elif IS_WINDOWS:
        d = os.path.join(_user_dir("Pictures", "圖片"), "PowerUp_Albums")
    else:
        d = os.path.expanduser("~/Pictures/PowerUp_Albums")
    os.makedirs(d, exist_ok=True)
    return d


def is_known_album_path(path):
    """True when path is one of the albums returned by list_all_local_albums() (confines write destinations)."""
    try:
        p = os.path.normcase(os.path.abspath(path))
        return any(os.path.normcase(os.path.abspath(a["path"])) == p for a in list_all_local_albums())
    except Exception:
        return False


def _count_images(folder):
    try:
        return sum(1 for f in os.listdir(folder)
                   if not f.startswith(".") and os.path.splitext(f)[1].lower() in IMAGE_EXTS)
    except OSError:
        return 0

def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def scan_folder_photos(folder_path, recursive=False, limit=200):
    """Scan real photo files in a folder."""
    if not os.path.isdir(folder_path):
        return []
    photos = []
    try:
        if recursive:
            for root, dirs, files in os.walk(folder_path):
                for f in files:
                    if f.startswith("."):
                        continue
                    ext = os.path.splitext(f)[1].lower()
                    if ext in IMAGE_EXTS:
                        full_p = os.path.join(root, f)
                        if _entry_skipped(full_p):
                            continue
                        try:
                            st = os.stat(full_p)
                            photos.append({
                                "id": f"p_{hash(full_p) & 0xffffffff:x}",
                                "filename": f,
                                "path": full_p,
                                "size": st.st_size,
                                "size_fmt": format_size(st.st_size),
                                "mtime": st.st_mtime,
                                "mtime_fmt": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                                "thumb_url": f"/api/image?path={urllib.parse.quote(full_p)}&thumb=1",
                                "raw_url": f"/api/image?path={urllib.parse.quote(full_p)}",
                                "is_local": True
                            })
                            if len(photos) >= limit:
                                break
                        except Exception:
                            pass
                if len(photos) >= limit:
                    break
        else:
            for f in sorted(os.listdir(folder_path)):
                if f.startswith("."):
                    continue
                ext = os.path.splitext(f)[1].lower()
                if ext in IMAGE_EXTS:
                    full_p = os.path.join(folder_path, f)
                    if _entry_skipped(full_p):
                        continue
                    if os.path.isfile(full_p):
                        try:
                            st = os.stat(full_p)
                            photos.append({
                                "id": f"p_{hash(full_p) & 0xffffffff:x}",
                                "filename": f,
                                "path": full_p,
                                "size": st.st_size,
                                "size_fmt": format_size(st.st_size),
                                "mtime": st.st_mtime,
                                "mtime_fmt": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                                "thumb_url": f"/api/image?path={urllib.parse.quote(full_p)}&thumb=1",
                                "raw_url": f"/api/image?path={urllib.parse.quote(full_p)}",
                                "is_local": True
                            })
                            if len(photos) >= limit:
                                break
                        except Exception:
                            pass
    except Exception as e:
        print(f"[LocalAlbums] Scan folder error {folder_path}: {e}")
    
    # Sort latest modified first
    photos.sort(key=lambda x: x["mtime"], reverse=True)
    return photos

def list_all_local_albums():
    """Discover real albums across system folders and custom albums."""
    albums = []
    
    # 1. Custom User Albums in ~/Pictures/PowerUp_Albums
    root = get_albums_root()
    try:
        for entry in sorted(os.listdir(root)):
            p = os.path.join(root, entry)
            if _entry_skipped(p):
                continue
            if os.path.isdir(p) and not entry.startswith("."):
                photos = scan_folder_photos(p, recursive=False, limit=1)
                count = sum(1 for f in os.listdir(p) if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
                cover_url = photos[0]["thumb_url"] if photos else ""
                albums.append({
                    "id": f"custom_{entry}",
                    "name": f"🗂️ {entry}",
                    "path": p,
                    "type": "custom",
                    "count": count,
                    "cover_url": cover_url,
                    "is_custom": True,
                    "description": "自訂本機相簿資料夾"
                })
    except Exception:
        pass

    # 2. Downloads Photos (~/Downloads)
    downloads = _user_dir("Downloads")
    if os.path.isdir(downloads):
        d_photos = scan_folder_photos(downloads, recursive=False, limit=1)
        d_count = sum(1 for f in os.listdir(downloads) if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
        if d_count > 0:
            albums.append({
                "id": "system_downloads",
                "name": "📥 下載照片庫",
                "path": downloads,
                "type": "system",
                "count": d_count,
                "cover_url": d_photos[0]["thumb_url"] if d_photos else "",
                "is_custom": False,
                "description": "下載項目中的圖片與照片"
            })

    # 3. Desktop Photos (~/Desktop)
    desktop = _user_dir("Desktop", "桌面")
    if os.path.isdir(desktop):
        dt_photos = scan_folder_photos(desktop, recursive=False, limit=1)
        dt_count = sum(1 for f in os.listdir(desktop) if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
        if dt_count > 0:
            albums.append({
                "id": "system_desktop",
                "name": "💻 桌面照片相簿",
                "path": desktop,
                "type": "system",
                "count": dt_count,
                "cover_url": dt_photos[0]["thumb_url"] if dt_photos else "",
                "is_custom": False,
                "description": "桌面即時照片與視覺圖檔"
            })

    # 3b. Windows: the Pictures folder itself and its Screenshots subfolder (there is no Photos library)
    if IS_WINDOWS:
        pictures = _user_dir("Pictures", "圖片")
        for aid, name, folder, desc in (
            ("system_pictures", "🖼️ 圖片資料夾", pictures, "「圖片」資料夾中的照片"),
            ("system_screenshots", "✂️ 螢幕擷取畫面", os.path.join(pictures, "Screenshots"), "Windows 螢幕擷取畫面"),
        ):
            if os.path.isdir(folder) and not _entry_skipped(folder):
                cnt = _count_images(folder)
                if cnt > 0:
                    cover = scan_folder_photos(folder, recursive=False, limit=1)
                    albums.append({
                        "id": aid,
                        "name": name,
                        "path": folder,
                        "type": "system",
                        "count": cnt,
                        "cover_url": cover[0]["thumb_url"] if cover else "",
                        "is_custom": False,
                        "description": desc
                    })

    # 4. Captures (the app's captures folder; ~/Pictures/MacToolbox_Captures in the Mac app bundle)
    captures = CAPTURES_DIR or os.path.expanduser("~/Pictures/MacToolbox_Captures")
    if os.path.isdir(captures):
        cap_photos = scan_folder_photos(captures, recursive=False, limit=1)
        cap_count = sum(1 for f in os.listdir(captures) if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
        if cap_count > 0:
            albums.append({
                "id": "system_captures",
                "name": "📸 截圖與快照匣",
                "path": captures,
                "type": "system",
                "count": cap_count,
                "cover_url": cap_photos[0]["thumb_url"] if cap_photos else "",
                "is_custom": False,
                "description": "PowerUp Toolbox 擷取與剪貼簿存檔"
            })

    # 5. macOS System Photos Library (~/Pictures/Photos Library.photoslibrary)
    photos_lib_dir = os.path.expanduser("~/Pictures/Photos Library.photoslibrary")
    if os.path.isdir(photos_lib_dir):
        derivatives = os.path.join(photos_lib_dir, "resources", "derivatives")
        if os.path.isdir(derivatives):
            pl_photos = scan_folder_photos(derivatives, recursive=True, limit=1)
            # Fast count estimate
            pl_count = 0
            for r, d, files in os.walk(derivatives):
                pl_count += sum(1 for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTS)
            if pl_count > 0:
                albums.append({
                    "id": "system_macos_photos",
                    "name": "🍎 macOS 系統相簿庫",
                    "path": derivatives,
                    "type": "system_photoslibrary",
                    "count": pl_count,
                    "cover_url": pl_photos[0]["thumb_url"] if pl_photos else "",
                    "is_custom": False,
                    "description": "Apple Photos 圖庫同步快照與相片"
                })

    return albums

def get_album_photos(album_id, album_path=None):
    """Retrieve full photo list for the given album."""
    target_path = album_path
    recursive = False

    if not target_path or not os.path.exists(target_path):
        # Resolve by ID
        albums = list_all_local_albums()
        for a in albums:
            if a["id"] == album_id:
                target_path = a["path"]
                if a["type"] == "system_photoslibrary":
                    recursive = True
                break
    
    if not target_path or not os.path.exists(target_path):
        return {"ok": False, "error": f"找不到相簿目錄: {album_id}"}

    if "Photos Library.photoslibrary" in target_path:
        recursive = True

    photos = scan_folder_photos(target_path, recursive=recursive, limit=300)
    return {
        "ok": True,
        "album_id": album_id,
        "path": target_path,
        "total": len(photos),
        "photos": photos
    }

def create_album(title):
    """Create a real album directory under ~/Pictures/PowerUp_Albums."""
    if not isinstance(title, str):
        return {"ok": False, "error": "相簿名稱不可為空"}
    title = title.strip()
    if not title:
        return {"ok": False, "error": "相簿名稱不可為空"}

    # Sanitize title: no path separators / control / bidi-override characters, no trailing dots or spaces
    # (Windows silently drops them), no DOS device names (CON, NUL, COM1 ... cannot be opened or deleted normally).
    bidi = "\u200b\u200c\u200d\u2060\ufeff\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"   # zero-width + bidi controls (kept as escapes: they are invisible)
    safe_title = "".join(c for c in title if c not in r'\/:*?"<>|' and ord(c) >= 32 and c not in bidi)
    safe_title = safe_title.lstrip(". ")          # a leading dot would make a folder the album list hides
    safe_title = safe_title[:100].strip().rstrip(". ")
    if not safe_title:
        safe_title = f"新相簿_{int(time.time())}"
    stem = safe_title.split(".")[0].strip().upper()
    if stem in ("CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$") or (
            len(stem) == 4 and stem[:3] in ("COM", "LPT") and stem[3] in "0123456789¹²³"):
        return {"ok": False, "error": f"「{safe_title}」是系統保留名稱，請換一個相簿名稱"}
    
    root = get_albums_root()
    target_dir = os.path.join(root, safe_title)
    if os.path.exists(target_dir):
        return {"ok": False, "error": f"相簿「{safe_title}」已存在"}
    
    try:
        os.makedirs(target_dir, exist_ok=True)
        return {
            "ok": True,
            "album": {
                "id": f"custom_{safe_title}",
                "name": f"🗂️ {safe_title}",
                "path": target_dir,
                "type": "custom",
                "count": 0,
                "cover_url": "",
                "is_custom": True,
                "description": "自訂本機相簿資料夾"
            },
            "message": f"成功建立相簿「{safe_title}」"
        }
    except Exception as e:
        return {"ok": False, "error": f"建立相簿資料夾失敗: {e}"}

def add_photos_to_album(album_path, photo_paths, mode="copy"):
    """Copy or move real photo files into the album directory."""
    if not album_path or not os.path.isdir(album_path):
        return {"ok": False, "error": "無效的相簿目錄"}
    
    if not photo_paths or not isinstance(photo_paths, list):
        return {"ok": False, "error": "未選取任何相片檔案"}
    
    success_count = 0
    errors = []
    
    for src in photo_paths:
        if not isinstance(src, str) or os.path.splitext(src)[1].lower() not in IMAGE_EXTS:
            continue
        if not os.path.isfile(src):
            continue
        try:
            fname = os.path.basename(src)
            dst = os.path.join(album_path, fname)
            # Avoid overwriting with same name
            base, ext = os.path.splitext(fname)
            counter = 1
            while os.path.exists(dst) and os.path.abspath(src) != os.path.abspath(dst):
                dst = os.path.join(album_path, f"{base}_{counter}{ext}")
                counter += 1
            
            if os.path.abspath(src) != os.path.abspath(dst):
                if mode == "move":
                    shutil.move(src, dst)
                else:
                    shutil.copy2(src, dst)
            success_count += 1
        except Exception as e:
            errors.append(f"{os.path.basename(src)}: {e}")
            
    return {
        "ok": True,
        "success_count": success_count,
        "errors": errors,
        "message": f"成功加入 {success_count} 張相片至相簿！"
    }

def reveal_in_system(path):
    """Open folder in Finder (macOS) or Explorer (Windows)."""
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform == "win32":
            os.startfile(path)
        return True
    except Exception:
        return False
