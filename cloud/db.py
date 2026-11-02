# -*- coding: utf-8 -*-
"""
Lớp truy cập Firebase Realtime Database cho bản cloud (chạy trên Vercel).

- Truy cập bằng Service Account (OAuth2) qua REST API -> bỏ qua Security Rules, nên Rules đặt
  ".read": false, ".write": false cho mọi client (trình duyệt KHÔNG bao giờ gọi Firebase trực tiếp,
  khác bản DuAnAI cũ để DB mở công khai).
- Biến môi trường:
    FIREBASE_DB_URL            https://<project>-default-rtdb.<region>.firebasedatabase.app
    FIREBASE_SERVICE_ACCOUNT   nội dung JSON service account (dán nguyên, hoặc base64)
- Không có FIREBASE_DB_URL -> dùng kho JSON cục bộ (chỉ để chạy thử trên máy dev).
"""

import base64
import json
import os
import tempfile
import threading
import time
from typing import Any, Dict, Optional

import requests

_FB_URL = os.environ.get("FIREBASE_DB_URL", "").rstrip("/")
_SCOPES = [
    "https://www.googleapis.com/auth/firebase.database",
    "https://www.googleapis.com/auth/userinfo.email",
]


def fb_key(text: str) -> str:
    """Chuẩn hóa chuỗi thành khóa Firebase hợp lệ (cấm . $ # [ ] /)."""
    out = text.strip().lower()
    for ch, rep in ((".", ","), ("$", "_"), ("#", "_"), ("[", "_"), ("]", "_"), ("/", "_")):
        out = out.replace(ch, rep)
    return out


class _FirebaseStore:
    def __init__(self, url: str):
        self.url = url
        self._creds = None
        self._lock = threading.Lock()
        self._local = threading.local()

    def _session(self) -> requests.Session:
        """Giữ kết nối HTTPS (keep-alive) giữa các lần gọi trong cùng 1 phiên bản hàm đang ấm: bỏ bắt tay TLS mỗi
        lần gọi -> nhanh hơn nhiều và Firebase không tính thêm dung lượng bắt tay vào hạn mức tải xuống."""
        s = getattr(self._local, "s", None)
        if s is None:
            s = requests.Session()
            self._local.s = s
        return s

    def _token(self) -> str:
        with self._lock:
            if self._creds is None:
                from google.oauth2 import service_account

                raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()
                if not raw:
                    raise RuntimeError("Thiếu biến môi trường FIREBASE_SERVICE_ACCOUNT")
                if not raw.startswith("{"):
                    raw = base64.b64decode(raw).decode("utf-8")
                self._creds = service_account.Credentials.from_service_account_info(json.loads(raw), scopes=_SCOPES)
            if not self._creds.valid or (self._creds.expiry and self._creds.expiry.timestamp() - time.time() < 120):
                from google.auth.transport.requests import Request

                self._creds.refresh(Request())
            return self._creds.token

    def _req(self, method: str, path: str, data: Any = None, params: Optional[Dict[str, str]] = None) -> Any:
        url = f"{self.url}/{path.strip('/')}.json"
        headers = {"Authorization": f"Bearer {self._token()}"}
        resp = self._session().request(method, url, json=data, params=params, headers=headers, timeout=8)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def get(self, path: str, shallow: bool = False) -> Any:
        """shallow=True: chỉ lấy tên khóa con ({khóa: true}) — rất nhẹ, dùng để biết 'có hay không'."""
        return self._req("GET", path, params={"shallow": "true"} if shallow else None)

    # print=silent: Firebase không gửi lại dữ liệu vừa ghi (mặc định có gửi lại -> tốn hạn mức TẢI XUỐNG 10 GB/tháng)
    def set(self, path: str, value: Any) -> None:
        self._req("PUT", path, value, params={"print": "silent"})

    def update(self, path: str, values: Dict[str, Any]) -> None:
        self._req("PATCH", path, values, params={"print": "silent"})

    def push(self, path: str, value: Any) -> str:
        return self._req("POST", path, value)["name"]

    def delete(self, path: str) -> None:
        self._req("DELETE", path, params={"print": "silent"})


class _LocalStore:
    """Kho JSON trong 1 file - mô phỏng đúng ngữ nghĩa REST của Firebase để test offline."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock = threading.Lock()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        tmp = self.file_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, self.file_path)

    @staticmethod
    def _parts(path: str):
        return [p for p in path.strip("/").split("/") if p]

    def get(self, path: str, shallow: bool = False) -> Any:
        with self._lock:
            node: Any = self._load()
            for p in self._parts(path):
                if not isinstance(node, dict) or p not in node:
                    return None
                node = node[p]
            if shallow and isinstance(node, dict):
                return {k: True for k in node}
            return node

    def _write(self, path: str, fn) -> Any:
        with self._lock:
            root = self._load()
            parts = self._parts(path)
            node = root
            for p in parts[:-1]:
                if not isinstance(node.get(p), dict):
                    node[p] = {}
                node = node[p]
            out = fn(node, parts[-1])
            self._prune(root)
            self._save(root)
            return out

    def _prune(self, node: Dict[str, Any]) -> None:
        for k in list(node.keys()):
            if isinstance(node[k], dict):
                self._prune(node[k])
                if not node[k]:
                    del node[k]
            elif node[k] is None:
                del node[k]

    def set(self, path: str, value: Any) -> None:
        self._write(path, lambda n, k: n.__setitem__(k, value))

    def update(self, path: str, values: Dict[str, Any]) -> None:
        def fn(n, k):
            cur = n.get(k) if isinstance(n.get(k), dict) else {}
            for vk, vv in values.items():
                sub = cur
                segs = vk.split("/")
                for s in segs[:-1]:
                    sub = sub.setdefault(s, {})
                sub[segs[-1]] = vv
            n[k] = cur
        self._write(path, fn)

    def push(self, path: str, value: Any) -> str:
        key = f"-{int(time.time() * 1000):013d}{os.urandom(3).hex()}"
        self.update(path, {key: value})
        return key

    def delete(self, path: str) -> None:
        self._write(path, lambda n, k: n.pop(k, None))


def get_store():
    if _FB_URL:
        return _FirebaseStore(_FB_URL)
    return _LocalStore(os.environ.get("LOCAL_DB_FILE", os.path.join(tempfile.gettempdir(), "focusdesk_local_db.json")))


db = get_store()
