package com.example.focusdesk

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Log
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/**
 * Khóa bảo mật của điện thoại (nằm trong chip bảo mật TEE / StrongBox, không lấy ra được).
 * - Khóa riêng: chỉ dùng để KÝ mã ngẫu nhiên tủ gửi tới.
 * - Khóa công khai: tủ ESP32 đọc 1 lần lúc ghép rồi lưu lại để kiểm tra chữ ký.
 * - Mã ghép 6 số: rút gọn từ khóa công khai; tủ, web phụ huynh và app tính GIỐNG HỆT nhau để đối chiếu.
 */
object KeyHelper {
    const val ALIAS = "FocusDeskSecureKey"
    const val PREFS = "FocusDeskPrefs"
    private const val TAG = "KeyHelper"

    private fun keyStore(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    /** Có khóa thật trong Keystore không (không tin cờ đã lưu — cài lại app / khôi phục máy sẽ mất khóa). */
    fun hasKey(): Boolean = try {
        keyStore().getCertificate(ALIAS) != null
    } catch (e: Exception) {
        false
    }

    /** Chưa có khóa thì tạo. Trả về true nếu sau cùng đã có khóa. */
    fun ensureKey(): Boolean = hasKey() || generate()

    fun generate(): Boolean {
        return try {
            val kpg = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
            fun spec(strongBox: Boolean) = KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .setUserAuthenticationRequired(false)
                .setIsStrongBoxBacked(strongBox)
                .build()
            try {
                kpg.initialize(spec(true))       // ưu tiên chip StrongBox (máy nào có)
                kpg.generateKeyPair()
            } catch (e: Exception) {
                kpg.initialize(spec(false))      // không có StrongBox -> dùng TEE thường
                kpg.generateKeyPair()
            }
            hasKey()
        } catch (e: Exception) {
            Log.e(TAG, "Không tạo được khóa: ${e.message}")
            false
        }
    }

    fun deleteKey() {
        try {
            keyStore().deleteEntry(ALIAS)
        } catch (e: Exception) {
            Log.e(TAG, "Không xóa được khóa: ${e.message}")
        }
    }

    fun publicKey(): ByteArray? = try {
        keyStore().getCertificate(ALIAS)?.publicKey?.encoded
    } catch (e: Exception) {
        null
    }

    fun sign(data: ByteArray): ByteArray? = try {
        val key = keyStore().getKey(ALIAS, null) as? PrivateKey
        if (key == null) null else Signature.getInstance("SHA256withECDSA").run {
            initSign(key)
            update(data)
            sign()
        }
    } catch (e: Exception) {
        Log.e(TAG, "Ký lỗi: ${e.message}")
        null
    }

    /** Mã ghép 6 số = (4 byte đầu SHA-256 của khóa công khai, số không dấu) mod 1 000 000 — giống firmware tủ. */
    fun pairCode(): String {
        val pk = publicKey() ?: return "------"
        val h = MessageDigest.getInstance("SHA-256").digest(pk)
        val v = ((h[0].toLong() and 0xFF) shl 24) or ((h[1].toLong() and 0xFF) shl 16) or
                ((h[2].toLong() and 0xFF) shl 8) or (h[3].toLong() and 0xFF)
        return "%06d".format(v % 1_000_000L)
    }

    /** Hiển thị "123 456" cho dễ đọc. */
    fun pairCodePretty(): String = pairCode().let { it.substring(0, 3) + " " + it.substring(3) }
}
