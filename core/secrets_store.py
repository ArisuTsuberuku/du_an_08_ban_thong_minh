# -*- coding: utf-8 -*-
"""
Kho bí mật cục bộ (core/secrets_store.py) — KHÔNG bao giờ commit lên git.

Thứ tự ưu tiên khi đọc 1 khóa:
  1. Biến môi trường (vd: FOCUSDESK_TELEGRAM_BOT_TOKEN, FOCUSDESK_CLOUD_URL)
  2. Tệp secrets.local.json cạnh mã nguồn / cạnh file .exe (đã nằm trong .gitignore)

Mẫu secrets.local.json:
{
  "telegram_bot_token": "123456:ABC...",      # chỉ cần khi chạy chế độ Telegram trực tiếp (không qua cloud)
  "cloud_url": "https://banhocthongminh.vercel.app"
}

Khi dùng bản cloud (Vercel), máy học sinh KHÔNG cần token bot: token chỉ nằm trong
Environment Variables của Vercel, nên giải nén file .exe cũng không lấy được.
"""

import json
import os
from typing import Any, Dict, Optional

from core.config_manager import get_app_dir

SECRETS_FILE = os.path.join(get_app_dir(), "secrets.local.json")
_ENV_MAP = {
    "telegram_bot_token": "FOCUSDESK_TELEGRAM_BOT_TOKEN",
    "cloud_url": "FOCUSDESK_CLOUD_URL",
}


def _load_file() -> Dict[str, Any]:
    try:
        with open(SECRETS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    env_name = _ENV_MAP.get(key, "FOCUSDESK_" + key.upper())
    # Đọc cả tên biến cũ SMARTDESK_* (trước khi đổi thương hiệu) để máy đã cấu hình không bị mất kết nối
    for name in (env_name, env_name.replace("FOCUSDESK_", "SMARTDESK_", 1)):
        val = os.environ.get(name, "").strip()
        if val:
            return val
    val = str(_load_file().get(key, "") or "").strip()
    return val or default
