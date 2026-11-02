# -*- coding: utf-8 -*-
"""
Chặn tên miền ở mức hệ điều hành bằng file hosts (port từ DuAnAI/tracker.py, có gia cố).

- Mọi trình duyệt / ứng dụng đều không phân giải được tên miền bị chặn (khác cách đóng tab dễ lách).
- Chỉ ghi các dòng có đánh dấu MARKER, không đụng dòng khác của người dùng; gỡ sạch khi danh sách rỗng
  hoặc khi kích hoạt khôi phục khẩn cấp.
- Cần quyền Administrator (FocusDeskLauncher.exe chạy --uac-admin). Không đủ quyền -> trả False, không làm gì.
- Giới hạn: trình duyệt bật "DNS bảo mật" (DoH) có thể bỏ qua hosts; bộ giám sát URL vẫn đóng tab như lớp 2.
"""

import os
import subprocess
import sys
from typing import Iterable, List, Tuple

HOSTS_PATH = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
MARKER = "# FOCUSDESK-BLOCK"
LEGACY_MARKERS = ("# SMARTDESK-BLOCK",)   # tên cũ trước khi đổi thương hiệu -> vẫn gỡ sạch
# Không bao giờ chặn các miền hệ thống / dịch vụ của chính FocusDesk
SAFE_DOMAINS = ("vercel.app", "trycloudflare.com", "cloudflare.com", "firebasedatabase.app", "googleapis.com",
                "gstatic.com", "google.com", "telegram.org", "microsoft.com", "windowsupdate.com", "localhost")


# Web AI nằm dưới tên miền "an toàn" (google.com, microsoft.com) nhưng vẫn phải chặn được khi hết giờ AI
SAFE_EXCEPTIONS = ("gemini.google.com", "bard.google.com", "aistudio.google.com", "notebooklm.google.com",
                   "copilot.microsoft.com")
# Web AI bị chặn DNS khi học sinh dùng hết giờ AI trong ngày (phụ huynh chọn "Hết giờ thì chặn")
AI_LOCK_DOMAINS = ("chatgpt.com", "chat.openai.com", "gemini.google.com", "aistudio.google.com", "claude.ai",
                   "copilot.microsoft.com", "chat.deepseek.com", "perplexity.ai", "poe.com", "grok.com",
                   "chat.qwen.ai", "meta.ai", "chat.mistral.ai", "you.com")


def _ai_lock_domains() -> List[str]:
    """Danh sách web AI đang bị khóa HÔM NAY (lưu ở runtime_state, tự hết hiệu lực khi sang ngày mới)."""
    try:
        import time

        from core.config_manager import load_runtime_state

        lock = load_runtime_state().get("ai_dns_lock") or {}
        if isinstance(lock, dict) and lock.get("day") == time.strftime("%Y-%m-%d"):
            return [str(d) for d in lock.get("domains") or []]
    except Exception:
        pass
    return []


def rules_dns_list(cfg) -> List[str]:
    """Tên miền chặn DNS theo luật phụ huynh (YouTube chế độ 'theo từ khóa' thì KHÔNG chặn DNS cả trang)."""
    if not cfg.get("dns_block_enabled", True):
        return []
    yt_doms = ("youtube.com", "youtu.be", "m.youtube.com", "www.youtube.com")
    return [d for d in (cfg.get("bad_urls") or [])
            if cfg.get("youtube_mode", "keyword") == "block" or d not in yt_doms]


def _normalize(domains: Iterable[str]) -> List[str]:
    out = []
    for d in domains:
        d = str(d).strip().lower().replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        if d.startswith("www."):
            d = d[4:]
        if not d or "." not in d or (d not in SAFE_EXCEPTIONS
                                     and any(d == s or d.endswith("." + s) for s in SAFE_DOMAINS)):
            continue
        if d not in out:
            out.append(d)
    return out[:300]


def apply_hosts_block(domains: Iterable[str]) -> Tuple[bool, str]:
    """Ghi lại khối chặn trong hosts. Trả về (thành_công, mô_tả)."""
    if not sys.platform.startswith("win"):
        return False, "Không phải Windows"
    doms = _normalize(list(domains) + _ai_lock_domains())   # luôn giữ khóa AI của hôm nay (nếu có)
    try:
        with open(HOSTS_PATH, "rb") as fh:
            _save_original_once(fh.read())
        with open(HOSTS_PATH, "r", encoding="utf-8", errors="ignore") as fh:
            lines = fh.readlines()
        keep = [ln for ln in lines if MARKER not in ln and not any(m in ln for m in LEGACY_MARKERS)]
        if keep and not keep[-1].endswith("\n"):
            keep[-1] += "\n"
        for d in doms:
            keep.append(f"0.0.0.0 {d} {MARKER}\n")
            keep.append(f"0.0.0.0 www.{d} {MARKER}\n")
            keep.append(f"0.0.0.0 m.{d} {MARKER}\n")
        if keep == lines:
            return True, f"Không đổi ({len(doms)} tên miền)"
        if not doms:                       # không còn gì để chặn -> trả nguyên bản gốc
            return clear_hosts_block()
        with open(HOSTS_PATH, "w", encoding="utf-8") as fh:
            fh.writelines(keep)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=flags, timeout=10)
        return True, f"Đã chặn DNS {len(doms)} tên miền"
    except PermissionError:
        return False, "Thiếu quyền Admin để ghi file hosts (chạy FocusDeskLauncher.exe để chặn DNS)"
    except Exception as exc:
        return False, f"Lỗi ghi hosts: {exc}"


def _backup_path() -> str:
    try:
        from core.config_manager import get_app_dir

        return os.path.join(get_app_dir(), "hosts_goc.bak")
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hosts_goc.bak")


def _save_original_once(raw: bytes) -> None:
    """Lần ĐẦU FocusDesk sửa hosts: lưu nguyên văn file gốc (bỏ dòng FocusDesk sót lại nếu có) để trả lại y hệt."""
    bp = _backup_path()
    if os.path.exists(bp):
        return
    try:
        text = raw.decode("utf-8", errors="surrogateescape")
        if MARKER in text or any(m in text for m in LEGACY_MARKERS):
            keep = [ln for ln in text.splitlines(keepends=True)
                    if MARKER not in ln and not any(m in ln for m in LEGACY_MARKERS)]
            raw = "".join(keep).encode("utf-8", errors="surrogateescape")
        with open(bp, "wb") as fh:
            fh.write(raw)
    except Exception:
        pass


def clear_hosts_block() -> Tuple[bool, str]:
    """Tắt FocusDesk: gỡ MỌI dòng FocusDesk (kể cả khóa AI) và trả file hosts về ĐÚNG như trước khi FocusDesk sửa.
    Nếu trong lúc chạy người dùng tự sửa hosts thì giữ phần họ sửa, chỉ gỡ dòng của FocusDesk."""
    if not sys.platform.startswith("win"):
        return False, "Không phải Windows"
    bp = _backup_path()
    try:
        with open(HOSTS_PATH, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8", errors="surrogateescape")
        rest = "".join(ln for ln in text.splitlines(keepends=True)
                       if MARKER not in ln and not any(m in ln for m in LEGACY_MARKERS))
        out = rest.encode("utf-8", errors="surrogateescape")
        if os.path.exists(bp):
            with open(bp, "rb") as fh:
                orig = fh.read()

            def norm(b: bytes) -> str:
                return b.decode("utf-8", errors="ignore").replace("\r\n", "\n").rstrip()

            if norm(orig) == norm(out):
                out = orig                       # y hệt bản gốc (kể cả xuống dòng cuối file, CRLF)
        if out != raw:
            with open(HOSTS_PATH, "wb") as fh:
                fh.write(out)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=flags, timeout=10)
        try:
            os.remove(bp)
        except OSError:
            pass
        return True, "Đã trả file hosts về như ban đầu"
    except PermissionError:
        return False, "Thiếu quyền Admin để sửa file hosts"
    except Exception as exc:
        return False, f"Lỗi khôi phục hosts: {exc}"
