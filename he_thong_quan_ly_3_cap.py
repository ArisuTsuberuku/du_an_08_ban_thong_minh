# -*- coding: utf-8 -*-
"""
Phần Mềm Trung Tâm Quản Lý FocusDesk 3 Cấp Quyền (he_thong_quan_ly_3_cap.py)
Đóng gói thành FocusDeskLauncher.exe (--uac-admin):
  - Giao diện Khóa Toàn màn hình kiểu Gcafe / CSM + 3 Tài khoản Demo nhanh.
  - Phân quyền 3 cấp: Admin (Quản trị hệ thống), Phụ huynh (Quản lý Whitelist/Web/AI & Báo cáo), Học sinh (Custom Shell).
  - Student Task Manager riêng: Chỉ hiện ứng dụng học tập được phép, ẩn tuyệt đối explorer.exe & python.
  - Tích hợp 5 Lớp An toàn Khẩn cấp chống kẹt máy (phím cứu hộ do Admin đặt + PIN, Crash Hooks, UNLOCK.txt, .bat).
  - Tự động khởi chạy ngầm Web Dashboard (0.0.0.0:5000) & Bộ giám sát URL/Game/AI (BoGiamSatThongMinh).
"""

import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import webbrowser
from typing import Any, Dict, Optional, Set

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
    ack_rescue_prompt,
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
    "focusdesklauncher.exe",
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
SERIAL_BAUDS = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600)


class PhanMemQuanLyBanHocEXE:
    """Ứng dụng Desktop 3 cấp quyền quản lý kỷ luật số Bàn học Thông minh."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("FocusDesk – Hệ thống quản lý bàn học thông minh 3 cấp quyền")
        self.root.configure(bg=UI["bg"])
        self._dat_icon_cua_so()

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

        # Hàng đợi gọi giao diện từ luồng nền (Tkinter chỉ an toàn khi thao tác trên luồng chính)
        import queue

        self._ui_q: "queue.Queue" = queue.Queue()
        self._bom_ui()
        self.hien_man_hinh_dang_nhap()

    def _dat_icon_cua_so(self) -> None:
        """Icon FocusDesk cho cửa sổ, thanh tác vụ và mọi hộp thoại con (assets/focusdesk.ico + .png)."""
        base = os.path.join(get_app_dir(), "assets")
        try:
            ico = os.path.join(base, "focusdesk.ico")
            if sys.platform.startswith("win") and os.path.exists(ico):
                self.root.iconbitmap(default=ico)
                try:   # tách khỏi nhóm "Python" trên thanh tác vụ -> hiện đúng icon FocusDesk
                    import ctypes

                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("D08.FocusDesk")
                except Exception:
                    pass
            png = os.path.join(base, "focusdesk_256.png")
            if os.path.exists(png):
                self._icon_img = tk.PhotoImage(file=png)
                self.root.iconphoto(True, self._icon_img)
        except Exception as exc:
            safe_log(f"[UI] Không đặt được icon cửa sổ: {exc}")

    def _goi_ui(self, fn) -> None:
        self._ui_q.put(fn)

    def _bom_ui(self) -> None:
        try:
            while True:
                fn = self._ui_q.get_nowait()
                try:
                    fn()
                except Exception as exc:
                    safe_log(f"[UI] Lỗi cập nhật từ luồng nền: {exc}")
        except Exception:
            pass
        try:
            self.root.after(80, self._bom_ui)
        except Exception:
            pass

    # =====================================================================
    # TIỆN ÍCH HỆ THỐNG & AN TOÀN KHẨN CẤP (FAIL-SAFE UI)
    # =====================================================================
    def _xu_ly_nut_dong_cua_so(self) -> None:
        """Ở màn hình Khóa và Học sinh thì chặn Alt+F4; ở quyền Phụ huynh/Admin thì hỏi chuyển về Khóa."""
        if self._current_mode in ("parent", "admin"):
            if messagebox.askyesno(
                "Chuyển về Màn hình Khóa?",
                "Bạn có muốn bật lại Màn hình Khóa để bàn giao máy cho Học sinh không?\n"
                "(Muốn tắt hẳn phần mềm: Admin → Thoát phần mềm, hoặc phím cứu hộ + PIN).",
                parent=self.root,
            ):
                self.hien_man_hinh_dang_nhap()

    def _nhan_lenh_khoa_tu_xa(self) -> None:
        """Callback an toàn đa luồng khi Phụ huynh bấm 'Khóa máy từ xa' trên Web hoặc Bot Telegram."""
        self._goi_ui(self._thuc_thi_khoa_tu_xa_ui)

    def _thuc_thi_khoa_tu_xa_ui(self) -> None:
        self.hien_man_hinh_dang_nhap()
        messagebox.showwarning(
            "Khóa Máy Từ Xa",
            "🔒 Phụ huynh vừa kích hoạt lệnh Khóa màn hình từ xa!",
            parent=self.root,
        )

    def thoat_khan_cap_tu_luong_phu(self, ly_do: str) -> None:
        """Được gọi từ luồng Win32 RegisterHotKey (phím cứu hộ do Admin đặt) hoặc luồng UNLOCK.txt.
        Phím cứu hộ -> hỏi PIN (học sinh lỡ biết phím cũng không thoát được); UNLOCK.txt -> thoát ngay."""
        if ly_do.startswith("Phím cứu hộ"):
            self._goi_ui(lambda: self.nhap_pin_cuu_ho(ly_do))
        else:
            self._goi_ui(lambda: self.thoat_khan_cap(ly_do, hien_thong_bao=False))

    def thoat_khan_cap(self, ly_do: str = "Khẩn cấp", hien_thong_bao: bool = True) -> None:
        if getattr(self, "_dang_thoat", False):
            return
        self._dang_thoat = True
        self._ket_thuc_ca_hoc("Thoát phần mềm")
        self._huy_bo_dem_ui()
        self._dong_dock()
        self._tat_kiosk()
        self.bo_giam_sat.dung_lai()

        # Cửa sổ tiến trình: hiện từng phần đang được tắt (báo phụ huynh, Cloudflare, AI camera, web nội bộ...)
        win = lbl = None
        try:
            win = tk.Toplevel(self.root)
            win.title("Đang tắt FocusDesk")
            win.configure(bg="white", padx=22, pady=18)
            win.attributes("-topmost", True)
            win.resizable(False, False)
            tk.Label(win, text="⏻  Đang tắt FocusDesk…", bg="white", fg="#0f172a",
                     font=("Segoe UI", 13, "bold")).pack(anchor="w")
            lbl = tk.Label(win, text="", bg="white", fg="#334155", font=("Segoe UI", 10), justify="left")
            lbl.pack(anchor="w", pady=(8, 0))
            win.update()
        except Exception:
            win = None
        shown: list = []

        def tien_trinh(line: str) -> None:
            shown.append(line)
            if lbl is not None:
                try:
                    lbl.config(text="\n".join(shown))
                    win.update()
                except Exception:
                    pass

        try:
            from services.app_shutdown import shutdown_all

            ket_qua = shutdown_all(ly_do, progress=tien_trinh)
        except Exception as exc:
            ket_qua = [f"⚠  Lỗi khi dọn dẹp: {exc}"]
            posture_manager.stop()
            stop_web_server_in_background()
        restore_windows_normal_state()
        ket_qua.append("✓  Đã mở khóa Task Manager, hiện lại thanh Taskbar và Desktop")
        tien_trinh(ket_qua[-1])
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
        if hien_thong_bao:
            try:
                messagebox.showinfo(
                    "Đã tắt FocusDesk",
                    f"Lý do: {ly_do}\n\n" + "\n".join(ket_qua),
                    parent=self.root,
                )
            except Exception:
                pass
        try:
            self.root.destroy()
        except Exception:
            pass
        emergency_restore_and_exit(ly_do, 0)

    def nhap_pin_cuu_ho(self, ly_do: str = "Cứu hộ") -> None:
        """Hộp nhập PIN cứu hộ — chỉ mở bằng phím cứu hộ Admin đặt; không có nút nào trên màn hình học sinh."""
        ack_rescue_prompt()   # giao diện còn phản hồi -> hủy hẹn giờ cưỡng bức khôi phục
        if getattr(self, "_dang_hoi_pin", False):
            return
        self._dang_hoi_pin = True
        try:
            pin = simpledialog.askstring("Cứu hộ", "Nhập mã PIN cứu hộ để mở khóa Windows và thoát FocusDesk:",
                                         show="*", parent=self.root)
        finally:
            self._dang_hoi_pin = False
        if pin is None:
            return
        if verify_rescue_pin(pin):
            self.thoat_khan_cap(ly_do + " + PIN đúng", hien_thong_bao=True)
        else:
            safe_log(f"[CỨU HỘ] Sai PIN cứu hộ ({ly_do}).")
            try:
                gui_canh_bao_len_web("⚠️ Có người bấm phím cứu hộ và nhập SAI mã PIN", "SYSTEM")
            except Exception:
                pass
            messagebox.showerror("Sai mã PIN", "Mã PIN cứu hộ không chính xác!", parent=self.root)

    def _huy_bo_dem_ui(self) -> None:
        if self._student_clock_job is not None:
            try:
                self.root.after_cancel(self._student_clock_job)
            except Exception:
                pass
            self._student_clock_job = None

    def xoa_man_hinh(self) -> None:
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):   # bỏ ràng buộc cuộn của trang cũ
            try:
                self.root.unbind_all(seq)
            except Exception:
                pass
        self._huy_bo_dem_ui()
        self._dong_dock()
        self._dong_man_nghi()
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

        # Không ghi phím / PIN cứu hộ ở đây: thanh này hiện cả ở màn khóa mà học sinh nhìn thấy
        info_text = f"{admin_badge}  |  📱 Web Phụ huynh: {lan_url}"
        tk.Label(
            bar,
            text=info_text,
            bg=UI["card"],
            fg=UI["mut"],
            font=("Segoe UI", 9),
        ).pack(side="left", padx=14, pady=6)


    # =====================================================================
    # MÀN HÌNH KHÓA ĐĂNG NHẬP 3 CẤP KIỂU GCAFE / CSM
    # =====================================================================
    @staticmethod
    def _hop_dt(on: bool) -> None:
        """Báo cho hộp điện thoại biết đang / hết giờ học (vào học + đã cất điện thoại -> tự khóa)."""
        try:
            from services.phone_box import set_studying

            threading.Thread(target=set_studying, args=(on,), daemon=True).start()
        except Exception:
            pass
        if not on:   # rời giờ học -> ngừng quét tắt app chạy nền
            try:
                from services.process_cleanup import stop_sweeper

                stop_sweeper()
            except Exception:
                pass

    def _ket_thuc_ca_hoc(self, ly_do: str) -> None:
        """Rời chế độ học sinh -> chốt ca học (gửi web phụ huynh để chấm ca thành công / cộng điểm thưởng)."""
        if getattr(self, "_current_mode", "") != "student":
            return
        try:
            from services import study_sessions

            cur = study_sessions.current() or {}
            box = load_runtime_state().get("phone_box") or {}
            if cur.get("source") == "box" and box.get("connected"):
                # Ca do tủ ESP32 đếm giờ: rời chế độ học sinh trước giờ -> báo tủ kết thúc ca (tủ gửi session_end,
                # máy chốt ca "Rời chế độ học sinh trước giờ" -> chưa đủ thời gian nên không đạt)
                if box.get("phase") == "active" and not box.get("test"):
                    from services.phone_box import get_box

                    threading.Thread(target=lambda: get_box().end_session("left_student"), daemon=True).start()
                return
            study_sessions.end_session(ly_do)
        except Exception as exc:
            safe_log(f"[CA HỌC] Không chốt được ca: {exc}")

    def hien_man_hinh_dang_nhap(self) -> None:
        self._ket_thuc_ca_hoc("Về màn hình khóa")
        self._current_mode = "locked"
        self._dong_serial_monitor()
        self._hop_dt(False)
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        update_runtime_state(current_role="locked", monitoring_active=False)
        threading.Thread(target=_dong_bo_dns, args=("locked",), daemon=True).start()   # chặn DNS chỉ khi học sinh dùng

        cfg = load_config()
        set_windows_task_manager_lock(True)
        if cfg.get("kiosk_hide_explorer", False):
            set_desktop_explorer_state(False)

        self.xoa_man_hinh()
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=UI["bg"])
        self._bat_kiosk(set())   # màn hình khóa: ẩn taskbar + chặn phím Windows, không app nào được lên trước
        self.tao_thanh_an_toan_day_man_hinh()

        card = tk.Frame(self.root, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        card.place(relx=0.5, rely=0.46, anchor="center", width=960, height=560)

        # ---- Nửa trái: thương hiệu ----
        brand = tk.Frame(card, bg="#1d4ed8", width=380)
        brand.pack(side="left", fill="y")
        brand.pack_propagate(False)
        logo = os.path.join(get_app_dir(), "assets", "focusdesk_64.png")
        if os.path.exists(logo):
            self._logo_img = tk.PhotoImage(file=logo)
            tk.Label(brand, image=self._logo_img, bg="#1d4ed8").pack(anchor="w", padx=36, pady=(48, 10))
        else:
            tk.Label(brand, text="📚", bg="#1d4ed8", fg="white", font=("Segoe UI Emoji", 40)).pack(anchor="w", padx=36,
                                                                                               pady=(48, 6))
        tk.Label(brand, text="FocusDesk", bg="#1d4ed8", fg="white", justify="left",
                 font=("Segoe UI", 28, "bold")).pack(anchor="w", padx=36)
        tk.Label(brand, text="Bàn học thông minh · góc học tập tập trung", bg="#1d4ed8", fg="#bfdbfe", wraplength=300,
                 justify="left",
                 font=("Segoe UI", 11)).pack(anchor="w", padx=36, pady=(6, 26))
        for line in ("🧩  Chỉ mở ứng dụng & tài liệu được phép", "📷  Camera AI nhắc tư thế, chống ngủ gật",
                     "📱  Phụ huynh theo dõi qua điện thoại"):
            tk.Label(brand, text=line, bg="#1d4ed8", fg="#e0e7ff", font=("Segoe UI", 10)).pack(anchor="w", padx=36, pady=4)
        self.lbl_lock_clock = tk.Label(brand, text="", bg="#1d4ed8", fg="white", font=("Segoe UI", 30, "bold"))
        self.lbl_lock_clock.pack(side="bottom", anchor="w", padx=36, pady=(0, 6))
        self.lbl_lock_date = tk.Label(brand, text="", bg="#1d4ed8", fg="#bfdbfe", font=("Segoe UI", 10))
        self.lbl_lock_date.pack(side="bottom", anchor="w", padx=36)

        # ---- Nửa phải: biểu mẫu ----
        form = tk.Frame(card, bg=UI["card"], padx=44, pady=36)
        form.pack(side="left", fill="both", expand=True)
        tk.Label(form, text="Đăng nhập", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(form, text="Chọn người dùng rồi nhập mật khẩu", bg=UI["card"], fg=UI["mut"],
                 font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 16))

        self.role_var = tk.StringVar(value="student")
        self.user_var = tk.StringVar(value=cfg["usernames"].get("student", "hocsinh"))
        roles = tk.Frame(form, bg=UI["card"])
        roles.pack(fill="x")
        role_desc = {"student": "Vào góc học tập", "parent": "Cài đặt & báo cáo", "admin": "Quản trị hệ thống"}

        hs_khong_mk = not cfg.get("student_needs_password", False)

        def chon_role() -> None:
            r = self.role_var.get()
            self.user_var.set(cfg["usernames"].get(r, r))
            self.pwd_entry.delete(0, "end")
            # Học sinh không cần mật khẩu (chế độ học sinh là chế độ bị giới hạn nhất) -> chỉ cần bấm "Vào học"
            no_pwd = r == "student" and hs_khong_mk
            self.pwd_entry.config(state="disabled" if no_pwd else "normal")
            btn_login.config(text="Vào học  →" if no_pwd else "Đăng nhập  →")
            lbl_role_desc.config(text=role_desc.get(r, "") + (" · không cần mật khẩu" if no_pwd else ""))
            (btn_login if no_pwd else self.pwd_entry).focus_set()

        for val, txt in (("student", "👨‍🎓\nHọc sinh"), ("parent", "👪\nPhụ huynh"), ("admin", "🛠️\nAdmin")):
            tk.Radiobutton(roles, text=txt, value=val, variable=self.role_var, command=chon_role, indicatoron=0,
                           font=("Segoe UI", 11, "bold"), bg="#f1f5f9", fg=UI["txt"], selectcolor="#dbeafe",
                           activebackground="#e0e7ff", relief="flat", bd=0, padx=10, pady=10, cursor="hand2",
                           highlightthickness=0).pack(side="left", expand=True, fill="x", padx=(0, 8))
        lbl_role_desc = tk.Label(form, text=role_desc["student"], bg=UI["card"], fg=UI["acc"], font=("Segoe UI", 9, "bold"))
        lbl_role_desc.pack(anchor="w", pady=(6, 10))

        def field(label: str) -> tk.Frame:
            tk.Label(form, text=label, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 3))
            wrap = tk.Frame(form, bg=UI["card"], highlightbackground=UI["line"], highlightcolor=UI["acc"],
                            highlightthickness=1)
            wrap.pack(fill="x")
            return wrap

        w1 = field("Tài khoản")
        tk.Entry(w1, textvariable=self.user_var, font=("Segoe UI", 12), bg=UI["card"], fg=UI["txt"], relief="flat",
                 highlightthickness=0, insertbackground=UI["txt"]).pack(fill="x", padx=10, ipady=7)
        w2 = field("Mật khẩu")
        self.pwd_entry = tk.Entry(w2, show="•", font=("Segoe UI", 12), bg=UI["card"], fg=UI["txt"], relief="flat",
                                  highlightthickness=0, insertbackground=UI["txt"])
        self.pwd_entry.pack(side="left", fill="x", expand=True, padx=10, ipady=7)
        self.pwd_entry.bind("<Return>", lambda _e: self.xu_ly_dang_nhap())
        self._show_pwd = False

        def toggle_pwd() -> None:
            self._show_pwd = not self._show_pwd
            self.pwd_entry.config(show="" if self._show_pwd else "•")

        tk.Button(w2, text="👁", command=toggle_pwd, bg=UI["card"], fg=UI["mut"], relief="flat", bd=0,
                  highlightthickness=0, cursor="hand2", font=("Segoe UI Emoji", 11)).pack(side="right", padx=6)

        btn_login = tk.Button(form, text="Đăng nhập  →", command=self.xu_ly_dang_nhap, bg=UI["acc"], fg="white",
                              activebackground="#1d4ed8", activeforeground="white", font=("Segoe UI", 12, "bold"),
                              relief="flat", bd=0, pady=10, cursor="hand2")
        btn_login.pack(fill="x", pady=(20, 10))
        btn_login.bind("<Return>", lambda _e: self.xu_ly_dang_nhap())

        # Chỉ nhắc đổi mật khẩu PHỤ HUYNH / ADMIN (không in mật khẩu ra màn hình học sinh nhìn thấy)
        defaults = {"parent": "phuhuynh123", "admin": "admin123"}
        if any(cfg.get("passwords", {}).get(k) == v for k, v in defaults.items()):
            tk.Label(form, bg=UI["card"], fg=UI["warn"], font=("Segoe UI", 9), justify="left", wraplength=470, text=(
                "⚠️ Mật khẩu Phụ huynh / Admin vẫn là mặc định — hãy đổi trong Admin → Mật khẩu trước khi giao máy."
            )).pack(anchor="w")
        chon_role()
        self._cap_nhat_dong_ho_khoa()

    def _cap_nhat_dong_ho_khoa(self) -> None:
        if self._current_mode != "locked":
            return
        try:
            self.lbl_lock_clock.config(text=time.strftime("%H:%M"))
            thu = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"][time.localtime().tm_wday]
            self.lbl_lock_date.config(text=f"{thu}, {time.strftime('%d/%m/%Y')}")
        except Exception:
            return
        self._student_clock_job = self.root.after(10_000, self._cap_nhat_dong_ho_khoa)

    # =====================================================================
    # KIOSK: ẨN TASKBAR + CHẶN PHÍM WINDOWS + CANH CỬA SỔ NGOÀI DANH SÁCH
    # =====================================================================
    def _root_hwnd(self) -> int:
        try:
            return int(self.root.wm_frame(), 16)
        except Exception:
            return 0

    def _bat_kiosk(self, allowed_exes: Set[str]) -> None:
        from services import kiosk_guard

        cfg = load_config()
        if not cfg.get("kiosk_strict", True) or "--self-test" in sys.argv:
            return
        hwnd = self._root_hwnd()

        def vi_pham(exe: str, title: str) -> None:
            if title.startswith("thư mục ngoài danh sách"):
                gui_canh_bao_len_web(f"📁 Học sinh mở {title[:120]} — đã tự đưa về thư mục được phép", "GAME")
                return
            gui_canh_bao_len_web(f"🚫 Học sinh mở ứng dụng ngoài danh sách: {exe} ({title[:60]}) — đã tự thu nhỏ",
                                 "GAME")

        kiosk_guard.start_kiosk(allowed_exes, on_violation=vi_pham if self._current_mode == "student" else None,
                                on_refocus=lambda: kiosk_guard.focus_window(hwnd),
                                allowed_folders=cfg.get("allowed_folders", []),
                                allow_win_tools=cfg.get("kiosk_allow_win_tools", True))

    @staticmethod
    def _tat_kiosk() -> None:
        try:
            from services import kiosk_guard

            kiosk_guard.stop_kiosk()
        except Exception:
            pass

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
        no_pwd_student = role == "student" and not cfg.get("student_needs_password", False)
        if no_pwd_student or pwd == expected_pwd:
            self.so_lan_sai_mat_khau = 0
            if role == "student":
                if load_config().get("phone_box_enabled") and load_config().get("phone_box_required"):
                    st = load_runtime_state().get("phone_box") or {}
                    if st.get("connected") and not st.get("phone"):
                        if int(st.get("proto", 1) or 1) >= 2:
                            messagebox.showwarning("Cất điện thoại", "📱 Hãy cất điện thoại vào tủ, cắm sạc và đóng cửa "
                                                   "tủ trước khi vào học nhé!\nCa học bắt đầu tính giờ khi tủ đóng.",
                                                   parent=self.root)
                        else:
                            messagebox.showwarning("Cất điện thoại", "📱 Hãy bỏ điện thoại vào hộp trước khi vào học nhé!\n"
                                                   "Hộp sẽ tự khóa khi bạn bắt đầu học.", parent=self.root)
                        return
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
        try:
            from services import study_sessions

            _st = load_runtime_state()
            _box = _st.get("phone_box") or {}
            _tu_v2 = bool(load_config().get("phone_box_enabled") and _box.get("connected")
                          and int(_box.get("proto", 1) or 1) >= 2)
            if _tu_v2:
                pass   # tủ điện thoại v2: ca học bắt đầu khi đóng cửa tủ + cắm sạc (tủ tự đếm giờ), không mở ca ở đây
            else:
                study_sessions.start_session(ai_seconds=float(_st.get("ai_seconds", 0) or 0),
                                             phone_in_box=bool(_box.get("phone")) if load_config().get("phone_box_enabled")
                                             else None)
        except Exception as exc:
            safe_log(f"[CA HỌC] Không mở được ca: {exc}")
        self._current_mode = "student"
        self._dong_serial_monitor()
        self._hop_dt(True)
        cfg = load_config()
        update_runtime_state(current_role="student", remote_lock_requested=False)
        threading.Thread(target=_dong_bo_dns, args=("student",), daemon=True).start()   # chặn DNS chỉ khi học sinh dùng

        self.xoa_man_hinh()
        set_windows_task_manager_lock(True)
        if cfg.get("kiosk_hide_explorer", False):
            set_desktop_explorer_state(False)

        # Giữ fullscreen để che Desktop nhưng tắt topmost để cửa sổ Chrome/App học tập mở lên trên được
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", False)
        self.root.configure(bg=UI["bg"])
        # Taskbar Windows bị ẩn -> thay bằng thanh dock riêng (ứng dụng đang mở, đồng hồ, khóa, 🆘)
        tk.Frame(self.root, bg=UI["bg"], height=56).pack(side="bottom", fill="x")
        allowed_exes = {a.get("exe_name", "").lower() for a in cfg.get("allowed_apps", []) if a.get("exe_name")}
        self._bat_kiosk(allowed_exes)
        self._tao_dock_hoc_sinh(cfg)
        if "--self-test" not in sys.argv:
            # Tắt ứng dụng chạy nền (trừ Windows, bộ gõ, OneDrive, driver, diệt virus, app được cấp) — chạy nền
            from services.process_cleanup import on_student_start

            threading.Thread(target=on_student_start, args=(allowed_exes,),
                             kwargs={"notify": lambda m: gui_canh_bao_len_web(m, "SYSTEM")}, daemon=True).start()

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
        self._ui_button(header, "🚻  Xin đi vệ sinh", self.xin_di_ve_sinh, kind="ghost").pack(side="right", padx=6, pady=16)
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

    # ---------- Thanh dock học sinh (thay Taskbar Windows đã bị ẩn) ----------
    def _tao_dock_hoc_sinh(self, cfg: Dict[str, Any]) -> None:
        self._dong_dock()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        hgt = 56
        dock = tk.Toplevel(self.root)
        dock.overrideredirect(True)
        dock.attributes("-topmost", True)
        dock.geometry(f"{sw}x{hgt}+0+{sh - hgt}")
        dock.configure(bg="#0f172a")
        self._dock = dock
        self._dock_apps_sig = None
        self._dock_allowed = {a.get("exe_name", "").lower(): a for a in cfg.get("allowed_apps", []) if a.get("exe_name")}

        def ve_goc_hoc_tap() -> None:
            try:
                self.root.deiconify()
                self.root.lift()
                from services import kiosk_guard

                kiosk_guard.focus_window(self._root_hwnd())
            except Exception:
                pass

        def nut(parent, text, cmd, bg="#1e293b", fg="white", bold=True):
            return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground="#334155",
                             activeforeground="white", relief="flat", bd=0, padx=14, pady=8, cursor="hand2",
                             font=("Segoe UI", 10, "bold" if bold else "normal"))

        nut(dock, "🏠  Góc học tập", ve_goc_hoc_tap, bg=UI["acc"]).pack(side="left", padx=(10, 6), pady=8)
        self._dock_apps = tk.Frame(dock, bg="#0f172a")
        self._dock_apps.pack(side="left", fill="y", padx=4)
        nut(dock, "🔒 Khóa", self.hien_man_hinh_dang_nhap).pack(side="right", padx=(4, 10), pady=8)
        nut(dock, "🚻 Xin đi vệ sinh", self.xin_di_ve_sinh, bg="#0e7490").pack(side="right", padx=4, pady=8)
        self._dock_clock = tk.Label(dock, text="", bg="#0f172a", fg="white", font=("Segoe UI", 12, "bold"))
        self._dock_clock.pack(side="right", padx=12)
        self._dock_focus = tk.Label(dock, text="", bg="#0f172a", fg="#86efac", font=("Segoe UI", 10, "bold"))
        self._dock_focus.pack(side="right", padx=8)
        self._dock_box = tk.Label(dock, text="", bg="#0f172a", fg="#cbd5e1", font=("Segoe UI", 10, "bold"))
        self._dock_box.pack(side="right", padx=8)
        self._dock_alert = tk.Label(dock, text="", bg="#0f172a", fg="#fcd34d", font=("Segoe UI", 10, "bold"))
        self._dock_alert.pack(side="right", padx=8)
        alerts = load_runtime_state().get("alerts") or []
        self._dock_seen_alert = f"{alerts[0].get('time')}|{alerts[0].get('message')}" if alerts else ""
        self._dock_last_ai = ""
        self._cap_nhat_dock()
        if (load_runtime_state().get("toilet_break") or {}).get("active"):
            self._hien_man_nghi()

    def _cap_nhat_dock(self) -> None:
        dock = getattr(self, "_dock", None)
        if dock is None or self._current_mode != "student":
            return
        try:
            from services import kiosk_guard
            from services.app_icons import emoji_for, get_icon_image

            self._dock_clock.config(text=time.strftime("%H:%M"))
            bx = load_runtime_state().get("phone_box") or {}
            if load_config().get("phone_box_enabled") and bx.get("connected") and int(bx.get("proto", 1) or 1) >= 2:
                # Tủ v2: đếm ngược ca học ngay trên thanh dock
                if bx.get("phase") in ("active", "break"):
                    self._dem_nguoc_dock()            # đồng hồ riêng 0,25 s/lần cho mượt (dock chính 1,5 s/lần)
                elif bx.get("phase") == "done":
                    self._dock_box.config(text="✅ Hết giờ học", fg="#86efac")
                elif bx.get("ble") and bx.get("phone") and not bx.get("verified"):
                    self._dock_box.config(text="🔑 Điện thoại chưa xác thực — nhờ phụ huynh ghép lại", fg="#fca5a5")
                else:
                    thieu = [t for ok, t in ((bx.get("phone"), "cắm sạc"), (bx.get("door") == "closed", "đóng cửa tủ"))
                             if not ok]
                    self._dock_box.config(text="📦 Cất điện thoại vào tủ" + (": " + ", ".join(thieu) if thieu else
                                                                             " — đang xác thực…"), fg="#fcd34d")
            elif load_config().get("phone_box_enabled"):
                self._dock_box.config(text=("📦 mất kết nối" if not bx.get("connected") else
                                            ("📱🔒 đã cất" if bx.get("phone") and bx.get("lock") == "locked" else
                                             ("📱 trong hộp" if bx.get("phone") else "📭 hộp trống"))))
            pt = load_runtime_state().get("posture_telemetry") or {}
            if pt.get("camera_connected") and pt.get("calibrated"):
                cfi = float(pt.get("cfi_score", 100) or 0)
                self._dock_focus.config(text=f"🎯 {cfi:.0f}%", fg="#86efac" if cfi >= 75 else ("#fcd34d" if cfi >= 50 else "#fca5a5"))
            # Cảnh báo cho HỌC SINH: dải chữ trên dock (đang vi phạm) + thông báo nổi khi có cảnh báo mới
            ai_alerts = (pt.get("alerts") or []) if pt.get("camera_connected") else []
            tip = self._loi_khuyen(ai_alerts[0]) if ai_alerts else ""
            self._dock_alert.config(text=tip[:70])
            if ai_alerts and ai_alerts[0] != self._dock_last_ai:
                self._toast_hoc_sinh(self._loi_khuyen(ai_alerts[0]))
            self._dock_last_ai = ai_alerts[0] if ai_alerts else ""
            alerts = load_runtime_state().get("alerts") or []
            if alerts:
                key = f"{alerts[0].get('time')}|{alerts[0].get('message')}"
                if key != self._dock_seen_alert:
                    self._dock_seen_alert = key
                    msg = str(alerts[0].get("message", ""))
                    if "Camera AI" not in msg and "Tư thế AI" not in msg and "vệ sinh" not in msg:
                        self._toast_hoc_sinh(msg)
            wins = kiosk_guard.list_app_windows(self._dock_allowed.keys())[:8]
            sig = tuple((w["hwnd"], w["title"]) for w in wins)
            if sig != self._dock_apps_sig:
                self._dock_apps_sig = sig
                for w in self._dock_apps.winfo_children():
                    w.destroy()
                fg_hwnd = 0
                try:
                    import ctypes

                    fg_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
                except Exception:
                    pass
                for w in wins:
                    app = self._dock_allowed.get(w["exe"], {})
                    img = get_icon_image(app.get("path", ""), 22) if app else None
                    title = str(w["title"])
                    title = title if len(title) <= 26 else title[:24] + "…"
                    b = tk.Button(self._dock_apps, text=f" {title}", command=lambda h=w["hwnd"]: kiosk_guard.focus_window(h),
                                  bg="#334155" if w["hwnd"] == fg_hwnd else "#1e293b", fg="white",
                                  activebackground="#475569", relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
                                  font=("Segoe UI", 9), compound="left")
                    if img is not None:
                        b.config(image=img)
                        b.image = img
                    else:
                        b.config(text=f"{emoji_for(w['exe'])} {title}")
                    b.pack(side="left", padx=3, pady=9)
        except Exception as exc:
            safe_log(f"[UI] Lỗi cập nhật dock: {exc}")
        self._dock_job = self.root.after(1500, self._cap_nhat_dock)

    @staticmethod
    def _loi_khuyen(alert: str) -> str:
        """Đổi cảnh báo kỹ thuật thành lời nhắc thân thiện cho học sinh."""
        a = alert.upper()
        for key, tip in (("BUỒN NGỦ", "😴 Bạn có vẻ buồn ngủ — đứng dậy vươn vai, uống nước nhé!"),
                         ("NGHỊCH ĐỒ", "📵 Cất điện thoại / đồ chơi đi và quay lại bài nhé!"),
                         ("XAO NHÃNG", "📱 Để điện thoại ra xa bàn học nhé!"),
                         ("MẤT TẬP TRUNG", "👀 Tập trung vào bài nào!"),
                         ("GỤC ĐẦU", "🙆 Ngồi dậy học tiếp nào, mệt thì xin nghỉ 5 phút nhé!"),
                         ("CẬN THỊ", "📏 Ngồi xa màn hình ra một chút (ít nhất 30 cm)!"),
                         ("SAI TƯ THẾ", "🧍 Ngồi thẳng lưng, đầu thẳng nhé!"),
                         ("VẮNG MẶT", "🪑 Không thấy bạn trước camera — đi đâu thì bấm 🚻 xin phép nhé!"),
                         ("NGHI ẢNH TĨNH", "📷 Đừng che hoặc đặt ảnh trước camera nhé!"),
                         ("GIAN LẬN", "📷 Không dùng camera ảo khi học nhé!")):
            if key in a:
                return tip
        return "⚠️ " + alert

    def _dem_nguoc_dock(self) -> None:
        """Đếm ngược ca học trên dock theo mốc end_at ổn định (không nhảy cóc 19 -> 17 s)."""
        lbl = getattr(self, "_dock_box", None)
        if lbl is None or self._current_mode != "student" or not lbl.winfo_exists():
            return
        bx = load_runtime_state().get("phone_box") or {}
        end_at = bx.get("end_at")
        if bx.get("phase") not in ("active", "break") or not isinstance(end_at, (int, float)):
            return
        left = max(0, math.ceil(end_at - time.time()))
        if bx.get("phase") == "break":
            lbl.config(text=f"☕ Nghỉ giữa ca còn {left // 60:02d}:{left % 60:02d}", fg="#fcd34d")
        else:
            lbl.config(text=f"⏳ Ca học còn {left // 60:02d}:{left % 60:02d}", fg="#86efac")
        old = getattr(self, "_dock_cd_job", None)
        if old is not None:
            try:
                self.root.after_cancel(old)
            except Exception:
                pass
        self._dock_cd_job = self.root.after(250, self._dem_nguoc_dock)

    def _dong_dock(self) -> None:
        job = getattr(self, "_dock_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._dock_job = None
        dock = getattr(self, "_dock", None)
        if dock is not None:
            try:
                dock.destroy()
            except Exception:
                pass
            self._dock = None
    # ---------- Quét ứng dụng đã cài (Start Menu) để phụ huynh tick chọn ----------
    def _hop_quet_ung_dung(self, cfg: Dict[str, Any], on_done) -> None:
        from services.app_icons import emoji_for, get_icon_image
        from services.app_scanner import scan_installed_apps

        win = tk.Toplevel(self.root)
        win.title("Chọn ứng dụng cho học sinh")
        win.configure(bg=UI["bg"])
        win.geometry("720x620")
        win.transient(self.root)
        win.grab_set()
        tk.Label(win, text="🔍  Ứng dụng đã cài trên máy", bg=UI["bg"], fg=UI["txt"],
                 font=("Segoe UI", 14, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(win, text="Tick các ứng dụng học tập muốn cho phép rồi bấm “Thêm”.", bg=UI["bg"], fg=UI["mut"],
                 font=("Segoe UI", 9)).pack(anchor="w", padx=18)
        sbox = tk.Frame(win, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        sbox.pack(fill="x", padx=18, pady=10)
        q = tk.StringVar()
        tk.Label(sbox, text="🔎", bg=UI["card"]).pack(side="left", padx=(8, 0))
        tk.Entry(sbox, textvariable=q, relief="flat", highlightthickness=0, bg=UI["card"],
                 font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, padx=6, ipady=6)
        lst_wrap = tk.Frame(win, bg=UI["bg"])
        lst_wrap.pack(fill="both", expand=True, padx=18)
        canvas = tk.Canvas(lst_wrap, bg=UI["card"], highlightthickness=1, highlightbackground=UI["line"])
        sb = ttk.Scrollbar(lst_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        inner = tk.Frame(canvas, bg=UI["card"])
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        status = tk.Label(win, text="⏳ Đang quét menu Start…", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 9))
        status.pack(anchor="w", padx=18, pady=4)
        have = {a.get("path", "").lower() for a in cfg.get("allowed_apps", [])}
        state: Dict[str, Any] = {"apps": [], "vars": {}}

        def ve() -> None:
            for w in inner.winfo_children():
                w.destroy()
            key = q.get().strip().lower()
            shown = 0
            for a in state["apps"]:
                if key and key not in a["name"].lower() and key not in a["exe_name"].lower():
                    continue
                shown += 1
                var = state["vars"].setdefault(a["path"], tk.BooleanVar(value=False))
                row = tk.Frame(inner, bg=UI["card"])
                row.pack(fill="x", padx=6, pady=1)
                already = a["path"].lower() in have
                tk.Checkbutton(row, variable=var, bg=UI["card"], selectcolor=UI["card"], highlightthickness=0,
                               state="disabled" if already else "normal").pack(side="left")
                img = get_icon_image(a["path"], 22)
                ic = tk.Label(row, image=img, bg=UI["card"]) if img is not None else \
                    tk.Label(row, text=emoji_for(a["exe_name"]), bg=UI["card"], font=("Segoe UI Emoji", 12))
                if img is not None:
                    ic.image = img
                ic.pack(side="left", padx=6)
                tk.Label(row, text=a["name"], bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10, "bold")).pack(side="left")
                tk.Label(row, text=("  ✓ đã có" if already else f"  {a['exe_name']}"), bg=UI["card"],
                         fg=UI["ok"] if already else UI["mut"], font=("Segoe UI", 9)).pack(side="left")
                for w in row.winfo_children():
                    if not isinstance(w, tk.Checkbutton) and not already:
                        w.bind("<Button-1>", lambda _e, v=var: v.set(not v.get()))
            status.config(text=f"{len(state['apps'])} ứng dụng · đang hiện {shown}")

        q.trace_add("write", lambda *_a: ve())

        def quet() -> None:
            apps = scan_installed_apps()

            def xong() -> None:
                if not win.winfo_exists():
                    return
                state["apps"] = [a for a in apps if a["exe_name"].lower() not in FORBIDDEN_STUDENT_PROCESSES]
                if not state["apps"]:
                    status.config(text="Không đọc được menu Start (chỉ hỗ trợ Windows có pywin32).")
                ve()
            self._goi_ui(xong)

        threading.Thread(target=quet, daemon=True).start()

        def them() -> None:
            added = 0
            for a in state["apps"]:
                var = state["vars"].get(a["path"])
                if var is not None and var.get() and a["path"].lower() not in have:
                    cfg.setdefault("allowed_apps", []).append({"name": a["name"], "exe_name": a["exe_name"],
                                                               "path": a["path"]})
                    added += 1
            if added:
                save_config(cfg)
                on_done()
            win.destroy()

        bar = tk.Frame(win, bg=UI["bg"])
        bar.pack(fill="x", padx=18, pady=(4, 14))
        self._ui_button(bar, "➕  Thêm mục đã chọn", them).pack(side="right")
        self._ui_button(bar, "Đóng", win.destroy, kind="ghost").pack(side="right", padx=8)

    # ---------- Xin đi vệ sinh: khóa màn hình nghỉ + tính giờ từ lúc camera không còn thấy học sinh ----------
    def xin_di_ve_sinh(self) -> None:
        if self._current_mode != "student":
            return
        if not messagebox.askyesno("Xin đi vệ sinh", "Bạn muốn xin đi vệ sinh?\nMáy sẽ tạm khóa và tính giờ cho tới "
                                   "khi bạn quay lại.", parent=self.root):
            return
        now = time.time()
        update_runtime_state(toilet_break={"active": True, "start": now, "left_t": None})
        gui_canh_bao_len_web(f"🚻 Học sinh xin đi vệ sinh lúc {time.strftime('%H:%M')}", "BREAK")
        self._hien_man_nghi()

    def _hien_man_nghi(self) -> None:
        self._dong_man_nghi()
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.geometry(f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0")
        ov.configure(bg="#0f172a")
        self._break_ov = ov
        box = tk.Frame(ov, bg="#0f172a")
        box.place(relx=0.5, rely=0.45, anchor="center")
        tk.Label(box, text="🚻", bg="#0f172a", font=("Segoe UI Emoji", 54)).pack()
        tk.Label(box, text="Đang nghỉ đi vệ sinh", bg="#0f172a", fg="white", font=("Segoe UI", 26, "bold")).pack(pady=(4, 2))
        self._lbl_break_info = tk.Label(box, text="", bg="#0f172a", fg="#94a3b8", font=("Segoe UI", 12))
        self._lbl_break_info.pack()
        self._lbl_break_timer = tk.Label(box, text="00:00", bg="#0f172a", fg="#38bdf8", font=("Consolas", 56, "bold"))
        self._lbl_break_timer.pack(pady=12)
        tk.Label(box, text="Camera sẽ tự nhận ra khi bạn ngồi lại vào bàn.", bg="#0f172a", fg="#cbd5e1",
                 font=("Segoe UI", 11)).pack()
        tk.Button(box, text="✅  Tôi đã quay lại", command=lambda: self._ket_thuc_nghi("button"), bg=UI["ok"], fg="white",
                  activebackground="#15803d", activeforeground="white", relief="flat", bd=0, padx=24, pady=10,
                  font=("Segoe UI", 13, "bold"), cursor="hand2").pack(pady=18)
        self._cap_nhat_man_nghi()

    def _cap_nhat_man_nghi(self) -> None:
        ov = getattr(self, "_break_ov", None)
        if ov is None or not ov.winfo_exists():
            return
        brk = load_runtime_state().get("toilet_break") or {}
        if not brk.get("active"):
            dur = brk.get("duration_s")
            self._dong_man_nghi()
            if dur is not None:
                self._toast_hoc_sinh(f"✅ Chào mừng trở lại! Bạn đã nghỉ {int(dur) // 60} phút {int(dur) % 60} giây.", ok=True)
            return
        now = time.time()
        if brk.get("left_t"):
            ref = float(brk["left_t"])
            self._lbl_break_info.config(text=f"Rời bàn lúc {time.strftime('%H:%M', time.localtime(ref))}")
        else:
            ref = float(brk.get("start", now))
            self._lbl_break_info.config(text=f"Đã xin lúc {time.strftime('%H:%M', time.localtime(ref))} · chờ camera "
                                             "xác nhận rời bàn")
        el = max(0, int(now - ref))
        lim = int(load_config().get("toilet_max_minutes", 10)) * 60
        self._lbl_break_timer.config(text=f"{el // 60:02d}:{el % 60:02d}", fg="#f87171" if el > lim else "#38bdf8")
        self._break_job = self.root.after(1000, self._cap_nhat_man_nghi)

    def _ket_thuc_nghi(self, by: str) -> None:
        brk = load_runtime_state().get("toilet_break") or {}
        if brk.get("active"):
            now = time.time()
            ref = float(brk.get("left_t") or brk.get("start") or now)
            dur = int(now - ref)
            update_runtime_state(toilet_break={**brk, "active": False, "back_t": now, "duration_s": dur, "ended_by": by})
            gui_canh_bao_len_web(f"✅ Học sinh bấm “Đã quay lại” sau {dur // 60} phút {dur % 60} giây "
                                 f"(tính từ {time.strftime('%H:%M', time.localtime(ref))})", "BREAK")
        self._dong_man_nghi()

    def _dong_man_nghi(self) -> None:
        job = getattr(self, "_break_job", None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
            self._break_job = None
        ov = getattr(self, "_break_ov", None)
        if ov is not None:
            try:
                ov.destroy()
            except Exception:
                pass
            self._break_ov = None

    # ---------- Thông báo nổi cho học sinh (luôn nằm trên mọi ứng dụng, cạnh thanh dock) ----------
    def _toast_hoc_sinh(self, text: str, ok: bool = False, seconds: float = 7.0) -> None:
        old = getattr(self, "_toast", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        bg = "#065f46" if ok else "#7f1d1d"
        t.configure(bg=bg)
        tk.Label(t, text=text, bg=bg, fg="white", font=("Segoe UI", 11, "bold"), wraplength=420, justify="left",
                 padx=16, pady=12).pack()
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t.geometry(f"+{sw - t.winfo_width() - 16}+{sh - 56 - t.winfo_height() - 12}")
        self._toast = t
        t.after(int(seconds * 1000), lambda: t.winfo_exists() and t.destroy())

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
        """Task Manager bản học sinh, dạng "Ứng dụng" như Task Manager Windows: mỗi ỨNG DỤNG 1 dòng (gộp mọi tiến trình
        con, vd Edge 11 tiến trình -> 1 dòng), mở ra thấy từng cửa sổ. Chỉ hiện ứng dụng học tập được phép."""
        from services import kiosk_guard

        cfg = load_config()
        names = {a["exe_name"].lower(): a.get("name") or a["exe_name"]
                 for a in cfg.get("allowed_apps", []) if a.get("exe_name")}
        allowed_exes = {e for e in names if e not in FORBIDDEN_STUDENT_PROCESSES}
        my_pid = os.getpid()

        old = getattr(self, "_tm_win", None)
        if old is not None and old.winfo_exists():
            old.deiconify()
            old.lift()
            return
        tm = tk.Toplevel(self.root)
        self._tm_win = tm
        tm.title("Task Manager - Phiên Bản Học Sinh")
        tm.geometry("760x470")
        tm.minsize(600, 360)
        tm.configure(bg="#111827")
        tm.attributes("-topmost", True)

        tk.Label(tm, text="🧰 ỨNG DỤNG ĐANG CHẠY", bg="#111827", fg="#38bdf8",
                 font=("Segoe UI", 11, "bold")).pack(pady=(12, 2))
        tk.Label(tm, text="Chọn 1 ứng dụng hoặc 1 cửa sổ rồi bấm Kết thúc. Nháy đúp cửa sổ để chuyển tới.",
                 bg="#111827", fg="#94a3b8", font=("Segoe UI", 9)).pack(pady=(0, 8))

        st = ttk.Style(tm)
        st.configure("TM.Treeview", rowheight=26, font=("Segoe UI", 10))
        st.configure("TM.Treeview.Heading", font=("Segoe UI", 9, "bold"))
        body = tk.Frame(tm, bg="#111827")
        body.pack(fill="both", expand=True, padx=16)
        tree = ttk.Treeview(body, columns=("info", "proc", "ram"), show="tree headings", style="TM.Treeview")
        tree.heading("#0", text="Tên")
        tree.heading("info", text="Trạng thái")
        tree.heading("proc", text="Tiến trình")
        tree.heading("ram", text="Bộ nhớ")
        tree.column("#0", width=380, anchor="w")
        tree.column("info", width=110, anchor="center")
        tree.column("proc", width=90, anchor="center")
        tree.column("ram", width=110, anchor="e")
        sb = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        lbl_sum = tk.Label(tm, text="", bg="#111827", fg="#94a3b8", font=("Segoe UI", 9))
        lbl_sum.pack(anchor="w", padx=18, pady=(6, 0))

        def du_lieu():
            """{exe: {"procs": [Process], "ram": byte, "wins": [(hwnd, title)]}} cho ứng dụng được phép đang chạy."""
            apps: dict = {}
            for p in psutil.process_iter(["pid", "name", "memory_info"]):
                try:
                    n = (p.info.get("name") or "").lower()
                    if p.info["pid"] == my_pid or n not in allowed_exes:
                        continue
                    a = apps.setdefault(n, {"procs": [], "ram": 0, "wins": []})
                    a["procs"].append(p)
                    a["ram"] += p.info["memory_info"].rss if p.info.get("memory_info") else 0
                except Exception:
                    continue
            try:
                for w in kiosk_guard.list_app_windows(allowed_exes):
                    exe = str(w.get("exe", "")).lower()
                    if exe in apps:
                        apps[exe]["wins"].append((int(w["hwnd"]), str(w.get("title", ""))))
            except Exception:
                pass
            return apps

        def mb(b: int) -> str:
            return f"{b / 1048576:,.1f} MB"

        def lam_moi() -> None:
            if not tm.winfo_exists():
                return
            sel = tree.selection()
            keep_sel = sel[0] if sel else ""
            opened = {iid for iid in tree.get_children() if tree.item(iid, "open")}
            first = not tree.get_children()
            tree.delete(*tree.get_children())
            apps = du_lieu()
            fg_apps = sorted((e for e in apps if apps[e]["wins"]), key=lambda e: names.get(e, e).lower())
            bg_apps = sorted((e for e in apps if not apps[e]["wins"]), key=lambda e: names.get(e, e).lower())
            for exe in fg_apps + bg_apps:
                a = apps[exe]
                iid = f"app:{exe}"
                n_win = len(a["wins"])
                tree.insert("", "end", iid=iid, text=f"  {names.get(exe, exe)}   ({exe})",
                            values=(f"{n_win} cửa sổ" if n_win else "chạy nền", len(a["procs"]), mb(a["ram"])),
                            open=(iid in opened) or (first and n_win > 1))
                for hwnd, title in a["wins"]:
                    tree.insert(iid, "end", iid=f"win:{hwnd}", text=f"    🗔  {title[:80]}", values=("", "", ""))
            if keep_sel and tree.exists(keep_sel):
                tree.selection_set(keep_sel)
            lbl_sum.config(text=f"{len(fg_apps)} ứng dụng đang mở · {len(bg_apps)} chạy nền · "
                                f"tổng {mb(sum(a['ram'] for a in apps.values()))}")

        def _post_close(hwnd: int) -> None:
            try:
                import ctypes

                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE: đóng lịch sự, ứng dụng hỏi lưu bài
            except Exception:
                pass

        def ket_thuc() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showwarning("Chưa chọn", "Chọn 1 ứng dụng hoặc 1 cửa sổ để kết thúc.", parent=tm)
                return
            iid = sel[0]
            if iid.startswith("win:"):
                _post_close(int(iid[4:]))
                tm.after(800, lam_moi)
                return
            exe = iid[4:]
            if exe not in allowed_exes:
                return
            a = du_lieu().get(exe)
            if not a:
                lam_moi()
                return
            if not messagebox.askyesno("Kết thúc ứng dụng",
                                       f"Đóng {names.get(exe, exe)} ({len(a['wins'])} cửa sổ, {len(a['procs'])} tiến trình)?\n"
                                       "Nhớ lưu bài trước khi đóng.", parent=tm):
                return
            for hwnd, _t in a["wins"]:
                _post_close(hwnd)
            procs = [p for p in a["procs"] if p.pid != my_pid]

            def _tat_han() -> None:     # 3 giây sau còn chạy (treo / không có cửa sổ) thì tắt hẳn
                try:
                    _g, alive = psutil.wait_procs(procs, timeout=3)
                    for p in alive:
                        try:
                            p.terminate()
                        except Exception:
                            pass
                except Exception:
                    pass

            threading.Thread(target=_tat_han, daemon=True).start()
            tm.after(3600, lam_moi)

        def chuyen_toi(_e=None) -> None:
            sel = tree.selection()
            if sel and sel[0].startswith("win:"):
                kiosk_guard.focus_window(int(sel[0][4:]))

        tree.bind("<Double-1>", chuyen_toi)
        tree.bind("<Delete>", lambda _e: ket_thuc())

        btn_bar = tk.Frame(tm, bg="#111827")
        btn_bar.pack(pady=12)
        for text, cmd, bg in (("🔄 Làm mới", lam_moi, "#334155"), ("↗ Chuyển tới cửa sổ", chuyen_toi, "#1d4ed8"),
                              ("✖ Kết thúc tác vụ", ket_thuc, "#e11d48")):
            tk.Button(btn_bar, text=text, command=cmd, bg=bg, fg="white", activebackground=bg, activeforeground="white",
                      font=("Segoe UI", 9, "bold"), padx=14, pady=6, relief="flat", cursor="hand2").pack(side="left", padx=6)

        def tu_lam_moi() -> None:
            if tm.winfo_exists():
                if not tree.focus() or tm.focus_get() is None:
                    lam_moi()
                tm.after(3000, tu_lam_moi)

        lam_moi()
        tm.after(3000, tu_lam_moi)

    # =====================================================================
    # KHUNG QUẢN TRỊ DÙNG CHUNG (PHỤ HUYNH / ADMIN): THANH TRÊN + MENU TRÁI + NỘI DUNG
    # =====================================================================
    def _khung_quan_tri(self, icon: str, title: str, subtitle: str, pages, actions, start: str = "") -> None:
        self.xoa_man_hinh()
        self._tat_kiosk()
        self.root.attributes("-fullscreen", False)
        self.root.attributes("-topmost", False)
        self.root.configure(bg=UI["bg"])
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self._can_giua_cua_so(min(1240, sw - 40), min(800, sh - 70))
        self.root.minsize(min(1040, sw - 60), min(620, sh - 90))
        self.tao_thanh_an_toan_day_man_hinh()

        top = tk.Frame(self.root, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
        top.pack(fill="x")
        tk.Label(top, text=icon, bg=UI["card"], font=("Segoe UI Emoji", 18)).pack(side="left", padx=(20, 8), pady=12)
        tt = tk.Frame(top, bg=UI["card"])
        tt.pack(side="left")
        tk.Label(tt, text=title, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(tt, text=subtitle, bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9)).pack(anchor="w")
        for txt, cmd, kind in reversed(actions):
            self._ui_button(top, txt, cmd, kind=kind).pack(side="right", padx=(0, 10), pady=12)

        body = tk.Frame(self.root, bg=UI["bg"])
        body.pack(fill="both", expand=True)
        nav = tk.Frame(body, bg=UI["card"], width=252, highlightbackground=UI["line"], highlightthickness=1)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)
        tk.Label(nav, text="MENU", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=20, pady=(16, 6))
        content = tk.Frame(body, bg=UI["bg"])
        content.pack(side="left", fill="both", expand=True)
        items: Dict[str, Any] = {}

        def show(key: str) -> None:
            for k, (row, bar, ic, lbl) in items.items():
                on = k == key
                bg = "#eff6ff" if on else UI["card"]
                for wdg in (row, ic, lbl):
                    wdg.configure(bg=bg)
                bar.configure(bg=UI["acc"] if on else UI["card"])
                lbl.configure(fg=UI["acc"] if on else UI["txt"])
            for wdg in content.winfo_children():
                wdg.destroy()
            page = self._vung_cuon(content)
            builder = next(b for k, _i, _l, b in pages if k == key)
            try:
                builder(page)
            except Exception as exc:
                safe_log(f"[UI] Lỗi dựng trang {key}: {exc}")
                tk.Label(page, text=f"Lỗi hiển thị trang: {exc}", bg=UI["bg"], fg=UI["bad"]).pack(anchor="w")

        for key, icon_txt, label, _builder in pages:
            row = tk.Frame(nav, bg=UI["card"], cursor="hand2")
            row.pack(fill="x", padx=8, pady=1)
            bar = tk.Frame(row, bg=UI["card"], width=4)
            bar.pack(side="left", fill="y")
            ic = tk.Label(row, text=icon_txt, bg=UI["card"], font=("Segoe UI Emoji", 13), width=2)
            ic.pack(side="left", padx=(12, 8), pady=9)
            lbl = tk.Label(row, text=label, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10, "bold"), anchor="w")
            lbl.pack(side="left", fill="x", expand=True)
            for wdg in (row, ic, lbl):
                wdg.bind("<Button-1>", lambda _e, k=key: show(k))
            items[key] = (row, bar, ic, lbl)
        show(start or pages[0][0])

    def _vung_cuon(self, parent) -> tk.Frame:
        """Vùng nội dung cuộn dọc bằng con lăn chuột (trang dài không bị cắt trên màn hình nhỏ)."""
        canvas = tk.Canvas(parent, bg=UI["bg"], highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        page = tk.Frame(canvas, bg=UI["bg"], padx=28, pady=22)
        win = canvas.create_window((0, 0), window=page, anchor="nw")
        page.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(e) -> None:
            # bind_all là ràng buộc TOÀN CỤC: trang này có thể đã bị xóa (chuyển sang chế độ học sinh) hoặc chuột
            # đang ở cửa sổ khác (Task Manager học sinh) -> chỉ cuộn khi trang còn tồn tại VÀ chuột nằm trên nó.
            try:
                if not (canvas.winfo_exists() and page.winfo_exists()):
                    return
                under = canvas.winfo_containing(e.x_root, e.y_root)
                if under is None or not str(under).startswith(str(canvas)):
                    return
                if page.winfo_height() > canvas.winfo_height():
                    canvas.yview_scroll(int(-e.delta / 120) if e.delta else (-1 if e.num == 4 else 1), "units")
            except tk.TclError:
                return
        canvas.bind_all("<MouseWheel>", wheel, add="+")
        canvas.bind_all("<Button-4>", wheel, add="+")
        canvas.bind_all("<Button-5>", wheel, add="+")
        return page

    def _page_header(self, page, title: str, desc: str = "") -> None:
        tk.Label(page, text=title, bg=UI["bg"], fg=UI["txt"], font=("Segoe UI", 17, "bold")).pack(anchor="w")
        if desc:
            tk.Label(page, text=desc, bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 10), justify="left",
                     wraplength=880).pack(anchor="w", pady=(2, 14))

    def _card(self, parent, title: str = "", side: Optional[str] = None, **pack):
        card = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1, padx=18, pady=14)
        if side:
            card.pack(side=side, fill="both", expand=True, **pack)
        else:
            card.pack(fill="x", **pack)
        if title:
            tk.Label(card, text=title, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0, 8))
        return card

    def _entry(self, parent, value: str = "", show: str = "") -> tk.Entry:
        wrap = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightcolor=UI["acc"], highlightthickness=1)
        wrap.pack(fill="x", pady=(2, 10))
        ent = tk.Entry(wrap, font=("Segoe UI", 10), bg=UI["card"], fg=UI["txt"], relief="flat", show=show,
                       highlightthickness=0, insertbackground=UI["txt"])
        ent.insert(0, value)
        ent.pack(fill="x", padx=8, ipady=6)
        return ent

    def _label(self, parent, text: str, bold: bool = True, mut: bool = False, size: int = 10) -> tk.Label:
        """Nhãn căn trái, tự xuống dòng theo bề rộng thật của khung chứa (không bị cắt chữ 2 đầu)."""
        lbl = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=UI["mut"] if mut else UI["txt"], justify="left",
                       anchor="w", font=("Segoe UI", size, "bold" if bold else "normal"), wraplength=300)
        lbl.pack(anchor="w", fill="x")
        lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(120, e.width - 4)))
        return lbl

    def _check(self, parent, text: str, value: bool) -> tk.BooleanVar:
        var = tk.BooleanVar(value=bool(value))
        tk.Checkbutton(parent, text=text, variable=var, bg=parent.cget("bg"), fg=UI["txt"], selectcolor=UI["card"],
                       activebackground=parent.cget("bg"), font=("Segoe UI", 10), anchor="w", justify="left",
                       wraplength=420, highlightthickness=0, bd=0).pack(anchor="w", pady=3)
        return var

    # =====================================================================
    # CẤP 2: CHẾ ĐỘ PHỤ HUYNH
    # =====================================================================
    def vao_che_do_phu_huynh(self, start: str = "") -> None:
        self._ket_thuc_ca_hoc("Phụ huynh đăng nhập")
        self._current_mode = "parent"
        self._hop_dt(False)
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        restore_windows_normal_state()
        update_runtime_state(current_role="parent", monitoring_active=False)
        threading.Thread(target=_dong_bo_dns, args=("parent",), daemon=True).start()   # chặn DNS chỉ khi học sinh dùng
        port = int(load_config().get("web_port", 5000))
        self._khung_quan_tri(
            "👪", "Bảng điều khiển Phụ huynh", "Cài đặt góc học tập, camera AI và xem báo cáo",
            [("phone", "📱", "Điện thoại phụ huynh", self._trang_ket_noi),
             ("apps", "🧩", "Ứng dụng & thư mục", self._trang_ung_dung),
             ("rules", "🛡️", "Luật chặn & giới hạn", self._trang_luat),
             ("camera", "📷", "Camera AI", self._trang_camera),
             ("stats", "📊", "Thống kê & vi phạm", self._trang_thong_ke),
             ("telegram", "💬", "Telegram (nâng cao)", self._trang_telegram)],
            [("🌐  Web nội bộ", lambda: webbrowser.open(f"http://127.0.0.1:{port}"), "ghost"),
             ("🔒  Giao máy cho học sinh", self.hien_man_hinh_dang_nhap, "primary")],
            start=start,
        )

    def _trang_ket_noi(self, page) -> None:
        self._page_header(page, "Điện thoại phụ huynh",
                          "Ghép máy này với web phụ huynh để xem tình hình học, nhận cảnh báo và điều khiển từ xa.")
        self._dung_tab_ket_noi_cloud(page)

    def _trang_ung_dung(self, page) -> None:
        from services.app_icons import emoji_for, get_icon_image

        cfg = load_config()
        self._page_header(page, "Ứng dụng & thư mục được phép",
                          "Học sinh chỉ thấy và mở được những mục dưới đây. Ứng dụng khác mở lên sẽ bị thu nhỏ ngay "
                          "và báo về phụ huynh.")
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="both", expand=True)
        c_apps = self._card(row, "🧩  Ứng dụng học tập", side="left", padx=(0, 8))
        c_dirs = self._card(row, "📁  Thư mục tài liệu", side="left", padx=(8, 0))
        apps_box = tk.Frame(c_apps, bg=UI["card"])
        apps_box.pack(fill="both", expand=True)
        dirs_box = tk.Frame(c_dirs, bg=UI["card"])
        dirs_box.pack(fill="both", expand=True)

        def dong_muc(parent, icon_img, emoji, title, sub, on_delete):
            r = tk.Frame(parent, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1)
            r.pack(fill="x", pady=3)
            if icon_img is not None:
                ic = tk.Label(r, image=icon_img, bg=UI["card"])
                ic.image = icon_img
            else:
                ic = tk.Label(r, text=emoji, bg=UI["card"], font=("Segoe UI Emoji", 16))
            ic.pack(side="left", padx=10, pady=6)
            t = tk.Frame(r, bg=UI["card"])
            t.pack(side="left", fill="x", expand=True)
            tk.Label(t, text=title, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10, "bold"), anchor="w").pack(anchor="w")
            tk.Label(t, text=sub if len(sub) < 60 else "…" + sub[-58:], bg=UI["card"], fg=UI["mut"],
                     font=("Segoe UI", 8), anchor="w").pack(anchor="w")
            tk.Button(r, text="✕", command=on_delete, bg=UI["card"], fg=UI["bad"], relief="flat", bd=0,
                      cursor="hand2", font=("Segoe UI", 11, "bold")).pack(side="right", padx=10)

        def ve_lai() -> None:
            for box in (apps_box, dirs_box):
                for wdg in box.winfo_children():
                    wdg.destroy()
            if not cfg.get("allowed_apps"):
                self._label(apps_box, "Chưa có ứng dụng nào.", bold=False, mut=True)
            for i, a in enumerate(cfg.get("allowed_apps", [])):
                dong_muc(apps_box, get_icon_image(a.get("path", ""), 28), emoji_for(a.get("exe_name", "")),
                         a.get("name", "?"), f"{a.get('exe_name', '')} · {a.get('path', '')}", lambda i=i: xoa_app(i))
            if not cfg.get("allowed_folders"):
                self._label(dirs_box, "Chưa có thư mục nào.", bold=False, mut=True)
            for i, fd in enumerate(cfg.get("allowed_folders", [])):
                dong_muc(dirs_box, None, "📁", os.path.basename(os.path.normpath(fd)) or fd, fd, lambda i=i: xoa_dir(i))

        def them_app() -> None:
            fpath = filedialog.askopenfilename(title="Chọn file ứng dụng (.exe) cho phép học sinh mở",
                                               filetypes=[("Ứng dụng Windows (*.exe)", "*.exe")], parent=self.root)
            if not fpath:
                return
            exe_name = os.path.basename(fpath)
            if exe_name.lower() in FORBIDDEN_STUDENT_PROCESSES:
                messagebox.showwarning("Tiến trình hệ thống", f"Không thể thêm '{exe_name}'.", parent=self.root)
                return
            name = simpledialog.askstring("Tên hiển thị", "Tên hiển thị trên màn hình học sinh:",
                                          initialvalue=os.path.splitext(exe_name)[0].capitalize(), parent=self.root)
            if not name:
                return
            cfg.setdefault("allowed_apps", []).append({"name": name.strip(), "exe_name": exe_name, "path": fpath})
            save_config(cfg)
            ve_lai()

        def xoa_app(i: int) -> None:
            a = cfg["allowed_apps"][i]
            if messagebox.askyesno("Xóa ứng dụng", f"Bỏ '{a.get('name')}' khỏi danh sách được phép?", parent=self.root):
                cfg["allowed_apps"].pop(i)
                save_config(cfg)
                ve_lai()

        def them_dir() -> None:
            d = filedialog.askdirectory(title="Chọn thư mục tài liệu cho phép", parent=self.root)
            if d and os.path.normpath(d) not in cfg.setdefault("allowed_folders", []):
                cfg["allowed_folders"].append(os.path.normpath(d))
                save_config(cfg)
                ve_lai()

        def xoa_dir(i: int) -> None:
            cfg["allowed_folders"].pop(i)
            save_config(cfg)
            ve_lai()

        abar = tk.Frame(c_apps, bg=UI["card"])
        abar.pack(anchor="w", pady=(10, 0))
        self._ui_button(abar, "🔍  Quét ứng dụng đã cài", lambda: self._hop_quet_ung_dung(cfg, ve_lai)).pack(side="left")
        self._ui_button(abar, "➕  Chọn file .exe", them_app, kind="ghost").pack(side="left", padx=8)
        self._ui_button(c_dirs, "➕  Thêm thư mục", them_dir).pack(anchor="w", pady=(10, 0))
        ve_lai()

    def _trang_luat(self, page) -> None:
        cfg = load_config()
        self._page_header(page, "Luật chặn & giới hạn",
                          "Nhập nhiều mục, cách nhau bằng dấu phẩy. Thay đổi trên web phụ huynh cũng tự đồng bộ về đây.")
        grid = tk.Frame(page, bg=UI["bg"])
        grid.pack(fill="both", expand=True)
        left = self._card(grid, "⛔  Chặn", side="left", padx=(0, 8))
        right = self._card(grid, "✅  Cho phép & giới hạn", side="left", padx=(8, 0))
        self._label(left, "Trang web bị chặn (vd: facebook.com, tiktok.com)")
        e_urls = self._entry(left, ", ".join(cfg.get("bad_urls", [])))
        self._label(left, "Từ khóa tiêu đề tab bị chặn (vd: game, phim)")
        e_tabs = self._entry(left, ", ".join(cfg.get("bad_tabs", [])))
        self._label(left, "Game / ứng dụng bị tự đóng (tên tiến trình, vd: roblox, steam)")
        e_apps = self._entry(left, ", ".join(cfg.get("bad_apps", [])))
        self._label(right, "Trang học tập luôn cho phép")
        e_good = self._entry(right, ", ".join(cfg.get("allowed_urls", [])))
        self._label(right, "Từ khóa bài giảng YouTube được miễn chặn")
        e_kw = self._entry(right, ", ".join(cfg.get("academic_keywords", [])))
        self._label(right, "Thời gian tối đa dùng ChatGPT / chatbot AI (phút mỗi ngày)")
        e_ai = self._entry(right, str(cfg.get("max_ai_minutes", 15)))

        from services.giam_sat_worker import DEFAULT_YT_KEYWORDS

        yt = self._card(page, "▶️  YouTube", pady=(12, 0))
        self._label(yt, "Bài giảng vẫn xem được. Chỉ đóng tab khi TỪ KHÓA TÌM KIẾM hoặc TÊN VIDEO chứa từ khóa dưới đây "
                        "(trừ khi có từ khóa bài giảng như toán, vật lý...).", bold=False, mut=True, size=9)
        v_ytm = tk.StringVar(value=cfg.get("youtube_mode", "keyword"))
        seg = tk.Frame(yt, bg=UI["card"])
        seg.pack(fill="x", pady=8)
        for val, txt in (("keyword", "🔎 Chặn theo từ khóa"), ("block", "⛔ Chặn toàn bộ"), ("allow", "✅ Cho phép hết")):
            tk.Radiobutton(seg, text=txt, value=val, variable=v_ytm, indicatoron=0, bg="#f1f5f9", fg=UI["txt"],
                           selectcolor="#dbeafe", relief="flat", bd=0, padx=10, pady=7, cursor="hand2",
                           font=("Segoe UI", 9, "bold")).pack(side="left", expand=True, fill="x", padx=2)
        self._label(yt, "Từ khóa cấm khi tìm / xem trên YouTube")
        e_ytk = self._entry(yt, ", ".join(cfg.get("youtube_keywords") or DEFAULT_YT_KEYWORDS))
        v_shorts = self._check(yt, "Chặn YouTube Shorts (video ngắn lướt liên tục)", cfg.get("youtube_block_shorts", True))

        def luu() -> None:
            split = lambda e: [x.strip() for x in e.get().split(",") if x.strip()]  # noqa: E731
            cfg["bad_urls"], cfg["bad_tabs"], cfg["bad_apps"] = split(e_urls), split(e_tabs), split(e_apps)
            cfg["allowed_urls"], cfg["academic_keywords"] = split(e_good), split(e_kw)
            cfg["youtube_mode"] = v_ytm.get()
            cfg["youtube_keywords"] = [x.lower() for x in split(e_ytk)]
            cfg["youtube_block_shorts"] = bool(v_shorts.get())
            try:
                cfg["max_ai_minutes"] = max(1, int(e_ai.get().strip()))
            except ValueError:
                cfg["max_ai_minutes"] = 15
            cfg["rules_ver"] = int(cfg.get("rules_ver", 0) or 0)
            save_config(cfg)
            messagebox.showinfo("Đã lưu", "Đã lưu luật chặn và giới hạn AI.", parent=self.root)

        self._ui_button(page, "💾  Lưu luật", luu).pack(anchor="e", pady=(12, 0))

    def _trang_camera(self, page) -> None:
        """Chọn camera theo TÊN + ảnh xem trước, tự nhận diện camera chính / mặt bàn; tùy chọn AI."""
        from services import camera_setup

        cfg = load_config()
        self._page_header(page, "Camera AI",
                          "Chọn camera bằng ảnh xem trước — không cần nhớ số thứ tự. Có 2 camera: bấm “Tự nhận diện”, "
                          "camera thấy khuôn mặt sẽ là camera chính, camera còn lại dùng soi mặt bàn (tay + vở).")
        cams = [c for c in camera_setup.list_cameras(cfg) if not c["virtual"]]
        cur_main = (cfg.get("camera_preferred_names") or [""])[0]
        cur_desk = cfg.get("desk_camera_name", "") if cfg.get("desk_camera_enabled") else ""
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="x")
        role_vars: Dict[str, tk.StringVar] = {}
        previews: Dict[str, Any] = {}
        if not cams:
            self._label(row, "Không tìm thấy webcam nào. Hãy cắm webcam rồi mở lại trang này.", bold=False, mut=True)
        for i, c in enumerate(cams[:3]):
            card = self._card(row, "", side="left", padx=(0 if i == 0 else 8, 0))
            tk.Label(card, text=f"📷  {c['name']}", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 11, "bold")).pack(anchor="w")
            pv_box = tk.Frame(card, bg="#e2e8f0", width=320, height=200)
            pv_box.pack(anchor="w", pady=8)
            pv_box.pack_propagate(False)
            pv = tk.Label(pv_box, text="Bấm “Xem thử” để chụp 1 ảnh", bg="#e2e8f0", fg=UI["mut"])
            pv.pack(fill="both", expand=True)
            st = tk.Label(card, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9))
            st.pack(anchor="w")
            default = "main" if c["name"] == cur_main or (not cur_main and i == 0) else ("desk" if c["name"] == cur_desk else "off")
            var = tk.StringVar(value=default)
            role_vars[c["name"]] = var
            seg = tk.Frame(card, bg=UI["card"])
            seg.pack(fill="x", pady=(6, 0))
            for val, txt in (("main", "🙂 Camera chính"), ("desk", "✍️ Mặt bàn"), ("off", "Không dùng")):
                tk.Radiobutton(seg, text=txt, value=val, variable=var, indicatoron=0, bg="#f1f5f9", fg=UI["txt"],
                               selectcolor="#dbeafe", relief="flat", bd=0, padx=8, pady=6, cursor="hand2",
                               font=("Segoe UI", 9, "bold")).pack(side="left", expand=True, fill="x", padx=2)
            previews[c["name"]] = (pv, st, c["index"])

        def show_result(name: str, img, info: Dict[str, Any]) -> None:
            pv, st, _idx = previews[name]
            if not pv.winfo_exists():
                return
            if img is not None:
                try:
                    from PIL import Image, ImageTk

                    import cv2 as _cv2

                    ph = ImageTk.PhotoImage(Image.fromarray(_cv2.cvtColor(img, _cv2.COLOR_BGR2RGB)))
                    pv.config(image=ph, text="")
                    pv.image = ph
                except Exception:
                    pv.config(text="(không hiển thị được ảnh)")
            if info.get("ok"):
                st.config(text=(f"✅ Thấy {info['faces']} khuôn mặt" if info.get("faces") else "Không thấy khuôn mặt (hợp làm camera mặt bàn)")
                          + (" · ⚠️ Ảnh quá tối / nắp che?" if info.get("dark") else ""),
                          fg=UI["ok"] if info.get("faces") else UI["mut"])
            else:
                st.config(text="❌ " + str(info.get("error", "Không mở được")), fg=UI["bad"])

        def chay_nen(fn) -> None:
            lbl_busy.config(text="⏳ Đang mở camera…")

            def work():
                try:
                    out = fn()
                except Exception as exc:
                    out = exc
                self._goi_ui(lambda: xong(out))

            def xong(out):
                if lbl_busy.winfo_exists():
                    lbl_busy.config(text="" if not isinstance(out, Exception) else f"Lỗi: {out}")

            threading.Thread(target=work, daemon=True).start()

        def xem_thu() -> None:
            def fn():
                for name, (_pv, _st, idx) in previews.items():
                    img, info = camera_setup.grab_preview(idx)
                    self._goi_ui(lambda n=name, im=img, inf=info: show_result(n, im, inf))
            chay_nen(fn)

        def tu_nhan_dien() -> None:
            def fn():
                res = camera_setup.auto_assign(cfg)
                for r in res["results"]:
                    if r["name"] in previews:
                        self._goi_ui(lambda r=r: show_result(r["name"], r.get("image"), r))

                def gan():
                    for name, var in role_vars.items():
                        var.set("main" if name == res["main"] else ("desk" if name == res["desk"] else "off"))
                self._goi_ui(gan)
            chay_nen(fn)

        bar = tk.Frame(page, bg=UI["bg"])
        bar.pack(fill="x", pady=10)
        self._ui_button(bar, "✨  Tự nhận diện camera", tu_nhan_dien).pack(side="left")
        self._ui_button(bar, "👁  Xem thử", xem_thu, kind="ghost").pack(side="left", padx=8)
        lbl_busy = tk.Label(bar, text="", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 9))
        lbl_busy.pack(side="left", padx=8)

        opts = tk.Frame(page, bg=UI["bg"])
        opts.pack(fill="both", expand=True)
        c_ai = self._card(opts, "🤖  Tùy chọn nhận diện", side="left", padx=(0, 8))
        v_ai = self._check(c_ai, "Bật Camera AI khi học sinh vào học", cfg.get("enable_posture_ai", True))
        v_obj = self._check(c_ai, "Nhận diện đồ vật: điện thoại, tay cầm game, đồ chơi (sách/vở được miễn)",
                            cfg.get("posture_object_detection", True))
        v_photo = self._check(c_ai, "Tự gửi 1 ảnh bằng chứng qua Telegram khi học sinh cầm điện thoại/đồ chơi "
                                    "(tối đa 1 ảnh / 5 phút)", cfg.get("auto_photo_on_object", True))
        v_act = self._check(c_ai, "Nhận diện hành động bằng mô hình MoViNet (nhắn tin, chơi game, ăn, ngáp...)",
                            cfg.get("posture_action_recognition", True))
        v_draw = self._check(c_ai, "Vẽ điểm mốc AI trên màn hình giám sát nội bộ", cfg.get("posture_draw_landmarks", True))
        from services.posture_ai_engine import SCAN_MODES

        self._label(c_ai, "Chế độ quét", size=9)
        mode_labels = {v["label"]: k for k, v in SCAN_MODES.items()}
        v_mode = tk.StringVar(value=SCAN_MODES.get(cfg.get("ai_scan_mode", "standard"), SCAN_MODES["standard"])["label"])
        ttk.Combobox(c_ai, textvariable=v_mode, values=list(mode_labels), state="readonly", width=58).pack(anchor="w", pady=(2, 8))
        self._label(c_ai, "Phút tối đa cho mỗi lần đi vệ sinh (quá giờ sẽ báo phụ huynh)", size=9)
        e_wc = self._entry(c_ai, str(cfg.get("toilet_max_minutes", 10)))
        self._label(c_ai, "Loại ống kính camera chính", size=9)
        fov_opts = {"Webcam thường (~70°)": 70.0, "Góc rộng (~90°)": 90.0, "Siêu rộng (~120°)": 120.0}
        cur_fov = float(cfg.get("camera_hfov_deg", 70.0))
        v_fov = tk.StringVar(value=min(fov_opts, key=lambda k: abs(fov_opts[k] - cur_fov)))
        ttk.Combobox(c_ai, textvariable=v_fov, values=list(fov_opts), state="readonly", width=28).pack(anchor="w", pady=(2, 8))
        self._label(c_ai, "Độ phân giải", size=9)
        res_opts = {"HD 1280×720 (khuyên dùng)": (1280, 720), "Full HD 1920×1080 (góc rộng, máy mạnh)": (1920, 1080)}
        v_res = tk.StringVar(value=next((k for k, v in res_opts.items() if v[0] == int(cfg.get("camera_width", 1280))),
                                        list(res_opts)[0]))
        ttk.Combobox(c_ai, textvariable=v_res, values=list(res_opts), state="readonly", width=34).pack(anchor="w", pady=(2, 0))

        c_tip = self._card(opts, "📐  Đặt camera chuẩn", side="left", padx=(8, 0))
        tk.Label(c_tip, bg=UI["card"], fg=UI["txt"], justify="left", font=("Segoe UI", 10), wraplength=360, text=(
            "Camera chính (trên màn hình):\n"
            "  • Ngang tầm mắt, chúc xuống 10–15° để thấy cả mặt, 2 vai và mép bàn.\n"
            "  • Học sinh ngồi cách 40–70 cm (tối thiểu 30 cm), ở giữa khung hình.\n"
            "  • Webcam góc rộng 90–120° thấy được tay khi viết.\n\n"
            "Camera mặt bàn (tùy chọn):\n"
            "  • Kẹp lên đèn bàn / giá đỡ, cao 40–60 cm, chúc thẳng xuống vở.\n"
            "  • AI dùng camera này để biết đang viết hay đang cầm đồ chơi.\n\n"
            "Sau khi đổi vị trí camera: vào máy học sinh, AI tự hiệu chỉnh lại trong 5 giây.")).pack(anchor="w")

        def luu() -> None:
            main = next((n for n, v in role_vars.items() if v.get() == "main"), None)
            desk = next((n for n, v in role_vars.items() if v.get() == "desk"), None)
            if role_vars and not main:
                messagebox.showwarning("Thiếu camera chính", "Hãy chọn 1 camera làm “Camera chính”.", parent=self.root)
                return
            camera_setup.apply_assignment(cfg, main, desk)
            cfg["enable_posture_ai"] = bool(v_ai.get())
            cfg["posture_object_detection"] = bool(v_obj.get())
            cfg["auto_photo_on_object"] = bool(v_photo.get())
            cfg["posture_draw_landmarks"] = bool(v_draw.get())
            cfg["posture_action_recognition"] = bool(v_act.get())
            cfg["ai_scan_mode"] = mode_labels.get(v_mode.get(), "standard")
            try:
                cfg["toilet_max_minutes"] = max(1, int(e_wc.get().strip()))
            except ValueError:
                cfg["toilet_max_minutes"] = 10
            cfg["camera_hfov_deg"] = fov_opts.get(v_fov.get(), 70.0)
            cfg["camera_width"], cfg["camera_height"] = res_opts.get(v_res.get(), (1280, 720))
            save_config(cfg)
            messagebox.showinfo("Đã lưu", f"Camera chính: {main or '—'}\nCamera mặt bàn: {desk or 'không dùng'}",
                                parent=self.root)

        self._ui_button(page, "💾  Lưu cài đặt camera", luu).pack(anchor="e", pady=(12, 0))

    def _trang_thong_ke(self, page) -> None:
        self._page_header(page, "Thống kê & vi phạm", "Số liệu phiên học trên máy này.")
        state = load_runtime_state()
        cfg = load_config()
        pt = state.get("posture_telemetry") or {}
        alerts = state.get("alerts", [])
        kpi = tk.Frame(page, bg=UI["bg"])
        kpi.pack(fill="x")
        ai_min = round(int(state.get("ai_seconds", 0)) / 60.0, 1)
        app_u = state.get("app_usage", {}) or {}
        for i, (lbl, val, sub) in enumerate((
                ("🤖 Dùng chatbot AI", f"{ai_min} phút", f"giới hạn {cfg.get('max_ai_minutes', 15)} phút"),
                ("🧩 Ứng dụng đã mở", str(len(app_u)), "trong phiên"),
                ("🎯 Mức tập trung", f"{pt.get('cfi_score', '—')}%", pt.get("activity_state_vi") or "Camera AI"),
                ("🔔 Cảnh báo", str(len(alerts)), "gần nhất"))):
            c = self._card(kpi, "", side="left", padx=(0 if i == 0 else 8, 0))
            tk.Label(c, text=lbl, bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
            tk.Label(c, text=val, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 20, "bold")).pack(anchor="w")
            tk.Label(c, text=sub, bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9)).pack(anchor="w")
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="both", expand=True, pady=(12, 0))
        c1 = self._card(row, "⏱️  Thời gian dùng ứng dụng", side="left", padx=(0, 8))
        for k, sec in sorted(app_u.items(), key=lambda kv: -kv[1])[:10]:
            r = tk.Frame(c1, bg=UI["card"])
            r.pack(fill="x", pady=2)
            tk.Label(r, text=k[:40], bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 10)).pack(side="left")
            tk.Label(r, text=f"{round(sec / 60.0, 1)} phút", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10)).pack(side="right")
        if not app_u:
            self._label(c1, "Chưa có dữ liệu.", bold=False, mut=True)
        c2 = self._card(row, "🔔  Cảnh báo gần đây", side="left", padx=(8, 0))
        box = tk.Text(c2, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 9), relief="flat", height=14, wrap="word")
        box.pack(fill="both", expand=True)
        for a in alerts[:40]:
            box.insert("end", f"{a.get('time', '')}   {a.get('message', '')}\n")
        if not alerts:
            box.insert("end", "✅ Chưa ghi nhận vi phạm nào.")
        box.config(state="disabled")

        def dat_lai() -> None:
            if messagebox.askyesno("Đặt lại", "Xóa bộ đếm thời gian và lịch sử cảnh báo?", parent=self.root):
                self.bo_giam_sat.app_usage = {}
                self.bo_giam_sat.ai_seconds = 0
                update_runtime_state(alerts=[], app_usage={}, ai_seconds=0, ai_reset_at=time.time())
                self.vao_che_do_phu_huynh(start="stats")

        self._ui_button(page, "🧹  Đặt lại bộ đếm & cảnh báo", dat_lai, kind="danger").pack(anchor="e", pady=(12, 0))

    def _trang_telegram(self, page) -> None:
        cfg = load_config()
        tg = cfg.setdefault("telegram", {})
        self._page_header(page, "Telegram (nâng cao)",
                          "Thường không cần: web phụ huynh đã có nút “Kết nối Telegram” dùng bot chung. "
                          "Chỉ điền ở đây nếu muốn máy gửi thẳng qua bot riêng của gia đình.")
        c = self._card(page, "🤖  Bot riêng")
        self._label(c, "Bot Token (từ @BotFather)")
        e_tok = self._entry(c, tg.get("bot_token", ""), show="•")
        self._label(c, "Chat ID của phụ huynh")
        e_chat = self._entry(c, tg.get("chat_id", ""))
        self._label(c, "Giãn cách chống spam giữa 2 cảnh báo cùng loại (giây)")
        e_cd = self._entry(c, str(tg.get("cooldown_seconds", 30)))
        v_en = self._check(c, "Gửi cảnh báo tự động", tg.get("enable_alerts", True))
        v_gm = self._check(c, "Báo khi mở web/ứng dụng game", tg.get("alert_on_game", True))
        v_wl = self._check(c, "Báo khi đăng nhập Phụ huynh/Admin sai nhiều lần", tg.get("alert_on_wrong_login", True))
        v_ai = self._check(c, "Báo khi dùng chatbot AI quá giờ", tg.get("alert_on_ai_overuse", True))

        def luu(test: bool) -> None:
            try:
                cd = max(5, int(e_cd.get().strip() or 30))
            except ValueError:
                cd = 30
            tg.update({"bot_token": e_tok.get().strip(), "chat_id": e_chat.get().strip(), "cooldown_seconds": cd,
                       "enable_alerts": bool(v_en.get()), "alert_on_game": bool(v_gm.get()),
                       "alert_on_wrong_login": bool(v_wl.get()), "alert_on_ai_overuse": bool(v_ai.get())})
            save_config(cfg)
            if test:
                from services.telegram_service import trigger_parent_alert

                ok = trigger_parent_alert("SYSTEM_TEST", "Kết nối từ FocusDesk thành công!", sync=True)
                (messagebox.showinfo if ok else messagebox.showwarning)(
                    "Telegram", "📨 Đã gửi tin thử!" if ok else "Đã lưu, nhưng chưa gửi được — kiểm tra Token / Chat ID.",
                    parent=self.root)
            else:
                messagebox.showinfo("Đã lưu", "Đã lưu cấu hình Telegram.", parent=self.root)

        bar = tk.Frame(page, bg=UI["bg"])
        bar.pack(fill="x", pady=(12, 0))
        self._ui_button(bar, "📨  Lưu & gửi thử", lambda: luu(True), kind="ghost").pack(side="right")
        self._ui_button(bar, "💾  Lưu", lambda: luu(False)).pack(side="right", padx=8)

    def _dung_tab_ket_noi_cloud(self, tab) -> None:
        """Mã ghép máy + trạng thái kết nối web phụ huynh / Telegram, cập nhật mỗi 3 giây."""
        card = tk.Frame(tab, bg=UI["card"], highlightbackground=UI["line"], highlightthickness=1, padx=26, pady=20)
        card.pack(fill="x")
        lbl_status = tk.Label(card, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10, "bold"))
        lbl_status.pack(anchor="w")
        tk.Label(card, text="MÃ GHÉP MÁY", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(14, 0))
        lbl_code = tk.Label(card, text="······", bg=UI["card"], fg=UI["acc"], font=("Consolas", 44, "bold"))
        lbl_code.pack(anchor="w")
        lbl_exp = tk.Label(card, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 10))
        lbl_exp.pack(anchor="w", pady=(0, 12))
        row = tk.Frame(card, bg=UI["card"])
        row.pack(anchor="w")

        def ma_moi() -> None:
            update_runtime_state(cloud_pair_refresh=True)
            lbl_exp.config(text="Đang xin mã mới từ server…")

        self._ui_button(row, "🔄  Tạo mã ghép mới", ma_moi).pack(side="left", padx=(0, 8))
        self._ui_button(row, "🌐  Mở web phụ huynh", lambda: webbrowser.open(
            (load_runtime_state().get("cloud") or {}).get("url") or "https://banhocthongminh.vercel.app"),
            kind="ghost").pack(side="left")

        steps = tk.Frame(tab, bg="#eff6ff", highlightbackground="#bfdbfe", highlightthickness=1, padx=20, pady=14)
        steps.pack(fill="x", pady=14)
        tk.Label(steps, text="Cách ghép", bg="#eff6ff", fg="#1e3a8a", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        for i, line in enumerate((
                "Trên điện thoại, mở web phụ huynh và đăng nhập bằng Google.",
                "Bấm “➕ Thêm máy học sinh” rồi nhập mã 6 số ở trên (mã đổi sau 15 phút).",
                "Bấm “Kết nối Telegram” để nhận cảnh báo; gõ /status /lock /anh ngay trong Telegram.",
                "Android: menu Chrome → “Thêm vào màn hình chính” để dùng như một ứng dụng."), 1):
            r = tk.Frame(steps, bg="#eff6ff")
            r.pack(anchor="w", pady=3)
            tk.Label(r, text=str(i), bg=UI["acc"], fg="white", font=("Segoe UI", 9, "bold"), width=2).pack(side="left")
            tk.Label(r, text="  " + line, bg="#eff6ff", fg="#1e293b", font=("Segoe UI", 10)).pack(side="left")

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
                                  + f"   ·   đồng bộ {ago}s trước   ·   mã máy {c.get('device_id', '')}",
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
    # CẤP 1: CHẾ ĐỘ ADMIN
    # =====================================================================
    def vao_che_do_admin(self, start: str = "") -> None:
        self._ket_thuc_ca_hoc("Admin đăng nhập")
        self._current_mode = "admin"
        self._hop_dt(False)
        self.bo_giam_sat.dung_lai()
        posture_manager.stop()
        restore_windows_normal_state()
        update_runtime_state(current_role="admin", monitoring_active=False)
        threading.Thread(target=_dong_bo_dns, args=("admin",), daemon=True).start()   # chặn DNS chỉ khi học sinh dùng
        self._khung_quan_tri(
            "🛠️", "Quản trị hệ thống", "Quyền cao nhất: bảo mật Windows, camera, mật khẩu, thoát phần mềm",
            [("system", "🛡️", "Hệ thống & kiosk", self._trang_he_thong),
             ("camera", "📷", "Camera AI", self._trang_camera),
             ("passwords", "🔑", "Mật khẩu", self._trang_mat_khau),
             ("rescue", "🆘", "Cứu hộ", self._trang_cuu_ho),
             ("phonebox", "📦", "Hộp điện thoại", self._trang_hop_dien_thoai),
             ("exit", "🚪", "Thoát phần mềm", self._trang_thoat)],
            [("👪  Sang bảng Phụ huynh", self.vao_che_do_phu_huynh, "ghost"),
             ("🔒  Giao máy cho học sinh", self.hien_man_hinh_dang_nhap, "primary")],
            start=start,
        )

    def _trang_hop_dien_thoai(self, page) -> None:
        """Admin: hộp khóa điện thoại ESP32-S3 qua cổng USB/COM — bật, chọn cổng, ghép, thử lệnh."""
        from services import phone_box

        cfg = load_config()
        self._page_header(page, "Mô-đun ESP32: tủ điện thoại & tưới cây",
                          "Cắm vào máy bằng cáp USB. Tủ v2: cất điện thoại, cắm sạc, đóng cửa (công tắc hành trình) → bắt "
                          "đầu ca học; hết giờ & đạt chuẩn → tưới cây. Hộp khóa v1 (giai đoạn 1) vẫn dùng được.")
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="x")
        c1 = self._card(row, "⚙️  Cài đặt", side="left", padx=(0, 8))
        v_en = self._check(c1, "Bật mô-đun ESP32 (tủ / hộp điện thoại)", cfg.get("phone_box_enabled", False))
        v_req = self._check(c1, "Bắt buộc cất điện thoại vào hộp mới được vào học", cfg.get("phone_box_required", False))
        v_auto = self._check(c1, "Tự khóa hộp khi học sinh đang học và đã cất điện thoại", cfg.get("phone_box_auto_lock", True))
        self._label(c1, "Cổng kết nối", size=9)
        ports = ["auto"] + [p["port"] for p in phone_box.find_box_ports()]
        cur = str(cfg.get("esp32_com_port", "auto") or "auto")
        v_port = tk.StringVar(value=cur if cur in ports else "auto")
        ttk.Combobox(c1, textvariable=v_port, values=ports, state="readonly", width=20).pack(anchor="w", pady=(2, 6))
        tk.Label(c1, text="“auto” = tự tìm cổng USB của ESP32 (bỏ qua cổng COM Bluetooth).", bg=UI["card"],
                 fg=UI["mut"], font=("Segoe UI", 9)).pack(anchor="w")
        self._label(c1, "Tốc độ (baud) — phải trùng Serial.begin() trong firmware", size=9)
        v_baud = tk.StringVar(value=str(cfg.get("esp32_baud", phone_box.BAUD)))
        ttk.Combobox(c1, textvariable=v_baud, values=[str(b) for b in SERIAL_BAUDS], width=20).pack(anchor="w", pady=(2, 6))

        c2 = self._card(row, "📡  Trạng thái", side="left", padx=(8, 0))
        lbl = tk.Label(c2, text="—", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 11), justify="left", anchor="w")
        lbl.pack(anchor="w", fill="x")
        out = tk.Label(c2, text="", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9), justify="left", anchor="w")
        out.pack(anchor="w", fill="x", pady=(6, 0))

        def chay(fn, ten) -> None:
            def work():
                try:
                    r = fn()
                except Exception as exc:
                    r = {"ok": False, "err": str(exc)}
                self._goi_ui(lambda: out.winfo_exists() and out.config(
                    text=f"{ten}: " + ("✅ thành công" if r.get("ok") else f"❌ {r.get('err')}"),
                    fg=UI["ok"] if r.get("ok") else UI["bad"]))
            threading.Thread(target=work, daemon=True).start()

        btns = tk.Frame(c2, bg=UI["card"])
        btns.pack(anchor="w", pady=(10, 0))
        box = phone_box.get_box()
        self._ui_button(btns, "🔔 Kêu thử", lambda: chay(box.beep, "Kêu thử"), kind="ghost").pack(side="left")
        self._ui_button(btns, "🔒 Khóa (v1)", lambda: chay(box.lock, "Khóa"), kind="ghost").pack(side="left", padx=6)
        self._ui_button(btns, "🔓 Mở (v1)", lambda: chay(lambda: box.unlock(0), "Mở khóa"), kind="ghost").pack(side="left")
        self._ui_button(c2, "🔗  Ghép hộp v1 (giữ nút trên hộp 5 giây trước)", lambda: chay(box.pair, "Ghép hộp"),
                        kind="ghost").pack(anchor="w", pady=(8, 0))

        # ---- Tủ v2: đủ mọi lệnh của giao thức để thử nghiệm (firmware/GIAO_THUC.md) ----
        t2 = self._card(page, "🧪  Tủ điện thoại v2 — bảng lệnh thử nghiệm", pady=(12, 0))
        self._label(t2, "Mỗi nút gửi 1 lệnh đúng giao thức v2 và hiện kết quả ack. Mở Serial monitor để xem từng dòng "
                        "TX/RX. Thời gian ca & ngưỡng CFI bình thường do phụ huynh đặt trên web (thẻ 🏆 Thưởng).",
                    bold=False, size=9)
        bc = phone_box.box_cfg()
        self._label(t2, f"🌐 Đang dùng cài đặt của WEB PHỤ HUYNH: ca {bc['session_min']:g} phút · đèn đỏ khi CFI < "
                        f"{bc['led_cfi']} · còi khi CFI < {bc['buzz_cfi']} (đổi trên web → thẻ 🏆 Thưởng → Tiêu chí).",
                    bold=True, size=9)
        f_cfg = tk.Frame(t2, bg=UI["card"])
        f_cfg.pack(anchor="w", fill="x", pady=(6, 0))

        def o_so(parent, nhan, gia_tri, rong=6):
            tk.Label(parent, text=nhan, bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 9)).pack(side="left")
            v = tk.StringVar(value=str(gia_tri))
            tk.Entry(parent, textvariable=v, width=rong, font=("Segoe UI", 10), relief="flat", highlightthickness=1,
                     highlightbackground=UI["line"]).pack(side="left", padx=(4, 12), ipady=3)
            return v

        def so(v, mac_dinh: float) -> float:
            try:
                return float(str(v.get()).replace(",", ".").strip())
            except ValueError:
                return mac_dinh

        v_min = o_so(f_cfg, "Thời gian ca (phút)", f"{bc['session_min']:g}")
        v_led = o_so(f_cfg, "Đèn đỏ khi CFI <", bc["led_cfi"])
        v_buzz = o_so(f_cfg, "Còi khi CFI <", bc["buzz_cfi"])

        def gui_cfg() -> Dict[str, Any]:
            # CHỈ GỬI THỬ xuống tủ, KHÔNG lưu: cài đặt thật là của web phụ huynh (tủ nối lại / web đổi -> về giá trị web)
            return box.set_cfg(max(1.0, min(240.0, so(v_min, 25))), int(max(0, min(100, so(v_led, 50)))),
                               int(max(0, min(100, so(v_buzz, 30)))))

        self._ui_button(f_cfg, "⏱️ Gửi thử (tạm thời)", lambda: chay(gui_cfg, "cfg thử — lần kết nối sau tủ về cài đặt web"),
                        kind="ghost").pack(side="left")
        self._ui_button(f_cfg, "🌐 Về cài đặt web", lambda: chay(box.push_cfg, "Áp lại cài đặt web"),
                        kind="ghost").pack(side="left", padx=6)

        f_cfi = tk.Frame(t2, bg=UI["card"])
        f_cfi.pack(anchor="w", fill="x", pady=(8, 0))
        v_cfi = o_so(f_cfi, "CFI thử (0–100)", 40)
        v_giay = o_so(f_cfi, "giữ trong (giây)", 60)

        def gui_cfi() -> Dict[str, Any]:
            phone_box.set_test_cfi(int(so(v_cfi, 40)), int(so(v_giay, 60)))
            pha = (load_runtime_state().get("phone_box") or {}).get("phase")
            if pha != "active":   # đèn / còi theo CFI chỉ chạy TRONG CA — nhắc để khỏi tưởng hỏng
                return {"ok": False, "err": f"đã gửi CFI, nhưng tủ đang ở pha “{pha or '?'}” — đèn/còi theo CFI chỉ "
                                            "chạy TRONG CA: bấm ▶️ Bắt đầu ca thử, hoặc mở rồi đóng lại cửa tủ"}
            return {"ok": True}

        self._ui_button(f_cfi, "🎯 Giả lập CFI", lambda: chay(gui_cfi, "CFI thử (gửi kèm ping 2 giây/lần)"),
                        kind="ghost").pack(side="left")
        self._ui_button(f_cfi, "↩️ Dùng CFI camera", lambda: chay(lambda: (phone_box.set_test_cfi(None), {"ok": True})[1],
                                                                  "Bỏ CFI thử"), kind="ghost").pack(side="left", padx=6)

        f_test = tk.Frame(t2, bg=UI["card"])
        f_test.pack(anchor="w", fill="x", pady=(8, 0))
        v_test = o_so(f_test, "Ca thử (phút)", 1)
        self._ui_button(f_test, "▶️ Bắt đầu ca thử", lambda: chay(lambda: box.start_test(so(v_test, 1)),
                                                                "Ca thử (bỏ qua cửa/sạc/điện thoại, không chấm điểm)"),
                        kind="ghost").pack(side="left")
        self._ui_button(f_test, "💡 Thử đèn & còi", lambda: chay(box.test_io, "Thử đèn đỏ → xanh → còi"),
                        kind="ghost").pack(side="left", padx=6)
        self._ui_button(f_test, "🔌 Giả lập sạc BẬT", lambda: chay(lambda: box.sim_charge(True), "Giả lập cắm sạc"),
                        kind="ghost").pack(side="left")
        self._ui_button(f_test, "TẮT", lambda: chay(lambda: box.sim_charge(False), "Bỏ giả lập sạc"),
                        kind="ghost").pack(side="left", padx=(4, 0))

        f_rl = tk.Frame(t2, bg=UI["card"])
        f_rl.pack(anchor="w", fill="x", pady=(8, 0))
        tk.Label(f_rl, text="Rơ-le bơm:", bg=UI["card"], fg=UI["txt"], font=("Segoe UI", 9)).pack(side="left")
        for ma, nhan in (("low", "Kích mức THẤP (đa số module)"), ("low_od", "THẤP + thả nổi khi tắt"),
                         ("high", "Kích mức CAO")):
            self._ui_button(f_rl, nhan, lambda m=ma: chay(lambda: box.set_relay(m), f"Rơ-le kiểu {m} (bơm phải TẮT ngay)"),
                            kind="ghost").pack(side="left", padx=(6, 0))
        f_rp = tk.Frame(t2, bg=UI["card"])
        f_rp.pack(anchor="w", fill="x", pady=(6, 0))
        v_rpin = o_so(f_rp, "Chân GPIO nối IN rơ-le", 4, rong=4)
        self._ui_button(f_rp, "📌 Đặt chân rơ-le", lambda: chay(lambda: box.set_relay_pin(int(so(v_rpin, 4))),
                                                                 "Đổi chân rơ-le"), kind="ghost").pack(side="left")
        self._label(t2, "Cắm vào bơm chạy luôn / water không đổi gì → thử lần lượt 3 kiểu: kiểu đúng là kiểu mà bơm TẮT khi "
                        "chờ và chạy đúng 2 giây khi bấm water.", bold=False, size=9)

        f_cmd = tk.Frame(t2, bg=UI["card"])
        f_cmd.pack(anchor="w", fill="x", pady=(8, 0))
        v_ms = o_so(f_cmd, "Bơm (ms)", 2500)
        self._ui_button(f_cmd, "🌱 water", lambda: chay(lambda: box.water(int(so(v_ms, 2500))), "Tưới cây"),
                        kind="ghost").pack(side="left")
        self._ui_button(f_cmd, "🔔 beep", lambda: chay(lambda: box.command("beep", ms=600), "beep"),
                        kind="ghost").pack(side="left", padx=6)
        self._ui_button(f_cmd, "⏹️ end (kết thúc ca)", lambda: chay(lambda: box.end_session("admin"), "Kết thúc ca"),
                        kind="ghost").pack(side="left")
        def ghep_dt() -> None:
            from tkinter import simpledialog

            ma = simpledialog.askstring("📱 Ghép điện thoại mới",
                                        "Nhập MÃ GHÉP 6 SỐ hiện trên app điện thoại\n(để trống = nhận điện thoại đầu tiên "
                                        "kết nối).\nKhóa cũ bị xóa; trong 2 phút bấm “Liên kết lại tủ” trên app.",
                                        parent=self.root)
            if ma is None:
                return
            ma = "".join(ch for ch in ma if ch.isdigit())
            if ma and len(ma) != 6:
                messagebox.showwarning("Mã ghép", "Mã ghép phải đủ 6 chữ số.", parent=self.root)
                return
            chay(lambda: box.reset_key(expect=ma), f"Ghép điện thoại {'mã ' + ma if ma else 'mới'}")

        self._ui_button(f_cmd, "📱 Ghép điện thoại (reset_key)", ghep_dt, kind="ghost").pack(side="left", padx=6)
        self._ui_button(f_cmd, "📡 get", lambda: chay(lambda: (box.send_text('{"t":"get"}'), {"ok": True})[1],
                                                     "get (xem RX ở Serial monitor)"), kind="ghost").pack(side="left")

        def refresh() -> None:
            if not lbl.winfo_exists():
                return
            st = load_runtime_state().get("phone_box") or {}
            if not load_config().get("phone_box_enabled"):
                lbl.config(text="⚪ Chưa bật", fg=UI["mut"])
            elif not st.get("connected"):
                lbl.config(text="🔌 Chưa thấy hộp — kiểm tra cáp USB / cổng", fg=UI["warn"])
            elif int(st.get("proto", 1) or 1) >= 2:
                pha = {"active": f"{'🧪 CA THỬ' if st.get('test') else '📚 ĐANG TRONG CA'} — còn {int(st.get('left_s') or 0) // 60}:{int(st.get('left_s') or 0) % 60:02d}"
                                 f" · vi phạm {st.get('viol', 0)}",
                       "done": "✅ Hết giờ học (mở tủ / rút sạc không báo) — MỞ rồi ĐÓNG lại cửa để bắt đầu ca mới"
                       }.get(st.get("phase"), "⏸️ Chờ: đóng cửa + cắm sạc + điện thoại xác thực")
                cfi = phone_box.current_cfi()
                pl = int(st.get("pair_left") or 0)
                cho_ghep = (f" · ⏳ chờ điện thoại ghép{(' mã ' + st['pair_expect']) if st.get('pair_expect') else ''}"
                            f" còn {pl} s") if pl else ""
                if st.get("ble") and st.get("phone") and st.get("paired") and not st.get("verified") \
                        and st.get("phase") == "idle":
                    cho_ghep += "\n⚠️ Điện thoại kết nối nhưng CHƯA xác thực (khóa khác khóa tủ đã lưu) → bấm 📱 Ghép điện thoại"
                lbl.config(fg=UI["txt"], text=(
                    f"🟢 Tủ v2 {st.get('box_id', '')} ở {st.get('port', '')} (fw {st.get('fw', '?')})\n{pha}\n"
                    f"🚪 cửa {'đóng' if st.get('door') == 'closed' else 'MỞ'} · "
                    f"{('🔌 đang sạc' + (' (giả lập)' if st.get('sim_charge') else '')) if st.get('phone') else '📭 chưa sạc'} · BLE {'✓' if st.get('ble') else '✗'} · "
                    f"{'🔑 đã xác thực' if st.get('verified') else ('🔑 đã ghép' if st.get('paired') else '🔑 CHƯA GHÉP')}\n"
                    f"📱 Mã ghép: {st.get('key_fp') or '— (chưa ghép)'}{cho_ghep}\n"
                    f"Ca {int(st.get('session_s') or 0) // 60} phút · CFI gửi tủ: {cfi if cfi >= 0 else '—'}"
                    f"{' · 💧 đang bơm' if st.get('pump') else ''} · rơ-le: {st.get('relay') or '?'} ở GPIO{st.get('relay_pin', 4)}"))
            else:
                lbl.config(fg=UI["txt"], text=(
                    f"🟢 Đã kết nối {st.get('box_id', '')} ở {st.get('port', '')} (fw {st.get('fw', '?')})\n"
                    f"{'📱 Có điện thoại trong hộp' if st.get('phone') else '📭 Hộp trống'} · "
                    f"{'🔒 Đang khóa' if st.get('lock') == 'locked' else '🔓 Đang mở'} · "
                    f"nắp {'đóng' if st.get('lid') == 'closed' else 'mở'} · "
                    f"{'đã ghép' if load_config().get('phone_box_key') else 'CHƯA GHÉP'}"))
            lbl.after(1000, refresh)

        refresh()

        def luu() -> None:
            cfg["phone_box_enabled"] = bool(v_en.get())
            cfg["phone_box_required"] = bool(v_req.get())
            cfg["phone_box_auto_lock"] = bool(v_auto.get())
            cfg["esp32_com_port"] = v_port.get()
            try:
                cfg["esp32_baud"] = int(v_baud.get().strip() or phone_box.BAUD)
            except ValueError:
                cfg["esp32_baud"] = phone_box.BAUD
            save_config(cfg)
            if cfg["phone_box_enabled"]:
                phone_box.start_phone_box()
            else:
                phone_box.get_box().stop()
            messagebox.showinfo("Đã lưu", "Đã lưu cài đặt hộp điện thoại.", parent=self.root)

        dbg = self._card(page, "🛠️  Gỡ lỗi giao tiếp (Serial monitor)", pady=(12, 0))
        self._label(dbg, "Xem từng dòng máy tính gửi (TX) và hộp trả về (RX), gửi lệnh tay, mở cổng thô với baud tùy chọn "
                         "khi hộp chưa kết nối được. Chỉ dùng trong trang Admin.", bold=False, size=9)
        self._ui_button(dbg, "🛠️  Mở Serial monitor", self._serial_monitor, kind="ghost").pack(anchor="w", pady=(6, 0))

        tip = self._card(page, "🧰  Cách lắp & ghép", pady=(12, 0))
        self._label(tip, "TỦ v2: nạp firmware/focusdesk_esp32 (Arduino IDE, ArduinoJson 7). Công tắc hành trình cửa: COM→GND, "
                         "NO→GPIO5; rơ-le bơm GPIO4; LED đỏ GPIO7, LED xanh GPIO8, còi GPIO9. Bật mô-đun, Lưu; mở app "
                         "FocusDesk trên điện thoại để ghép (điện thoại đầu tiên kết nối được ghép; đổi máy: 🔑 reset_key).",
                    bold=False, size=9)
        self._label(tip, "HỘP v1: 1. Nạp firmware firmware/hop_dien_thoai_esp32s3 cho ESP32-S3 (Arduino IDE, USB CDC On Boot = "
                         "Enabled). 2. Cắm hộp vào máy bằng cáp USB. 3. Bật “Bật hộp khóa điện thoại”, Lưu. "
                         "4. Mở nắp hộp, giữ nút trên hộp 5 giây (kêu 1 tiếng dài), bấm “Ghép hộp”. "
                         "5. Nên có chìa khóa cơ dự phòng phòng khi mất điện. Chuẩn giao tiếp cho người làm mạch: "
                         "firmware/GIAO_THUC.md.", bold=False, size=9)
        self._ui_button(page, "💾  Lưu", luu).pack(anchor="e", pady=(12, 0))

    # ---------- Serial monitor cho hộp điện thoại (chỉ Admin) ----------
    def _dong_serial_monitor(self) -> None:
        win = getattr(self, "_serial_win", None)
        if win is not None:
            try:
                win.event_generate("<<DongMonitor>>")
            except Exception:
                pass
            self._serial_win = None

    def _serial_monitor(self) -> None:
        """Cửa sổ Serial monitor: theo dõi TX/RX của kết nối hộp, gửi lệnh tay, hoặc mở cổng thô (tạm dừng kết nối tự động)."""
        import json as _json

        from services import phone_box

        old = getattr(self, "_serial_win", None)
        if old is not None and old.winfo_exists():
            old.lift()
            return
        box = phone_box.get_box()
        win = tk.Toplevel(self.root)
        self._serial_win = win
        win.title("Serial monitor — hộp điện thoại")
        win.configure(bg=UI["bg"])
        win.geometry("980x640")
        win.attributes("-topmost", True)
        st: Dict[str, Any] = {"raw": None, "seq": 0, "id": 800000}

        top = tk.Frame(win, bg=UI["bg"])
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text="Cổng", bg=UI["bg"], fg=UI["txt"], font=("Segoe UI", 9, "bold")).pack(side="left")
        ports = phone_box.list_all_ports()
        labels = [f"{p['port']} — {p['desc'][:38]}" + (" ★" if p["likely_box"] else "") + (" (Bluetooth)" if p["bluetooth"] else "")
                  for p in ports]
        v_port = tk.StringVar(value=labels[0] if labels else "")
        cb_port = ttk.Combobox(top, textvariable=v_port, values=labels, width=46)
        cb_port.pack(side="left", padx=(4, 10))
        tk.Label(top, text="Baud", bg=UI["bg"], fg=UI["txt"], font=("Segoe UI", 9, "bold")).pack(side="left")
        v_baud = tk.StringVar(value=str(phone_box.configured_baud()))
        ttk.Combobox(top, textvariable=v_baud, values=[str(b) for b in SERIAL_BAUDS], width=9).pack(side="left", padx=(4, 10))
        mode_lbl = tk.Label(top, text="", bg=UI["bg"], fg=UI["acc"], font=("Segoe UI", 9, "bold"))
        mode_lbl.pack(side="right")

        def cap_nhat_mode() -> None:
            if st["raw"] is not None:
                mode_lbl.config(text=f"● Cổng thô {st['raw'].port} @ {st['raw'].baud}", fg=UI["warn"])
                btn_raw.config(text="⏹ Đóng cổng thô")
            else:
                s2 = box.state
                mode_lbl.config(text=(f"● Theo dõi kết nối tự động: {box.port}" if s2.get("connected")
                                      else "○ Kết nối tự động: chưa thấy hộp"),
                                fg=UI["ok"] if s2.get("connected") else UI["mut"])
                btn_raw.config(text="▶ Mở cổng thô")

        def mo_dong_raw() -> None:
            if st["raw"] is not None:
                st["raw"].close()
                st["raw"] = None
                box.resume()
                cap_nhat_mode()
                return
            port = v_port.get().split(" — ")[0].strip()
            if not port:
                messagebox.showwarning("Serial monitor", "Chưa chọn cổng COM.", parent=win)
                return
            box.suspend()
            time.sleep(0.3)
            try:
                st["raw"] = phone_box.RawSerialMonitor(port, int(v_baud.get() or phone_box.BAUD))
            except Exception as exc:
                box.resume()
                messagebox.showerror("Serial monitor", f"Không mở được {port}: {exc}\n(Cổng đang bị chương trình khác "
                                     "như Arduino IDE giữ?)", parent=win)
            cap_nhat_mode()

        btn_raw = self._ui_button(top, "▶ Mở cổng thô", mo_dong_raw, kind="ghost")
        btn_raw.pack(side="left")

        opts = tk.Frame(win, bg=UI["bg"])
        opts.pack(fill="x", padx=12)
        v_hide_ping = tk.BooleanVar(value=True)
        v_auto = tk.BooleanVar(value=True)
        v_time = tk.BooleanVar(value=True)
        for text, var in (("Ẩn ping/state định kỳ", v_hide_ping), ("Tự cuộn", v_auto), ("Hiện giờ", v_time)):
            tk.Checkbutton(opts, text=text, variable=var, bg=UI["bg"], activebackground=UI["bg"],
                           font=("Segoe UI", 9)).pack(side="left", padx=(0, 10))

        body = tk.Frame(win, bg=UI["bg"])
        body.pack(fill="both", expand=True, padx=12, pady=6)
        txt = tk.Text(body, bg="#0b1220", fg="#e2e8f0", insertbackground="white", font=("Consolas", 10),
                      relief="flat", wrap="none", state="disabled")
        sb = ttk.Scrollbar(body, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.tag_configure("TX", foreground="#60a5fa")
        txt.tag_configure("RX", foreground="#4ade80")
        txt.tag_configure("RXRAW", foreground="#94a3b8")
        txt.tag_configure("ERR", foreground="#f87171")
        txt.tag_configure("SYS", foreground="#fbbf24")

        def them_dong(ts: float, d: str, line: str) -> None:
            tag = d
            if d == "RX":
                try:
                    m = _json.loads(line)
                    if isinstance(m, dict) and (m.get("ok") is False or m.get("e") == "tamper"):
                        tag = "ERR"
                except Exception:
                    tag = "RXRAW"
            prefix = (time.strftime("%H:%M:%S", time.localtime(ts)) + f".{int(ts * 1000) % 1000:03d} " if v_time.get() else "")
            arrow = {"TX": "→ ", "RX": "← ", "SYS": "• "}.get(d, "  ")
            txt.configure(state="normal")
            txt.insert("end", f"{prefix}{arrow}{line}\n", tag)
            if int(txt.index("end-1c").split(".")[0]) > 4000:
                txt.delete("1.0", "500.0")
            txt.configure(state="disabled")
            if v_auto.get():
                txt.see("end")

        def poll() -> None:
            if not win.winfo_exists():
                return
            rows = phone_box.traffic_since(st["seq"])
            for seq, ts, d, line, _port in rows:
                st["seq"] = seq
                compact = line.replace(" ", "")
                if v_hide_ping.get():
                    # Ẩn ping tự động 2s/lần và đúng câu trả lời state của nó; state trả cho lệnh "get" bấm tay vẫn hiện
                    if d == "TX" and '"t":"ping"' in compact:
                        st["ping_cho"] = st.get("ping_cho", 0) + 1
                        continue
                    if d == "RX" and '"t":"state"' in compact and st.get("ping_cho", 0) > 0:
                        st["ping_cho"] -= 1
                        continue
                them_dong(ts, d, line)
            cap_nhat_mode()
            win.after(250, poll)

        send = tk.Frame(win, bg=UI["bg"])
        send.pack(fill="x", padx=12, pady=(0, 6))
        e_cmd = tk.Entry(send, font=("Consolas", 10), relief="flat", highlightthickness=1,
                         highlightbackground=UI["line"], highlightcolor=UI["acc"])
        e_cmd.pack(side="left", fill="x", expand=True, ipady=6)

        def gui_dong(line: str) -> None:
            line = line.strip()
            if not line:
                return
            try:
                if st["raw"] is not None:
                    st["raw"].send_text(line)
                else:
                    box.send_text(line)
            except Exception as exc:
                phone_box.log_traffic("SYS", f"Không gửi được: {exc}")

        def gui_tu_o_nhap(_e=None) -> None:
            gui_dong(e_cmd.get())
            e_cmd.delete(0, "end")

        def gui_json(t: str, **extra: Any) -> None:
            st["id"] += 1
            gui_dong(_json.dumps({"t": t, "id": st["id"], **extra}, separators=(",", ":")))

        def gui_ky(t: str, sec: int = 0) -> None:
            def work():
                try:
                    if st["raw"] is not None:
                        st["raw"].send_signed(t, sec)
                    else:
                        r = box.lock() if t == "lock" else box.unlock(sec)
                        phone_box.log_traffic("SYS", f"Kết quả {t}: {'OK' if r.get('ok') else r.get('err')}")
                except Exception as exc:
                    phone_box.log_traffic("SYS", f"{t}: {exc}")
            threading.Thread(target=work, daemon=True).start()

        e_cmd.bind("<Return>", gui_tu_o_nhap)
        self._ui_button(send, "Gửi ↵", gui_tu_o_nhap).pack(side="left", padx=(8, 0))

        quick = tk.Frame(win, bg=UI["bg"])
        quick.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(quick, text="Lệnh nhanh:", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 9)).pack(side="left")
        for text, fn in (("hello", lambda: gui_json("hello")), ("get", lambda: gui_json("get")),
                         ("ping", lambda: gui_json("ping", ts=int(time.time()))), ("beep 2", lambda: gui_json("beep", n=2)),
                         ("🔒 lock (ký)", lambda: gui_ky("lock")), ("🔓 unlock 60s (ký)", lambda: gui_ky("unlock", 60))):
            self._ui_button(quick, text, fn, kind="ghost").pack(side="left", padx=(6, 0))
        quick2 = tk.Frame(win, bg=UI["bg"])
        quick2.pack(fill="x", padx=12, pady=(0, 10))
        tk.Label(quick2, text="Tủ v2:", bg=UI["bg"], fg=UI["mut"], font=("Segoe UI", 9)).pack(side="left")
        for text, fn in (("cfg 1′", lambda: gui_json("cfg", session_ms=60000, led_cfi=50, buzz_cfi=30)),
                         ("ping cfi=40", lambda: gui_json("ping", cfi=40)), ("ping cfi=20", lambda: gui_json("ping", cfi=20)),
                         ("ping cfi=-1", lambda: gui_json("ping", cfi=-1)), ("water 2s", lambda: gui_json("water", ms=2000)),
                         ("beep", lambda: gui_json("beep", ms=600)), ("start 1′ (thử)", lambda: gui_json("start", session_ms=60000)),
                         ("io", lambda: gui_json("io")), ("charge on", lambda: gui_json("charge", on=True)),
                         ("charge off", lambda: gui_json("charge", on=False)), ("end", lambda: gui_json("end", reason="admin")),
                         ("reset_key", lambda: gui_json("reset_key"))):
            self._ui_button(quick2, text, fn, kind="ghost").pack(side="left", padx=(6, 0))

        def xoa() -> None:
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.configure(state="disabled")

        def luu_log() -> None:
            os.makedirs(os.path.join(get_app_dir(), "logs"), exist_ok=True)
            path = os.path.join(get_app_dir(), "logs", time.strftime("serial_%Y%m%d_%H%M%S.txt"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(txt.get("1.0", "end"))
            phone_box.log_traffic("SYS", f"Đã lưu nhật ký: {path}")

        self._ui_button(quick, "💾 Lưu log", luu_log, kind="ghost").pack(side="right")
        self._ui_button(quick, "🧹 Xóa", xoa, kind="ghost").pack(side="right", padx=6)

        def dong(_e=None) -> None:
            if st["raw"] is not None:
                try:
                    st["raw"].close()
                except Exception:
                    pass
                st["raw"] = None
                box.resume()
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", dong)
        win.bind("<<DongMonitor>>", dong)
        st["seq"] = max(0, (phone_box.traffic_since(0) or [(0,)])[-1][0] - 200)   # hiện 200 dòng gần nhất
        try:
            import serial  # noqa: F401
        except Exception:
            phone_box.log_traffic("SYS", "Chưa cài thư viện pyserial nên chưa đọc được cổng COM. Chạy KIEM_TRA.bat "
                                         "(hoặc: python -m pip install pyserial) rồi mở lại Serial monitor.")
        cap_nhat_mode()
        poll()
        e_cmd.focus_set()

    def _trang_he_thong(self, page) -> None:
        cfg = load_config()
        self._page_header(page, "Hệ thống & kiosk", "Mức khóa máy khi học sinh đăng nhập.")
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="both", expand=True)
        c1 = self._card(row, "🔐  Khóa máy (kiosk)", side="left", padx=(0, 8))
        v_strict = self._check(c1, "Ẩn thanh Taskbar, chặn phím Windows và tự thu nhỏ ứng dụng ngoài danh sách "
                                   "(khuyên dùng)", cfg.get("kiosk_strict", True))
        v_exp = self._check(c1, "Tắt hẳn explorer.exe (Desktop) khi học sinh học — chặn mạnh nhất, máy khởi lại "
                                "Desktop khi thoát", cfg.get("kiosk_hide_explorer", False))
        from services.kiosk_guard import WIN_TOOL_COMBOS

        v_tools = self._check(c1, "Cho dùng công cụ có sẵn của Windows khi khóa máy: chụp màn hình, clipboard, emoji, "
                                  f"đổi bàn phím ({WIN_TOOL_COMBOS})", cfg.get("kiosk_allow_win_tools", True))
        v_always = self._check(c1, "Giám sát web/game luôn bật, kể cả khi chưa đăng nhập học sinh",
                               cfg.get("monitor_always", False))
        tk.Label(c1, text="Thoát khẩn cấp: phím cứu hộ + PIN (đặt ở trang “🆘 Cứu hộ”) · file UNLOCK.txt · "
                          "KHOI_PHUC_KHAN_CAP.bat", bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9), wraplength=420,
                 justify="left").pack(anchor="w", pady=(10, 0))
        bgc = self._card(page, "🧹  Tắt ứng dụng chạy nền khi vào học", pady=(12, 0))
        v_bg = self._check(bgc, "Khi học sinh đăng nhập: tắt mọi ứng dụng đang mở / chạy ngầm (giữ lại Windows, bộ gõ "
                                "EVKey/UniKey, OneDrive, driver AMD/NVIDIA/Intel/âm thanh, diệt virus, app được cấp)",
                           cfg.get("student_close_background", True))
        v_sweep = self._check(bgc, "Trong giờ học, cứ 60 giây tắt tiếp ứng dụng chạy nền mới mở",
                              cfg.get("student_bg_sweep", True))
        self._label(bgc, "Giữ thêm các ứng dụng sau (tên file .exe, cách nhau dấu phẩy, dùng * thay nhiều ký tự, "
                         "vd: zalo.exe, hwinfo*.exe)", size=9)
        e_bg = self._entry(bgc, ", ".join(cfg.get("background_allow", [])))
        tk.Label(bgc, text="⚠️ Ứng dụng chưa lưu bài sẽ được yêu cầu đóng, sau 4 giây bị buộc tắt. Nhớ lưu công việc "
                           "trước khi giao máy cho học sinh.", bg=UI["card"], fg=UI["warn"], font=("Segoe UI", 9),
                 wraplength=880, justify="left").pack(anchor="w")
        out_bg = tk.Label(bgc, text="", bg=UI["card"], fg=UI["txt"], font=("Consolas", 9), justify="left",
                          wraplength=880, anchor="w")

        def xem_truoc() -> None:
            out_bg.config(text="⏳ Đang quét…")
            extra = [x.strip() for x in e_bg.get().split(",") if x.strip()]

            def work():
                try:
                    from services.process_cleanup import find_targets

                    tg = find_targets(extra)
                    txt = (f"Nếu vào học lúc này sẽ tắt {len(tg)} ứng dụng:\n" +
                           "\n".join(f"  • {g['name']:<34}" + (f"{g['windows']} cửa sổ" if g["windows"] else "chạy ngầm").ljust(11)
                                     + f"  {os.path.dirname(g.get('exe') or '')[-70:]}" for g in tg[:60])
                           + "\nThấy ứng dụng cần giữ (driver chuột/bàn phím, bút vẽ…)? Gõ tên .exe vào ô Giữ thêm rồi Lưu."
                           ) if tg else "Không có ứng dụng nào bị tắt."
                except Exception as exc:
                    txt = f"Lỗi khi quét: {exc}"
                self._goi_ui(lambda: out_bg.winfo_exists() and out_bg.config(text=txt))
            threading.Thread(target=work, daemon=True).start()

        self._ui_button(bgc, "👁  Xem trước danh sách sẽ bị tắt (không tắt gì)", xem_truoc, kind="ghost").pack(
            anchor="w", pady=(6, 4))
        out_bg.pack(anchor="w", fill="x")

        c2 = self._card(row, "⚙️  Kết nối", side="left", padx=(8, 0))
        self._label(c2, "Cổng web nội bộ")
        e_port = self._entry(c2, str(cfg.get("web_port", 5000)))
        tk.Label(c2, text="Cổng COM / baud của hộp điện thoại: xem trang “📦 Hộp điện thoại”.", bg=UI["card"],
                 fg=UI["mut"], font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 6))
        tools = tk.Frame(c2, bg=UI["card"])
        tools.pack(fill="x", pady=(6, 0))
        self._ui_button(tools, "🧰 Task Manager", lambda: subprocess.Popen(["taskmgr.exe"]), kind="ghost").pack(side="left")
        self._ui_button(tools, "📂 Thư mục phần mềm", lambda: os.startfile(get_app_dir()), kind="ghost").pack(side="left", padx=6)

        def luu() -> None:
            cfg["kiosk_strict"] = bool(v_strict.get())
            cfg["kiosk_hide_explorer"] = bool(v_exp.get())
            cfg["kiosk_allow_win_tools"] = bool(v_tools.get())
            cfg["monitor_always"] = bool(v_always.get())
            cfg["student_close_background"] = bool(v_bg.get())
            cfg["student_bg_sweep"] = bool(v_sweep.get())
            cfg["background_allow"] = [x.strip().lower() for x in e_bg.get().split(",") if x.strip()]
            try:
                cfg["web_port"] = int(e_port.get().strip() or 5000)
            except ValueError:
                cfg["web_port"] = 5000
            save_config(cfg)
            messagebox.showinfo("Đã lưu", "Đã lưu cấu hình hệ thống.", parent=self.root)

        self._ui_button(page, "💾  Lưu", luu).pack(anchor="e", pady=(12, 0))

    def _trang_cuu_ho(self, page) -> None:
        """Admin: đặt phím cứu hộ riêng + PIN cứu hộ. Học sinh không thấy nút / gợi ý cứu hộ ở bất kỳ màn hình nào."""
        from core.failsafe import (RESCUE_HOTKEY_DEFAULT, get_rescue_hotkey, parse_hotkey, rescue_pin_is_default,
                                   restart_global_panic_hotkeys, set_rescue_pin)

        self._page_header(page, "Cứu hộ khẩn cấp",
                          "Khi máy bị kẹt ở chế độ khóa: bấm phím cứu hộ ở bất kỳ màn hình nào → nhập PIN → FocusDesk mở "
                          "khóa Windows và thoát. Phím và PIN chỉ Admin biết; màn hình học sinh không có nút cứu hộ.")
        row = tk.Frame(page, bg=UI["bg"])
        row.pack(fill="x")
        c1 = self._card(row, "⌨️  Phím cứu hộ", side="left", padx=(0, 8))
        cur = parse_hotkey(get_rescue_hotkey()) or parse_hotkey(RESCUE_HOTKEY_DEFAULT)
        parts = cur[2].split("+")
        mods = tk.Frame(c1, bg=UI["card"])
        mods.pack(anchor="w")
        v_mod = {}
        for m in ("Ctrl", "Alt", "Shift"):
            v_mod[m] = tk.BooleanVar(value=m in parts[:-1])
            tk.Checkbutton(mods, text=m, variable=v_mod[m], bg=UI["card"], selectcolor=UI["card"],
                           font=("Segoe UI", 10, "bold")).pack(side="left", padx=(0, 10))
        keys = [f"F{i}" for i in range(1, 13)] + [chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(i) for i in range(10)]
        v_key = tk.StringVar(value=parts[-1])
        ttk.Combobox(c1, textvariable=v_key, values=keys, state="readonly", width=8).pack(anchor="w", pady=(6, 4))
        lbl_now = tk.Label(c1, text=f"Đang dùng: {cur[2]}", bg=UI["card"], fg=UI["acc"], font=("Segoe UI", 10, "bold"))
        lbl_now.pack(anchor="w", pady=(4, 0))
        tk.Label(c1, text="Cần ít nhất 2 phím bổ trợ (Ctrl/Alt/Shift). Tránh tổ hợp trùng phần mềm khác.",
                 bg=UI["card"], fg=UI["mut"], font=("Segoe UI", 9), wraplength=380, justify="left").pack(anchor="w")

        c2 = self._card(row, "🔢  PIN cứu hộ", side="left", padx=(8, 0))
        tk.Label(c2, text=("⚠️ Đang dùng PIN mặc định — hãy đổi." if rescue_pin_is_default() else "✅ Đã đặt PIN riêng."),
                 bg=UI["card"], fg=UI["warn"] if rescue_pin_is_default() else UI["ok"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))
        self._label(c2, "PIN mới (6–12 chữ số, để trống nếu không đổi)", size=9)
        e_pin1 = self._entry(c2, "", show="•")
        self._label(c2, "Nhập lại PIN mới", size=9)
        e_pin2 = self._entry(c2, "", show="•")
        tk.Label(c2, text="PIN chỉ lưu dạng băm SHA-256 trong cấu hình, không lưu chữ số thật.", bg=UI["card"],
                 fg=UI["mut"], font=("Segoe UI", 9)).pack(anchor="w")

        other = self._card(page, "🧯  Cách cứu hộ khác (chỉ người có quyền truy cập ổ đĩa)", pady=(12, 0))
        self._label(other, "• Tạo file UNLOCK.txt trong thư mục phần mềm → thoát ngay.\n"
                           "• Chạy KHOI_PHUC_KHAN_CAP.bat → bật lại Task Manager, taskbar, tắt FocusDesk.\n"
                           "• Nếu giao diện bị treo, bấm phím cứu hộ: sau 4 giây không hiện được hộp PIN, phần mềm tự "
                           "khôi phục Windows.", bold=False, size=9)

        def luu() -> None:
            combo = "+".join([m for m in ("Ctrl", "Alt", "Shift") if v_mod[m].get()] + [v_key.get()])
            parsed = parse_hotkey(combo)
            if not parsed:
                messagebox.showwarning("Phím chưa hợp lệ", "Chọn ít nhất 2 phím bổ trợ và 1 phím chính.", parent=self.root)
                return
            p1, p2 = e_pin1.get().strip(), e_pin2.get().strip()
            if p1 or p2:
                if p1 != p2:
                    messagebox.showwarning("PIN không khớp", "Hai lần nhập PIN không giống nhau.", parent=self.root)
                    return
                if not (p1.isdigit() and 6 <= len(p1) <= 12):
                    messagebox.showwarning("PIN chưa hợp lệ", "PIN cần 6–12 chữ số.", parent=self.root)
                    return
                set_rescue_pin(p1)
            cfg = load_config()
            cfg["rescue_hotkey"] = parsed[2]
            save_config(cfg)
            try:
                restart_global_panic_hotkeys()
            except Exception as exc:
                safe_log(f"[CỨU HỘ] Không đăng ký lại phím: {exc}")
            lbl_now.config(text=f"Đang dùng: {parsed[2]}")
            e_pin1.delete(0, "end")
            e_pin2.delete(0, "end")
            messagebox.showinfo("Đã lưu", f"Phím cứu hộ: {parsed[2]}" + ("\nĐã đổi PIN cứu hộ." if p1 else ""),
                                parent=self.root)

        self._ui_button(page, "💾  Lưu cài đặt cứu hộ", luu).pack(anchor="e", pady=(12, 0))

    def _trang_mat_khau(self, page) -> None:
        cfg = load_config()
        self._page_header(page, "Mật khẩu 3 cấp", "Đổi mật khẩu mặc định trước khi giao máy cho học sinh.")
        c = self._card(page, "🔑  Mật khẩu")
        ents = {}
        for role, label in (("admin", "Admin"), ("parent", "Phụ huynh"), ("student", "Học sinh")):
            self._label(c, f"Mật khẩu {label}" + (" (chỉ dùng khi bật ô bên dưới)" if role == "student" else ""))
            ents[role] = self._entry(c, cfg["passwords"].get(role, ""), show="•")
        v_hs = self._check(c, "Học sinh cần mật khẩu để vào học (mặc định TẮT: học sinh chỉ bấm “Vào học”, "
                              "vì chế độ học sinh là chế độ bị khóa chặt nhất)", cfg.get("student_needs_password", False))
        v_show = tk.BooleanVar(value=False)
        tk.Checkbutton(c, text="Hiện mật khẩu", variable=v_show, bg=UI["card"], selectcolor=UI["card"],
                       command=lambda: [e.config(show="" if v_show.get() else "•") for e in ents.values()]).pack(anchor="w")

        def luu() -> None:
            vals = {k: e.get().strip() for k, e in ents.items()}
            cfg["student_needs_password"] = bool(v_hs.get())
            need = [k for k in vals if k != "student" or cfg["student_needs_password"]]
            if any(len(vals[k]) < 4 for k in need):
                messagebox.showwarning("Mật khẩu quá ngắn", "Mỗi mật khẩu cần tối thiểu 4 ký tự.", parent=self.root)
                return
            cfg["passwords"].update(vals)
            save_config(cfg)
            messagebox.showinfo("Đã lưu", "Đã đổi mật khẩu.", parent=self.root)

        self._ui_button(page, "💾  Lưu mật khẩu", luu).pack(anchor="e", pady=(12, 0))

    def _trang_thoat(self, page) -> None:
        self._page_header(page, "Thoát phần mềm",
                          "Mở khóa Task Manager, hiện lại Taskbar/Desktop, dừng giám sát và đóng hẳn phần mềm.")
        c = self._card(page, "")
        self._label(c, "Máy sẽ trở về Windows bình thường. Học sinh có thể dùng mọi ứng dụng cho tới khi bật lại "
                       "phần mềm.", bold=False)
        tk.Button(c, text="🚪  Thoát hoàn toàn phần mềm", command=lambda: self.thoat_khan_cap(
            "Admin chủ động đóng phần mềm", hien_thong_bao=True), bg=UI["bad"], fg="white", activebackground="#b91c1c",
            activeforeground="white", font=("Segoe UI", 11, "bold"), relief="flat", bd=0, padx=18, pady=10,
            cursor="hand2").pack(anchor="w", pady=(12, 0))


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


def _dong_bo_dns(role: str) -> None:
    """Đổi chế độ -> bật/gỡ chặn DNS (file hosts): chỉ chặn khi HỌC SINH đang dùng máy, phụ huynh/admin dùng tự do."""
    try:
        from services.hosts_blocker import sync_hosts_for_role

        ok, msg = sync_hosts_for_role(role)
        safe_log(f"[HOSTS] Chế độ {role}: {msg}")
    except Exception as exc:
        safe_log(f"[HOSTS] Lỗi đồng bộ chặn DNS: {exc}")


def main() -> None:
    if "--self-test" in sys.argv:
        sys.exit(run_self_test())

    ensure_data_files_exist()
    root = tk.Tk()
    app_instance = PhanMemQuanLyBanHocEXE(root)
    install_all_failsafes(on_ui_emergency=app_instance.thoat_khan_cap_tu_luong_phu)
    try:   # cloudflared / AI camera còn sót từ lần chạy trước (bản cũ, hoặc máy bị tắt ngang)
        from services.app_shutdown import kill_orphan_helpers

        threading.Thread(target=kill_orphan_helpers, name="OrphanCleanup", daemon=True).start()
        from services.study_sessions import close_stale

        close_stale(force=True)   # ca còn mở từ lần chạy trước (mất điện / tắt ngang) -> chốt và gửi web

    except Exception as exc:
        safe_log(f"[THOÁT] Không dọn được tiến trình mồ côi: {exc}")
    start_web_server_in_background()
    try:
        from services.phone_box import start_phone_box

        start_phone_box()
    except Exception as exc:
        safe_log(f"[HỘP] Không khởi động được kết nối hộp điện thoại: {exc}")
    try:
        root.mainloop()
    finally:
        # Cửa sổ chính đóng theo đường khác (Windows đăng xuất / tắt máy...) -> vẫn tắt sạch tiến trình phụ
        if not getattr(app_instance, "_dang_thoat", False):
            try:
                from services.app_shutdown import shutdown_all

                shutdown_all("Cửa sổ FocusDesk bị đóng (Windows đăng xuất / tắt máy?)")
            except Exception:
                pass
            restore_windows_normal_state()


if __name__ == "__main__":
    main()
