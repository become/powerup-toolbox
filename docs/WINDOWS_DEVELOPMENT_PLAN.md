# PowerUp Toolbox - Windows 跨平台開發架構規劃書

> 本規劃書說明 **PowerUp Toolbox** 跨平台支援 Windows 的架構決策、技術替代方案與實作路線圖。

---

## 1. 架構決策：單一儲存庫 (Monorepo)

### 決策結論
**維持單一 GitHub Repo (`become/powerup-toolbox`)，不建立獨立的 Windows 專案。**

### 決策依據
1. **前端資產高度共用 (85%+)**：
   - 工具箱核心為現代化 Web GUI (`index.html` + 原生 CSS/JS)，在 macOS、Windows、Linux 的渲染完全一致。
   - 若拆分為兩個 Repo，後續所有 UI/UX 改進、新工具加入、快捷鍵優化都需要手動雙向同步，極易產生版本漂移與重複維護成本。
2. **後端服務統一抽象**：
   - 後端 `server.py` 透過 Python 跨平台層封裝系統原生差異：
     - macOS `Finder` vs Windows `File Explorer` (`reveal_in_file_manager`)
     - macOS `AppleScript` 對話框 vs Windows `PowerShell WinForms` (`pick_native_path`)
     - macOS `open` vs Windows `os.startfile` (`open_with_default_app`)
3. **安裝與建置分離**：
   - macOS 打包使用 `build_installer.sh`（產生 `.app` / `.dmg` / `.pkg`）。
   - Windows 打包使用 `build_windows_installer.bat`（透過 Inno Setup 產生 `.exe` 安裝檔）。

---

## 2. 功能支援度與 Windows 技術替代評估

Windows 是否有功能無法支援？**答案是：所有功能在 Windows 上皆有成熟、甚至效能更佳的對應替代方案。**

| 功能模組 | macOS 實作 | Windows 對應方案 | 支援難度 | 狀態 |
| :--- | :--- | :--- | :---: | :---: |
| **Markdown / JSON / CSV 檢視與編輯** | 純 Web 前端 + Python | 完全共用，原生支援 | ⭐ (零門檻) | **已就緒** |
| **圖片瀏覽與縮圖快取** | macOS `sips` | Python `Pillow` (PIL) 原生處理 | ⭐ (零門檻) | **已就緒** |
| **圖片裁切與旋轉** | `mac_vision_helper` | Python `Pillow` 原生處理 | ⭐ (零門檻) | **已就緒** |
| **本機資料夾與檔案瀏覽** | Unix POSIX 路徑 | 支援磁碟代號 (`C:\`, `D:\`) 與反斜線 | ⭐ (零門檻) | **已就緒** |
| **檔案管理器定位 (Reveal)** | macOS `open -R` | Windows `explorer.exe /select,` | ⭐ (零門檻) | **已就緒** |
| **原生系統檔案選擇器** | AppleScript POSIX | PowerShell `OpenFileDialog` | ⭐ (零門檻) | **已就緒** |
| **螢幕截圖與剪貼簿貼上** | macOS `screencapture` | Windows `ms-screenclip:` + PowerShell 剪貼簿 | ⭐ (零門檻) | **已就緒** |
| **人臉偵測 (Face Detection)** | VisionKit `VNDetectFaceRectangles` | **方案 A**：OpenCV YuNet ONNX (200KB，超輕量 CPU 60fps)<br>**方案 B**：WinRT `Windows.Media.FaceAnalysis` | ⭐⭐ (低) | Phase 2 |
| **OCR 文字辨識** | VisionKit `VNRecognizeText` | **Windows 原生 WinRT OCR** (`Windows.Media.Ocr.OcrEngine`)<br>Windows 10/11 內建，硬體加速，繁中/英/日支援 | ⭐⭐ (低) | Phase 2 |
| **智慧去背 (Segment)** | VisionKit PersonSegmentation | **RMBG-1.4 / MODNet ONNX** 搭配 DirectML GPU 加速 | ⭐⭐⭐ (中) | Phase 2 |
| **超解析度 / 影像修復** | MPS / PyTorch (GFPGAN) | DirectML / CUDA / CPU PyTorch | ⭐⭐ (低) | Phase 3 |
| **獨立桌面視窗 (Desktop Shell)** | macOS `.app` 封裝 | Microsoft Edge **WebView2** (輕量無痛) | ⭐⭐ (低) | Phase 3 |
| **安裝檔與自動更新** | `.pkg` / `.dmg` + GitHub Release | **Inno Setup** 產生 `.exe` 安裝程式 | ⭐⭐ (低) | Phase 4 |

---

## 3. 開發實作路線圖 (Roadmap)

### Phase 1：基礎跨平台相容（當前階段 - 已完成）
- [x] 後端 API 跨平台抽象（檔案總管定位、原生選擇框、預設軟體開啟）。
- [x] 靜態路徑與 Windows 磁碟代號列舉相容 (`C:\`, `D:\`)。
- [x] 純 Python Pillow 縮圖生成與圖片裁切旋轉相容。
- [x] 新增 `run_windows.bat`（一鍵啟動腳本，具備環境檢測與套件自動安裝）。
- [x] 建立 `requirements.txt`。

### Phase 2：Windows 原生 Vision 與 AI 補完
- [ ] **人臉偵測**：整合 OpenCV YuNet 模型（免裝大型 PyTorch，純 CPU 即時辨識人臉並回傳座標）。
- [ ] **原生 OCR**：呼叫 Windows 10/11 內建 WinRT OCR API，無縫辨識繁中與多國語系。
- [ ] **去背支援**：導入 ONNX Runtime DirectML 輕量去背推論。

### Phase 3：WebView2 原生桌面視窗
- [ ] 透過 `pywebview` 或輕量 C# / Rust 啟動微軟內建 Edge WebView2 核心。
- [ ] 隱藏控制台視窗，提供如原生應用程式般的獨立視窗外觀（含自訂標題列與圖示）。

### Phase 4：Inno Setup 打包與自動更新
- [ ] 撰寫 Inno Setup 腳本 `installer.iss`，打包為 `PowerUp-Toolbox-Setup.exe`。
- [ ] 註冊 Windows 右鍵選單（「以 PowerUp Toolbox 開啟」）。
- [ ] 串接 GitHub Release 自動下載更新 Windows 安裝檔。

---

## 4. 如何在 Windows 電腦上立即執行

在任何 Windows 10 / 11 電腦上，只需以下步驟：

### 步驟 1：安裝 Git 與 Python
- 若尚未安裝 Python，至 [python.org/downloads](https://www.python.org/downloads/) 下載安裝，**安裝時務必勾選「Add Python to PATH」**。

### 步驟 2：Clone 專案
開啟 PowerShell 或 CMD 終端機：
```cmd
git clone https://github.com/become/powerup-toolbox.git
cd powerup-toolbox
```

### 步驟 3：一鍵啟動
直接雙擊執行目錄下的 `run_windows.bat`，或在命令提示字元中輸入：
```cmd
run_windows.bat
```

腳本將會自動：
1. 檢測 Python 環境。
2. 自動安裝必備的影像處理模組 (`Pillow`)。
3. 啟動本機伺服器 (`127.0.0.1:18765`)。
4. 自動喚起預設瀏覽器開啟 PowerUp Toolbox 完整介面！
