@echo off
chcp 936 >nul
cd /d "%~dp0"
echo 正在创建虚拟环境...
python -m venv .venv
if errorlevel 1 (
    echo 创建失败：请确认已安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)
echo 正在安装依赖（走阿里云镜像）...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
if errorlevel 1 (
    echo 依赖安装失败
    pause
    exit /b 1
)
echo.
echo 安装完成，双击「启动.cmd」即可运行。
pause
