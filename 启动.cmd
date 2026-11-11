@echo off
chcp 936 >nul
cd /d "%~dp0"
set "EXE=发布\手机电脑互通\手机电脑互通.exe"
if exist "%EXE%" (
    start "" "%EXE%"
    exit /b 0
)
echo 未找到打包版，改用源码方式启动...
if not exist ".venv\Scripts\pythonw.exe" (
    echo 请先运行 install.cmd 安装依赖
    pause
    exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "app\main.py"
exit /b 0
