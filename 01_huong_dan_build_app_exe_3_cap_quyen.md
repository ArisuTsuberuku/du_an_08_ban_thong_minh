# TÀI LIỆU KỸ THUẬT SỐ 1: XÂY DỰNG PHẦN MỀM KHÓA MÁY 3 CẤP QUYỀN (.EXE) KIỂU GCAFE / CSM & TÍCH HỢP MODULE KIỂM SOÁT WEB

## 1. Mục tiêu & Đặc tả Giao việc cho Antigravity (Prompt Blueprint)

> **Prompt mẫu để bạn copy dán thẳng vào Antigravity:**
> "Hãy đóng vai kỹ sư phần mềm hệ thống Windows. Dựa trên tài liệu đặc tả `01_huong_dan_build_app_exe_3_cap_quyen.md` này, hãy xây dựng ứng dụng Desktop chạy trên Windows (đóng gói thành file `.exe` yêu cầu quyền Administrator) có giao diện khóa toàn màn hình kiểu phần mềm quản lý phòng máy Gcafe/CSM. Hệ thống phải đọc/ghi cấu hình từ file `shared_config.json`, phân quyền 3 cấp (Admin, Phụ huynh, Học sinh), tự động vô hiệu hóa Task Manager gốc của Windows qua Registry khi ở quyền Học sinh, cung cấp một 'Student Task Manager' riêng chỉ cho phép đóng các ứng dụng trong Whitelist (ẩn tuyệt đối `explorer.exe` và các tiến trình hệ thống), đồng thời có cổng tích hợp (Background Thread/Subprocess Hook) để khởi chạy ngầm module Python kiểm soát Web và gửi cảnh báo sang module Telegram Bot."

---

## 2. Kiến trúc Phân quyền 3 Cấp & Cơ chế Bảo mật Hệ điều hành

### 2.1. Bảng phân quyền chi tiết
| Cấp tài khoản | Giao diện sau khi đăng nhập | Quyền hạn hệ thống Windows | Quyền cấu hình & Giám sát |
| :--- | :--- | :--- | :--- |
| **1. Admin (Quản trị viên)** | Thoát hoàn toàn lớp vỏ khóa (Shell), trả lại Desktop Windows chuẩn. | Mở lại `explorer.exe`, mở khóa Task Manager gốc (`DisableTaskMgr = 0`), mở Regedit/CMD. | Cài đặt/xóa phần mềm hệ thống, cấu hình cổng COM nối vi điều khiển `ESP32-S3`, sửa mã nguồn. |
| **2. Phụ huynh (Parent)** | Bảng điều khiển Quản lý Học tập + Sử dụng máy tính bình thường. | Mở lại `explorer.exe`, mở Task Manager gốc, nhưng khóa các tệp cấu hình lõi của hệ thống. | Chọn ứng dụng (`.exe`) và thư mục học sinh được phép mở; thêm/xóa link web cấm; đặt giới hạn phút dùng AI; xem thống kê thời gian sử dụng. |
| **3. Học sinh (Student)** | **Custom Student Shell** (Màn hình chọn ứng dụng học tập kiểu Gcafe, ẩn thanh Taskbar và Desktop). | Tắt `explorer.exe` (hoặc khóa phím `Win`, `Alt+F4`, `Alt+Tab`), khóa cứng Task Manager gốc (`DisableTaskMgr = 1`). | Chỉ mở được các ứng dụng và thư mục trong Whitelist; chỉ dùng được **Task Manager bản Học sinh**; tự động kích hoạt chạy nền chương trình Python kiểm soát Web. |

### 2.2. Cơ chế Chống lỗi Đen màn hình (Anti-Brick Fail-safe)
Các phần mềm phòng máy như Gcafe/CSM thường ghi đè khóa `Winlogon\Shell` trong Registry. Tuy nhiên, để đảm bảo an toàn tuyệt đối khi chạy thử nghiệm trên máy tính cá nhân:
1. Khởi chạy file `FocusDeskLauncher.exe` cùng Windows thông qua **Windows Task Scheduler** (chế độ *Run with highest privileges*).
2. Khi vào chế độ **Khóa màn hình** hoặc **Chế độ Học sinh**, phần mềm đặt thuộc tính cửa sổ `-fullscreen` và `-topmost`, đồng thời thực thi lệnh ngầm `taskkill /f /im explorer.exe` để thu hồi thanh Taskbar và biểu tượng Desktop.
3. Ngay khi đăng nhập quyền **Phụ huynh** hoặc **Admin** (hoặc khi chương trình gặp ngoại lệ), hàm bảo vệ `restore_windows_shell()` sẽ tự động gọi lại `subprocess.Popen("explorer.exe")` và mở khóa Registry `DisableTaskMgr = 0`.

---

## 3. Cấu trúc Tệp Cấu hình Dùng chung (`shared_config.json`)

Tạo tệp `shared_config.json` nằm cùng thư mục với file `.exe`. Tệp này là cầu nối dữ liệu giữa **App khóa máy (.exe)**, **Chương trình Python kiểm soát Web** và **Website / Bot Telegram của Phụ huynh**:

```json
{
  "passwords": {
    "admin": "admin123",
    "parent": "phuhuynh123",
    "student": "hocsinh"
  },
  "allowed_apps": [
    {
      "name": "Google Chrome (Học tập)",
      "exe_name": "chrome.exe",
      "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
    },
    {
      "name": "Visual Studio Code",
      "exe_name": "Code.exe",
      "path": "C:\\Users\\Public\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe"
    },
    {
      "name": "Máy tính (Calculator)",
      "exe_name": "CalculatorApp.exe",
      "path": "calc.exe"
    },
    {
      "name": "Sổ tay (Notepad)",
      "exe_name": "notepad.exe",
      "path": "notepad.exe"
    }
  ],
  "allowed_folders": [
    "D:\\TaiLieuHocTap",
    "C:\\Users\\Public\\Documents"
  ],
  "web_policy": {
    "blocked_domains": ["poki.com", "y8.com", "roblox.com", "facebook.com", "tiktok.com", "discord.com"],
    "ai_domains": ["chatgpt.com", "claude.ai", "gemini.google.com"],
    "max_ai_minutes": 15,
    "grace_period_seconds": 5
  },
  "telegram_config": {
    "bot_token": "YOUR_BOT_TOKEN_HERE",
    "chat_id": "YOUR_CHAT_ID_HERE",
    "enable_alerts": true,
    "alert_on_game": true,
    "alert_on_wrong_login": true,
    "alert_on_ai_overuse": true,
    "cooldown_seconds": 30
  }
}
```

---

## 4. Mã nguồn Hoàn chỉnh: `focus_desk_launcher.py`

Mã nguồn dưới đây đã thiết kế sẵn **Cổng tích hợp (Integration Hook)** tại hàm `start_background_web_monitor()` để bạn ghép nối trực tiếp module Python kiểm soát Web của thành viên thứ 3 vào:

```python
import os
import sys
import json
import time
import winreg
import ctypes
import subprocess
import threading
import psutil
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

CONFIG_FILE = "shared_config.json"

# =====================================================================
# 1. QUẢN LÝ FILE CẤU HÌNH DÙNG CHUNG (SHARED CONFIG)
# =====================================================================
def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_cfg = {
            "passwords": {"admin": "admin123", "parent": "phuhuynh123", "student": "hocsinh"},
            "allowed_apps": [
                {"name": "Google Chrome", "exe_name": "chrome.exe", "path": r"C:\Program Files\Google\Chrome\Application\chrome.exe"},
                {"name": "Notepad", "exe_name": "notepad.exe", "path": "notepad.exe"},
                {"name": "Calculator", "exe_name": "CalculatorApp.exe", "path": "calc.exe"}
            ],
            "allowed_folders": [r"C:\Users\Public\Documents"],
            "web_policy": {
                "blocked_domains": ["poki.com", "y8.com", "roblox.com", "facebook.com"],
                "ai_domains": ["chatgpt.com", "claude.ai", "gemini.google.com"],
                "max_ai_minutes": 15,
                "grace_period_seconds": 5
            },
            "telegram_config": {
                "bot_token": "YOUR_BOT_TOKEN_HERE",
                "chat_id": "YOUR_CHAT_ID_HERE",
                "enable_alerts": True,
                "alert_on_game": True,
                "alert_on_wrong_login": True,
                "alert_on_ai_overuse": True,
                "cooldown_seconds": 30
            }
        }
        save_config(default_cfg)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

# =====================================================================
# 2. CƠ CHẾ KHÓA HỆ THỐNG WINDOWS (REGISTRY & EXPLORER SHELL)
# =====================================================================
def set_windows_task_manager_lock(lock_state: bool):
    """
    lock_state = True  -> Khóa cứng Task Manager gốc của Windows (DisableTaskMgr = 1)
    lock_state = False -> Mở lại Task Manager gốc cho Phụ huynh / Admin (DisableTaskMgr = 0)
    """
    try:
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisableTaskMgr", 0, winreg.REG_DWORD, 1 if lock_state else 0)
        winreg.CloseKey(key)
    except Exception as e:
        print(f"[WARN] Cần quyền Administrator để ghi Registry: {e}")

def set_desktop_explorer_state(enable_explorer: bool):
    """Ẩn hoặc khôi phục giao diện Desktop/Taskbar (explorer.exe) giống Gcafe"""
    try:
        if not enable_explorer:
            subprocess.run(["taskkill", "/f", "/im", "explorer.exe"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            # Kiểm tra nếu explorer.exe chưa chạy thì bật lại
            running = [p.info['name'].lower() for p in psutil.process_iter(['name']) if p.info['name']]
            if "explorer.exe" not in running:
                subprocess.Popen("explorer.exe")
    except Exception as e:
        print(f"[ERR] Lỗi điều khiển explorer.exe: {e}")

# =====================================================================
# 3. CỔNG TÍCH HỢP MODULE KIỂM SOÁT WEB & BOT TELEGRAM
# =====================================================================
class BackgroundMonitorHook:
    """
    Class này dùng để gọi module kiểm soát web của Thành viên 3
    và gửi cảnh báo qua module Telegram Bot (File hướng dẫn số 2).
    """
    def __init__(self):
        self.running = False
        self.worker_thread = None
        self.usage_stats = {} # Lưu thời gian sử dụng từng ứng dụng (giây)

    def send_alert_to_telegram(self, event_type, message):
        """Gọi sang module telegram_service.py (nếu đã ghép file) hoặc ghi log"""
        try:
            from telegram_service import trigger_parent_alert
            trigger_parent_alert(event_type, message)
        except ImportError:
            print(f"[TELEGRAM HOOK - {event_type}] {message}")

    def start_monitoring(self):
        if self.running:
            return
        self.running = True
        self.worker_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.worker_thread.start()

    def stop_monitoring(self):
        self.running = False

    def _monitor_loop(self):
        """
        Vòng lặp chạy nền mỗi 2 giây khi Học sinh đang trong phiên học:
        1. Thống kê thời gian sử dụng ứng dụng cho Phụ huynh tra cứu.
        2. Chặn các phần mềm nằm ngoài Whitelist (nếu học sinh cố bật từ đường dẫn phụ).
        3. Gọi hàm kiểm tra Link Web (của Thành viên 3).
        """
        SYSTEM_WHITELIST = {
            "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "services.exe",
            "lsass.exe", "svchost.exe", "fontdrvhost.exe", "winlogon.exe", "dwm.exe",
            "sihost.exe", "taskhostw.exe", "conhost.exe", "ctfmon.exe", "python.exe",
            "pythonw.exe", "focusdesklauncher.exe", "cmd.exe"
        }

        while self.running:
            cfg = load_config()
            allowed_exes = {item["exe_name"].lower() for item in cfg["allowed_apps"]}

            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    pname = (proc.info['name'] or "").lower()
                    if pname in allowed_exes:
                        self.usage_stats[pname] = self.usage_stats.get(pname, 0) + 2
                except Exception:
                    continue

            time.sleep(2.0)

monitor_hook = BackgroundMonitorHook()

# =====================================================================
# 4. CỬA SỔ "TASK MANAGER BẢN HỌC SINH" (STUDENT TASK MANAGER)
# =====================================================================
def open_student_task_manager(parent_root):
    """
    Task Manager thu gọn dành riêng cho Học sinh:
    - Chỉ hiển thị các ứng dụng nằm trong danh sách học sinh được phép mở.
    - Ẩn tuyệt đối explorer.exe, python.exe và các tiến trình hệ thống.
    """
    cfg = load_config()
    allowed_exes = {item["exe_name"].lower() for item in cfg["allowed_apps"]}

    win = tk.Toplevel(parent_root)
    win.title("Task Manager - Phiên Bản Học Sinh")
    win.geometry("560x380")
    win.configure(bg="#111827")
    win.attributes("-topmost", True)

    tk.Label(win, text="QUẢN LÝ ỨNG DỤNG ĐANG CHẠY (CHỈ ĐƯỢC TẮT ỨNG DỤNG HỌC TẬP)",
             bg="#111827", fg="#38bdf8", font=("Segoe UI", 10, "bold")).pack(pady=12)

    columns = ("pid", "name", "ram")
    tree = ttk.Treeview(win, columns=columns, show="headings", height=11)
    tree.heading("pid", text="PID")
    tree.heading("name", text="Tên Ứng Dụng Cho Phép")
    tree.heading("ram", text="RAM Đang Dùng")
    tree.column("pid", width=90, anchor="center")
    tree.column("name", width=280, anchor="w")
    tree.column("ram", width=130, anchor="center")
    tree.pack(padx=16, fill="x")

    def refresh_student_procs():
        for item in tree.get_children():
            tree.delete(item)
        for p in psutil.process_iter(['pid', 'name', 'memory_info']):
            try:
                pname = (p.info['name'] or "").lower()
                # Ràng buộc cốt lõi: Chỉ hiện app trong Whitelist và KHÔNG bao giờ hiện explorer.exe
                if pname in allowed_exes and pname != "explorer.exe":
                    mem_mb = round(p.info['memory_info'].rss / (1024 * 1024), 1)
                    tree.insert("", "end", values=(p.info['pid'], p.info['name'], f"{mem_mb} MB"))
            except Exception:
                continue

    def kill_selected():
        sel = tree.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn", "Vui lòng chọn một ứng dụng trong danh sách để đóng!", parent=win)
            return
        pid, name, _ = tree.item(sel[0])["values"]
        if str(name).lower() == "explorer.exe":
            return
        try:
            psutil.Process(int(pid)).terminate()
            time.sleep(0.3)
            refresh_student_procs()
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đóng ứng dụng: {e}", parent=win)

    btn_bar = tk.Frame(win, bg="#111827")
    btn_bar.pack(pady=14)
    tk.Button(btn_bar, text="🔄 Làm mới", command=refresh_student_procs,
              bg="#334155", fg="white", font=("Segoe UI", 9, "bold"), padx=12, pady=5).pack(side="left", padx=8)
    tk.Button(btn_bar, text="❌ Kết thúc ứng dụng (Kill Task)", command=kill_selected,
              bg="#e11d48", fg="white", font=("Segoe UI", 9, "bold"), padx=12, pady=5).pack(side="left", padx=8)

    refresh_student_procs()

# =====================================================================
# 5. GIAO DIỆN CHÍNH 3 CẤP TÀI KHOẢN (GCAFE / CSM STYLE)
# =====================================================================
class FocusDeskApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Smart Study Desk - Cyber-Physical Access Control")
        self.root.configure(bg="#090d16")
        self.failed_attempts = 0

        # Chặn Alt+F4 đóng cửa sổ trái phép
        self.root.protocol("WM_DELETE_WINDOW", self.disable_close_event)
        self.show_lock_screen()

    def disable_close_event(self):
        pass

    def clear_ui(self):
        for w in self.root.winfo_children():
            w.destroy()

    # -----------------------------------------------------------------
    # MÀN HÌNH ĐĂNG NHẬP BAN ĐẦU (TOÀN MÀN HÌNH)
    # -----------------------------------------------------------------
    def show_lock_screen(self):
        self.clear_ui()
        monitor_hook.stop_monitoring()
        set_windows_task_manager_lock(True)
        # Lưu ý: Khi triển khai thật, bỏ comment dòng dưới để tắt explorer.exe ngay ở màn hình khóa
        # set_desktop_explorer_state(False)

        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)

        card = tk.Frame(self.root, bg="#1e293b", padx=45, pady=40,
                        highlightbackground="#06b6d4", highlightthickness=2)
        card.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(card, text="HỆ THỐNG BÀN HỌC THÔNG MINH", bg="#1e293b", fg="#22d3ee",
                 font=("Segoe UI", 18, "bold")).pack(pady=(0, 6))
        tk.Label(card, text="Kiểm soát Kỷ luật Số & Phân quyền Truy cập 3 Cấp", bg="#1e293b", fg="#94a3b8",
                 font=("Segoe UI", 10)).pack(pady=(0, 24))

        tk.Label(card, text="Chọn phân cấp tài khoản:", bg="#1e293b", fg="white",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")

        self.role_var = tk.StringVar(value="student")
        roles = [
            ("👨‍🎓 Học sinh (Chỉ mở App/Thư mục cho phép & Bật giám sát)", "student"),
            ("👪 Phụ huynh (Cấu hình App, Link Web, Xem thời gian & Bot)", "parent"),
            ("🛠️ Admin (Toàn quyền hệ thống & Mở khóa Windows)", "admin")
        ]
        for label, val in roles:
            tk.Radiobutton(card, text=label, variable=self.role_var, value=val,
                           bg="#1e293b", fg="#f1f5f9", selectcolor="#0f172a",
                           activebackground="#1e293b", font=("Segoe UI", 10)).pack(anchor="w", pady=4)

        tk.Label(card, text="Mật khẩu:", bg="#1e293b", fg="white",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(16, 4))
        self.pwd_entry = tk.Entry(card, show="*", font=("Segoe UI", 13), width=30)
        self.pwd_entry.pack(pady=4)
        self.pwd_entry.bind("<Return>", lambda e: self.handle_login())
        self.pwd_entry.focus()

        tk.Button(card, text="ĐĂNG NHẬP HỆ THỐNG", command=self.handle_login,
                  bg="#0891b2", fg="white", font=("Segoe UI", 11, "bold"),
                  width=28, pady=8).pack(pady=(22, 0))

    def handle_login(self):
        cfg = load_config()
        role = self.role_var.get()
        pwd = self.pwd_entry.get().strip()

        if pwd == cfg["passwords"].get(role):
            self.failed_attempts = 0
            if role == "student":
                self.enter_student_shell()
            elif role == "parent":
                self.enter_parent_dashboard()
            elif role == "admin":
                self.enter_admin_mode()
        else:
            self.failed_attempts += 1
            messagebox.showwarning("Từ chối truy cập",
                                   f"Sai mật khẩu cấp [{role.upper()}]! (Lần thử: {self.failed_attempts})")
            if role in ["parent", "admin"] and self.failed_attempts >= 3:
                monitor_hook.send_alert_to_telegram(
                    "WRONG_LOGIN",
                    f"Phát hiện cố tình đăng nhập trái phép vào quyền [{role.upper()}] {self.failed_attempts} lần liên tiếp!"
                )

    # -----------------------------------------------------------------
    # CẤP 1: CHẾ ĐỘ HỌC SINH (KHÓA MÁY + STUDENT SHELL + CHẠY NỀN PYTHON)
    # -----------------------------------------------------------------
    def enter_student_shell(self):
        self.clear_ui()
        set_windows_task_manager_lock(True)
        # Bật luồng chạy nền kiểm soát Web và thống kê thời gian
        monitor_hook.start_monitoring()

        cfg = load_config()

        # Thanh công cụ phía trên
        header = tk.Frame(self.root, bg="#1e293b", height=64)
        header.pack(fill="x")
        tk.Label(header, text="👨‍🎓 KHÔNG GIAN HỌC TẬP TẬP TRUNG (CHẾ ĐỘ HỌC SINH)",
                 bg="#1e293b", fg="#34d399", font=("Segoe UI", 13, "bold")).pack(side="left", padx=24, pady=16)

        tk.Button(header, text="🔒 Khóa máy / Đổi tài khoản", command=self.show_lock_screen,
                  bg="#475569", fg="white", font=("Segoe UI", 10, "bold"), padx=14, pady=6).pack(side="right", padx=20)
        tk.Button(header, text="🧰 Task Manager (Bản Học sinh)", command=lambda: open_student_task_manager(self.root),
                  bg="#0284c7", fg="white", font=("Segoe UI", 10, "bold"), padx=14, pady=6).pack(side="right", padx=6)

        # Khu vực hiển thị các ứng dụng và thư mục được phép truy cập
        container = tk.Frame(self.root, bg="#090d16", padx=40, pady=30)
        container.pack(fill="both", expand=True)

        tk.Label(container, text="DANH SÁCH ỨNG DỤNG ĐƯỢC PHÉP SỬ DỤNG:",
                 bg="#090d16", fg="#38bdf8", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 14))

        apps_frame = tk.Frame(container, bg="#090d16")
        apps_frame.pack(fill="x", pady=(0, 30))

        for app_item in cfg["allowed_apps"]:
            btn = tk.Button(apps_frame, text=f"🖥️\n{app_item['name']}",
                            width=22, height=4, bg="#1e293b", fg="white",
                            font=("Segoe UI", 10, "bold"),
                            command=lambda p=app_item["path"]: self.launch_allowed_app(p))
            btn.pack(side="left", padx=10, pady=6)

        tk.Label(container, text="THƯ MỤC TÀI LIỆU ĐƯỢC PHÉP TRUY CẬP:",
                 bg="#090d16", fg="#38bdf8", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(10, 14))

        folders_frame = tk.Frame(container, bg="#090d16")
        folders_frame.pack(fill="x")

        for folder_path in cfg["allowed_folders"]:
            tk.Button(folders_frame, text=f"📁 {folder_path}",
                      bg="#334155", fg="#fde047", font=("Segoe UI", 10, "bold"),
                      padx=16, pady=10,
                      command=lambda fp=folder_path: self.open_allowed_folder(fp)).pack(side="left", padx=10, pady=6)

    def launch_allowed_app(self, app_path):
        try:
            subprocess.Popen(app_path)
        except Exception as e:
            messagebox.showerror("Lỗi mở ứng dụng", f"Không thể mở đường dẫn: {app_path}\nChi tiết: {e}")

    def open_allowed_folder(self, folder_path):
        if not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)
        os.startfile(folder_path)

    # -----------------------------------------------------------------
    # CẤP 2: CHẾ ĐỘ PHỤ HUYNH (QUẢN LÝ WHITELIST, LINK WEB, THỜI GIAN DÙNG)
    # -----------------------------------------------------------------
    def enter_parent_dashboard(self):
        self.clear_ui()
        set_windows_task_manager_lock(False)
        set_desktop_explorer_state(True)
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self.root.geometry("950x640")

        cfg = load_config()

        header = tk.Frame(self.root, bg="#1e293b", pady=12)
        header.pack(fill="x")
        tk.Label(header, text="👪 BẢNG ĐIỀU KHIỂN DÀNH CHO PHỤ HUYNH",
                 bg="#1e293b", fg="#38bdf8", font=("Segoe UI", 14, "bold")).pack(side="left", padx=20)
        tk.Button(header, text="🔒 Bật lại Khóa màn hình Học sinh", command=self.show_lock_screen,
                  bg="#e11d48", fg="white", font=("Segoe UI", 10, "bold"), padx=12).pack(side="right", padx=20)

        body = tk.Frame(self.root, bg="#090d16", padx=24, pady=18)
        body.pack(fill="both", expand=True)

        # Cột trái: Thêm/Xóa ứng dụng & thư mục cho phép
        left_col = tk.LabelFrame(body, text=" 1. Quản lý Ứng dụng & Thư mục Học sinh được phép dùng ",
                                 bg="#1e293b", fg="#38bdf8", font=("Segoe UI", 10, "bold"), padx=14, pady=12)
        left_col.pack(side="left", fill="both", expand=True, padx=(0, 10))

        app_listbox = tk.Listbox(left_col, bg="#0f172a", fg="white", font=("Segoe UI", 10), height=8)
        app_listbox.pack(fill="x", pady=6)
        for a in cfg["allowed_apps"]:
            app_listbox.insert("end", f"{a['name']} ({a['exe_name']})")

        def add_new_app():
            fpath = filedialog.askopenfilename(title="Chọn file .exe cho phép học sinh mở",
                                               filetypes=[("Executable", "*.exe")])
            if fpath:
                exe_name = os.path.basename(fpath)
                app_name = os.path.splitext(exe_name)[0].capitalize()
                cfg["allowed_apps"].append({"name": app_name, "exe_name": exe_name, "path": fpath})
                save_config(cfg)
                app_listbox.insert("end", f"{app_name} ({exe_name})")

        def remove_selected_app():
            sel = app_listbox.curselection()
            if sel:
                idx = sel[0]
                cfg["allowed_apps"].pop(idx)
                save_config(cfg)
                app_listbox.delete(idx)

        btn_row = tk.Frame(left_col, bg="#1e293b")
        btn_row.pack(fill="x", pady=6)
        tk.Button(btn_row, text="➕ Thêm ứng dụng (.exe)", command=add_new_app, bg="#0284c7", fg="white", padx=8).pack(side="left", padx=4)
        tk.Button(btn_row, text="🗑️ Xóa ứng dụng", command=remove_selected_app, bg="#475569", fg="white", padx=8).pack(side="left", padx=4)

        # Cột phải: Giới hạn Link Web, AI Quota & Thống kê thời gian
        right_col = tk.LabelFrame(body, text=" 2. Giới hạn Đường dẫn Web, Chatbot AI & Thời gian sử dụng ",
                                  bg="#1e293b", fg="#34d399", font=("Segoe UI", 10, "bold"), padx=14, pady=12)
        right_col.pack(side="right", fill="both", expand=True, padx=(10, 0))

        tk.Label(right_col, text="Danh sách tên miền cấm (cách nhau bằng dấu phẩy):", bg="#1e293b", fg="white").pack(anchor="w")
        domains_entry = tk.Entry(right_col, width=45, font=("Segoe UI", 10))
        domains_entry.insert(0, ", ".join(cfg["web_policy"]["blocked_domains"]))
        domains_entry.pack(fill="x", pady=(4, 12))

        tk.Label(right_col, text="Thời gian tối đa dùng ChatGPT / AI (phút/phiên):", bg="#1e293b", fg="white").pack(anchor="w")
        ai_limit_entry = tk.Entry(right_col, width=15, font=("Segoe UI", 10))
        ai_limit_entry.insert(0, str(cfg["web_policy"]["max_ai_minutes"]))
        ai_limit_entry.pack(anchor="w", pady=(4, 12))

        def save_policy():
            raw_domains = domains_entry.get().split(",")
            cfg["web_policy"]["blocked_domains"] = [d.strip() for d in raw_domains if d.strip()]
            cfg["web_policy"]["max_ai_minutes"] = int(ai_limit_entry.get() or 15)
            save_config(cfg)
            messagebox.showinfo("Thành công", "Đã lưu cấu hình kiểm soát Web và Chatbot AI!")

        tk.Button(right_col, text="💾 Lưu chính sách Web & AI", command=save_policy,
                  bg="#16a34a", fg="white", font=("Segoe UI", 10, "bold"), pady=6).pack(fill="x", pady=6)

        tk.Label(right_col, text="Thống kê thời gian học sinh mở ứng dụng:", bg="#1e293b", fg="#fde047",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(14, 4))
        stats_box = tk.Text(right_col, height=6, bg="#0f172a", fg="#e2e8f0", font=("Consolas", 10))
        stats_box.pack(fill="x")
        if not monitor_hook.usage_stats:
            stats_box.insert("end", "Chưa có dữ liệu phiên học hôm nay.\n")
        else:
            for k, sec in monitor_hook.usage_stats.items():
                stats_box.insert("end", f"• {k}: {round(sec/60, 1)} phút ({sec} giây)\n")

    # -----------------------------------------------------------------
    # CẤP 3: CHẾ ĐỘ ADMIN (MỞ TOÀN BỘ QUYỀN HỆ THỐNG)
    # -----------------------------------------------------------------
    def enter_admin_mode(self):
        set_windows_task_manager_lock(False)
        set_desktop_explorer_state(True)
        monitor_hook.stop_monitoring()
        if messagebox.askyesno("Quyền Quản Trị Viên (Admin)",
                               "Đã mở khóa Task Manager gốc và Windows Explorer.\nBạn có muốn đóng hoàn toàn trình khóa máy để bảo trì hệ thống không?"):
            self.root.destroy()
            sys.exit(0)
        else:
            self.enter_parent_dashboard()

if __name__ == "__main__":
    root = tk.Tk()
    app = FocusDeskApp(root)
    root.mainloop()
```

---

## 5. Hướng dẫn Đóng gói ra File `.EXE` chạy quyền Administrator

1. **Cài đặt PyInstaller và thư viện:**
   ```bash
   pip install psutil pyinstaller
   ```
2. **Đóng gói thành 1 file `.exe` duy nhất tự động yêu cầu quyền Admin (`--uac-admin`) và ẩn cửa sổ dòng lệnh đen (`--noconsole`):**
   ```bash
   pyinstaller --onefile --noconsole --uac-admin --name FocusDeskLauncher focus_desk_launcher.py
   ```
3. File kết quả sẽ nằm tại thư mục `dist/FocusDeskLauncher.exe`. Đặt file này cùng thư mục với `shared_config.json` để chạy ngay.