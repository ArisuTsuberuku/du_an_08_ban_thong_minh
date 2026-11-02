# -*- coding: utf-8 -*-
"""
Quét ứng dụng đã cài trên Windows (services/app_scanner.py) — để phụ huynh tick chọn thay vì tự tìm file .exe.

Nguồn: lối tắt (.lnk) trong Start Menu của máy (ProgramData) và của người dùng (AppData) — đúng danh sách
ứng dụng người dùng nhìn thấy trong menu Start. Lối tắt được giải ra file .exe đích bằng WScript.Shell (pywin32);
bỏ qua trình gỡ cài đặt, trợ giúp, trang web, tiến trình hệ thống.
"""

import os
import sys
from typing import Dict, List

SKIP_WORDS = ("uninstall", "gỡ cài đặt", "gỡ bỏ", "readme", "help", "trợ giúp", "license", "website", "manual",
              "documentation", "release notes", "update", "updater", "repair", "setup")
SKIP_EXES = {"explorer.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "regedit.exe", "taskmgr.exe", "mmc.exe",
             "msiexec.exe", "rundll32.exe", "control.exe", "python.exe", "pythonw.exe", "wscript.exe", "cscript.exe",
             "focusdesklauncher.exe", "unins000.exe", "conhost.exe"}


def _start_menu_dirs() -> List[str]:
    dirs = []
    pd = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
    ad = os.environ.get("APPDATA", "")
    dirs.append(os.path.join(pd, "Microsoft", "Windows", "Start Menu", "Programs"))
    if ad:
        dirs.append(os.path.join(ad, "Microsoft", "Windows", "Start Menu", "Programs"))
    return [d for d in dirs if os.path.isdir(d)]


def scan_installed_apps() -> List[Dict[str, str]]:
    """[{name, exe_name, path, folder}] sắp theo tên; trùng file .exe chỉ giữ 1."""
    if not sys.platform.startswith("win"):
        return []
    try:
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("WScript.Shell")
    except Exception:
        return []
    seen: Dict[str, Dict[str, str]] = {}
    for root in _start_menu_dirs():
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.lower().endswith(".lnk"):
                    continue
                name = os.path.splitext(fn)[0]
                if any(w in name.lower() for w in SKIP_WORDS):
                    continue
                try:
                    target = shell.CreateShortCut(os.path.join(dirpath, fn)).Targetpath or ""
                except Exception:
                    continue
                if not target.lower().endswith(".exe") or not os.path.isfile(target):
                    continue
                exe = os.path.basename(target)
                if exe.lower() in SKIP_EXES or any(w in exe.lower() for w in ("unins", "uninst", "setup", "update")):
                    continue
                key = target.lower()
                if key not in seen:
                    seen[key] = {"name": name, "exe_name": exe, "path": target,
                                 "folder": os.path.relpath(dirpath, root) if dirpath != root else ""}
    return sorted(seen.values(), key=lambda a: a["name"].lower())
