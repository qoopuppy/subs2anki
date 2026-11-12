@echo off
cd /d "%~dp0"
echo ============================================================
echo   Subs2Anki 离线示例自测
echo ============================================================
echo.
call "%~dp0_find_python.bat"
if errorlevel 1 goto :nopython
set /p PYEXE=<"%~dp0runtime-python.txt"
echo 使用 Python: %PYEXE%
echo.
"%PYEXE%" -m server --demo
echo.
echo 产物在 out 目录里，示例音频是合成提示音（占位），不是真实语音。
echo 想验证真实 ffmpeg 切片，运行： python tools\selftest_real_audio.py
pause
exit /b 0

:nopython
echo [X] 没找到可用的 Python 3.10+。请先双击 setup.bat。
echo.
pause
exit /b 1
