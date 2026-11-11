@echo off
chcp 936 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未找到虚拟环境，请先运行 install.cmd
    pause
    exit /b 1
)
echo 调试模式：关闭此窗口即退出程序
".venv\Scripts\python.exe" "app\main.py"
pause
