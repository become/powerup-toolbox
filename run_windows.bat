@echo off
chcp 65001 >nul
title PowerUp Toolbox - Windows Launcher

echo ========================================================
echo         PowerUp Toolbox for Windows
echo         https://github.com/become/powerup-toolbox
echo ========================================================
echo.

:: 1. Check Python
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 Python！
    echo 請先安裝 Python 3.9 或更高版本，並勾選「Add Python to PATH」：
    echo https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo [1/3] 檢測 Python 環境...
python --version

:: 2. Check / Install Pillow
echo [2/3] 檢查依賴套件 (Pillow)...
python -c "import PIL" >nul 2>nul
if %errorlevel% neq 0 (
    echo 正在自動安裝 Pillow...
    python -m pip install --quiet Pillow
    if %errorlevel% neq 0 (
        echo [警告] 自動安裝 Pillow 失敗，嘗試繼續啟動...
    ) else (
        echo Pillow 安裝成功！
    )
) else (
    echo 依賴套件已就緒。
)

:: 3. Launch Server & Open Browser
echo [3/3] 啟動 PowerUp Toolbox 本地服務 (Port: 18765)...
echo.
echo 服務運行中，請勿關閉此視窗！如欲停止請按 Ctrl + C。
echo.

start "" "http://127.0.0.1:18765/"
python server.py

pause
