@echo off
rem Find a usable Python 3.10+ and cache its absolute path in runtime-python.txt
rem Some machines have an old Python (e.g. MGLTools' 2.7) earlier in PATH,
rem so "python" alone is NOT safe. Prefer the Windows launcher "py -3".
set "PYEXE="
set "RT=%~dp0runtime-python.txt"

if not exist "%RT%" goto :nocache
set /p CACHED=<"%RT%"
if not defined CACHED goto :nocache
"%CACHED%" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if not errorlevel 1 exit /b 0
:nocache

for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if defined PYEXE goto :verify

for %%p in (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
) do if not defined PYEXE if exist %%p set "PYEXE=%%~p"
if defined PYEXE goto :verify

for /f "delims=" %%i in ('where python 2^>nul') do if not defined PYEXE (
  "%%i" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
  if not errorlevel 1 set "PYEXE=%%i"
)

:verify
if not defined PYEXE exit /b 1
"%PYEXE%" -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
> "%RT%" echo %PYEXE%
exit /b 0
