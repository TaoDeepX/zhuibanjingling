@echo off
chcp 65001 >nul
title 追板精灵
cd /d "%~dp0"

py -3 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 请先安装Python 3.8+
    pause
    exit /b 1
)

py -3 -c "import win32gui; import win32com.client; from PIL import Image; import requests" >nul 2>&1
if errorlevel 1 (
    echo [提示] 安装依赖中...
    py -3 -m pip install pywin32 pillow requests
)

echo 正在启动追板精灵...
start pyw -3 zhuiban_app.py
