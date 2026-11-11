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
import urllib.parse

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif", ".heic", ".svg"
}

def get_albums_root():
    """Base directory for user custom albums."""
    d = os.path.expanduser("~/Pictures/PowerUp_Albums")
    os.makedirs(d, exist_ok=True)
    return d

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
    downloads = os.path.expanduser("~/Downloads")
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
    desktop = os.path.expanduser("~/Desktop")
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

    # 4. Captures (~/Pictures/MacToolbox_Captures)
    captures = os.path.expanduser("~/Pictures/MacToolbox_Captures")
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
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "相簿名稱不可為空"}
    
    # Sanitize title
    safe_title = "".join(c for c in title if c not in r'\/:*?"<>|').strip()
    if not safe_title:
        safe_title = f"新相簿_{int(time.time())}"
    
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
