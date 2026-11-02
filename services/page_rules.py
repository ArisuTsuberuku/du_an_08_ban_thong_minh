# -*- coding: utf-8 -*-
"""
Chặn THEO TỪNG LINK / TRANG (services/page_rules.py)

Phụ huynh bấm "Chặn link này" trên web (hoặc Gemini đánh giá 1 trang là giải trí) -> chỉ trang đó bị chặn, không chặn
cả tên miền. Vd: chặn 1 video YouTube ca nhạc nhưng vẫn xem được video bài giảng.

Mỗi luật: {"d": tên miền, "t": tiêu đề đã chuẩn hóa, "u": khóa trang (vd "v=abc123" của YouTube, hoặc đường dẫn)}.
Khớp khi: cùng tên miền VÀ (khóa trang có trong URL đang mở HOẶC tiêu đề cửa sổ chuẩn hóa trùng tiêu đề đã chặn).
Bản sao hàm norm_title / page_key nằm trong cloud/app.py — sửa ở đây thì sửa cả bên đó.
"""

import re
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qs, urlparse

# Trang "nhiều nội dung" — cùng tên miền có cả học tập lẫn giải trí -> theo dõi & phân loại theo TỪNG trang
CONTENT_DOMAINS = ("youtube.com", "youtu.be", "google.com", "google.com.vn", "bing.com", "coccoc.com",
                   "vimeo.com", "dailymotion.com")
# Tên trang chủ: chặn "link" trang chủ = chặn cả web (mọi video đều đi qua trang chủ) -> không cho chặn riêng
GENERIC_PAGE_NAMES = ("youtube", "google", "google tìm kiếm", "bing", "cốc cốc", "coc coc", "vimeo", "dailymotion", "")
_BROWSER_SUFFIX = (" - microsoft edge", " - google chrome", " - cốc cốc", " - coc coc", " - mozilla firefox",
                   " — mozilla firefox", " - brave", " - opera", " - vivaldi")


def norm_title(title: str) -> str:
    """Tiêu đề cửa sổ trình duyệt -> dạng so sánh được (bỏ số thông báo '(50)', 'and 2 more pages', tên trình duyệt)."""
    t = (title or "")
    for ch in ("​", "‎", "‏", "‪", "‬"):
        t = t.replace(ch, "")
    t = t.lower().strip()
    t = re.sub(r"^\(\d+\+?\)\s*", "", t)
    t = re.sub(r"\s+and \d+ more pages?\b", "", t)
    t = re.sub(r"\s+và \d+ trang khác\b", "", t)
    for suf in _BROWSER_SUFFIX:
        i = t.find(suf)
        if i > 0:
            t = t[:i]
    return re.sub(r"\s+", " ", t).strip()[:100]


def domain_matches(dom: str, base: str) -> bool:
    dom, base = (dom or "").lower(), (base or "").lower()
    return bool(base) and (dom == base or dom.endswith("." + base))


def is_content_domain(dom: str) -> bool:
    return any(domain_matches(dom, c) for c in CONTENT_DOMAINS)


def page_key(url: str) -> str:
    """Khóa định danh 1 trang trong URL: YouTube -> 'v=<id>' / 'shorts/<id>'; trang khác -> đường dẫn (không query)."""
    if not url:
        return ""
    u = url if "://" in url else "https://" + url
    try:
        p = urlparse(u)
    except Exception:
        return ""
    host = (p.hostname or "").lower()
    path = (p.path or "").rstrip("/")
    if domain_matches(host, "youtube.com"):
        v = (parse_qs(p.query).get("v") or [""])[0]
        if v:
            return f"v={v[:20]}"
        q = (parse_qs(p.query).get("search_query") or [""])[0].strip().lower()
        if q:
            return f"search={q[:80]}"
        m = re.match(r"^/(shorts|live)/([\w-]{6,20})", path)
        return f"{m.group(1)}/{m.group(2)}" if m else ""
    if domain_matches(host, "youtu.be"):
        return f"v={path.strip('/')[:20]}" if path.strip("/") else ""
    if domain_matches(host, "google.com") or domain_matches(host, "bing.com") or domain_matches(host, "coccoc.com"):
        q = (parse_qs(p.query).get("q") or [""])[0].strip().lower()
        return f"q={q[:80]}" if q else ""
    return path[:120] if len(path) > 1 else ""


def is_generic_page(dom: str, title: str, page: str = "") -> bool:
    """Trang chủ / trang chung của web nhiều nội dung (vd tiêu đề chỉ là "YouTube") -> KHÔNG phải 1 link riêng."""
    if page or not is_content_domain(dom):
        return False
    first = norm_title(title).split(" - ")[0].strip()
    return first in GENERIC_PAGE_NAMES


def match_blocked_page(url: str, title: str, rules: Optional[Iterable[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    """Trả về luật chặn khớp với trang đang mở, hoặc None."""
    if not rules:
        return None
    u = (url or "").lower()
    host = ""
    if u:
        try:
            host = (urlparse(u if "://" in u else "https://" + u).hostname or "").lower()
        except Exception:
            host = ""
    key = page_key(url) if url else ""
    nt = norm_title(title)
    for r in rules:
        if not isinstance(r, dict):
            continue
        d = str(r.get("d", "")).lower()
        if host and d and not domain_matches(host, d):
            continue
        rk, rt = str(r.get("u", "")).lower(), str(r.get("t", ""))
        if rk and key and rk == key.lower():
            return r
        if rk and key:
            continue    # luật có mã trang (vd 1 video) + đang mở trang có mã khác -> KHÔNG xét tiêu đề
        if is_generic_page(d or host, rt):
            continue    # luật "trang chủ YouTube / Google" tạo nhầm = chặn cả web -> bỏ qua
        # Tiêu đề khớp: bằng nhau, hoặc 1 bên là tiêu đề TAB ("X - youtube"), bên kia là tiêu đề CỬA SỔ
        # ("X - youtube - personal": thêm tên hồ sơ trình duyệt ở cuối)
        if rt and nt and (rt == nt or nt.startswith(rt + " - ") or rt.startswith(nt + " - ")):
            # Không có URL (UIA đọc lỗi) thì chỉ tin tiêu đề khi tiêu đề đủ dài để không trùng nhầm
            if host or len(rt) >= 12:
                return r
    return None
