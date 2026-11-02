# -*- coding: utf-8 -*-
"""
Tạo cặp khóa VAPID cho Web Push (thông báo điện thoại kể cả khi đã đóng web phụ huynh).

Chạy 1 lần trên máy của bạn:   python cloud/tao_khoa_vapid.py
Rồi dán 2 dòng in ra vào Vercel → Project → Settings → Environment Variables:
    VAPID_PUBLIC_KEY  = ...   (công khai, gửi cho trình duyệt)
    VAPID_PRIVATE_KEY = ...   (BÍ MẬT — không commit lên GitHub, không gửi cho ai)
Sau đó Redeploy. Đổi khóa = mọi điện thoại phải bấm "Bật thông báo" lại.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def main() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print("VAPID_PUBLIC_KEY=" + b64u(pub))
    print("VAPID_PRIVATE_KEY=" + b64u(priv))


if __name__ == "__main__":
    main()
