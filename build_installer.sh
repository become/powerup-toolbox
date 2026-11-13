#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VERSION="2.8.2"
APP_NAME="MacToolbox.app"
DIST_DIR="$ROOT_DIR/dist"
BUILD_TMP="$ROOT_DIR/.cache/pkg_build"

# Keep Info.plist versions synchronized
plutil -replace CFBundleShortVersionString -string "${VERSION}" "$ROOT_DIR/$APP_NAME/Contents/Info.plist" 2>/dev/null || true
plutil -replace CFBundleVersion -string "282" "$ROOT_DIR/$APP_NAME/Contents/Info.plist" 2>/dev/null || true

echo "🔨 [1/5] 編譯 macOS 原生二進位執行檔 (Vision Helper & App Launcher)..."
swiftc -O "$ROOT_DIR/mac_vision_helper.swift" -o "$ROOT_DIR/mac_vision_helper"
rm -rf "$ROOT_DIR/$APP_NAME/Contents/MacOS"
mkdir -p "$ROOT_DIR/$APP_NAME/Contents/MacOS"
mkdir -p "$ROOT_DIR/$APP_NAME/Contents/Resources"
swiftc -O "$ROOT_DIR/app_launcher.swift" -o "$ROOT_DIR/$APP_NAME/Contents/MacOS/MacToolbox"
swiftc -O "$ROOT_DIR/set_default_handlers.swift" -o "$ROOT_DIR/$APP_NAME/Contents/Resources/set_default_handlers"

echo "🎨 [2/5] 設定高解析度 macOS 3D 科技工具箱圖示 (AppIcon.icns)..."
rm -rf "$BUILD_TMP"
mkdir -p "$BUILD_TMP" "$DIST_DIR"
if [ -f "$ROOT_DIR/assets/AppIcon.icns" ]; then
    cp -f "$ROOT_DIR/assets/AppIcon.icns" "$ROOT_DIR/$APP_NAME/Contents/Resources/AppIcon.icns"
fi

echo "📦 [3/5] 封裝應用程式資源至 $APP_NAME/Contents/Resources..."
cp -f "$ROOT_DIR/server.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/server.py"
cp -f "$ROOT_DIR/local_albums.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/local_albums.py" 2>/dev/null || true
cp -f "$ROOT_DIR/gphotos.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/gphotos.py" 2>/dev/null || true
cp -f "$ROOT_DIR/index.html" "$ROOT_DIR/$APP_NAME/Contents/Resources/index.html"
rm -rf "$ROOT_DIR/$APP_NAME/Contents/Resources/assets"
cp -R "$ROOT_DIR/assets" "$ROOT_DIR/$APP_NAME/Contents/Resources/assets"
cp -f "$ROOT_DIR/mac_vision_helper" "$ROOT_DIR/$APP_NAME/Contents/Resources/mac_vision_helper"
cp -f "$ROOT_DIR/README.md" "$ROOT_DIR/$APP_NAME/Contents/Resources/README.md"
cp -f "$ROOT_DIR/sample_demo.json" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.json"
cp -f "$ROOT_DIR/sample_demo.csv" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.csv"
cp -f "$ROOT_DIR/sample_demo.pptx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.pptx"
cp -f "$ROOT_DIR/sample_demo.docx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.docx"
cp -f "$ROOT_DIR/sample_demo.xlsx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.xlsx"
cp -f "$ROOT_DIR/winpath.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/winpath.py" 2>/dev/null || true
cp -f "$ROOT_DIR/winshell.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/winshell.py" 2>/dev/null || true
rm -rf "$ROOT_DIR/$APP_NAME/Contents/Resources/xlrd"
cp -R "$ROOT_DIR/xlrd" "$ROOT_DIR/$APP_NAME/Contents/Resources/xlrd"
chmod +x "$ROOT_DIR/$APP_NAME/Contents/MacOS/MacToolbox"
chmod +x "$ROOT_DIR/$APP_NAME/Contents/Resources/mac_vision_helper"
chmod +x "$ROOT_DIR/$APP_NAME/Contents/Resources/set_default_handlers"
chmod +x "$ROOT_DIR/$APP_NAME/Contents/Resources/server.py"

# Official Apple Developer ID Signing & Notarization
SIGN_IDENTITY="Developer ID Application: FITs Inc. (2ZLWWY5NS3)"
NOTARY_PROFILE="mactoolbox-notary"

if security find-identity -p codesigning -v | grep -q "$SIGN_IDENTITY"; then
    echo "🔏 [3.5/5] 使用官方 Apple Developer ID 簽名 ($SIGN_IDENTITY)..."
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$ROOT_DIR/$APP_NAME/Contents/Resources/mac_vision_helper"
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$ROOT_DIR/$APP_NAME/Contents/Resources/set_default_handlers"
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$ROOT_DIR/$APP_NAME/Contents/MacOS/MacToolbox"
    codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$ROOT_DIR/$APP_NAME"
else
    echo "⚠️  未偵測到官方簽名憑證，使用 Ad-hoc 簽名..."
    codesign --force --deep --sign - "$ROOT_DIR/$APP_NAME" >/dev/null 2>&1 || true
fi

echo "💿 [4/5] 製作 macOS 標準安裝套件 (.pkg)..."
PKG_ROOT="$BUILD_TMP/pkg_root/Applications"
PKG_SCRIPTS="$BUILD_TMP/pkg_scripts"
mkdir -p "$PKG_ROOT" "$PKG_SCRIPTS"
cp -R "$ROOT_DIR/$APP_NAME" "$PKG_ROOT/$APP_NAME"

cat << 'EOF' > "$PKG_SCRIPTS/postinstall"
#!/usr/bin/env bash
APP_PATH="/Applications/MacToolbox.app"
if [ -d "$APP_PATH" ]; then
    xattr -cr "$APP_PATH" 2>/dev/null || true
fi
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
if [ -x "$LSREGISTER" ] && [ -d "$APP_PATH" ]; then
    "$LSREGISTER" -f "$APP_PATH" || true
fi
if [ -x "$APP_PATH/Contents/Resources/set_default_handlers" ]; then
    su -l "${USER:-$(stat -f%Su /dev/console)}" -c "\"$APP_PATH/Contents/Resources/set_default_handlers\"" >/dev/null 2>&1 || true
fi
exit 0
EOF
chmod +x "$PKG_SCRIPTS/postinstall"

PKG_OUT="$DIST_DIR/MacToolbox-${VERSION}-Installer.pkg"
rm -f "$PKG_OUT"
pkgbuild \
    --root "$BUILD_TMP/pkg_root" \
    --identifier "com.become.mactoolbox.pkg" \
    --version "$VERSION" \
    --scripts "$PKG_SCRIPTS" \
    --install-location "/" \
    "$PKG_OUT" >/dev/null

echo "💽 [5/5] 製作 macOS 拖曳安裝磁碟映像檔 (.dmg)..."
DMG_STAGE="$BUILD_TMP/dmg_stage"
mkdir -p "$DMG_STAGE"
cp -R "$ROOT_DIR/$APP_NAME" "$DMG_STAGE/$APP_NAME"
ln -s /Applications "$DMG_STAGE/Applications"

DMG_OUT="$DIST_DIR/MacToolbox-${VERSION}.dmg"
rm -f "$DMG_OUT"
hdiutil create \
    -volname "MacToolbox ${VERSION}" \
    -srcfolder "$DMG_STAGE" \
    -ov \
    -format UDZO \
    "$DMG_OUT" >/dev/null

rm -rf "$BUILD_TMP"

if security find-identity -p codesigning -v | grep -q "$SIGN_IDENTITY"; then
    echo "🔏 簽署 DMG 映像檔 ($SIGN_IDENTITY)..."
    codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG_OUT" >/dev/null 2>&1 || true

    echo "🍏 提交 Apple 雲端公證 (xcrun notarytool)..."
    NOTARY_OUT=$(xcrun notarytool submit "$DMG_OUT" --keychain-profile "$NOTARY_PROFILE" --wait 2>&1 || true)
    echo "$NOTARY_OUT"
    if echo "$NOTARY_OUT" | grep -q "status: Accepted"; then
        echo "📎 釘選公證票據 (xcrun stapler staple)..."
        xcrun stapler staple "$DMG_OUT" || true
        echo "🎉 Apple 雲端公證與票據釘選完成！"
    else
        echo "⚠️  公證尚未完成，請檢查輸出。"
    fi
fi

echo "📡 [6/7] 更新部署版本清單 (dist/version.json & latest 軟連結)..."
cp -f "$DIST_DIR/MacToolbox-${VERSION}.dmg" "$DIST_DIR/MacToolbox-latest.dmg"
cp -f "$DIST_DIR/MacToolbox-${VERSION}-Installer.pkg" "$DIST_DIR/MacToolbox-latest.pkg"

# Windows build facts come from dist/win-version.json (written by windows/publish_website.ps1 on the
# Windows machine); never hard-code them here or they drift from the zip the website actually serves.
WIN_MANIFEST="$DIST_DIR/win-version.json"
win_field() {
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get(sys.argv[2], ""))' "$WIN_MANIFEST" "$1" 2>/dev/null || true
}
WIN_VERSION="$(win_field version)"
WIN_SHA256="$(win_field sha256)"
WIN_SIZE="$(win_field size)"
WIN_SIZE="${WIN_SIZE:-0}"
if [ -z "$WIN_VERSION" ] || [ -z "$WIN_SHA256" ]; then
  echo "⚠️  無法讀取 $WIN_MANIFEST（檔案不存在、格式錯誤或沒有 python3）：version.json 的 windows 區塊將是空值，同步到官網前請先修正！" >&2
fi

cat << EOF > "$DIST_DIR/version.json"
{
  "version": "${VERSION}",
  "name": "PowerUp Toolbox",
  "release_date": "$(date +%Y-%m-%d)",
  "min_macos": "12.0",
  "latest_dmg": "MacToolbox-latest.dmg",
  "latest_pkg": "MacToolbox-latest.pkg",
  "dmg_url": "https://powerup.dancingarden.com/downloads/toolbox/MacToolbox-latest.dmg",
  "pkg_url": "https://powerup.dancingarden.com/downloads/toolbox/MacToolbox-latest.pkg",
  "windows_version": "${WIN_VERSION}",
  "windows_zip": "PowerUpToolbox-portable.zip",
  "windows_zip_url": "https://powerup.dancingarden.com/downloads/toolbox/PowerUpToolbox-portable.zip",
  "macos": {
    "version": "${VERSION}",
    "min_macos": "12.0",
    "latest_dmg": "MacToolbox-latest.dmg",
    "latest_pkg": "MacToolbox-latest.pkg",
    "dmg_url": "https://powerup.dancingarden.com/downloads/toolbox/MacToolbox-latest.dmg",
    "pkg_url": "https://powerup.dancingarden.com/downloads/toolbox/MacToolbox-latest.pkg"
  },
  "windows": {
    "version": "${WIN_VERSION}",
    "latest_zip": "PowerUpToolbox-portable.zip",
    "zip_url": "https://powerup.dancingarden.com/downloads/toolbox/PowerUpToolbox-portable.zip",
    "sha256": "${WIN_SHA256}",
    "size": ${WIN_SIZE}
  },
  "changelog": [
    "📷 本機真實相簿工坊：100% 讀取電腦真實照片庫（Downloads、Desktop、macOS Photos 圖庫與自訂相簿），支援 Finder 整合與修圖",
    "☁️ Google 相簿雲端同步：支援個人 OAuth 憑證連線，一鍵備份照片至 Google 雲端",
    "🍎 macOS 顯示與體驗優化：修復 Safari / Chrome 左上角圖標與 Favicon 多格式支援，消除持續 loading",
    "🌐 官網獨立發布支援：支援 powerup.dancingarden.com 官方高速更新節點，原始碼完全保密",
    "⚡ 跨平台一鍵自動更新引擎 (官方高速更新節點)"
  ]
}
EOF

POWERUP_PUBLIC_DOWNLOADS="/Users/become/Claude/Projects/powerup/public/downloads/toolbox"
if [ -d "$POWERUP_PUBLIC_DOWNLOADS" ]; then
  echo "🌐 [7/7] 自動同步最新安裝檔與版本元數據至 PowerUp 官網目錄..."
  cp -f "$DIST_DIR/MacToolbox-${VERSION}.dmg" "$POWERUP_PUBLIC_DOWNLOADS/"
  cp -f "$DIST_DIR/MacToolbox-${VERSION}-Installer.pkg" "$POWERUP_PUBLIC_DOWNLOADS/"
  cp -f "$DIST_DIR/MacToolbox-latest.dmg" "$POWERUP_PUBLIC_DOWNLOADS/"
  cp -f "$DIST_DIR/MacToolbox-latest.pkg" "$POWERUP_PUBLIC_DOWNLOADS/"
  cp -f "$DIST_DIR/version.json" "$POWERUP_PUBLIC_DOWNLOADS/"
  echo "✨ 官網安裝包同步完成: $POWERUP_PUBLIC_DOWNLOADS"
fi

echo ""
echo "✅ 打包完成！安裝檔與版本元數據已輸出至："
ls -lh "$PKG_OUT" "$DMG_OUT" "$DIST_DIR/version.json"
