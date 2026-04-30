@echo off
chcp 65001 >nul
title 股票异动语音播报
echo ==========================================
echo     股票异动语音播报工具
echo     按 Ctrl+C 停止运行
echo ==========================================
echo.

cd /d "%~dp0"

REM 检查Python是否安装
py -3 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 检查依赖是否安装
py -3 -c "import pyttsx3" >nul 2>&1
if errorlevel 1 (
    echo [提示] 首次运行，正在安装依赖...
    py -3 -m pip install -r requirements.txt
    echo.
)

echo [启动] 正在启动语音播报...
echo.
py -3 stock_voice_alert.py

pause
