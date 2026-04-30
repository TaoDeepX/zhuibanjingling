@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM 检查Python
py -3 --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到Python，请先安装Python 3.8+
    pause
    exit /b 1
)

REM 检查依赖
py -3 -c "import pyttsx3" >nul 2>&1
if errorlevel 1 (
    echo [提示] 正在安装依赖...
    py -3 -m pip install -r requirements.txt
)

start pyw -3 stock_voice_alert_gui.py
