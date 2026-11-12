@echo off
cd /d "%~dp0"
echo ============================================================
echo   Subs2Anki 本地服务  http://127.0.0.1:8766
echo   这个窗口请保持开着；关掉后插件会降级成纯文字 TSV
echo   Ctrl+C 可停止
echo ============================================================
echo.
echo 正在查找 Python ...
call "%~dp0_find_python.bat"
if errorlevel 1 goto :nopython
set /p PYEXE=<"%~dp0runtime-python.txt"
echo 使用 Python: %PYEXE%
echo.
"%PYEXE%" -m server
echo.
echo [!] 服务已退出。如果上面有报错，把这段内容复制给助手看。
pause
exit /b 0

:nopython
echo [X] 没找到可用的 Python 3.10+。
echo     请先双击 setup.bat 完成安装；若仍失败，见 README.md。
echo.
pause
exit /b 1
