@echo off
cd /d "%~dp0"
echo ============================================================
echo   Subs2Anki 安装依赖（只需要运行一次）
echo ============================================================
echo.
echo [1/4] 查找可用的 Python 3.10+ ...
call "%~dp0_find_python.bat"
if errorlevel 1 goto :nopython
set /p PYEXE=<"%~dp0runtime-python.txt"
echo       %PYEXE%
"%PYEXE%" -c "import sys;print('       version', sys.version.split()[0])"
echo.
echo [2/4] 安装 Python 依赖（genanki / yt-dlp）...
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :pipfail
echo.
echo [3/4] 生成扩展图标...
"%PYEXE%" tools\make_icons.py
if errorlevel 1 goto :iconfail
echo.
echo [4/4] 检查工具是否齐全...
"%PYEXE%" -m server --check
echo.
echo [OK] 装好了。接下来：
echo       1) 双击 start.bat，那个黑窗口保持开着
echo       2) chrome://extensions 打开开发者模式，加载 extension 文件夹
echo       详细步骤见 README.md
echo.
pause
exit /b 0

:nopython
echo [X] 没找到可用的 Python 3.10 以上版本。
echo     请到 python.org 安装 Python 3.10+，安装时勾选
echo     Add python.exe to PATH，然后重新双击本文件。
echo     详见 README.md
echo.
pause
exit /b 1

:pipfail
echo [X] 依赖安装失败。检查网络或代理后重试。
echo     也可以手动运行：
echo       "%PYEXE%" -m pip install -r requirements.txt
echo.
pause
exit /b 1

:iconfail
echo [X] 图标生成失败。不影响主功能，可继续用，但扩展没有图标。
echo.
pause
exit /b 1
