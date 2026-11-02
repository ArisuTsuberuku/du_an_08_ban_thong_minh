# -*- coding: utf-8 -*-
"""
Phần Mềm Trung Tâm Quản Lý Bàn Học Thông Minh 3 Cấp Quyền (he_thong_quan_ly_3_cap.py)
Đóng gói thành SmartDeskLauncher.exe (--uac-admin):
  - Giao diện Khóa Toàn màn hình kiểu Gcafe / CSM + 3 Tài khoản Demo nhanh.
  - Phân quyền 3 cấp: Admin (Quản trị hệ thống), Phụ huynh (Quản lý Whitelist/Web/AI & Báo cáo), Học sinh (Custom Shell).
  - Student Task Manager riêng: Chỉ hiện ứng dụng học tập được phép, ẩn tuyệt đối explorer.exe & python.
  - Tích hợp 5 Lớp An toàn Khẩn cấp chống kẹt máy (Win32 Panic Hotkeys, Crash Hooks, PIN 999999, UNLOCK.txt, .bat).
  - Tự động khởi chạy ngầm Web Dashboard (0.0.0.0:5000) & Bộ giám sát URL/Game/AI (BoGiamSatThongMinh).
"""

import os
import subprocess
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import webbrowser
from typing import Any, Dict, Set

import psutil

from core.config_manager import (
    ensure_data_files_exist,
    get_app_dir,
    load_config,
    load_runtime_state,
    safe_log,
    save_config,
    update_runtime_state,
)
from core.failsafe import (
    MASTER_RESCUE_PIN,
    attach_tkinter_failsafe,
    emergency_restore_and_exit,
    install_all_failsafes,
    is_running_as_admin,
    register_worker_cleanup,
    restore_windows_normal_state,
    set_desktop_explorer_state,
    set_windows_task_manager_lock,
    verify_rescue_pin,
)
from services.giam_sat_worker import BoGiamSatThongMinh, gui_canh_bao_len_web
from services.posture_controller import posture_manager
from trang_web import get_local_lan_ip, start_web_server_in_background, stop_web_server_in_background

# Danh sách tiến trình hệ thống cấm hiển thị và cấm đóng tuyệt đối trong Student Task Manager
FORBIDDEN_STUDENT_PROCESSES: Set[str] = {
    "explorer.exe",
    "python.exe",
    "pythonw.exe",
    "smartdesklauncher.exe",
    "he_thong_quan_ly_3_cap.exe",
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "conhost.exe",
    "taskmgr.exe",
    "regedit.exe",
    "svchost.exe",
    "csrss.exe",
    "wininit.exe",
    "winlogon.exe",
    "services.exe",
    "lsass.exe",
    "dwm.exe",
    "system",
}


# Bảng màu giao diện nền sáng (đồng bộ với web phụ huynh)
UI = {"bg": "#f4f6fb", "card": "#ffffff", "line": "#e2e8f0", "txt": "#0f172a", "mut": "#64748b",
      "acc": "#2563eb", "ok": "#16a34a", "warn": "#d97706", "bad": "#dc2626"}


class PhanMemQuanLyBanHocEXE:
    """Ứng dụng Desktop 3 cấp quyền quản lý kỷ luật số Bàn học Thông minh."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hệ Thống Quản Lý Bàn Học Thông Minh - 3 Cấp Quyền")
        self.root.configure(bg=UI["bg"])

        self.bo_giam_sat = BoGiamSatThongMinh(on_remote_lock=self._nhan_lenh_khoa_tu_xa)
        # Chỉ dừng bộ giám sát tab/game & Camera AI khi mở khóa sang Phụ huynh/Admin; giữ nguyên Web Server cổng 5000!
        register_worker_cleanup(self.bo_giam_sat.dung_lai)
        register_worker_cleanup(posture_manager.stop)

        self.so_lan_sai_mat_khau = 0
        self._student_clock_job = None
        self._current_mode = "locked"

        # Chặn Alt+F4 đóng cửa sổ trái phép khi đang ở chế độ khóa
        self.root.protocol("WM_DELETE_WINDOW", self._xu_ly_nut_dong_cua_so)

        # Gắn bảo vệ ngoại lệ giao diện & phím tắt dự phòng trên Tkinter
        attach_tkinter_failsafe(self.root, self.thoat_khan_cap)

        self.hien_man_hinh_dang_nhap()

    # =====================================================================
    # TIỆN ÍCH HỆ THỐNG & AN TOÀN KHẨN CẤP (FAIL-SAFE UI)
    # =====================================================================
    def _xu_ly_nut_dong_cua_so(self) -> None:
        """Ở màn hình Khóa và Học sinh thì chặn Alt+F4; ở quyền Phụ huynh/Admin thì hỏi chuyển về Khóa."""
        if self._current_mode in ("parent", "admin"):
            if messagebox.askyesno(
                "Chuyển về Màn hình Khóa?",
                "Bạn có muốn bật lại Màn hình Khóa để bàn giao máy cho Học sinh không?\n"
                "(Nếu muốn tắt hẳn phần mềm, hãy dùng quyền Admin hoặc mã PIN cứu hộ 999999).",
                parent=self.root,
            ):
                self.hien_man_hinh_dang_nhap()

    def _nhan_lenh_khoa_tu_xa(self) -> None:
        """Callback an toàn đa luồng khi Phụ huynh bấm 'Khóa máy từ xa' trên Web hoặc Bot Telegram."""
        self.root.after(0, self._thuc_thi_khoa_tu_xa_ui)

    def _thuc_thi_khoa_tu_xa_ui(self) -> None:
        self.hien_man_hinh_dang_nhap()
        messagebox.showwarning(
            "Khóa Máy Từ Xa",
            "🔒 Phụ huynh vừa kích hoạt lệnh Khóa màn hình từ xa!",
            parent=self.root,
        )

    def thoat_khan_cap_tu_luong_phu(self, ly_do: str) -> None:
        """Được gọi từ luồng Win32 RegisterHotKey hoặc luồng UNLOCK.txt."""
        self.root.after(0, lambda: self.thoat_khan_cap(ly_do, hien_thong_bao=False))

    def thoat_khan_cap(self, ly_do: str = "Khẩn cấp", hien_thong_bao: bool = True) -> None:
        self._huy_bo_dem_ui()
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        stop_web_server_in_background()
        restore_windows_normal_state()
        if hien_thong_bao:
            try:
                messagebox.showinfo(
                    "An Toàn Hệ Thống (Fail-Safe)",
                    f"Đã kích hoạt thoát an toàn ({ly_do}).\n"
                    "Đã mở khóa lại Task Manager gốc, khôi phục Windows Explorer và dừng giám sát ngầm!",
                    parent=self.root,
                )
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        emergency_restore_and_exit(ly_do, 0)

    def nhap_pin_cuu_ho(self) -> None:
        pin = simpledialog.askstring(
            "Mã PIN Cứu Hộ Khẩn Cấp",
            f"Nhập mã PIN cứu hộ khẩn cấp (Mặc định: {MASTER_RESCUE_PIN}) để mở khóa Windows & tắt ngay:",
            show="*",
            parent=self.root,
        )
        if pin is None:
            return
        if verify_rescue_pin(pin):
            self.thoat_khan_cap(f"Mã PIN Cứu hộ {MASTER_RESCUE_PIN}", hien_thong_bao=True)
        else:
            messagebox.showerror("Sai mã PIN", "Mã PIN cứu hộ không chính xác!", parent=self.root)

    def _huy_bo_dem_ui(self) -> None:
        if self._student_clock_job is not None:
            try:
                self.root.after_cancel(self._student_clock_job)
            except Exception:
                pass
            self._student_clock_job = None

    def xoa_man_hinh(self) -> None:
        self._huy_bo_dem_ui()
        for widget in self.root.winfo_children():
            widget.destroy()

    def _can_giua_cua_so(self, width: int = 1080, height: int = 700) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2 - 20)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def tao_thanh_an_toan_day_man_hinh(self) -> None:
        """Thanh cứu hộ luôn hiển thị ở góc dưới mọi màn hình (Lớp bảo vệ số 3)."""
        cfg = load_config()
        port = int(cfg.get("web_port", 5000))
        lan_url = f"http://{get_local_lan_ip()}:{port}"
        admin_badge = "🛡️ Quyền Admin: ĐÃ CẤP" if is_running_as_admin() else "⚠️ Quyền Admin: Chưa nâng quyền"

        bar = tk.Frame(self.root, bg=UI["card"], height=38, highlightbackground=UI["line"], highlightthickness=1)
        bar.pack(side="bottom", fill="x")

        info_text = (
            f"{admin_badge}  |  📱 Web Phụ huynh: {lan_url}  |  "
            "Phím tắt Cứu hộ Toàn cục: [Ctrl+Alt+Shift+F12] hoặc [Ctrl+Shift+F10]"
        )
        tk.Label(
            bar,
            text=info_text,
            bg=UI["card"],
            fg=UI["mut"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=14, pady=6)

        tk.Button(
            bar,
            text=f"🆘 Thoát Khẩn Cấp (PIN: {MASTER_RESCUE_PIN})",
            command=self.nhap_pin_cuu_ho,
            bg="#be123c",
            activebackground="#9f1239",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=3,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=14, pady=5)

    # =====================================================================
    # MÀN HÌNH KHÓA ĐĂNG NHẬP 3 CẤP KIỂU GCAFE / CSM
    # =====================================================================
    def hien_man_hinh_dang_nhap(self) -> None:
        self._current_mode = "locked"
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        update_runtime_state(current_role="locked", monitoring_active=False)

        cfg = load_config()
        set_windows_task_manager_lock(True)
        if cfg.get("kiosk_hide_explorer", False):
            set_desktop_explorer_state(False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.tao_thanh_an_toan_day_man_hinh()

        box = tk.Frame(
            self.root,
            bg="#ffffff",
            padx=42,
            pady=30,
            highlightbackground="#06b6d4",
            highlightthickness=2,
        )
        box.place(relx=0.5, rely=0.47, anchor="center")

        tk.Label(
            box,
            text="HỆ THỐNG BÀN HỌC THÔNG MINH (STEM)",
            bg="#ffffff",
            fg="#2563eb",
            font=("Segoe UI", 18, "bold"),
        ).pack()
        tk.Label(
            box,
            text="Kiểm soát Kỷ luật Số & Phân quyền Truy cập 3 Cấp (Cơ chế Gcafe / CSM)",
            bg="#ffffff",
            fg="#64748b",
            font=("Segoe UI", 10),
        ).pack(pady=(4, 16))

        # Khu vực 3 nút Điền nhanh Tài khoản Demo
        demo_frame = tk.LabelFrame(
            box,
            text=" ⚡ Chọn nhanh Tài khoản Demo ",
            bg="#f4f6fb",
            fg="#b45309",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=8,
        )
        demo_frame.pack(fill="x", pady=(0, 14))

        self.role_var = tk.StringVar(value="student")
        self.user_var = tk.StringVar(value=cfg["usernames"].get("student", "hocsinh"))

        def dong_bo_theo_role(*_args) -> None:
            r = self.role_var.get()
            self.user_var.set(cfg["usernames"].get(r, r))

        def chon_demo(role: str, user_str: str, pwd_str: str) -> None:
            self.role_var.set(role)
            self.user_var.set(user_str)
            self.pwd_entry.delete(0, "end")
            self.pwd_entry.insert(0, pwd_str)
            self.pwd_entry.focus_set()

        tk.Button(
            demo_frame,
            text="👨‍🎓 Học sinh (hocsinh / hocsinh123)",
            command=lambda: chon_demo("student", "hocsinh", cfg["passwords"].get("student", "hocsinh123")),
            bg="#0284c7",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4, expand=True, fill="x")

        tk.Button(
            demo_frame,
            text="👪 Phụ huynh (phuhuynh / phuhuynh123)",
            command=lambda: chon_demo("parent", "phuhuynh", cfg["passwords"].get("parent", "phuhuynh123")),
            bg="#059669",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4, expand=True, fill="x")

        tk.Button(
            demo_frame,
            text="🛠️ Admin (admin / admin123)",
            command=lambda: chon_demo("admin", "admin", cfg["passwords"].get("admin", "admin123")),
            bg="#7c3aed",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            pady=4,
            cursor="hand2",
        ).pack(side="left", padx=4, expand=True, fill="x")

        tk.Label(
            box,
            text="Phân cấp quyền đăng nhập:",
            bg="#ffffff",
            fg="#0f172a",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(2, 4))

        for label, val in [
            ("👨‍🎓 Học sinh (Khóa máy, chỉ mở App/Thư mục cho phép & Bật chạy nền kiểm soát Web)", "student"),
            ("👪 Phụ huynh (Mở máy bình thường, Cài đặt App/Link cấm & Xem báo cáo)", "parent"),
            ("🛠️ Admin (Toàn quyền hệ thống, Thiết lập bảo mật lõi & Thoát hoàn toàn)", "admin"),
        ]:
            tk.Radiobutton(
                box,
                text=label,
                variable=self.role_var,
                value=val,
                command=dong_bo_theo_role,
                bg="#ffffff",
                fg="#0f172a",
                selectcolor="#ffffff",
                activebackground="#f1f5f9",
                activeforeground="#38bdf8",
                font=("Segoe UI", 9, "normal"),
            ).pack(anchor="w", pady=2)

        form_row = tk.Frame(box, bg="#ffffff")
        form_row.pack(fill="x", pady=(12, 4))

        left_f = tk.Frame(form_row, bg="#ffffff")
        left_f.pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Label(left_f, text="Tài khoản:", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        user_entry = tk.Entry(left_f, textvariable=self.user_var, font=("Segoe UI", 12), bg="#f4f6fb", fg="#0f172a", insertbackground="#0f172a")
        user_entry.pack(fill="x", pady=4, ipady=3)

        right_f = tk.Frame(form_row, bg="#ffffff")
        right_f.pack(side="left", expand=True, fill="x", padx=(8, 0))
        tk.Label(right_f, text="Mật khẩu:", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        pwd_wrap = tk.Frame(right_f, bg="#ffffff")
        pwd_wrap.pack(fill="x", pady=4)
        self.pwd_entry = tk.Entry(pwd_wrap, show="*", font=("Segoe UI", 12), bg="#f4f6fb", fg="#0f172a", insertbackground="#0f172a")
        self.pwd_entry.insert(0, cfg["passwords"].get("student", "hocsinh123"))
        self.pwd_entry.pack(side="left", expand=True, fill="x", ipady=3)
        self.pwd_entry.bind("<Return>", lambda _e: self.xu_ly_dang_nhap())
        self.pwd_entry.focus_set()

        self._show_pwd = False

        def toggle_pwd() -> None:
            self._show_pwd = not self._show_pwd
            self.pwd_entry.config(show="" if self._show_pwd else "*")

        tk.Button(
            pwd_wrap,
            text="👁",
            command=toggle_pwd,
            bg="#eef2ff",
            fg="#1e3a8a",
            relief="flat",
            padx=8,
        ).pack(side="left", padx=(4, 0), fill="y")

        tk.Button(
            box,
            text="🔓 ĐĂNG NHẬP HỆ THỐNG",
            command=self.xu_ly_dang_nhap,
            bg="#0891b2",
            activebackground="#0e7490",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            pady=9,
            relief="flat",
            cursor="hand2",
        ).pack(fill="x", pady=(16, 0))

    def xu_ly_dang_nhap(self) -> None:
        cfg = load_config()
        role = self.role_var.get()
        typed_user = self.user_var.get().strip().lower()
        pwd = self.pwd_entry.get().strip()

        # Cho phép nhận diện tự động cấp tài khoản nếu người dùng gõ trực tiếp tên tài khoản vào ô
        username_to_role = {v.lower(): k for k, v in cfg.get("usernames", {}).items()}
        if typed_user in username_to_role and username_to_role[typed_user] != role:
            role = username_to_role[typed_user]
            self.role_var.set(role)

        expected_pwd = cfg.get("passwords", {}).get(role, "")
        if pwd == expected_pwd:
            self.so_lan_sai_mat_khau = 0
            if role == "student":
                self.vao_che_do_hoc_sinh()
            elif role == "parent":
                self.vao_che_do_phu_huynh()
            else:
                self.vao_che_do_admin()
        else:
            self.so_lan_sai_mat_khau += 1
            messagebox.showwarning(
                "Từ chối truy cập",
                f"Mật khẩu cấp [{role.upper()}] không chính xác! (Lần thử: {self.so_lan_sai_mat_khau})",
                parent=self.root,
            )
            if role in ("parent", "admin") and self.so_lan_sai_mat_khau >= 3:
                gui_canh_bao_len_web(
                    f"🔐 Cảnh báo vượt quyền: Phát hiện thử đăng nhập trái phép vào tài khoản "
                    f"[{role.upper()}] thất bại {self.so_lan_sai_mat_khau} lần liên tiếp!",
                    "WRONG_LOGIN",
                )

    # =====================================================================
    # CẤP 3: CHẾ ĐỘ HỌC SINH (CUSTOM STUDENT SHELL + STUDENT TASK MANAGER)
    # =====================================================================
    def vao_che_do_hoc_sinh(self) -> None:
        self._current_mode = "student"
        cfg = load_config()
        update_runtime_state(current_role="student", remote_lock_requested=False)

        self.xoa_man_hinh()
        set_windows_task_manager_lock(True)
        if cfg.get("kiosk_hide_explorer", False):
            set_desktop_explorer_state(False)

        # Giữ fullscreen để che Desktop nhưng tắt topmost để cửa sổ Chrome/App học tập mở lên trên được
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", False)
        self.root.configure(bg=UI["bg"])
        self.tao_thanh_an_toan_day_man_hinh()

        # ---------- Thanh đầu trang ----------
        header = tk.Frame(self.root, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        header.pack(fill="x")
        left = tk.Frame(header, bg=UI["card"])
        left.pack(side="left", padx=24, pady=12)
        tk.Label(left, text="📚 Góc học tập", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 18, "bold")).pack(anchor="w")
        self.lbl_hs_clock = tk.Label(left, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10))
        self.lbl_hs_clock.pack(anchor="w")

        self._ui_button(header, "🔒  Khóa máy / Đổi tài khoản", self.hien_man_hinh_dang_nhap, kind="ghost").pack(
            side="right", padx=(6, 24), pady=16)
        self._ui_button(header, "🧰  Ứng dụng đang mở", self.mo_task_manager_hoc_sinh, kind="ghost").pack(
            side="right", padx=6, pady=16)
        self.lbl_hs_watch = tk.Label(header, text="🟢 Đang được giám sát", bg="#dcfce7", fg="#166534",
                                     font=("Segoe UI", 10, "bold"), padx=12, pady=5)
        self.lbl_hs_watch.pack(side="right", padx=12)

        # ---------- Thân: cột trái (ứng dụng/thư mục) + cột phải (trạng thái) ----------
        body = tk.Frame(self.root, bg=UI["bg"])
        body.pack(fill="both", expand=True, padx=24, pady=18)
        side = tk.Frame(body, bg=UI["bg"], width=360)
        side.pack(side="right", fill="y", padx=(18, 0))
        side.pack_propagate(False)
        main = tk.Frame(body, bg=UI["bg"])
        main.pack(side="left", fill="both", expand=True)

        self._ui_section_title(main, "🧩  Ứng dụng học tập", "Bấm vào biểu tượng để mở")
        grid = tk.Frame(main, bg=UI["bg"])
        grid.pack(fill="x", pady=(6, 18))
        allowed_apps = cfg.get("allowed_apps", [])
        if not allowed_apps:
            tk.Label(grid, text="Phụ huynh chưa cấp ứng dụng nào.", bg=UI["bg"], fg=UI["mut"],
                     font=("Segoe UI", 11)).pack(anchor="w")
        cols = 5
        for idx, item in enumerate(allowed_apps):
            r, c = divmod(idx, cols)
            self._ui_tile(grid, item.get("name", "?"), item.get("path", ""), item.get("exe_name", ""),
                          lambda p=item.get("path", ""), n=item.get("name", ""): self._mo_ung_dung_hoc_sinh(p, n)
                          ).grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="tile")

        self._ui_section_title(main, "📁  Thư mục tài liệu", "Bài tập, tài liệu được phép mở")
        frow = tk.Frame(main, bg=UI["bg"])
        frow.pack(fill="x", pady=(6, 18))
        folders = cfg.get("allowed_folders", [])
        if not folders:
            tk.Label(frow, text="Chưa có thư mục nào.", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 11)).pack(anchor="w")
        for idx, fd in enumerate(folders):
            r, c = divmod(idx, cols)
            name = os.path.basename(os.path.normpath(fd)) or fd
            self._ui_tile(frow, name, "", "folder", lambda f=fd: self._mo_thu_muc_hoc_sinh(f), emoji="📁",
                          sub=fd).grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
        for c in range(cols):
            frow.grid_columnconfigure(c, weight=1, uniform="tile")

        rules = tk.Frame(main, bg="#eff6ff", highlightbackground="#bfdbfe", highlightthickness=1, padx=16, pady=12)
        rules.pack(fill="x", side="bottom")
        tk.Label(rules, text="💡 Quy định góc học tập", bg="#eff6ff", fg="#1e3a8a",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w")
        blocked = (cfg.get("bad_urls", []) + cfg.get("bad_apps", []))[:10]
        tk.Label(rules, bg="#eff6ff", fg="#1e293b", justify="left", font=("Segoe UI", 10), wraplength=900,
                 text=("✅ Trang học tập (OLM, VietJack, Google Docs, Wikipedia, bài giảng YouTube...) dùng bình thường.\n"
                       "⛔ Đang chặn: " + (", ".join(blocked) if blocked else "theo cài đặt của phụ huynh")
                       + ". Mở trang/ứng dụng bị chặn sẽ tự đóng và báo phụ huynh.")).pack(anchor="w", pady=(4, 0))

        # ---------- Cột phải: thẻ trạng thái ----------
        c1 = self._ui_card(side, "🎯 Mức tập trung")
        self.lbl_hs_focus = tk.Label(c1, text="—", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 30, "bold"))
        self.lbl_hs_focus.pack(anchor="w")
        self.cv_hs_focus = tk.Canvas(c1, height=10, bg=UI["card"], highlightthickness=0)
        self.cv_hs_focus.pack(fill="x", pady=4)
        self.lbl_hs_state = tk.Label(c1, text="Camera AI đang khởi động…", bg=UI["card"], fg=UI["mut"],
                                     font=("Segoe UI", 10), wraplength=310, justify="left")
        self.lbl_hs_state.pack(anchor="w")

        c2 = self._ui_card(side, "🤖 Thời gian dùng AI (ChatGPT, Gemini…)")
        self.lbl_hs_ai = tk.Label(c2, text="0 / 15 phút", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 14, "bold"))
        self.lbl_hs_ai.pack(anchor="w")
        self.cv_hs_ai = tk.Canvas(c2, height=10, bg=UI["card"], highlightthickness=0)
        self.cv_hs_ai.pack(fill="x", pady=4)

        c3 = self._ui_card(side, "⏱️ Hôm nay đã dùng")
        self.box_hs_usage = tk.Frame(c3, bg=UI["card"])
        self.box_hs_usage.pack(fill="x")

        c4 = self._ui_card(side, "🔔 Thông báo gần đây")
        self.box_hs_alerts = tk.Frame(c4, bg=UI["card"])
        self.box_hs_alerts.pack(fill="x")

        # Khởi chạy bộ giám sát ngầm & luồng AI Nhận diện Tư thế 3D
        self.bo_giam_sat.bat_dau()
        posture_manager.start()
        self._usage_sig = None
        self._alerts_sig = None
        self._cap_nhat_dong_ho_hoc_sinh()

    # ---------- Bộ dựng giao diện dùng chung ----------
    def _ui_button(self, parent, text, cmd, kind="primary"):
        styles = {"primary": (UI["acc"], "white", "#1d4ed8"), "ghost": ("#eef2ff", "#1e3a8a", "#e0e7ff"),
                  "danger": ("#fee2e2", "#991b1b", "#fecaca")}
        bg, fg, act = styles[kind]
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=act, activeforeground=fg,
                         font=("Segoe UI", 10, "bold"), relief="flat", padx=14, pady=7, cursor="hand2", bd=0)

    def _ui_section_title(self, parent, title, sub=""):
        row = tk.Frame(parent, bg=UI["bg"])
        row.pack(fill="x")
        tk.Label(row, text=title, bg=UI["bg"], fg=UI["txt"], font=("Segoe UI", 13, "bold")).pack(side="left")
        if sub:
            tk.Label(row, text=f"   {sub}", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 10)).pack(side="left")

    def _ui_card(self, parent, title):
        card = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1, padx=16, pady=12)
        card.pack(fill="x", pady=(0, 12))
        tk.Label(card, text=title, bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
        return card

    def _ui_tile(self, parent, name, path, exe, cmd, emoji=None, sub=None):
        """Thẻ ứng dụng: icon thật của .exe (hoặc emoji) + tên, đổi màu khi rê chuột."""
        from services.app_icons import emoji_for, get_icon_image

        tile = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1,
                        padx=10, pady=12, cursor="hand2")
        img = get_icon_image(path, 40) if path else None
        if img is not None:
            icon = tk.Label(tile, image=img, bg=UI["card"])
            icon.image = img
        else:
            icon = tk.Label(tile, text=emoji or emoji_for(exe or name), bg=UI["card"], font=("Segoe UI Emoji", 26))
        icon.pack()
        lbl = tk.Label(tile, text=name, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10, "bold"),
                       wraplength=150, justify="center")
        lbl.pack(pady=(6, 0))
        widgets = [tile, icon, lbl]
        if sub:
            s = tk.Label(tile, text=sub if len(sub) < 28 else "…" + sub[-26:], bg=UI["card"], fg=UI["mut"],
                         font=("Segoe UI", 8))
            s.pack()
            widgets.append(s)

        def on(_e=None, col="#eff6ff", border=UI["acc"]):
            tile.configure(highlightbackground=border)
            for w in widgets:
                w.configure(bg=col)

        def off(_e=None):
            on(col=UI["card"], border=UI["line"])

        for w in widgets:
            w.bind("<Enter>", on)
            w.bind("<Leave>", off)
            w.bind("<Button-1>", lambda _e: cmd())
        return tile

    @staticmethod
    def _ui_bar(canvas, ratio, color):
        canvas.delete("all")
        w = max(canvas.winfo_width(), 300)
        canvas.create_rectangle(0, 0, w, 10, fill="#e2e8f0", width=0)
        canvas.create_rectangle(0, 0, int(w * max(0.0, min(1.0, ratio))), 10, fill=color, width=0)

    def _cap_nhat_dong_ho_hoc_sinh(self) -> None:
        if self._current_mode != "student":
            return
        try:
            cfg = load_config()
            st = load_runtime_state()
            self.lbl_hs_clock.config(text=time.strftime("🕒 %H:%M  ·  %d/%m/%Y"))
            # Mức tập trung
            pt = st.get("posture_telemetry") or {}
            if pt.get("camera_connected"):
                cfi = float(pt.get("cfi_score", pt.get("visual_focus_score", 100)) or 0)
                col = UI["ok"] if cfi >= 75 else (UI["warn"] if cfi >= 50 else UI["bad"])
                self.lbl_hs_focus.config(text=f"{cfi:.0f}%", fg=col)
                self._ui_bar(self.cv_hs_focus, cfi / 100.0, col)
                tip = pt.get("activity_state_vi") or ""
                if pt.get("alerts"):
                    tip = "⚠️ " + str(pt["alerts"][0])
                elif cfi < 75:
                    tip = (tip + " · " if tip else "") + "Hãy ngồi thẳng lưng, nhìn vào bài nhé!"
                if pt.get("distance_cm"):
                    tip += f"  ·  📏 {pt['distance_cm']:.0f} cm"
                self.lbl_hs_state.config(text=tip or "Đang tập trung tốt 👍")
            else:
                self.lbl_hs_focus.config(text="—", fg=UI["mut"])
                self._ui_bar(self.cv_hs_focus, 0, UI["mut"])
                self.lbl_hs_state.config(text="📷 Camera AI chưa chạy / đang khởi động")
            # Thời gian dùng AI
            max_ai = int(cfg.get("max_ai_minutes", 15))
            used = self.bo_giam_sat.ai_seconds / 60.0
            self.lbl_hs_ai.config(text=f"{used:.1f} / {max_ai} phút",
                                  fg=UI["bad"] if used >= max_ai else UI["txt"])
            self._ui_bar(self.cv_hs_ai, used / max(1, max_ai), UI["bad"] if used >= max_ai else UI["acc"])
            # Ứng dụng đã dùng hôm nay (icon + phút)
            usage = sorted(self.bo_giam_sat.app_usage.items(), key=lambda kv: -kv[1])[:5]
            sig = tuple((k, v // 60) for k, v in usage)
            if sig != self._usage_sig:
                self._usage_sig = sig
                for w in self.box_hs_usage.winfo_children():
                    w.destroy()
                paths = {a.get("exe_name", "").lower(): a.get("path", "") for a in cfg.get("allowed_apps", [])}
                if not usage:
                    tk.Label(self.box_hs_usage, text="Chưa có dữ liệu.", bg=UI["card"], fg=UI["mut"],
                             font=("Segoe UI", 10)).pack(anchor="w")
                from services.app_icons import emoji_for, get_icon_image

                for label, sec in usage:
                    exe = label.rsplit("(", 1)[-1].rstrip(")").lower() if "(" in label else label
                    row = tk.Frame(self.box_hs_usage, bg=UI["card"])
                    row.pack(fill="x", pady=2)
                    img = get_icon_image(paths.get(exe, ""), 20)
                    if img is not None:
                        ic = tk.Label(row, image=img, bg=UI["card"])
                        ic.image = img
                    else:
                        ic = tk.Label(row, text=emoji_for(exe), bg=UI["card"], font=("Segoe UI Emoji", 11))
                    ic.pack(side="left")
                    tk.Label(row, text=label.split(" (")[0][:28], bg=UI["card"], fg=UI["txt"],
                             font=("Segoe UI", 10)).pack(side="left", padx=6)
                    tk.Label(row, text=f"{max(1, sec // 60)} phút", bg=UI["card"], fg=UI["mut"],
                             font=("Segoe UI", 10)).pack(side="right")
            # Thông báo gần đây
            alerts = (st.get("alerts") or [])[:3]
            asig = tuple((a.get("time"), a.get("message")) for a in alerts)
            if asig != self._alerts_sig:
                self._alerts_sig = asig
                for w in self.box_hs_alerts.winfo_children():
                    w.destroy()
                if not alerts:
                    tk.Label(self.box_hs_alerts, text="Không có vi phạm nào. Giỏi lắm! 🌟", bg=UI["card"],
                             fg=UI["ok"], font=("Segoe UI", 10)).pack(anchor="w")
                for a in alerts:
                    tk.Label(self.box_hs_alerts, text=f"{a.get('time', '')}  {a.get('message', '')}"[:80],
                             bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 9), wraplength=310,
                             justify="left").pack(anchor="w", pady=1)
            mon = st.get("monitoring_active")
            self.lbl_hs_watch.config(text="🟢 Đang được giám sát" if mon else "⚪ Giám sát tạm dừng",
                                     bg="#dcfce7" if mon else "#f1f5f9", fg="#166534" if mon else UI["mut"])
        except Exception as exc:
            safe_log(f"[UI] Lỗi cập nhật bảng học sinh: {exc}")
        self._student_clock_job = self.root.after(2000, self._cap_nhat_dong_ho_hoc_sinh)

    def _mo_ung_dung_hoc_sinh(self, app_path: str, app_name: str) -> None:
        try:
            subprocess.Popen(app_path)
        except Exception as exc:
            messagebox.showerror(
                "Không thể mở ứng dụng",
                f"Ứng dụng '{app_name}' chưa được cài đặt tại đường dẫn:\n{app_path}\n\n"
                f"Chi tiết: {exc}\n(Vui lòng nhờ Phụ huynh cập lại đường dẫn trong Bảng điều khiển).",
                parent=self.root,
            )

    def _mo_thu_muc_hoc_sinh(self, folder_path: str) -> None:
        try:
            os.makedirs(folder_path, exist_ok=True)
            os.startfile(folder_path)
        except Exception as exc:
            messagebox.showerror("Lỗi mở thư mục", f"Không thể mở thư mục {folder_path}:\n{exc}", parent=self.root)

    # =====================================================================
    # TASK MANAGER BẢN HỌC SINH (CHỈ HIỆN WHITELIST, ẨN EXPLORER & PYTHON)
    # =====================================================================
    def mo_task_manager_hoc_sinh(self) -> None:
        cfg = load_config()
        allowed_exes = {
            a["exe_name"].lower()
            for a in cfg.get("allowed_apps", [])
            if a.get("exe_name") and a["exe_name"].lower() not in FORBIDDEN_STUDENT_PROCESSES
        }
        my_pid = os.getpid()

        tm = tk.Toplevel(self.root)
        tm.title("Task Manager - Phiên Bản Học Sinh")
        tm.geometry("600x410")
        tm.configure(bg="#111827")
        tm.attributes("-topmost", True)

        tk.Label(
            tm,
            text="🧰 QUẢN LÝ ỨNG DỤNG ĐANG CHẠY (CHỈ ĐƯỢC TẮT ỨNG DỤNG HỌC TẬP)",
            bg="#111827",
            fg="#38bdf8",
            font=("Segoe UI", 10, "bold"),
        ).pack(pady=(12, 4))
        tk.Label(
            tm,
            text="Các tiến trình hệ thống Windows và trình bảo vệ bàn học được ẩn & khóa an toàn.",
            bg="#111827",
            fg="#94a3b8",
            font=("Segoe UI", 9),
        ).pack(pady=(0, 10))

        cols = ("pid", "name", "ram")
        tree = ttk.Treeview(tm, columns=cols, show="headings", height=11)
        tree.heading("pid", text="PID")
        tree.heading("name", text="Tên Ứng Dụng Cho Phép")
        tree.heading("ram", text="RAM Đang Dùng")
        tree.column("pid", width=90, anchor="center")
        tree.column("name", width=320, anchor="w")
        tree.column("ram", width=140, anchor="center")
        tree.pack(padx=16, fill="x")

        def lam_moi() -> None:
            if not tm.winfo_exists():
                return
            for row in tree.get_children():
                tree.delete(row)
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    pid = p.info["pid"]
                    pname = p.info.get("name") or ""
                    pname_l = pname.lower()
                    if (
                        pid != my_pid
                        and pname_l in allowed_exes
                        and pname_l not in FORBIDDEN_STUDENT_PROCESSES
                    ):
                        mem_rss = p.info["memory_info"].rss if p.info.get("memory_info") else 0
                        mem_mb = round(mem_rss / (1024 * 1024), 1)
                        tree.insert("", "end", values=(pid, pname, f"{mem_mb} MB"))
                except Exception:
                    continue

        def dong_ung_dung() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Chưa chọn ứng dụng", "Vui lòng chọn 1 ứng dụng học tập để đóng!", parent=tm)
                return
            pid, name, _ = tree.item(sel[0])["values"]
            name_l = str(name).lower()
            if int(pid) == my_pid or name_l in FORBIDDEN_STUDENT_PROCESSES:
                return
            try:
                psutil.Process(int(pid)).terminate()
                time.sleep(0.25)
                lam_moi()
            except Exception as exc:
                messagebox.showerror("Không thể đóng", f"Chi tiết: {exc}", parent=tm)

        btn_bar = tk.Frame(tm, bg="#111827")
        btn_bar.pack(pady=14)
        tk.Button(
            btn_bar,
            text="🔄 Làm mới danh sách",
            command=lam_moi,
            bg="#334155",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=6,
            relief="flat",
        ).pack(side="left", padx=8)
        tk.Button(
            btn_bar,
            text="❌ Kết thúc ứng dụng (Kill Task)",
            command=dong_ung_dung,
            bg="#e11d48",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=14,
            pady=6,
            relief="flat",
        ).pack(side="left", padx=8)

        lam_moi()

    # =====================================================================
    # CẤP 2: CHẾ ĐỘ PHỤ HUYNH (QUẢN LÝ WHITELIST, LINK WEB, AI & THỐNG KÊ)
    # =====================================================================
    def vao_che_do_phu_huynh(self) -> None:
        self._current_mode = "parent"
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        restore_windows_normal_state()
        update_runtime_state(current_role="parent", monitoring_active=False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self._can_giua_cua_so(1080, 700)
        self.tao_thanh_an_toan_day_man_hinh()

        cfg = load_config()
        port = int(cfg.get("web_port", 5000))

        header = tk.Frame(self.root, bg="#ffffff", pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="👪 Bảng điều khiển Phụ huynh",
            bg="#ffffff",
            fg="#2563eb",
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left", padx=20)

        tk.Button(
            header,
            text="🔒 Bật lại Khóa màn hình Học sinh",
            command=self.hien_man_hinh_dang_nhap,
            bg="#e11d48",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=5,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=18)

        tk.Button(
            header,
            text="🌐 Web nội bộ",
            command=lambda: webbrowser.open(f"http://127.0.0.1:{port}"),
            bg="#0284c7",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=5,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=6)

        self.root.configure(bg=UI["bg"])
        _st = ttk.Style(self.root)
        try:
            _st.theme_use("clam")
        except Exception:
            pass
        _st.configure("TNotebook", background=UI["bg"], borderwidth=0)
        _st.configure("TNotebook.Tab", background="#e2e8f0", foreground=UI["txt"], padding=(14, 8),
                      font=("Segoe UI", 10, "bold"))
        _st.map("TNotebook.Tab", background=[("selected", UI["card"])], foreground=[("selected", UI["acc"])])
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=12)

        # --- TAB 1: QUẢN LÝ ỨNG DỤNG & THƯ MỤC CHO PHÉP ---
        tab_apps = tk.Frame(notebook, bg="#f4f6fb", padx=18, pady=16)
        notebook.add(tab_apps, text="  📦 Ứng dụng & Thư mục  ")

        left_box = tk.LabelFrame(
            tab_apps,
            text=" Danh sách Ứng dụng (.exe) cho phép Học sinh sử dụng ",
            bg="#ffffff",
            fg="#2563eb",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
        )
        left_box.pack(side="left", fill="both", expand=True, padx=(0, 8))

        app_list = tk.Listbox(left_box, bg="#f8fafc", fg="#0f172a", font=("Segoe UI", 10), height=14)
        app_list.pack(fill="both", expand=True, pady=6)

        def refresh_apps_ui() -> None:
            app_list.delete(0, "end")
            for a in cfg.get("allowed_apps", []):
                app_list.insert("end", f"{a['name']}  [{a['exe_name']}]  ->  {a['path']}")

        def them_ung_dung() -> None:
            fpath = filedialog.askopenfilename(
                title="Chọn file thực thi (.exe) cho phép Học sinh mở",
                filetypes=[("Ứng dụng Windows (*.exe)", "*.exe")],
                parent=self.root,
            )
            if not fpath:
                return
            exe_name = os.path.basename(fpath)
            if exe_name.lower() in FORBIDDEN_STUDENT_PROCESSES:
                messagebox.showwarning(
                    "Tiến trình hệ thống",
                    f"Không được thêm tiến trình hệ thống '{exe_name}' vào menu Học sinh!",
                    parent=self.root,
                )
                return
            default_label = os.path.splitext(exe_name)[0].capitalize()
            custom_name = simpledialog.askstring(
                "Tên hiển thị ứng dụng",
                "Nhập tên hiển thị trên màn hình Học sinh:",
                initialvalue=default_label,
                parent=self.root,
            )
            if not custom_name:
                return
            cfg["allowed_apps"].append({"name": custom_name.strip(), "exe_name": exe_name, "path": fpath})
            save_config(cfg)
            refresh_apps_ui()

        def xoa_ung_dung() -> None:
            sel = app_list.curselection()
            if not sel:
                return
            cfg["allowed_apps"].pop(sel[0])
            save_config(cfg)
            refresh_apps_ui()

        app_btns = tk.Frame(left_box, bg="#ffffff")
        app_btns.pack(fill="x", pady=6)
        tk.Button(app_btns, text="➕ Thêm ứng dụng (.exe)", command=them_ung_dung, bg="#0284c7", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        tk.Button(app_btns, text="🗑️ Xóa ứng dụng đã chọn", command=xoa_ung_dung, bg="#eef2ff", fg="#1e3a8a", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        refresh_apps_ui()

        right_box = tk.LabelFrame(
            tab_apps,
            text=" Danh sách Thư mục Tài liệu được phép mở ",
            bg="#ffffff",
            fg="#b45309",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
        )
        right_box.pack(side="right", fill="both", expand=True, padx=(8, 0))

        folder_list = tk.Listbox(right_box, bg="#f8fafc", fg="#b45309", font=("Segoe UI", 10), height=14)
        folder_list.pack(fill="both", expand=True, pady=6)

        def refresh_folders_ui() -> None:
            folder_list.delete(0, "end")
            for fd in cfg.get("allowed_folders", []):
                folder_list.insert("end", fd)

        def them_thu_muc() -> None:
            dpath = filedialog.askdirectory(title="Chọn thư mục học tập cho phép truy cập", parent=self.root)
            if dpath:
                norm_d = os.path.normpath(dpath)
                if norm_d not in cfg["allowed_folders"]:
                    cfg["allowed_folders"].append(norm_d)
                    save_config(cfg)
                    refresh_folders_ui()

        def xoa_thu_muc() -> None:
            sel = folder_list.curselection()
            if sel:
                cfg["allowed_folders"].pop(sel[0])
                save_config(cfg)
                refresh_folders_ui()

        fd_btns = tk.Frame(right_box, bg="#ffffff")
        fd_btns.pack(fill="x", pady=6)
        tk.Button(fd_btns, text="📁 Thêm thư mục", command=them_thu_muc, bg="#059669", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        tk.Button(fd_btns, text="🗑️ Xóa thư mục", command=xoa_thu_muc, bg="#eef2ff", fg="#1e3a8a", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        refresh_folders_ui()

        # --- TAB 2: CHÍNH SÁCH WEB, GAME & GIỚI HẠN AI ---
        tab_policy = tk.Frame(notebook, bg="#ffffff", padx=22, pady=16)
        notebook.add(tab_policy, text="  🌐 Luật web, game & AI  ")

        def make_policy_field(label_txt: str, init_val: str) -> tk.Entry:
            tk.Label(tab_policy, text=label_txt, bg="#ffffff", fg="#0f172a", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
            ent = tk.Entry(tab_policy, font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", insertbackground="#0f172a")
            ent.insert(0, init_val)
            ent.pack(fill="x", ipady=4)
            return ent

        ent_bad_urls = make_policy_field("Danh sách Link Address (URL) bị cấm (cách nhau dấu phẩy):", ", ".join(cfg.get("bad_urls", [])))
        ent_good_urls = make_policy_field("Danh sách Link Học tập được phép (ALLOWED_URLS):", ", ".join(cfg.get("allowed_urls", [])))
        ent_bad_tabs = make_policy_field("Từ khóa Tiêu đề Tab cấm (BAD_TABS gốc):", ", ".join(cfg.get("bad_tabs", [])))
        ent_acad_kw = make_policy_field("Từ khóa Bài giảng miễn trừ trên YouTube (ACADEMIC_KW):", ", ".join(cfg.get("academic_keywords", [])))
        ent_bad_apps = make_policy_field("Từ khóa Tiến trình Game bị tiêu diệt tự động (BAD_APPS):", ", ".join(cfg.get("bad_apps", [])))
        ent_ai_min = make_policy_field("Thời gian tối đa cho phép dùng ChatGPT / Chatbot AI (Phút/phiên):", str(cfg.get("max_ai_minutes", 15)))

        def luu_chinh_sach() -> None:
            cfg["bad_urls"] = [x.strip() for x in ent_bad_urls.get().split(",") if x.strip()]
            cfg["allowed_urls"] = [x.strip() for x in ent_good_urls.get().split(",") if x.strip()]
            cfg["bad_tabs"] = [x.strip() for x in ent_bad_tabs.get().split(",") if x.strip()]
            cfg["academic_keywords"] = [x.strip() for x in ent_acad_kw.get().split(",") if x.strip()]
            cfg["bad_apps"] = [x.strip() for x in ent_bad_apps.get().split(",") if x.strip()]
            try:
                cfg["max_ai_minutes"] = max(1, int(ent_ai_min.get().strip()))
            except ValueError:
                cfg["max_ai_minutes"] = 15
            save_config(cfg)
            messagebox.showinfo("Đã lưu", "Đã lưu chính sách kiểm soát Web, Game và Quota AI vào shared_config.json!", parent=self.root)

        tk.Button(
            tab_policy,
            text="💾 Lưu Toàn Bộ Chính Sách Web & AI",
            command=luu_chinh_sach,
            bg="#16a34a",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            pady=8,
            relief="flat",
            cursor="hand2",
        ).pack(fill="x", pady=(16, 4))

        # --- TAB 3: THỐNG KÊ THỜI GIAN DÙNG & LỊCH SỬ VI PHẠM ---
        tab_stats = tk.Frame(notebook, bg="#ffffff", padx=20, pady=16)
        notebook.add(tab_stats, text="  📊 Thống kê & Vi phạm  ")

        txt_report = tk.Text(tab_stats, bg="#f8fafc", fg="#0f172a", font=("Consolas", 10), height=20)
        txt_report.pack(fill="both", expand=True, pady=(0, 10))

        def tai_bao_cao() -> None:
            state = load_runtime_state()
            txt_report.delete("1.0", "end")
            ai_sec = int(state.get("ai_seconds", 0))
            txt_report.insert("end", "=== THỜI GIAN SỬ DỤNG ỨNG DỤNG & CHATBOT AI ===\n")
            txt_report.insert("end", f"• Tra cứu ChatGPT / Chatbot AI: {round(ai_sec / 60.0, 1)} phút ({ai_sec} giây)\n")
            app_u = state.get("app_usage", {})
            if not app_u:
                txt_report.insert("end", "• Chưa có dữ liệu mở ứng dụng trong phiên này.\n")
            else:
                for k, sec in app_u.items():
                    txt_report.insert("end", f"• {k}: {round(sec / 60.0, 1)} phút ({sec} giây)\n")

            pt = state.get("posture_telemetry") or {}
            txt_report.insert("end", "\n=== CHỈ SỐ AI NHẬN DIỆN TƯ THẾ NGỒI HỌC 3D (MEDIAPIPE) ===\n")
            txt_report.insert(
                "end",
                f"• Điểm tập trung thị giác (CFI): {pt.get('visual_focus_score', 100)}% | "
                f"EAR: {pt.get('ear', 0.30)} | PERCLOS: {pt.get('perclos', 0)}% | "
                f"MAR: {pt.get('mar', 0.10)} (Ngáp: {pt.get('yawn_count', 0)} lần)\n"
                f"• Góc Euler đầu 3D (solvePnP): Pitch={pt.get('pitch_deg', 0)}°, "
                f"Yaw={pt.get('yaw_deg', 0)}°, Roll={pt.get('roll_deg', 0)}°\n",
            )

            txt_report.insert("end", "\n=== LỊCH SỬ CẢNH BÁO VI PHẠM GẦN NHẤT ===\n")
            alerts = state.get("alerts", [])
            if not alerts:
                txt_report.insert("end", "✅ Chưa ghi nhận vi phạm nào.\n")
            else:
                for item in alerts:
                    txt_report.insert("end", f"[{item.get('time', '')}] {item.get('message', '')}\n")

        stat_btns = tk.Frame(tab_stats, bg="#ffffff")
        stat_btns.pack(fill="x")
        tk.Button(stat_btns, text="🔄 Làm mới số liệu", command=tai_bao_cao, bg="#0284c7", fg="white", padx=12, pady=6, relief="flat").pack(side="left", padx=4)
        tk.Button(
            stat_btns,
            text="🧹 Đặt lại bộ đếm & Xóa cảnh báo",
            command=lambda: (
                setattr(self.bo_giam_sat, "app_usage", {}),
                setattr(self.bo_giam_sat, "ai_seconds", 0),
                update_runtime_state(alerts=[], app_usage={}, ai_seconds=0),
                tai_bao_cao(),
            ),
            bg="#eef2ff",
            fg="#1e3a8a",
            padx=12,
            pady=6,
            relief="flat",
        ).pack(side="left", padx=6)
        tai_bao_cao()

        # --- TAB 4: CẤU HÌNH BOT TELEGRAM 2 CHIỀU (GIAI ĐOẠN 7) ---
        tab_tg = tk.Frame(notebook, bg="#ffffff", padx=22, pady=16)
        notebook.add(tab_tg, text="  💬 Telegram  ")

        tg_cfg = cfg.get("telegram", {})
        tk.Label(
            tab_tg,
            text="🤖 THIẾT LẬP BOT TELEGRAM CẢNH BÁO & ĐIỀU KHIỂN TỪ XA (/status, /lock, /reset_ai)",
            bg="#ffffff",
            fg="#15803d",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        tk.Label(tab_tg, text="1. Telegram Bot Token (Lấy từ @BotFather):", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ent_tg_token = tk.Entry(tab_tg, font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", insertbackground="#0f172a")
        ent_tg_token.insert(0, tg_cfg.get("bot_token", ""))
        ent_tg_token.pack(fill="x", ipady=4, pady=(2, 8))

        tk.Label(tab_tg, text="2. Telegram Chat ID của Phụ huynh:", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ent_tg_chat = tk.Entry(tab_tg, font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", insertbackground="#0f172a")
        ent_tg_chat.insert(0, tg_cfg.get("chat_id", ""))
        ent_tg_chat.pack(fill="x", ipady=4, pady=(2, 8))

        tk.Label(tab_tg, text="3. Thời gian giãn cách chống spam giữa 2 cảnh báo cùng loại (Giây):", bg="#ffffff", fg="#0f172a", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ent_tg_cd = tk.Entry(tab_tg, font=("Segoe UI", 10), bg="#f8fafc", fg="#0f172a", insertbackground="#0f172a")
        ent_tg_cd.insert(0, str(tg_cfg.get("cooldown_seconds", 30)))
        ent_tg_cd.pack(fill="x", ipady=4, pady=(2, 10))

        v_en = tk.BooleanVar(value=bool(tg_cfg.get("enable_alerts", True)))
        v_gm = tk.BooleanVar(value=bool(tg_cfg.get("alert_on_game", True)))
        v_wl = tk.BooleanVar(value=bool(tg_cfg.get("alert_on_wrong_login", True)))
        v_ai = tk.BooleanVar(value=bool(tg_cfg.get("alert_on_ai_overuse", True)))

        for txt_c, var_c in [
            ("Kích hoạt gửi cảnh báo tự động qua Bot Telegram", v_en),
            ("Báo cáo khi học sinh mở Web Game / Ứng dụng Game", v_gm),
            ("Báo cáo khi học sinh thử đăng nhập vượt quyền Phụ huynh / Admin thất bại", v_wl),
            ("Báo cáo khi học sinh dùng ChatGPT / Chatbot AI quá thời gian quy định", v_ai),
        ]:
            tk.Checkbutton(
                tab_tg,
                text=txt_c,
                variable=var_c,
                bg="#ffffff",
                fg="#0f172a",
                selectcolor="#ffffff",
                activebackground="#f1f5f9",
                font=("Segoe UI", 9),
            ).pack(anchor="w", pady=2)

        def luu_va_test_tg(gui_test: bool = False) -> None:
            try:
                cd_val = max(5, int(ent_tg_cd.get().strip() or 30))
            except ValueError:
                cd_val = 30
            cfg["telegram"].update(
                {
                    "bot_token": ent_tg_token.get().strip(),
                    "chat_id": ent_tg_chat.get().strip(),
                    "cooldown_seconds": cd_val,
                    "enable_alerts": bool(v_en.get()),
                    "alert_on_game": bool(v_gm.get()),
                    "alert_on_wrong_login": bool(v_wl.get()),
                    "alert_on_ai_overuse": bool(v_ai.get()),
                }
            )
            save_config(cfg)
            if gui_test:
                from services.telegram_service import trigger_parent_alert

                ok = trigger_parent_alert(
                    "SYSTEM_TEST",
                    "Kết nối từ Phần mềm Quản lý Bàn Học Thông Minh (.EXE) thành công! Gửi /help để xem lệnh điều khiển.",
                    sync=True,
                )
                if ok:
                    messagebox.showinfo("Thành công", "📨 Đã lưu & gửi tin nhắn thử nghiệm về Telegram Phụ huynh!", parent=self.root)
                else:
                    messagebox.showwarning("Chưa thể gửi", "Đã lưu cấu hình! Vui lòng kiểm tra lại Bot Token & Chat ID hợp lệ.", parent=self.root)
            else:
                messagebox.showinfo("Đã lưu", "Đã lưu cấu hình Bot Telegram thành công!", parent=self.root)

        tg_btn_row = tk.Frame(tab_tg, bg="#ffffff")
        tg_btn_row.pack(fill="x", pady=(14, 0))
        tk.Button(tg_btn_row, text="💾 Lưu Cấu Hình Telegram", command=lambda: luu_va_test_tg(False), bg="#0284c7", fg="white", font=("Segoe UI", 10, "bold"), padx=16, pady=8, relief="flat").pack(side="left", padx=(0, 8))
        tk.Button(tg_btn_row, text="📨 Lưu & Gửi Tin Nhắn Test", command=lambda: luu_va_test_tg(True), bg="#059669", fg="white", font=("Segoe UI", 10, "bold"), padx=16, pady=8, relief="flat").pack(side="left")

        # --- TAB 0: KẾT NỐI PHỤ HUYNH QUA CLOUD (đặt lên đầu) ---
        tab_cloud = tk.Frame(notebook, bg=UI["bg"], padx=22, pady=18)
        notebook.insert(0, tab_cloud, text="  ☁️ Kết nối phụ huynh (điện thoại)  ")
        notebook.select(0)
        self._dung_tab_ket_noi_cloud(tab_cloud)

    def _dung_tab_ket_noi_cloud(self, tab) -> None:
        """Mã ghép máy + trạng thái kết nối web phụ huynh / Telegram, cập nhật mỗi 3 giây."""
        card = tk.Frame(tab, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1, padx=24, pady=18)
        card.pack(fill="x")
        tk.Label(card, text="📱 Ghép máy này với điện thoại phụ huynh", bg=UI["card"], fg=UI["txt"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w")
        lbl_status = tk.Label(card, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10))
        lbl_status.pack(anchor="w", pady=(4, 10))
        lbl_code = tk.Label(card, text="······", bg=UI["card"], fg=UI["acc"], font=("Consolas", 40, "bold"))
        lbl_code.pack(anchor="w")
        lbl_exp = tk.Label(card, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10))
        lbl_exp.pack(anchor="w", pady=(0, 10))
        row = tk.Frame(card, bg=UI["card"])
        row.pack(anchor="w")

        def ma_moi() -> None:
            update_runtime_state(cloud_pair_refresh=True)
            lbl_exp.config(text="Đang xin mã mới từ server…")

        self._ui_button(row, "🔄  Tạo mã ghép mới", ma_moi).pack(side="left", padx=(0, 8))
        self._ui_button(row, "🌐  Mở web phụ huynh", lambda: webbrowser.open(
            (load_runtime_state().get("cloud") or {}).get("url") or "https://banhocthongminh.vercel.app"),
            kind="ghost").pack(side="left")

        steps = tk.Frame(tab, bg="#eff6ff", highlightbackground="#bfdbfe", highlightthickness=1, padx=18, pady=12)
        steps.pack(fill="x", pady=14)
        tk.Label(steps, bg="#eff6ff", fg="#1e293b", justify="left", font=("Segoe UI", 10), text=(
            "1️⃣  Trên điện thoại, mở web phụ huynh và đăng nhập bằng Google.\n"
            "2️⃣  Bấm  ➕ Thêm máy học sinh  → nhập mã 6 số ở trên (mã đổi sau 15 phút).\n"
            "3️⃣  Bấm  Kết nối Telegram  để nhận cảnh báo, gõ /status /lock /anh ngay trong Telegram.\n"
            "📲  Trên Android: menu Chrome → “Thêm vào màn hình chính” để dùng như một ứng dụng.")).pack(anchor="w")

        def refresh() -> None:
            if not tab.winfo_exists():
                return
            c = load_runtime_state().get("cloud") or {}
            if not c.get("enabled"):
                lbl_status.config(text="⚪ Chưa bật kết nối cloud — thêm cloud_url vào secrets.local.json", fg=UI["mut"])
            elif c.get("error"):
                lbl_status.config(text=f"🔴 Lỗi kết nối: {c['error'][:80]}", fg=UI["bad"])
            else:
                ago = int(time.time()) - int(c.get("last_ok", 0) or 0)
                lbl_status.config(text=("🟢 Đã ghép với phụ huynh" if c.get("paired") else "🟡 Chưa có phụ huynh nào ghép")
                                  + f"  ·  đồng bộ {ago}s trước  ·  mã máy {c.get('device_id', '')}",
                                  fg=UI["ok"] if c.get("paired") else UI["warn"])
            left = int(c.get("pair_expires", 0) or 0) - int(time.time())
            if c.get("pair_code") and left > 0:
                lbl_code.config(text=" ".join(str(c["pair_code"])), fg=UI["acc"])
                lbl_exp.config(text=f"Hết hạn lúc {time.strftime('%H:%M', time.localtime(c['pair_expires']))} "
                                    f"(còn {left // 60} phút)")
            else:
                lbl_code.config(text="······", fg=UI["mut"])
                if not load_runtime_state().get("cloud_pair_refresh"):
                    lbl_exp.config(text="Mã đã hết hạn — bấm “Tạo mã ghép mới”.")
            tab.after(3000, refresh)

        refresh()

    # =====================================================================
    # CẤP 1: CHẾ ĐỘ ADMIN (QUẢN TRỊ HỆ THỐNG LÕI & MỞ TOÀN QUYỀN WINDOWS)
    # =====================================================================
    def vao_che_do_admin(self) -> None:
        self._current_mode = "admin"
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        restore_windows_normal_state()
        update_runtime_state(current_role="admin", monitoring_active=False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self._can_giua_cua_so(980, 640)
        self.tao_thanh_an_toan_day_man_hinh()

        cfg = load_config()

        header = tk.Frame(self.root, bg="#ffffff", pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="🛠️ BẢNG QUẢN TRỊ HỆ THỐNG (QUYỀN ADMIN CAO NHẤT)",
            bg="#ffffff",
            fg="#7c3aed",
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left", padx=20)

        tk.Button(
            header,
            text="👪 Sang Bảng Phụ Huynh",
            command=self.vao_che_do_phu_huynh,
            bg="#059669",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            relief="flat",
        ).pack(side="right", padx=8)
        tk.Button(
            header,
            text="🔒 Bật lại Màn hình Khóa",
            command=self.hien_man_hinh_dang_nhap,
            bg="#eef2ff",
            fg="#1e3a8a",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            relief="flat",
        ).pack(side="right", padx=8)

        body = tk.Frame(self.root, bg="#f8fafc", padx=24, pady=20)
        body.pack(fill="both", expand=True)

        col1 = tk.LabelFrame(
            body,
            text=" 1. Thiết lập Bảo mật Hệ điều hành & Kiosk Shell ",
            bg="#ffffff",
            fg="#2563eb",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=14,
        )
        col1.pack(side="left", fill="both", expand=True, padx=(0, 10))

        var_hide_exp = tk.BooleanVar(value=bool(cfg.get("kiosk_hide_explorer", False)))
        tk.Checkbutton(
            col1,
            text="Ẩn hoàn toàn Taskbar & Desktop (explorer.exe) khi ở chế độ Học sinh",
            variable=var_hide_exp,
            bg="#ffffff",
            fg="#0f172a",
            selectcolor="#ffffff",
            activebackground="#f1f5f9",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=4)

        var_posture_ai = tk.BooleanVar(value=bool(cfg.get("enable_posture_ai", True)))
        tk.Checkbutton(
            col1,
            text="Tự động bật AI Camera nhận diện tư thế 3D (MediaPipe) khi Học sinh vào học",
            variable=var_posture_ai,
            bg="#ffffff",
            fg="#15803d",
            selectcolor="#ffffff",
            activebackground="#f1f5f9",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=4)

        tk.Label(col1, text="Chỉ số Webcam USB cho AI Tư thế (Mặc định: 0):", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(6, 2))
        ent_cam = tk.Entry(col1, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_cam.insert(0, str(cfg.get("camera_index", 0)))
        ent_cam.pack(fill="x", ipady=2)

        tk.Label(col1, text="Cổng Web Server nội bộ (Mặc định: 5000):", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(10, 2))
        ent_port = tk.Entry(col1, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_port.insert(0, str(cfg.get("web_port", 5000)))
        ent_port.pack(fill="x", ipady=3)

        tk.Label(col1, text="Cổng COM kết nối vi điều khiển ESP32-S3 (Hộp khóa điện thoại):", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(10, 2))
        ent_com = tk.Entry(col1, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_com.insert(0, str(cfg.get("esp32_com_port", "COM3")))
        ent_com.pack(fill="x", ipady=3)

        tk.Label(col1, text="Công cụ bảo trì hệ thống nhanh:", bg="#ffffff", fg="#b45309", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(16, 6))
        tk.Button(
            col1,
            text="🧰 Mở Task Manager gốc của Windows",
            command=lambda: subprocess.Popen(["taskmgr.exe"]),
            bg="#eef2ff",
            fg="#1e3a8a",
            relief="flat",
            pady=5,
        ).pack(fill="x", pady=3)
        tk.Button(
            col1,
            text="📂 Mở thư mục gốc chứa shared_config.json & File cứu hộ .bat",
            command=lambda: os.startfile(get_app_dir()),
            bg="#eef2ff",
            fg="#1e3a8a",
            relief="flat",
            pady=5,
        ).pack(fill="x", pady=3)

        col2 = tk.LabelFrame(
            body,
            text=" 2. Quản lý Mật khẩu 3 Cấp & Thoát Hệ Thống ",
            bg="#ffffff",
            fg="#15803d",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=14,
        )
        col2.pack(side="right", fill="both", expand=True, padx=(10, 0))

        tk.Label(col2, text="Mật khẩu cấp ADMIN:", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(4, 2))
        ent_pw_admin = tk.Entry(col2, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_pw_admin.insert(0, cfg["passwords"].get("admin", "admin123"))
        ent_pw_admin.pack(fill="x", ipady=3)

        tk.Label(col2, text="Mật khẩu cấp PHỤ HUYNH:", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(10, 2))
        ent_pw_parent = tk.Entry(col2, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_pw_parent.insert(0, cfg["passwords"].get("parent", "phuhuynh123"))
        ent_pw_parent.pack(fill="x", ipady=3)

        tk.Label(col2, text="Mật khẩu cấp HỌC SINH:", bg="#ffffff", fg="#334155").pack(anchor="w", pady=(10, 2))
        ent_pw_student = tk.Entry(col2, bg="#f4f6fb", fg="#0f172a", font=("Segoe UI", 10))
        ent_pw_student.insert(0, cfg["passwords"].get("student", "hocsinh123"))
        ent_pw_student.pack(fill="x", ipady=3)

        def luu_admin() -> None:
            cfg["kiosk_hide_explorer"] = bool(var_hide_exp.get())
            cfg["enable_posture_ai"] = bool(var_posture_ai.get())
            try:
                cfg["camera_index"] = max(0, int(ent_cam.get().strip() or 0))
            except ValueError:
                cfg["camera_index"] = 0
            try:
                cfg["web_port"] = int(ent_port.get().strip() or 5000)
            except ValueError:
                cfg["web_port"] = 5000
            cfg["esp32_com_port"] = ent_com.get().strip() or "COM3"
            cfg["passwords"]["admin"] = ent_pw_admin.get().strip() or "admin123"
            cfg["passwords"]["parent"] = ent_pw_parent.get().strip() or "phuhuynh123"
            cfg["passwords"]["student"] = ent_pw_student.get().strip() or "hocsinh123"
            save_config(cfg)
            messagebox.showinfo("Thành công", "Đã cập nhật cấu hình hệ thống Admin!", parent=self.root)

        tk.Button(
            col2,
            text="💾 Lưu Thiết Lập Hệ Thống Admin",
            command=luu_admin,
            bg="#0284c7",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            pady=7,
            relief="flat",
        ).pack(fill="x", pady=(18, 12))

        tk.Button(
            col2,
            text="🚪 THOÁT HOÀN TOÀN PHẦN MỀM (TRẢ LẠI WINDOWS CHUẨN)",
            command=lambda: self.thoat_khan_cap("Admin chủ động đóng phần mềm", hien_thong_bao=True),
            bg="#e11d48",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            pady=10,
            relief="flat",
            cursor="hand2",
        ).pack(fill="x", side="bottom", pady=4)


def run_self_test() -> int:
    """
    Chế độ tự kiểm tra tự động (--self-test):
    Khởi tạo giao diện ẩn nhanh, đi qua đủ 4 màn hình (Lock -> Student + Task Manager -> Parent -> Admin),
    kiểm tra uiautomation DLL, Web Server nhúng cổng 5000, khôi phục sạch sẽ Windows rồi thoát mã 0.
    """
    import requests as req
    from services.giam_sat_worker import UIA_AVAILABLE

    ensure_data_files_exist()
    start_web_server_in_background()

    root = tk.Tk()
    root.withdraw()
    app_ui = PhanMemQuanLyBanHocEXE(root)
    root.update_idletasks()

    # Kiểm tra chuyển qua chế độ Học sinh & mở Student Task Manager
    app_ui.vao_che_do_hoc_sinh()
    root.update_idletasks()
    app_ui.mo_task_manager_hoc_sinh()
    root.update_idletasks()
    app_ui.bo_giam_sat.dung_lai()

    # Kiểm tra chế độ Phụ huynh & chế độ Admin
    app_ui.vao_che_do_phu_huynh()
    root.update_idletasks()
    app_ui.vao_che_do_admin()
    root.update_idletasks()

    # Kiểm tra Web Server nhúng, luồng MJPEG /video_feed & uiautomation DLL trong bản đóng gói
    cfg = load_config()
    port = int(cfg.get("web_port", 5000))
    resp = req.get(f"http://127.0.0.1:{port}/api/du_lieu_dashboard", timeout=2)
    assert resp.status_code == 200, f"Dashboard HTTP lỗi: {resp.status_code}"
    dash_json = resp.json()

    resp_vid = req.get(f"http://127.0.0.1:{port}/video_feed?snapshot=1", timeout=2)
    assert resp_vid.status_code == 200 and resp_vid.content[:2] == b"\xff\xd8", "Luồng /video_feed không trả về JPEG hợp lệ!"

    resp_recal = req.post(f"http://127.0.0.1:{port}/api/posture_recalibrate", timeout=2)
    assert resp_recal.status_code == 200, "API /api/posture_recalibrate lỗi!"
    resp_tel = req.post(
        f"http://127.0.0.1:{port}/api/posture_telemetry",
        json={"status": "OK", "camera_connected": False, "visual_focus_score": 100.0},
        timeout=2,
    )
    assert resp_tel.status_code == 200 and resp_tel.json().get("recalibrate_requested") is True

    assert UIA_AVAILABLE is True, "uiautomation chưa khả dụng trong môi trường đóng gói!"

    restore_windows_normal_state()
    stop_web_server_in_background()
    update_runtime_state(
        current_role="locked",
        monitoring_active=False,
        self_test_passed=True,
        uia_available=UIA_AVAILABLE,
        telegram_ready=bool(dash_json.get("telegram_ready", False)),
        posture_web_ready=True,
    )
    root.destroy()
    safe_log(
        f"[SELF-TEST PASS] UIA={UIA_AVAILABLE} | Telegram={dash_json.get('telegram_ready')} | PostureWeb=True | "
        "Tất cả 4 màn hình UI + Student Task Manager + Embedded Web Server + MJPEG /video_feed hoạt động hoàn hảo!"
    )
    return 0


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(run_self_test())

    ensure_data_files_exist()
    root = tk.Tk()
    app_instance = PhanMemQuanLyBanHocEXE(root)
    install_all_failsafes(on_ui_emergency=app_instance.thoat_khan_cap_tu_luong_phu)
    start_web_server_in_background()
    root.mainloop()


if __name__ == "__main__":
    main()
