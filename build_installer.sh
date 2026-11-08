#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

VERSION="2.6"
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
if [ -f "$ROOT_DIR/website/assets/AppIcon.icns" ]; then
    cp -f "$ROOT_DIR/website/assets/AppIcon.icns" "$ROOT_DIR/$APP_NAME/Contents/Resources/AppIcon.icns"
fi

echo "📦 [3/5] 封裝應用程式資源至 $APP_NAME/Contents/Resources..."
cp -f "$ROOT_DIR/server.py" "$ROOT_DIR/$APP_NAME/Contents/Resources/server.py"
cp -f "$ROOT_DIR/index.html" "$ROOT_DIR/$APP_NAME/Contents/Resources/index.html"
cp -f "$ROOT_DIR/mac_vision_helper" "$ROOT_DIR/$APP_NAME/Contents/Resources/mac_vision_helper"
cp -f "$ROOT_DIR/README.md" "$ROOT_DIR/$APP_NAME/Contents/Resources/README.md"
cp -f "$ROOT_DIR/sample_demo.json" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.json"
cp -f "$ROOT_DIR/sample_demo.csv" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.csv"
cp -f "$ROOT_DIR/sample_demo.pptx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.pptx"
cp -f "$ROOT_DIR/sample_demo.docx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.docx"
cp -f "$ROOT_DIR/sample_demo.xlsx" "$ROOT_DIR/$APP_NAME/Contents/Resources/sample_demo.xlsx"
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
cp -f "MacToolbox-${VERSION}.dmg" "$DIST_DIR/MacToolbox-latest.dmg"
cp -f "MacToolbox-${VERSION}-Installer.pkg" "$DIST_DIR/MacToolbox-latest.pkg"

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
    "🚀 全新 3D 科技工具箱高質感立體品牌圖示與 macOS AppIcon",
    "🖼️ 海量照片極速網格瀏覽模式 (3,000+ 張照片 0 秒瞬顯)",
    "🔍 6048×4032 24.4MP 4K 原寸自適應檢視與 AI 影像增強側欄",
    "⚡ 內建 GitHub main 分支 dist 自動更新偵測與一鍵升級引擎"
  ]
}
EOF

echo ""
echo "✅ 打包完成！安裝檔與版本元數據已輸出至："
ls -lh "$PKG_OUT" "$DMG_OUT" "$DIST_DIR/version.json"
