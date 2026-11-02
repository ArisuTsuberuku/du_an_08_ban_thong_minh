# -*- coding: utf-8 -*-
"""
Bộ Giám Sát Ngầm Thông Minh (services/giam_sat_worker.py)
Kế thừa 100% biến, hàm và cơ chế từ giam_sat.pyw của nhóm, đồng thời nâng cấp:
  1. Đọc trực tiếp Link Address (URL) trên thanh địa chỉ trình duyệt bằng uiautomation (có khởi tạo COM).
  2. Phân biệt chính xác trang học tập (ALLOWED_URLS & bài giảng YouTube theo ACADEMIC_KW) với trang giải trí/Shorts.
  3. Theo dõi thời gian tra cứu ChatGPT / Chatbot AI và cảnh báo khi vượt ngưỡng MAX_AI_MINUTES.
  4. Quét tiêu diệt tiến trình Game chạy ngầm (BAD_APPS) và thống kê chính xác thời gian mở ứng dụng Whitelist.
"""

import ctypes
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse

import psutil
import pyautogui
import pygetwindow as gw
import requests

from core.config_manager import (
    load_config,
    load_runtime_state,
    safe_log,
    update_runtime_state,
)
from core.failsafe import check_and_consume_unlock_file, emergency_restore_and_exit

# Bảo toàn cấu hình an toàn cho PyAutoGUI từ file giam_sat.pyw gốc của nhóm
pyautogui.FAILSAFE = False

# Hằng số mặc định gốc của nhóm (tự động đồng bộ từ shared_config.json khi chạy)
BAD_TABS = ["facebook", "tiktok", "shopee", "phim", "youtube", "game", "truyện"]
BAD_APPS = ["roblox", "league", "valorant", "steam", "fc_online", "genshin"]
URL_WEB = "http://127.0.0.1:5000/api/nhan_canh_bao"
URL_STATS = "http://127.0.0.1:5000/api/cap_nhat_thong_ke"

# Chatbot AI tính giờ (khớp trong URL hoặc tiêu đề cửa sổ)
DEFAULT_AI_DOMAINS = ["chatgpt.com", "chat.openai.com", "gemini.google.com", "claude.ai", "copilot.microsoft.com",
                      "chat.deepseek.com", "perplexity.ai", "poe.com", "grok.com", "chat.qwen.ai", "meta.ai",
                      "chat.mistral.ai", "you.com"]
AI_TITLE_HINTS = ["chatgpt", "gemini", "copilot", "deepseek", "perplexity", "grok"]
# Học sinh nhờ AI viết game rồi chơi ngay trong khung Canvas / Artifact của AI:
#  - Tiêu đề cuộc trò chuyện (ChatGPT, Claude, Gemini đặt theo nội dung) có từ khóa game
#  - Mở trang chạy ứng dụng do AI tạo (khung chạy Gemini Canvas, Claude Artifact, các web "AI tạo game")
AI_GAME_KEYWORDS = ["game", "trò chơi", "tro choi", "snake", "rắn săn mồi", "tetris", "flappy", "mario", "pacman",
                    "pac-man", "minecraft", "2048", "platformer", "shooter", "bắn súng", "đua xe", "racing",
                    "pygame", "phaser", "html5 canvas game", "đánh nhau", "zombie", "sudoku game", "caro", "cờ caro"]
AI_GAME_EXCEPTIONS = ["game theory", "lý thuyết trò chơi", "gamification", "game-based learning"]
AI_SANDBOX_DOMAINS = ["usercontent.goog", "claudeusercontent.com", "claude.site", "websim.ai", "websim.com",
                      "rosebud.ai", "scf.usercontent"]
# Phím điều khiển game: mũi tên, Space, W A S D
GAME_KEYS = {0x25, 0x26, 0x27, 0x28, 0x20, 0x57, 0x41, 0x53, 0x44}
# Ứng dụng AI cài trên máy (tên .exe, dùng * thay nhiều ký tự) -> nhãn hiển thị. Phụ huynh/Admin sửa được qua "ai_apps".
DEFAULT_AI_APPS = {
    "chatgpt.exe": "ChatGPT", "claude.exe": "Claude", "copilot.exe": "Copilot", "m365copilot.exe": "Copilot",
    "microsoft.copilot.exe": "Copilot", "perplexity.exe": "Perplexity", "deepseek*.exe": "DeepSeek",
    "gemini*.exe": "Gemini", "grok*.exe": "Grok", "poe.exe": "Poe", "lm studio.exe": "LM Studio",
    "lmstudio.exe": "LM Studio", "ollama app.exe": "Ollama", "jan.exe": "Jan AI", "msty.exe": "Msty",
    "chatbox.exe": "Chatbox AI", "cherry studio.exe": "Cherry Studio", "cursor.exe": "Cursor (AI code)",
    "windsurf.exe": "Windsurf (AI code)", "monica*.exe": "Monica AI", "doubao*.exe": "Doubao",
}
IDLE_PAUSE_S = 120.0      # không chạm phím/chuột > 2 phút -> ngừng tính giờ AI & ứng dụng

# YouTube: mặc định CHỈ chặn tìm kiếm / video có từ khóa giải trí, bài giảng vẫn xem được
DEFAULT_YT_KEYWORDS = ["free fire", "liên quân", "lien quan", "minecraft", "roblox", "pubg", "genshin", "gameplay",
                       "game", "mukbang", "prank", "asmr", "tiktok", "review phim", "phim", "anime", "mv ",
                       "nhạc remix", "remix", "vlog", "livestream game", "highlight"]


def _idle_seconds() -> float:
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
        return (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) / 1000.0
    except Exception:
        return 0.0


def _youtube_query(url_l: str) -> str:
    """Từ khóa người dùng gõ trên YouTube (youtube.com/results?search_query=...)."""
    try:
        q = parse_qs(urlparse(url_l if "://" in url_l else "https://" + url_l).query)
        return " ".join(q.get("search_query", []) + q.get("q", [])).strip().lower()
    except Exception:
        return ""


def danh_gia_youtube(url_l: str, title_l: str, cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Luật riêng cho YouTube (None = không phải trang YouTube):
      - youtube_mode = "block": chặn toàn bộ; "allow": cho hết; "keyword" (mặc định): chỉ chặn khi
        TỪ KHÓA TÌM KIẾM hoặc TIÊU ĐỀ VIDEO chứa từ khóa cấm, trừ khi có từ khóa bài giảng (toán, vật lý...).
      - youtube_block_shorts: chặn YouTube Shorts (video ngắn gây nghiện).
    """
    is_yt = ("youtube.com" in url_l or "youtu.be" in url_l or title_l.endswith("- youtube")
             or " - youtube -" in title_l)
    if not is_yt:
        return None
    mode = str(cfg.get("youtube_mode", "keyword")).lower()
    ok = {"exempt": False, "is_ai": False, "should_block": False, "reason": ""}
    if mode == "allow":
        return ok
    if mode == "block":
        return {**ok, "should_block": True, "reason": "YouTube đang bị chặn toàn bộ"}
    if cfg.get("youtube_block_shorts", True) and ("/shorts" in url_l or title_l.startswith("#shorts")):
        return {**ok, "should_block": True, "reason": "YouTube Shorts (video ngắn)"}
    query = _youtube_query(url_l)
    title = title_l.replace("- youtube", "").strip()
    text = f" {query} {title} "
    kws = [k.lower() for k in (cfg.get("youtube_keywords") or DEFAULT_YT_KEYWORDS) if k and k.strip()]
    kws += [t.lower() for t in cfg.get("bad_tabs", []) if t and t.lower() not in ("youtube", "youtu.be")]
    hit = next((k for k in kws if k.strip() and k in text), None)
    if not hit:
        return ok
    academic = [k.lower() for k in cfg.get("academic_keywords", [])]
    if any(a and a in text for a in academic) and hit.strip() not in ("free fire", "liên quân", "roblox", "minecraft"):
        return ok   # "bài giảng vật lý ... game" -> vẫn cho học
    src = f"tìm kiếm “{query}”" if query else f"video “{title_l[:60]}”"
    return {**ok, "should_block": True, "reason": f"YouTube {src} (từ khóa '{hit.strip()}')"}


BROWSER_PROCESS_NAMES: Set[str] = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "browser.exe",  # Trình duyệt Cốc Cốc trên Windows
    "coccoc.exe",
    "firefox.exe",
    "opera.exe",
}

try:
    import win32gui
    import win32process
    import uiautomation as auto

    UIA_AVAILABLE = True
except ImportError:
    UIA_AVAILABLE = False


def _get_web_endpoints(cfg: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    port = int((cfg or {}).get("web_port", 5000))
    if port == 5000:
        return URL_WEB, URL_STATS
    base = f"http://127.0.0.1:{port}"
    return f"{base}/api/nhan_canh_bao", f"{base}/api/cap_nhat_thong_ke"


def gui_canh_bao_len_web(noi_dung: str, event_type: str = "GAME") -> bool:
    """
    Hàm gốc của nhóm gửi cảnh báo vi phạm lên Bảng điều khiển Web (kích hoạt luôn Hook Telegram).
    Nếu Web Server tạm thời chưa mở, vẫn ghi trực tiếp vào runtime_state.json để không mất dữ liệu.
    """
    cfg = load_config()
    alert_url, _ = _get_web_endpoints(cfg)
    try:
        resp = requests.post(
            alert_url,
            json={"message": noi_dung, "event_type": event_type},
            timeout=1.8,
        )
        if resp.status_code == 200:
            return True
    except Exception:
        pass

    # Fallback lưu trực tiếp vào runtime_state.json nếu gọi HTTP thất bại
    try:
        state = load_runtime_state()
        alerts = list(state.get("alerts", []))
        alerts.insert(0, {"time": time.strftime("%H:%M:%S"), "message": noi_dung, "type": event_type})
        del alerts[50:]
        update_runtime_state(alerts=alerts)
    except Exception:
        pass
    return False


def lay_thong_tin_cua_so_active() -> Tuple[str, str, bool]:
    """
    Trả về (window_title, process_name_lower, is_browser).
    Chỉ khi is_browser == True mới cần quét uiautomation để tiết kiệm tối đa tài nguyên CPU.
    """
    title = ""
    proc_name = ""
    try:
        active_win = gw.getActiveWindow()
        if active_win is not None and active_win.title:
            title = active_win.title.strip()
    except Exception:
        pass

    if UIA_AVAILABLE:
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                if not title:
                    title = (win32gui.GetWindowText(hwnd) or "").strip()
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid > 0:
                    proc_name = (psutil.Process(pid).name() or "").lower()
        except Exception:
            pass

    is_browser = proc_name in BROWSER_PROCESS_NAMES
    if not is_browser and title:
        tl = title.lower()
        if any(b in tl for b in ("google chrome", "microsoft​ edge", "microsoft edge", "cốc cốc", "brave", "mozilla firefox")):
            is_browser = True
    return title, proc_name, is_browser


def lay_link_address_trinh_duyet(is_browser: bool = True) -> str:
    """
    Đọc trực tiếp Link Address (URL) trên thanh địa chỉ của Chrome / Edge / Cốc Cốc / Brave / Firefox.
    Yêu cầu luồng gọi đã khởi tạo COM (CoInitialize / UIAutomationInitializerInThread).
    """
    if not UIA_AVAILABLE or not is_browser:
        return ""
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        ctrl = auto.ControlFromHandle(hwnd)
        # Tìm thanh địa chỉ (EditControl đầu tiên của trình duyệt chứa chuỗi URL)
        edit = ctrl.EditControl()
        if edit.Exists(0, 0):
            val = edit.GetValuePattern().Value
            if val:
                return val.strip().lower()
    except Exception:
        pass
    return ""


def ai_app_label(proc_name: str, cfg: Optional[Dict[str, Any]] = None) -> str:
    """Tên ứng dụng AI nếu tiến trình là app AI trên máy (ChatGPT, Claude, Copilot, LM Studio…), ngược lại ""."""
    import fnmatch

    name = (proc_name or "").lower()
    if not name:
        return ""
    apps = dict(DEFAULT_AI_APPS)
    apps.update({str(k).lower(): str(v) for k, v in ((cfg or {}).get("ai_apps") or {}).items()})
    for pat, label in apps.items():
        if fnmatch.fnmatch(name, pat):
            return label
    return ""


def ai_web_label(current_url: str, window_title: str, cfg: Dict[str, Any]) -> str:
    """Tên miền AI đang mở trên trình duyệt (chatgpt.com, gemini.google.com…), ngược lại ""."""
    combined = f"{(current_url or '').lower()} {(window_title or '').lower()}"
    for d in [d.lower() for d in (cfg.get("ai_domains") or DEFAULT_AI_DOMAINS)]:
        if d in combined:
            return d
    t = (window_title or "").lower()
    for h in AI_TITLE_HINTS:
        if h in t:
            return h
    return ""


def _key_stats(window_s: float = 60.0) -> Tuple[int, int]:
    """(tổng phím, phím điều khiển game) học sinh bấm trong `window_s` giây qua — đếm bởi hook bàn phím của kiosk."""
    try:
        from services.kiosk_guard import key_stats

        return key_stats(window_s)
    except Exception:
        return 0, 0


def _reset_key_stats() -> None:
    try:
        from services.kiosk_guard import reset_key_stats

        reset_key_stats()
    except Exception:
        pass


def _dong_app_ai(cfg: Dict[str, Any]) -> List[str]:
    """Đóng mọi ứng dụng AI cài trên máy (ChatGPT, Claude, Copilot…): gửi lệnh đóng cửa sổ, 3 giây sau còn thì tắt hẳn."""
    procs, names = [], set()
    for p in psutil.process_iter(["pid", "name"]):
        lbl = ai_app_label(p.info.get("name") or "", cfg)
        if lbl and p.pid != os.getpid():
            procs.append(p)
            names.add(lbl)
    if not procs:
        return []
    pids = {p.pid for p in procs}
    try:
        user32 = ctypes.windll.user32
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def _cb(hwnd, _lp):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.IsWindowVisible(hwnd):
                user32.PostMessageW(hwnd, 0x0010, 0, 0)       # WM_CLOSE
            return True

        user32.EnumWindows(WNDENUMPROC(_cb), 0)
    except Exception:
        pass

    def _ket_thuc() -> None:
        _gone, alive = psutil.wait_procs(procs, timeout=3)
        for p in alive:
            try:
                p.terminate()
            except Exception:
                pass

    threading.Thread(target=_ket_thuc, name="CloseAIApps", daemon=True).start()
    return sorted(names)


def _dong_cua_so_hien_tai(is_browser: bool) -> None:
    """Đóng tab trình duyệt đang mở (Ctrl+W) hoặc cửa sổ ứng dụng đang ở trên cùng (WM_CLOSE)."""
    try:
        if is_browser:
            pyautogui.hotkey("ctrl", "w")
        else:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
    except Exception:
        pass


def ai_game_reason(current_url: str, window_title: str, cfg: Dict[str, Any]) -> str:
    """Lý do nếu đang mở GAME DO AI TẠO (khung chạy Canvas/Artifact, hoặc cuộc trò chuyện AI về game), ngược lại ""."""
    url_l = (current_url or "").lower()
    title_l = (window_title or "").lower()
    for d in [str(x).lower() for x in (cfg.get("ai_sandbox_domains") or AI_SANDBOX_DOMAINS)]:
        if d and d in url_l:
            return f"Mở ứng dụng/game do AI tạo ({d})"
    if any(e in title_l for e in AI_GAME_EXCEPTIONS):
        return ""
    for k in [str(x).lower() for x in (cfg.get("ai_game_keywords") or AI_GAME_KEYWORDS)]:
        if k and k in title_l:
            return f"Dùng AI để làm/chơi game: «{(window_title or '').strip()[:70]}» (từ khóa '{k}')"
    return ""


def phan_loai_truy_cap_web(
    current_url: str,
    window_title: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Hàm thuần (pure function) đánh giá truy cập hiện tại theo Link Address & Tiêu đề Tab:
    Trả về dict:
      {
        "exempt": bool,          # Miễn trừ (Bảng điều khiển bàn học / localhost)
        "is_ai": bool,           # Có đang mở Chatbot AI (ChatGPT, Gemini, Claude, Copilot) không
        "should_block": bool,    # Có cần nhấn Ctrl+W đóng tab không
        "reason": str,           # Lý do hiển thị trong cảnh báo vi phạm
      }
    """
    url_l = (current_url or "").strip().lower()
    title_orig = (window_title or "").strip()
    title_l = title_orig.lower()
    port = int(cfg.get("web_port", 5000))

    # 1. Miễn trừ Bảng Điều Khiển Bàn Học & giao diện quản lý nội bộ
    if (
        "bảng điều khiển bàn học" in title_l
        or "hệ thống quản lý bàn học thông minh" in title_l
        or "focusdesk" in title_l
        or f"127.0.0.1:{port}" in url_l
        or f"localhost:{port}" in url_l
        or url_l.endswith(f":{port}/")
        or url_l.endswith(f":{port}")
    ):
        return {"exempt": True, "is_ai": False, "should_block": False, "reason": ""}

    # 0a. Link / trang cụ thể phụ huynh chặn (hoặc Gemini đánh giá là giải trí) — chỉ trang đó, không cả tên miền
    if cfg.get("blocked_pages"):
        try:
            from services.page_rules import match_blocked_page

            hit = match_blocked_page(current_url, window_title, cfg.get("blocked_pages"))
        except Exception:
            hit = None
        if hit:
            return {"exempt": False, "is_ai": False, "should_block": True,
                    "reason": f"Link bị chặn: {title_orig[:70]}" + (f" ({hit.get('why')})" if hit.get("why") else "")}

    ai_domains = [d.lower() for d in (cfg.get("ai_domains") or DEFAULT_AI_DOMAINS)]
    allowed_urls = [u.lower() for u in cfg.get("allowed_urls", [])]
    bad_urls = [u.lower() for u in cfg.get("bad_urls", [])]
    bad_tabs = [t.lower() for t in cfg.get("bad_tabs", BAD_TABS)]
    academic_kw = [k.lower() for k in cfg.get("academic_keywords", [])]

    combined = f"{url_l} {title_l}"
    game_action = str(cfg.get("ai_game_action", "block")).lower()
    # 0. Trang chạy game/ứng dụng do AI tạo (Gemini Canvas, Claude Artifact, websim...) = trang game
    if url_l and game_action != "off" and any(
            d in url_l for d in [str(x).lower() for x in (cfg.get("ai_sandbox_domains") or AI_SANDBOX_DOMAINS)]):
        return {"exempt": False, "is_ai": False, "should_block": game_action == "block", "ai_game": True,
                "reason": ai_game_reason(current_url, window_title, cfg)}
    is_ai = any(ai in combined for ai in ai_domains) or (not url_l and any(
        h in title_l for h in AI_TITLE_HINTS) and any(b in title_l for b in ("chrome", "edge", "cốc cốc", "brave", "firefox")))
    if is_ai:
        # Chatbot AI được phép mở (bộ đếm giám sát ngưỡng max_ai_minutes) — TRỪ KHI đang nhờ AI làm/chơi game
        why = ai_game_reason(current_url, window_title, cfg) if game_action != "off" else ""
        if why:
            return {"exempt": False, "is_ai": True, "should_block": game_action == "block", "ai_game": True,
                    "reason": why}
        return {"exempt": False, "is_ai": True, "should_block": False, "reason": ""}

    # 1b. YouTube: chặn theo từ khóa tìm kiếm / tiêu đề thay vì chặn cả trang
    yt = danh_gia_youtube(url_l, title_l, cfg)
    if yt is not None:
        return yt

    # 2. Nếu URL khớp trực tiếp danh sách Link học tập được phép (olm.vn, vietjack.com, scholar.google...)
    if url_l and any(good in url_l for good in allowed_urls):
        return {"exempt": False, "is_ai": False, "should_block": False, "reason": ""}

    # 3. Nếu URL nằm trong danh sách Link cấm cứng (bao gồm youtube.com/shorts, facebook.com, poki.com...)
    if url_l:
        matched_bad_url = next((bu for bu in bad_urls if bu in url_l), None)
        if matched_bad_url:
            return {
                "exempt": False,
                "is_ai": False,
                "should_block": True,
                "reason": f"Link cấm: {current_url} (khớp '{matched_bad_url}')",
            }

    # 4. Cho phép xem bài giảng học thuật hợp lệ (VD: youtube.com/watch có từ khóa Toán, Vật lý, Lập trình...)
    co_tu_khoa_hoc_tap = any(kw in title_l for kw in academic_kw)
    if co_tu_khoa_hoc_tap:
        # Chỉ miễn trừ nếu KHÔNG chứa các nền tảng mạng xã hội/game thuần túy trong tiêu đề
        strict_social_or_game = ("facebook", "tiktok", "shopee", "poki", "y8", "roblox")
        if not any(sg in title_l for sg in strict_social_or_game):
            return {"exempt": False, "is_ai": False, "should_block": False, "reason": ""}

    # 5. Kiểm tra theo danh sách từ khóa tiêu đề cấm (BAD_TABS gốc của nhóm)
    if title_l:
        matched_bad_tab = next((bt for bt in bad_tabs if bt in title_l), None)
        if matched_bad_tab:
            return {
                "exempt": False,
                "is_ai": False,
                "should_block": True,
                "reason": f"Tab giải trí: {title_orig} (từ khóa '{matched_bad_tab}')",
            }

    return {"exempt": False, "is_ai": False, "should_block": False, "reason": ""}


class BoGiamSatThongMinh:
    """Luồng chạy nền giám sát Web, Quota Chatbot AI và Ứng dụng khi ở Chế độ Học sinh."""

    def __init__(self, on_remote_lock: Optional[Callable[[], None]] = None):
        self.dang_chay = False
        self._worker_thread: Optional[threading.Thread] = None
        self.on_remote_lock = on_remote_lock

        state = load_runtime_state()
        today = time.strftime("%Y-%m-%d")
        same_day = state.get("usage_day") in (None, today)
        self.app_usage: Dict[str, float] = dict(state.get("app_usage", {})) if same_day else {}
        self.ai_seconds: float = float(state.get("ai_seconds", 0)) if same_day else 0.0
        # Giờ AI gộp chung app AI trên máy + web AI, có tách theo nguồn: {"ChatGPT (ứng dụng)": giây, "chatgpt.com (web)": …}
        self.ai_sources: Dict[str, float] = dict(state.get("ai_sources", {})) if same_day else {}
        self._last_ai_scan = 0.0
        self._last_ai_game_alert = 0.0
        self._last_ai_block_alert = 0.0
        lock = state.get("ai_dns_lock") or {}
        self._ai_locked = isinstance(lock, dict) and lock.get("day") == today
        self._usage_day = today
        self._ai_reset_seen = float(state.get("ai_reset_at", 0) or 0)
        self._last_ai_alert_at_sec: float = 0

    def bat_dau(self) -> None:
        if self.dang_chay:
            return
        self.dang_chay = True
        update_runtime_state(monitoring_active=True)
        # Khóa AI của HÔM QUA còn trong file hosts (máy tắt trước nửa đêm) -> gỡ ngay
        try:
            lock = load_runtime_state().get("ai_dns_lock") or {}
            if lock and lock.get("day") != time.strftime("%Y-%m-%d"):
                from services.hosts_blocker import sync_hosts_for_role

                update_runtime_state(ai_dns_lock={})
                sync_hosts_for_role()
        except Exception:
            pass
        self._worker_thread = threading.Thread(
            target=self._vong_lap_giam_sat,
            name="GiamSatWorkerThread",
            daemon=True,
        )
        self._worker_thread.start()

    def dung_lai(self) -> None:
        self.dang_chay = False
        try:
            update_runtime_state(monitoring_active=False)
        except Exception:
            pass

    def _gui_thong_ke(self, cfg: Dict[str, Any]) -> None:
        _, stats_url = _get_web_endpoints(cfg)
        try:
            usage = {k: int(v) for k, v in self.app_usage.items()}
            requests.post(
                stats_url,
                json={"app_usage": usage, "ai_seconds": int(self.ai_seconds)},
                timeout=0.9,
            )
            update_runtime_state(usage_day=self._usage_day, ai_sources={k: int(v) for k, v in self.ai_sources.items()})
        except Exception:
            update_runtime_state(app_usage={k: int(v) for k, v in self.app_usage.items()},
                                 ai_seconds=int(self.ai_seconds), usage_day=self._usage_day,
                                 ai_sources={k: int(v) for k, v in self.ai_sources.items()})

    def _dat_khoa_ai(self, lock: bool, cfg: Dict[str, Any], max_ai_min: int) -> None:
        """Hết giờ AI + phụ huynh chọn 'Hết giờ thì chặn' -> KHÓA AI tới hết ngày:
        đóng mọi app AI đang chạy, chặn DNS các web AI (tab AI tải lại không vào được), báo phụ huynh 1 lần.
        Sang ngày mới / phụ huynh đặt lại giờ / nâng giới hạn -> tự mở khóa."""
        if lock == self._ai_locked:
            return
        self._ai_locked = lock
        today = time.strftime("%Y-%m-%d")
        try:
            from services.hosts_blocker import AI_LOCK_DOMAINS, sync_hosts_for_role

            update_runtime_state(ai_dns_lock={"day": today, "domains": list(AI_LOCK_DOMAINS)} if lock else {})
            sync_hosts_for_role(cfg=cfg)
        except Exception as exc:
            safe_log(f"[AI] Không cập nhật được chặn DNS web AI: {exc}")
        if lock:
            closed = _dong_app_ai(cfg)
            gui_canh_bao_len_web(
                f"⛔ Hết {max_ai_min} phút dùng AI hôm nay → đã KHÓA AI tới hết ngày: chặn web AI"
                + (f", đóng {', '.join(closed)}" if closed else "") + ". Phụ huynh có thể 'Đặt lại giờ AI' để mở.",
                "AI_OVERUSE")
        else:
            safe_log("[AI] Mở khóa AI (ngày mới / phụ huynh đặt lại / nâng giới hạn).")

    def _kiem_tra_dat_lai(self, state: Dict[str, Any]) -> None:
        """Sang ngày mới -> đếm lại từ 0; phụ huynh bấm 'Đặt lại giờ AI' (web/Telegram) -> về 0 ngay."""
        today = time.strftime("%Y-%m-%d")
        if today != self._usage_day:
            self._usage_day = today
            self.ai_seconds = 0.0
            self.ai_sources = {}
            self.app_usage = {}
            self._last_ai_alert_at_sec = 0
        reset_at = float(state.get("ai_reset_at", 0) or 0)
        if reset_at > self._ai_reset_seen:
            self._ai_reset_seen = reset_at
            self.ai_seconds = 0.0
            self.ai_sources = {}
            self._last_ai_alert_at_sec = 0

    def _vong_lap_giam_sat(self) -> None:
        # Khởi tạo COM bắt buộc trên Windows cho uiautomation khi chạy trong Thread phụ
        com_initialized = False
        try:
            ctypes.windll.ole32.CoInitialize(None)
            com_initialized = True
        except Exception:
            pass

        if UIA_AVAILABLE:
            try:
                auto.uiautomation.SetGlobalSearchTimeout(0.15)
            except Exception:
                pass

        tick_count = 0
        last_t = time.monotonic()
        try:
            while self.dang_chay:
                # Thời gian THỰC giữa 2 vòng quét (UIA + quét tiến trình mất 0.3-2s) thay cho cộng cứng 1s/vòng
                now_m = time.monotonic()
                dt = max(0.0, min(5.0, now_m - last_t))
                last_t = now_m
                active = _idle_seconds() < IDLE_PAUSE_S
                # 1. Kiểm tra công tắc cứu hộ UNLOCK.txt
                if check_and_consume_unlock_file():
                    emergency_restore_and_exit("Phát hiện file cứu hộ UNLOCK.txt trong luồng giám sát", 0)
                    return

                # 2. Kiểm tra lệnh Khóa máy từ xa (từ Web Dashboard hoặc Bot Telegram ở Giai đoạn 7)
                state = load_runtime_state()
                if state.get("remote_lock_requested"):
                    update_runtime_state(remote_lock_requested=False)
                    if self.on_remote_lock is not None:
                        try:
                            self.on_remote_lock()
                        except Exception:
                            pass
                        break

                self._kiem_tra_dat_lai(state)
                cfg = load_config()
                try:
                    max_ai_min = max(0, int(cfg.get("max_ai_minutes", 15)))   # 0 = không cho dùng AI
                except (TypeError, ValueError):
                    max_ai_min = 15
                over_action = str(cfg.get("ai_over_action", "block")).lower()
                self._dat_khoa_ai(over_action == "block" and self.ai_seconds >= max_ai_min * 60, cfg, max_ai_min)
                bad_apps = [a.lower() for a in cfg.get("bad_apps", BAD_APPS)]
                allowed_map = {
                    a["exe_name"].lower(): a["name"]
                    for a in cfg.get("allowed_apps", [])
                    if a.get("exe_name")
                }

                # --- LỚP PHÒNG THỦ 1: QUÉT LINK ADDRESS (URL) & TIÊU ĐỀ CỬA SỔ ---
                fg_proc = ""
                try:
                    win_title, fg_proc, is_browser = lay_thong_tin_cua_so_active()
                    if win_title or is_browser:
                        current_url = lay_link_address_trinh_duyet(is_browser) if is_browser else ""
                        verdict = phan_loai_truy_cap_web(current_url, win_title, cfg)
                        # Gộp chung: app AI trên máy (đang ở trên cùng) cũng tính như web AI
                        app_ai = ai_app_label(fg_proc, cfg)
                        if app_ai and not verdict.get("exempt"):
                            g = ai_game_reason("", win_title, cfg) if str(cfg.get("ai_game_action", "block")) != "off" else ""
                            verdict = {**verdict, "is_ai": True, "ai_game": bool(g), "reason": g or verdict.get("reason", ""),
                                       "should_block": bool(g) and str(cfg.get("ai_game_action", "block")) == "block"}
                            ai_src = f"{app_ai} (ứng dụng)"
                        elif verdict["is_ai"]:
                            ai_src = f"{ai_web_label(current_url, win_title, cfg) or 'AI'} (web)"
                        else:
                            ai_src = ""

                        game_action = str(cfg.get("ai_game_action", "block")).lower()
                        # Nhận diện theo HÀNH VI: đang ở AI mà bấm liên tục mũi tên / WASD / Space = đang chơi game AI tạo
                        if verdict["is_ai"] and active and not verdict.get("ai_game") and game_action != "off":
                            tot, gamey = _key_stats(60)
                            if gamey >= 40 and gamey >= 0.6 * tot:
                                verdict = {**verdict, "ai_game": True, "should_block": game_action == "block",
                                           "reason": f"Bấm phím điều khiển liên tục ({gamey} lần mũi tên/WASD/Space "
                                                     f"trong 1 phút) khi đang mở {ai_src or 'AI'} — nghi chơi game do AI tạo"}

                        if verdict.get("ai_game"):
                            now_w = time.time()
                            if verdict["should_block"]:
                                _dong_cua_so_hien_tai(is_browser)
                                gui_canh_bao_len_web(f"🎮🤖 Đã chặn: {verdict['reason']}", "AI_GAME")
                                _reset_key_stats()
                                time.sleep(1.5)
                            elif now_w - self._last_ai_game_alert > 120:
                                self._last_ai_game_alert = now_w
                                gui_canh_bao_len_web(f"🎮🤖 Nghi chơi game trong AI: {verdict['reason']}", "AI_GAME")

                        if verdict["is_ai"] and self._ai_locked and not verdict.get("ai_game"):
                            # Đang khóa AI: tab / app AI vừa mở lại -> đóng ngay (kể cả khi chưa thao tác)
                            _dong_cua_so_hien_tai(is_browser)
                            if time.time() - self._last_ai_block_alert > 120:
                                self._last_ai_block_alert = time.time()
                                gui_canh_bao_len_web(f"⛔ Học sinh cố mở lại {ai_src or 'AI'} khi đã hết giờ AI — đã đóng",
                                                     "AI_OVERUSE")
                            time.sleep(1.0)
                        elif verdict["is_ai"] and active and not (verdict.get("ai_game") and verdict["should_block"]):
                            # Chỉ tính khi tab AI đang ở trên cùng VÀ học sinh đang thao tác (không tính lúc bỏ máy)
                            self.ai_seconds += dt
                            self.ai_sources[ai_src] = self.ai_sources.get(ai_src, 0.0) + dt
                            threshold_sec = max_ai_min * 60
                            # Cảnh báo ngay khi chạm ngưỡng và nhắc lại mỗi 5 phút nếu vẫn cố dùng
                            if self.ai_seconds >= threshold_sec and (
                                self._last_ai_alert_at_sec == 0
                                or self.ai_seconds - self._last_ai_alert_at_sec >= 300
                            ):
                                self._last_ai_alert_at_sec = self.ai_seconds
                                used_min = round(self.ai_seconds / 60.0, 1)
                                top = sorted(self.ai_sources.items(), key=lambda kv: -kv[1])[:3]
                                chi_tiet = ", ".join(f"{k} {round(v / 60)}′" for k, v in top)
                                msg = (
                                    f"🤖 Học sinh đã dùng AI (ứng dụng + web) tổng cộng {used_min} phút "
                                    f"— vượt giới hạn {max_ai_min} phút. Chi tiết: {chi_tiet}"
                                )
                                gui_canh_bao_len_web(msg, "AI_OVERUSE")
                            # Phụ huynh chọn "Hết giờ thì chặn": đóng tab AI / cửa sổ app AI ngay khi vượt giới hạn
                            if self.ai_seconds >= threshold_sec and over_action == "block":
                                _dong_cua_so_hien_tai(is_browser)
                                if time.time() - self._last_ai_block_alert > 60:
                                    self._last_ai_block_alert = time.time()
                                    gui_canh_bao_len_web(
                                        f"⛔ Đã đóng {ai_src or 'AI'}: hết {max_ai_min} phút dùng AI hôm nay", "AI_OVERUSE")
                                time.sleep(1.5)

                        elif verdict["should_block"] and is_browser and not verdict.get("ai_game"):
                            pyautogui.hotkey("ctrl", "w")
                            msg = f"🚨 Đã tự động đóng Tab giải trí: [{verdict['reason']}]"
                            gui_canh_bao_len_web(msg, "GAME")
                            time.sleep(1.5)
                except Exception as exc:
                    safe_log(f"[WARN] Lỗi quét cửa sổ Web: {exc}")

                # --- AI ĐANG CHẠY (app AI mở trên máy, kể cả không ở trên cùng) — 15 giây/lần, chỉ để báo cáo ---
                if time.monotonic() - self._last_ai_scan >= 15:
                    self._last_ai_scan = time.monotonic()
                    try:
                        running = sorted({lbl for lbl in (ai_app_label((p.info.get("name") or ""), cfg)
                                                          for p in psutil.process_iter(["name"])) if lbl})
                        update_runtime_state(ai_running=running,
                                             ai_sources={k: int(v) for k, v in self.ai_sources.items()})
                        if running and self._ai_locked:
                            _dong_app_ai(cfg)
                    except Exception:
                        pass

                # --- LỚP PHÒNG THỦ 2: QUÉT & TIÊU DIỆT GAME NGẦM + ĐẾM GIỜ ỨNG DỤNG ---
                try:
                    # Giờ dùng ứng dụng = thời gian ứng dụng đó ĐANG Ở TRÊN CÙNG (không tính app chạy nền)
                    if active and fg_proc and fg_proc in allowed_map:
                        label = f"{allowed_map[fg_proc]} ({fg_proc})"
                        self.app_usage[label] = self.app_usage.get(label, 0) + dt
                    for proc in psutil.process_iter(["pid", "name"]):
                        pname = proc.info.get("name")
                        if not pname:
                            continue
                        pname_l = pname.lower()

                        # Diệt ngay nếu khớp từ khóa Game cấm trong BAD_APPS
                        matched_game = next((g for g in bad_apps if g in pname_l), None)
                        if matched_game:
                            try:
                                proc.kill()
                                msg = f"🎮 Đã tự động tiêu diệt ứng dụng Game chạy ngầm: [{pname}]"
                                gui_canh_bao_len_web(msg, "GAME")
                            except Exception:
                                pass
                            continue

                except Exception:
                    pass

                tick_count += 1
                if tick_count % 2 == 0:
                    self._gui_thong_ke(cfg)

                time.sleep(1.0)
        finally:
            if com_initialized:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
