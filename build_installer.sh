#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VERSION="2.7"
APP_NAME="MacToolbox.app"
DIST_DIR="$ROOT_DIR/dist"
BUILD_TMP="$ROOT_DIR/.cache/pkg_build"

echo "🔨 [1/5] 編譯 macOS 原生二進位執行檔 (Vision Helper & App Launcher)..."
swiftc -O "$ROOT_DIR/mac_vision_helper.swift" -o "$ROOT_DIR/mac_vision_helper"
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

# Ad-hoc sign the app bundle so macOS Gatekeeper / LaunchServices accepts it cleanly
codesign --force --deep --sign - "$ROOT_DIR/$APP_NAME" >/dev/null 2>&1 || true

echo "💿 [4/5] 製作 macOS 標準安裝套件 (.pkg)..."
PKG_ROOT="$BUILD_TMP/pkg_root/Applications"
PKG_SCRIPTS="$BUILD_TMP/pkg_scripts"
mkdir -p "$PKG_ROOT" "$PKG_SCRIPTS"
cp -R "$ROOT_DIR/$APP_NAME" "$PKG_ROOT/$APP_NAME"

cat << 'EOF' > "$PKG_SCRIPTS/postinstall"
#!/usr/bin/env bash
APP_PATH="/Applications/MacToolbox.app"
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

echo "📡 [6/6] 更新 GitHub 部署版本清單 (dist/version.json & latest 軟連結)..."
cp -f "$DIST_DIR/MacToolbox-${VERSION}.dmg" "$DIST_DIR/MacToolbox-latest.dmg"
cp -f "$DIST_DIR/MacToolbox-${VERSION}-Installer.pkg" "$DIST_DIR/MacToolbox-latest.pkg"

cat << EOF > "$DIST_DIR/version.json"
{
  "version": "${VERSION}.0",
  "name": "PowerUp Toolbox",
  "release_date": "$(date +%Y-%m-%d)",
  "min_macos": "12.0",
  "latest_dmg": "MacToolbox-latest.dmg",
  "latest_pkg": "MacToolbox-latest.pkg",
  "dmg_url": "https://raw.githubusercontent.com/become/powerup-toolbox/main/dist/MacToolbox-latest.dmg",
  "pkg_url": "https://raw.githubusercontent.com/become/powerup-toolbox/main/dist/MacToolbox-latest.pkg",
  "changelog": [
    "🚀 PowerUp Toolbox 跨平台正式版：全面支援 macOS 與 Windows 10/11",
    "🛡️ 企業級安全強化：本機 Token 鑑權、Strict CSP、DOMPurify 與離線安全沙盒",
    "🖼️ 海量照片極速網格瀏覽模式 (3,000+ 張照片 0 秒瞬顯) 與原寸自適應檢視",
    "📑 Markdown / Office / PDF / JSON / CSV 全功能即時預覽與雙欄編輯",
    "⚡ 跨平台一鍵自動更新引擎 (GitHub Release / main 分支發布)"
  ]
}
EOF

echo ""
echo "✅ 打包完成！安裝檔與版本元數據已輸出至："
ls -lh "$PKG_OUT" "$DMG_OUT" "$DIST_DIR/version.json"
