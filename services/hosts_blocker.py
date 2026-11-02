# -*- coding: utf-8 -*-
"""
Chặn tên miền ở mức hệ điều hành bằng file hosts (port từ DuAnAI/tracker.py, có gia cố).

- Mọi trình duyệt / ứng dụng đều không phân giải được tên miền bị chặn (khác cách đóng tab dễ lách).
- Chỉ ghi các dòng có đánh dấu MARKER, không đụng dòng khác của người dùng; gỡ sạch khi danh sách rỗng
  hoặc khi kích hoạt khôi phục khẩn cấp.
- Cần quyền Administrator (SmartDeskLauncher.exe chạy --uac-admin). Không đủ quyền -> trả False, không làm gì.
- Giới hạn: trình duyệt bật "DNS bảo mật" (DoH) có thể bỏ qua hosts; bộ giám sát URL vẫn đóng tab như lớp 2.
"""

import os
import subprocess
import sys
from typing import Iterable, List, Tuple

HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
MARKER = "# SMARTDESK-BLOCK"
# Không bao giờ chặn các miền hệ thống / dịch vụ của chính SmartDesk
SAFE_DOMAINS = ("vercel.app", "trycloudflare.com", "cloudflare.com", "firebasedatabase.app", "googleapis.com",
                "gstatic.com", "google.com", "telegram.org", "microsoft.com", "windowsupdate.com", "localhost")


def _normalize(domains: Iterable[str]) -> List[str]:
    out = []
    for d in domains:
        d = str(d).strip().lower().replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        if d.startswith("www."):
            d = d[4:]
        if not d or "." not in d or any(d == s or d.endswith("." + s) for s in SAFE_DOMAINS):
            continue
        if d not in out:
            out.append(d)
    return out[:300]


def apply_hosts_block(domains: Iterable[str]) -> Tuple[bool, str]:
    """Ghi lại khối chặn trong hosts. Trả về (thành_công, mô_tả)."""
    if not sys.platform.startswith("win"):
        return False, "Không phải Windows"
    doms = _normalize(domains)
    try:
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        keep = [ln for ln in lines if MARKER not in ln]
        if keep and not keep[-1].endswith("\n"):
            keep[-1] += "\n"
        for d in doms:
            keep.append(f"0.0.0.0 {d} {MARKER}\n")
            keep.append(f"0.0.0.0 www.{d} {MARKER}\n")
            keep.append(f"0.0.0.0 m.{d} {MARKER}\n")
        if keep == lines:
            return True, f"Không đổi ({len(doms)} tên miền)"
        with open(HOSTS_PATH, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=flags, timeout=10)
        return True, f"Đã chặn DNS {len(doms)} tên miền"
    except PermissionError:
        return False, "Thiếu quyền Admin để ghi file hosts (chạy SmartDeskLauncher.exe để chặn DNS)"
    except Exception as exc:
        return False, f"Lỗi ghi hosts: {exc}"


def clear_hosts_block() -> Tuple[bool, str]:
    return apply_hosts_block([])
