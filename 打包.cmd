@echo off
chcp 936 >nul
cd /d "%~dp0"

REM 一键打包：PyInstaller + 把给朋友的说明放到 exe 旁边
REM 产物：发布\Ping\（含自带 adb），再手动压 zip 就能发人

if not exist ".venv\Scripts\pyinstaller.exe" (
    echo 未找到虚拟环境
    exit /b 1
)

cd packaging
"..\.venv\Scripts\pyinstaller.exe" app.spec --noconfirm --distpath "../发布" --workpath build > build.log 2>&1
findstr /C:"Build complete" /C:"ERROR" /C:"错误" build.log
cd ..

if not exist "发布\Ping\Ping.exe" (
    echo.
    echo 打包失败，详见 packaging\build.log
    exit /b 1
)

copy /y "packaging\好友上手说明.txt" "发布\Ping\好友上手说明.txt" >nul
echo.
echo 打包完成：发布\Ping\
echo 发给朋友时压缩整个「Ping」文件夹即可（说明已放在 exe 旁边）。
