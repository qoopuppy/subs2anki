"""模拟「双击 .bat」的真实环境来排查启动脚本。

双击时 cmd 用的是 系统 PATH（Machine + User），而不是当前终端被注入的 PATH。
用法：python tools/simulate_double_click.py setup.bat
"""
from __future__ import annotations

import os
import subprocess
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def system_path() -> str:
    parts = []
    for hive, sub in [
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ]:
        try:
            with winreg.OpenKey(hive, sub) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                parts.append(os.path.expandvars(value))
        except OSError:
            pass
    return ";".join(parts)


def main() -> int:
    bat = sys.argv[1] if len(sys.argv) > 1 else "setup.bat"
    env = os.environ.copy()
    env["PATH"] = system_path()
    print("模拟双击环境（系统 PATH）")
    print("PATH 中含 python 的项：")
    for item in env["PATH"].split(";"):
        if "python" in item.lower() or "mgltools" in item.lower():
            print("   ", item.strip())
    print("-" * 60)
    proc = subprocess.run(
        [r"C:\Windows\System32\cmd.exe", "/c", bat],
        cwd=str(ROOT), env=env, input="\r\n" * 6,
        capture_output=True, text=True, encoding="gbk", errors="replace",
        timeout=400,
    )
    print(proc.stdout[:8000])
    if proc.stderr.strip():
        print("--- stderr ---")
        print(proc.stderr[:2000])
    print("-" * 60)
    print("returncode:", proc.returncode)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
