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


class PhanMemQuanLyBanHocEXE:
    """Ứng dụng Desktop 3 cấp quyền quản lý kỷ luật số Bàn học Thông minh."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Hệ Thống Quản Lý Bàn Học Thông Minh - 3 Cấp Quyền")
        self.root.configure(bg="#090d16")

        self.bo_giam_sat = BoGiamSatThongMinh(on_remote_lock=self._nhan_lenh_khoa_tu_xa)
        # Chỉ dừng bộ giám sát tab/game khi mở khóa sang Phụ huynh/Admin; giữ nguyên Web Server cổng 5000 chạy suốt phiên!
        register_worker_cleanup(self.bo_giam_sat.dung_lai)

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

        bar = tk.Frame(self.root, bg="#111827", height=38, highlightbackground="#1e293b", highlightthickness=1)
        bar.pack(side="bottom", fill="x")

        info_text = (
            f"{admin_badge}  |  📱 Web Phụ huynh: {lan_url}  |  "
            "Phím tắt Cứu hộ Toàn cục: [Ctrl+Alt+Shift+F12] hoặc [Ctrl+Shift+F10]"
        )
        tk.Label(
            bar,
            text=info_text,
            bg="#111827",
            fg="#94a3b8",
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
            bg="#1e293b",
            padx=42,
            pady=30,
            highlightbackground="#06b6d4",
            highlightthickness=2,
        )
        box.place(relx=0.5, rely=0.47, anchor="center")

        tk.Label(
            box,
            text="HỆ THỐNG BÀN HỌC THÔNG MINH (STEM)",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 18, "bold"),
        ).pack()
        tk.Label(
            box,
            text="Kiểm soát Kỷ luật Số & Phân quyền Truy cập 3 Cấp (Cơ chế Gcafe / CSM)",
            bg="#1e293b",
            fg="#94a3b8",
            font=("Segoe UI", 10),
        ).pack(pady=(4, 16))

        # Khu vực 3 nút Điền nhanh Tài khoản Demo
        demo_frame = tk.LabelFrame(
            box,
            text=" ⚡ Chọn nhanh Tài khoản Demo ",
            bg="#0f172a",
            fg="#fde047",
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
            bg="#1e293b",
            fg="#e2e8f0",
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
                bg="#1e293b",
                fg="#f8fafc",
                selectcolor="#0f172a",
                activebackground="#1e293b",
                activeforeground="#38bdf8",
                font=("Segoe UI", 9, "normal"),
            ).pack(anchor="w", pady=2)

        form_row = tk.Frame(box, bg="#1e293b")
        form_row.pack(fill="x", pady=(12, 4))

        left_f = tk.Frame(form_row, bg="#1e293b")
        left_f.pack(side="left", expand=True, fill="x", padx=(0, 8))
        tk.Label(left_f, text="Tài khoản:", bg="#1e293b", fg="white", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        user_entry = tk.Entry(left_f, textvariable=self.user_var, font=("Segoe UI", 12), bg="#0f172a", fg="white", insertbackground="white")
        user_entry.pack(fill="x", pady=4, ipady=3)

        right_f = tk.Frame(form_row, bg="#1e293b")
        right_f.pack(side="left", expand=True, fill="x", padx=(8, 0))
        tk.Label(right_f, text="Mật khẩu:", bg="#1e293b", fg="white", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        pwd_wrap = tk.Frame(right_f, bg="#1e293b")
        pwd_wrap.pack(fill="x", pady=4)
        self.pwd_entry = tk.Entry(pwd_wrap, show="*", font=("Segoe UI", 12), bg="#0f172a", fg="white", insertbackground="white")
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
            bg="#334155",
            fg="white",
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
        self.tao_thanh_an_toan_day_man_hinh()

        topbar = tk.Frame(self.root, bg="#1e293b", height=64)
        topbar.pack(fill="x")

        tk.Label(
            topbar,
            text="👨‍🎓 KHÔNG GIAN HỌC TẬP TẬP TRUNG (CHẾ ĐỘ HỌC SINH)",
            bg="#1e293b",
            fg="#4ade80",
            font=("Segoe UI", 13, "bold"),
        ).pack(side="left", padx=22, pady=16)

        self.lbl_student_timer = tk.Label(
            topbar,
            text="🤖 Quota AI: 0.0 / 15 phút",
            bg="#0f172a",
            fg="#38bdf8",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=5,
        )
        self.lbl_student_timer.pack(side="left", padx=10)

        tk.Button(
            topbar,
            text="🔒 Khóa máy / Đổi tài khoản",
            command=self.hien_man_hinh_dang_nhap,
            bg="#475569",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=6,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=18, pady=12)

        tk.Button(
            topbar,
            text="🧰 Task Manager (Bản Học sinh)",
            command=self.mo_task_manager_hoc_sinh,
            bg="#0284c7",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=6,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=6, pady=12)

        body = tk.Frame(self.root, bg="#090d16", padx=38, pady=26)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="1. DANH SÁCH ỨNG DỤNG HỌC TẬP ĐƯỢC PHÉP SỬ DỤNG:",
            bg="#090d16",
            fg="#38bdf8",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 14))

        apps_grid = tk.Frame(body, bg="#090d16")
        apps_grid.pack(fill="x", pady=(0, 28))

        allowed_apps = cfg.get("allowed_apps", [])
        if not allowed_apps:
            tk.Label(
                apps_grid,
                text="(Chưa có ứng dụng nào được Phụ huynh cấp quyền)",
                bg="#090d16",
                fg="#94a3b8",
                font=("Segoe UI", 10),
            ).pack(anchor="w")
        else:
            for idx, item in enumerate(allowed_apps):
                r, c = divmod(idx, 4)
                btn = tk.Button(
                    apps_grid,
                    text=f"🖥️\n{item['name']}\n({item['exe_name']})",
                    width=24,
                    height=4,
                    bg="#1e293b",
                    activebackground="#334155",
                    fg="white",
                    font=("Segoe UI", 10, "bold"),
                    relief="ridge",
                    bd=1,
                    cursor="hand2",
                    command=lambda p=item["path"], n=item["name"]: self._mo_ung_dung_hoc_sinh(p, n),
                )
                btn.grid(row=r, column=c, padx=8, pady=8, sticky="w")

        tk.Label(
            body,
            text="2. CÁC THƯ MỤC TÀI LIỆU ĐƯỢC PHÉP TRUY CẬP:",
            bg="#090d16",
            fg="#38bdf8",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(4, 14))

        folder_row = tk.Frame(body, bg="#090d16")
        folder_row.pack(fill="x")

        for fd in cfg.get("allowed_folders", []):
            tk.Button(
                folder_row,
                text=f"📁  {fd}",
                bg="#334155",
                activebackground="#475569",
                fg="#fde047",
                font=("Segoe UI", 10, "bold"),
                padx=16,
                pady=10,
                relief="flat",
                cursor="hand2",
                command=lambda f=fd: self._mo_thu_muc_hoc_sinh(f),
            ).pack(side="left", padx=8, pady=6)

        note_box = tk.Frame(body, bg="#111827", padx=18, pady=14, highlightbackground="#1e293b", highlightthickness=1)
        note_box.pack(fill="x", side="bottom", pady=(20, 0))
        tk.Label(
            note_box,
            text=(
                "💡 Quy định phòng học số: Hệ thống đang bật chế độ bảo vệ tập trung tự động.\n"
                "• Các trang học tập (OLM, VietJack, Google Docs, Wikipedia, bài giảng YouTube môn học...) được phép truy cập bình thường.\n"
                "• Các trang mạng xã hội, YouTube Shorts, Web Game và phần mềm Game sẽ tự động đóng và gửi báo cáo về Phụ huynh."
            ),
            bg="#111827",
            fg="#cbd5e1",
            justify="left",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        # Khởi chạy bộ giám sát ngầm
        self.bo_giam_sat.bat_dau()
        self._cap_nhat_dong_ho_hoc_sinh()

    def _cap_nhat_dong_ho_hoc_sinh(self) -> None:
        if self._current_mode != "student":
            return
        try:
            cfg = load_config()
            max_ai = int(cfg.get("max_ai_minutes", 15))
            used_ai = round(self.bo_giam_sat.ai_seconds / 60.0, 1)
            self.lbl_student_timer.config(
                text=f"🤖 Quota AI: {used_ai} / {max_ai} phút  |  🟢 Đang bảo vệ tập trung"
            )
        except Exception:
            pass
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
        restore_windows_normal_state()
        update_runtime_state(current_role="parent", monitoring_active=False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self._can_giua_cua_so(1080, 700)
        self.tao_thanh_an_toan_day_man_hinh()

        cfg = load_config()
        port = int(cfg.get("web_port", 5000))

        header = tk.Frame(self.root, bg="#1e293b", pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="👪 BẢNG ĐIỀU KHIỂN PHỤ HUYNH (ĐÃ MỞ KHÓA WINDOWS BÌNH THƯỜNG)",
            bg="#1e293b",
            fg="#38bdf8",
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
            text=f"🌐 Mở Web Dashboard (:{port})",
            command=lambda: webbrowser.open(f"http://127.0.0.1:{port}"),
            bg="#0284c7",
            fg="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=5,
            relief="flat",
            cursor="hand2",
        ).pack(side="right", padx=6)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=12)

        # --- TAB 1: QUẢN LÝ ỨNG DỤNG & THƯ MỤC CHO PHÉP ---
        tab_apps = tk.Frame(notebook, bg="#0f172a", padx=18, pady=16)
        notebook.add(tab_apps, text="  1. Ứng dụng (.exe) & Thư mục Học sinh được phép mở  ")

        left_box = tk.LabelFrame(
            tab_apps,
            text=" Danh sách Ứng dụng (.exe) cho phép Học sinh sử dụng ",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
        )
        left_box.pack(side="left", fill="both", expand=True, padx=(0, 8))

        app_list = tk.Listbox(left_box, bg="#090d16", fg="white", font=("Segoe UI", 10), height=14)
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

        app_btns = tk.Frame(left_box, bg="#1e293b")
        app_btns.pack(fill="x", pady=6)
        tk.Button(app_btns, text="➕ Thêm ứng dụng (.exe)", command=them_ung_dung, bg="#0284c7", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        tk.Button(app_btns, text="🗑️ Xóa ứng dụng đã chọn", command=xoa_ung_dung, bg="#475569", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        refresh_apps_ui()

        right_box = tk.LabelFrame(
            tab_apps,
            text=" Danh sách Thư mục Tài liệu được phép mở ",
            bg="#1e293b",
            fg="#fde047",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=12,
        )
        right_box.pack(side="right", fill="both", expand=True, padx=(8, 0))

        folder_list = tk.Listbox(right_box, bg="#090d16", fg="#fde047", font=("Segoe UI", 10), height=14)
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

        fd_btns = tk.Frame(right_box, bg="#1e293b")
        fd_btns.pack(fill="x", pady=6)
        tk.Button(fd_btns, text="📁 Thêm thư mục", command=them_thu_muc, bg="#059669", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        tk.Button(fd_btns, text="🗑️ Xóa thư mục", command=xoa_thu_muc, bg="#475569", fg="white", padx=10, pady=5, relief="flat").pack(side="left", padx=4)
        refresh_folders_ui()

        # --- TAB 2: CHÍNH SÁCH WEB, GAME & GIỚI HẠN AI ---
        tab_policy = tk.Frame(notebook, bg="#1e293b", padx=22, pady=16)
        notebook.add(tab_policy, text="  2. Chính sách Link Web, Game cấm & Quota ChatGPT/AI  ")

        def make_policy_field(label_txt: str, init_val: str) -> tk.Entry:
            tk.Label(tab_policy, text=label_txt, bg="#1e293b", fg="#e2e8f0", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(6, 2))
            ent = tk.Entry(tab_policy, font=("Segoe UI", 10), bg="#090d16", fg="white", insertbackground="white")
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
        tab_stats = tk.Frame(notebook, bg="#1e293b", padx=20, pady=16)
        notebook.add(tab_stats, text="  3. Thống kê Thời gian sử dụng & Nhật ký Vi phạm  ")

        txt_report = tk.Text(tab_stats, bg="#090d16", fg="#e2e8f0", font=("Consolas", 10), height=20)
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

            txt_report.insert("end", "\n=== LỊCH SỬ CẢNH BÁO VI PHẠM GẦN NHẤT ===\n")
            alerts = state.get("alerts", [])
            if not alerts:
                txt_report.insert("end", "✅ Chưa ghi nhận vi phạm nào.\n")
            else:
                for item in alerts:
                    txt_report.insert("end", f"[{item.get('time', '')}] {item.get('message', '')}\n")

        stat_btns = tk.Frame(tab_stats, bg="#1e293b")
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
            bg="#475569",
            fg="white",
            padx=12,
            pady=6,
            relief="flat",
        ).pack(side="left", padx=6)
        tai_bao_cao()

    # =====================================================================
    # CẤP 1: CHẾ ĐỘ ADMIN (QUẢN TRỊ HỆ THỐNG LÕI & MỞ TOÀN QUYỀN WINDOWS)
    # =====================================================================
    def vao_che_do_admin(self) -> None:
        self._current_mode = "admin"
        self.bo_giam_sat.dung_lai()
        restore_windows_normal_state()
        update_runtime_state(current_role="admin", monitoring_active=False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self._can_giua_cua_so(980, 640)
        self.tao_thanh_an_toan_day_man_hinh()

        cfg = load_config()

        header = tk.Frame(self.root, bg="#1e293b", pady=12)
        header.pack(fill="x")
        tk.Label(
            header,
            text="🛠️ BẢNG QUẢN TRỊ HỆ THỐNG (QUYỀN ADMIN CAO NHẤT)",
            bg="#1e293b",
            fg="#c084fc",
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
            bg="#475569",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
            relief="flat",
        ).pack(side="right", padx=8)

        body = tk.Frame(self.root, bg="#090d16", padx=24, pady=20)
        body.pack(fill="both", expand=True)

        col1 = tk.LabelFrame(
            body,
            text=" 1. Thiết lập Bảo mật Hệ điều hành & Kiosk Shell ",
            bg="#1e293b",
            fg="#38bdf8",
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
            bg="#1e293b",
            fg="white",
            selectcolor="#0f172a",
            activebackground="#1e293b",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=6)

        tk.Label(col1, text="Cổng Web Server nội bộ (Mặc định: 5000):", bg="#1e293b", fg="#cbd5e1").pack(anchor="w", pady=(10, 2))
        ent_port = tk.Entry(col1, bg="#0f172a", fg="white", font=("Segoe UI", 10))
        ent_port.insert(0, str(cfg.get("web_port", 5000)))
        ent_port.pack(fill="x", ipady=3)

        tk.Label(col1, text="Cổng COM kết nối vi điều khiển ESP32-S3 (Hộp khóa điện thoại):", bg="#1e293b", fg="#cbd5e1").pack(anchor="w", pady=(10, 2))
        ent_com = tk.Entry(col1, bg="#0f172a", fg="white", font=("Segoe UI", 10))
        ent_com.insert(0, str(cfg.get("esp32_com_port", "COM3")))
        ent_com.pack(fill="x", ipady=3)

        tk.Label(col1, text="Công cụ bảo trì hệ thống nhanh:", bg="#1e293b", fg="#fde047", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(16, 6))
        tk.Button(
            col1,
            text="🧰 Mở Task Manager gốc của Windows",
            command=lambda: subprocess.Popen(["taskmgr.exe"]),
            bg="#334155",
            fg="white",
            relief="flat",
            pady=5,
        ).pack(fill="x", pady=3)
        tk.Button(
            col1,
            text="📂 Mở thư mục gốc chứa shared_config.json & File cứu hộ .bat",
            command=lambda: os.startfile(get_app_dir()),
            bg="#334155",
            fg="white",
            relief="flat",
            pady=5,
        ).pack(fill="x", pady=3)

        col2 = tk.LabelFrame(
            body,
            text=" 2. Quản lý Mật khẩu 3 Cấp & Thoát Hệ Thống ",
            bg="#1e293b",
            fg="#34d399",
            font=("Segoe UI", 10, "bold"),
            padx=16,
            pady=14,
        )
        col2.pack(side="right", fill="both", expand=True, padx=(10, 0))

        tk.Label(col2, text="Mật khẩu cấp ADMIN:", bg="#1e293b", fg="#cbd5e1").pack(anchor="w", pady=(4, 2))
        ent_pw_admin = tk.Entry(col2, bg="#0f172a", fg="white", font=("Segoe UI", 10))
        ent_pw_admin.insert(0, cfg["passwords"].get("admin", "admin123"))
        ent_pw_admin.pack(fill="x", ipady=3)

        tk.Label(col2, text="Mật khẩu cấp PHỤ HUYNH:", bg="#1e293b", fg="#cbd5e1").pack(anchor="w", pady=(10, 2))
        ent_pw_parent = tk.Entry(col2, bg="#0f172a", fg="white", font=("Segoe UI", 10))
        ent_pw_parent.insert(0, cfg["passwords"].get("parent", "phuhuynh123"))
        ent_pw_parent.pack(fill="x", ipady=3)

        tk.Label(col2, text="Mật khẩu cấp HỌC SINH:", bg="#1e293b", fg="#cbd5e1").pack(anchor="w", pady=(10, 2))
        ent_pw_student = tk.Entry(col2, bg="#0f172a", fg="white", font=("Segoe UI", 10))
        ent_pw_student.insert(0, cfg["passwords"].get("student", "hocsinh123"))
        ent_pw_student.pack(fill="x", ipady=3)

        def luu_admin() -> None:
            cfg["kiosk_hide_explorer"] = bool(var_hide_exp.get())
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

    # Kiểm tra Web Server nhúng & uiautomation DLL trong bản đóng gói
    cfg = load_config()
    port = int(cfg.get("web_port", 5000))
    resp = req.get(f"http://127.0.0.1:{port}/api/du_lieu_dashboard", timeout=2)
    assert resp.status_code == 200, f"Dashboard HTTP lỗi: {resp.status_code}"
    dash_json = resp.json()
    assert UIA_AVAILABLE is True, "uiautomation chưa khả dụng trong môi trường đóng gói!"

    restore_windows_normal_state()
    stop_web_server_in_background()
    update_runtime_state(
        current_role="locked",
        monitoring_active=False,
        self_test_passed=True,
        uia_available=UIA_AVAILABLE,
        telegram_ready=bool(dash_json.get("telegram_ready", False)),
    )
    root.destroy()
    safe_log(
        f"[SELF-TEST PASS] UIA={UIA_AVAILABLE} | Telegram={dash_json.get('telegram_ready')} | "
        "Tất cả 4 màn hình UI + Student Task Manager + Embedded Web Server hoạt động hoàn hảo!"
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
