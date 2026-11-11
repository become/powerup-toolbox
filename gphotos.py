#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PowerUp Toolbox - Google Photos Integration Module
Supports Google Photos OAuth2, albums browsing, media search, uploading, and organizing.
Includes built-in offline Demo mode for immediate testing and demonstration.
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import mimetypes
import shutil

IS_WINDOWS = sys.platform == "win32"
if IS_WINDOWS:
    CONFIG_DIR = os.environ.get("POWERUP_DATA_DIR") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "PowerUpToolbox")
else:
    CONFIG_DIR = os.path.expanduser("~/.powerup")

os.makedirs(CONFIG_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(CONFIG_DIR, "gphotos_config.json")
TOKENS_FILE = os.path.join(CONFIG_DIR, "gphotos_tokens.json")

# Google OAuth2 & Photos API endpoints
OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
PHOTOS_API_BASE = "https://photoslibrary.googleapis.com/v1"
SCOPES = [
    "https://www.googleapis.com/auth/photoslibrary.readonly",
    "https://www.googleapis.com/auth/photoslibrary.appendonly",
    "https://www.googleapis.com/auth/photoslibrary.sharing",
    "https://www.googleapis.com/auth/userinfo.email"
]

# In-memory Demo Data when demo mode is active or user hasn't configured real credentials
DEMO_ALBUMS = [
    {
        "id": "demo_album_tokyo",
        "title": "🌸 2026 日本賞櫻 · 富士山巡禮",
        "totalMediaItems": 6,
        "coverPhotoBaseUrl": "/assets/brand_logo_extracted.png"
    },
    {
        "id": "demo_album_ai",
        "title": "✨ AI 影像修復 & 證件照工坊",
        "totalMediaItems": 4,
        "coverPhotoBaseUrl": "/assets/app_icon.png"
    },
    {
        "id": "demo_album_screenshots",
        "title": "📸 工作工作台截圖與靈感收藏",
        "totalMediaItems": 5,
        "coverPhotoBaseUrl": "/assets/brand_logo_extracted.png"
    }
]

DEMO_PHOTOS = {
    "demo_album_tokyo": [
        {"id": "demo_pic_1", "filename": "Tokyo_Sakura_001.jpg", "description": "東京櫻花盛開景緻", "creationTime": "2026-04-02T10:30:00Z", "width": "4032", "height": "3024", "baseUrl": "/assets/brand_logo_extracted.png"},
        {"id": "demo_pic_2", "filename": "Fuji_Lake_002.jpg", "description": "河口湖逆富士日出", "creationTime": "2026-04-03T06:15:00Z", "width": "4032", "height": "3024", "baseUrl": "/assets/brand_logo_extracted.png"},
        {"id": "demo_pic_3", "filename": "Miyajima_Torii_003.jpg", "description": "嚴島神社海上鳥居夕照", "creationTime": "2026-04-04T17:40:00Z", "width": "4032", "height": "3024", "baseUrl": "/assets/brand_logo_extracted.png"},
    ],
    "demo_album_ai": [
        {"id": "demo_ai_1", "filename": "Portrait_Enhanced_01.png", "description": "GFPGAN 4K 人臉超解析度修復", "creationTime": "2026-11-05T14:20:00Z", "width": "2048", "height": "2048", "baseUrl": "/assets/app_icon.png"},
        {"id": "demo_ai_2", "filename": "Passport_Photo_3x4.png", "description": "3:4 證件照自動定位裁切", "creationTime": "2026-11-06T09:10:00Z", "width": "1200", "height": "1600", "baseUrl": "/assets/app_icon.png"},
    ],
    "demo_album_screenshots": [
        {"id": "demo_sc_1", "filename": "Toolbox_3000_Grid.png", "description": "PowerUp Toolbox 3,000+ 張照片瞬開網格", "creationTime": "2026-11-08T11:00:00Z", "width": "1920", "height": "1080", "baseUrl": "/assets/app_icon.png"},
    ]
}


def load_json(filepath, default=None):
    if default is None:
        default = {}
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filepath, data):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[GooglePhotos] Save JSON error ({filepath}): {e}")
        return False


def get_config():
    return load_json(CONFIG_FILE, {
        "client_id": "",
        "client_secret": "",
        "demo_mode": False
    })


def save_config(client_id, client_secret, demo_mode=False):
    cfg = get_config()
    cfg["client_id"] = (client_id or "").strip()
    cfg["client_secret"] = (client_secret or "").strip()
    cfg["demo_mode"] = bool(demo_mode)
    return save_json(CONFIG_FILE, cfg)


def get_tokens():
    return load_json(TOKENS_FILE, {
        "access_token": "",
        "refresh_token": "",
        "expires_at": 0,
        "email": ""
    })


def save_tokens(tokens):
    return save_json(TOKENS_FILE, tokens)


def disconnect():
    try:
        if os.path.exists(TOKENS_FILE):
            os.remove(TOKENS_FILE)
        return True
    except Exception:
        return False


def get_status():
    cfg = get_config()
    tokens = get_tokens()
    has_creds = bool(cfg.get("client_id") and cfg.get("client_secret"))
    has_token = bool(tokens.get("access_token") or tokens.get("refresh_token"))
    is_demo = cfg.get("demo_mode", False) or (not has_creds and not has_token)
    
    return {
        "ok": True,
        "connected": bool(has_token and not is_demo),
        "demo_mode": is_demo,
        "has_credentials": has_creds,
        "client_id_preview": (cfg.get("client_id")[:12] + "...") if cfg.get("client_id") else "",
        "email": tokens.get("email") or ("體驗帳號 (demo@gmail.com)" if is_demo else ""),
        "expires_in": max(0, int(tokens.get("expires_at", 0) - time.time())) if has_token else 0
    }


def get_auth_url(redirect_uri="http://127.0.0.1:18765/api/gphotos/oauth-callback"):
    cfg = get_config()
    client_id = cfg.get("client_id")
    if not client_id:
        return None, "請先設定 Google Cloud OAuth Client ID"
    
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true"
    }
    url = OAUTH_AUTH_URL + "?" + urllib.parse.urlencode(params)
    return url, None


def handle_oauth_callback(code, redirect_uri="http://127.0.0.1:18765/api/gphotos/oauth-callback"):
    cfg = get_config()
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    if not client_id or not client_secret:
        return False, "缺少 Client ID 或 Client Secret"
    
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }).encode("utf-8")
    
    req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, f"Token 交換失敗: {e}"
    
    access_token = res_data.get("access_token")
    refresh_token = res_data.get("refresh_token")
    expires_in = res_data.get("expires_in", 3600)
    
    email = ""
    # Try fetching user email
    try:
        u_req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(u_req, timeout=10) as u_resp:
            u_info = json.loads(u_resp.read().decode("utf-8"))
            email = u_info.get("email", "")
    except Exception:
        pass
    
    cur_tokens = get_tokens()
    save_tokens({
        "access_token": access_token,
        "refresh_token": refresh_token or cur_tokens.get("refresh_token", ""),
        "expires_at": time.time() + expires_in,
        "email": email or cur_tokens.get("email", "")
    })
    
    # Disable demo mode on successful auth
    cfg["demo_mode"] = False
    save_json(CONFIG_FILE, cfg)
    
    return True, email


def get_valid_token():
    status = get_status()
    if status["demo_mode"]:
        return "DEMO_TOKEN", None
    
    tokens = get_tokens()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    expires_at = tokens.get("expires_at", 0)
    
    # If token still valid for > 60 seconds
    if access_token and (expires_at - time.time() > 60):
        return access_token, None
    
    if not refresh_token:
        return None, "尚未登入或授權已過期，請重新連線 Google 帳號"
    
    cfg = get_config()
    client_id = cfg.get("client_id")
    client_secret = cfg.get("client_secret")
    if not client_id or not client_secret:
        return None, "缺少 Client ID 憑證"
    
    # Refresh token
    data = urllib.parse.urlencode({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "refresh_token"
    }).encode("utf-8")
    
    req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            new_acc = res_data.get("access_token")
            exp_in = res_data.get("expires_in", 3600)
            tokens["access_token"] = new_acc
            tokens["expires_at"] = time.time() + exp_in
            save_tokens(tokens)
            return new_acc, None
    except Exception as e:
        return None, f"刷新存取權杖失敗: {e}"


def list_albums():
    status = get_status()
    if status["demo_mode"]:
        return {"ok": True, "albums": DEMO_ALBUMS, "is_demo": True}
    
    token, err = get_valid_token()
    if err:
        return {"ok": False, "error": err}
    
    url = f"{PHOTOS_API_BASE}/albums?pageSize=50"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            albums = data.get("albums", [])
            out = []
            for a in albums:
                out.append({
                    "id": a.get("id"),
                    "title": a.get("title", "未命名相簿"),
                    "totalMediaItems": int(a.get("mediaItemsCount", 0)),
                    "coverPhotoBaseUrl": a.get("coverPhotoBaseUrl", "")
                })
            return {"ok": True, "albums": out, "is_demo": False}
    except Exception as e:
        return {"ok": False, "error": f"載入相簿清單失敗: {e}"}


def get_album_media(album_id=None, page_size=60):
    status = get_status()
    if status["demo_mode"]:
        if album_id and album_id in DEMO_PHOTOS:
            items = DEMO_PHOTOS[album_id]
        else:
            # Flatten all demo photos
            items = []
            for alb_items in DEMO_PHOTOS.values():
                items.extend(alb_items)
        return {"ok": True, "media": items, "is_demo": True}
    
    token, err = get_valid_token()
    if err:
        return {"ok": False, "error": err}
    
    try:
        if album_id:
            url = f"{PHOTOS_API_BASE}/mediaItems:search"
            payload = json.dumps({"albumId": album_id, "pageSize": page_size}).encode("utf-8")
            req = urllib.request.Request(url, data=payload, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            })
        else:
            url = f"{PHOTOS_API_BASE}/mediaItems?pageSize={page_size}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_items = data.get("mediaItems", [])
            out = []
            for m in raw_items:
                out.append({
                    "id": m.get("id"),
                    "filename": m.get("filename", "Photo.jpg"),
                    "description": m.get("description", ""),
                    "mimeType": m.get("mimeType", "image/jpeg"),
                    "creationTime": m.get("mediaMetadata", {}).get("creationTime", ""),
                    "width": m.get("mediaMetadata", {}).get("width", "0"),
                    "height": m.get("mediaMetadata", {}).get("height", "0"),
                    "baseUrl": m.get("baseUrl", "")
                })
            return {"ok": True, "media": out, "is_demo": False}
    except Exception as e:
        return {"ok": False, "error": f"讀取相片失敗: {e}"}


def create_album(title):
    title = (title or "").strip()
    if not title:
        return {"ok": False, "error": "請提供相簿名稱"}
    
    status = get_status()
    if status["demo_mode"]:
        new_id = f"demo_album_{int(time.time())}"
        new_alb = {
            "id": new_id,
            "title": title,
            "totalMediaItems": 0,
            "coverPhotoBaseUrl": "/assets/brand_logo_extracted.png"
        }
        DEMO_ALBUMS.insert(0, new_alb)
        DEMO_PHOTOS[new_id] = []
        return {"ok": True, "album": new_alb, "is_demo": True}
    
    token, err = get_valid_token()
    if err:
        return {"ok": False, "error": err}
    
    url = f"{PHOTOS_API_BASE}/albums"
    payload = json.dumps({"album": {"title": title}}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            return {
                "ok": True,
                "album": {
                    "id": res_data.get("id"),
                    "title": res_data.get("title"),
                    "totalMediaItems": 0,
                    "coverPhotoBaseUrl": ""
                },
                "is_demo": False
            }
    except Exception as e:
        return {"ok": False, "error": f"建立相簿失敗: {e}"}


def upload_media_file(file_path, album_id=None, description=""):
    if not file_path or not os.path.isfile(file_path):
        return {"ok": False, "error": "找不到本機檔案"}
    
    filename = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    mime_type = mime_type or "image/jpeg"
    
    status = get_status()
    if status["demo_mode"]:
        time.sleep(0.6) # simulate upload latency
        demo_id = f"demo_up_{int(time.time())}"
        demo_item = {
            "id": demo_id,
            "filename": filename,
            "description": description or f"上傳自 PowerUp Toolbox ({time.strftime('%Y-%m-%d %H:%M')})",
            "creationTime": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "width": "1920",
            "height": "1080",
            "baseUrl": "/assets/brand_logo_extracted.png"
        }
        target_alb = album_id or (DEMO_ALBUMS[0]["id"] if DEMO_ALBUMS else None)
        if target_alb and target_alb in DEMO_PHOTOS:
            DEMO_PHOTOS[target_alb].insert(0, demo_item)
            for a in DEMO_ALBUMS:
                if a["id"] == target_alb:
                    a["totalMediaItems"] += 1
                    break
        return {"ok": True, "mediaItemId": demo_id, "albumId": target_alb, "is_demo": True, "message": f"（Demo模式）成功模擬上傳 {filename} 至相簿"}
    
    token, err = get_valid_token()
    if err:
        return {"ok": False, "error": err}
    
    # Step 1: Upload Raw Bytes
    upload_url = f"{PHOTOS_API_BASE}/uploads"
    try:
        with open(file_path, "rb") as f:
            raw_bytes = f.read()
        
        up_req = urllib.request.Request(upload_url, data=raw_bytes, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
            "X-Goog-Upload-Content-Type": mime_type,
            "X-Goog-Upload-Protocol": "raw"
        })
        with urllib.request.urlopen(up_req, timeout=60) as up_resp:
            upload_token = up_resp.read().decode("utf-8").strip()
    except Exception as e:
        return {"ok": False, "error": f"圖片上傳失敗: {e}"}
    
    # Step 2: Batch Create Media Item in Library / Album
    create_url = f"{PHOTOS_API_BASE}/mediaItems:batchCreate"
    create_payload = {
        "newMediaItems": [
            {
                "description": description or f"Uploaded with PowerUp Toolbox ({time.strftime('%Y-%m-%d')})",
                "simpleMediaItem": {
                    "fileName": filename,
                    "uploadToken": upload_token
                }
            }
        ]
    }
    if album_id:
        create_payload["albumId"] = album_id
    
    try:
        c_req = urllib.request.Request(create_url, data=json.dumps(create_payload).encode("utf-8"), headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(c_req, timeout=20) as c_resp:
            res_data = json.loads(c_resp.read().decode("utf-8"))
            results = res_data.get("newMediaItemResults", [])
            if results and results[0].get("status", {}).get("message", "Success") in ("Success", ""):
                media_item = results[0].get("mediaItem", {})
                return {
                    "ok": True,
                    "mediaItemId": media_item.get("id"),
                    "albumId": album_id,
                    "is_demo": False,
                    "message": f"成功上傳 {filename} 至 Google 相簿！"
                }
            else:
                err_msg = results[0].get("status", {}).get("message", "建立相片項目失敗")
                return {"ok": False, "error": err_msg}
    except Exception as e:
        return {"ok": False, "error": f"建立相簿媒體項目失敗: {e}"}


def add_media_to_album(album_id, media_item_ids):
    if not album_id or not media_item_ids:
        return {"ok": False, "error": "缺少相簿 ID 或相片 ID"}
    
    status = get_status()
    if status["demo_mode"]:
        return {"ok": True, "is_demo": True, "message": "（Demo模式）已成功將照片整理至指定相簿"}
    
    token, err = get_valid_token()
    if err:
        return {"ok": False, "error": err}
    
    url = f"{PHOTOS_API_BASE}/albums/{album_id}:batchAddMediaItems"
    payload = json.dumps({"mediaItemIds": media_item_ids}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"ok": True, "is_demo": False, "message": "已成功將相片加入相簿"}
    except Exception as e:
        return {"ok": False, "error": f"加入相簿失敗: {e}"}
