package com.example.focusdesk

import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.ParcelUuid
import android.util.Log
import androidx.core.app.NotificationCompat
import java.util.UUID

/**
 * Dịch vụ Bluetooth của FocusDesk — điện thoại đóng vai "máy chủ" (GATT server), tủ ESP32 kết nối tới.
 *
 * Luồng 1 ca học (khớp firmware tủ >= 2.3, xem firmware/GIAO_THUC.md):
 *  1. Bấm BẮT ĐẦU HỌC -> phát sóng Bluetooth.  2. Tủ kết nối, ghi mã ngẫu nhiên (…de1).
 *  3. App ký bằng khóa TEE -> gửi chữ ký (…de2) -> NGAY SAU ĐÓ gửi trạng thái sạc HIỆN TẠI (…de4).
 *  4. Cắm / rút sạc bất cứ lúc nào -> gửi "CHARGED" / "UNCHARGED".
 *  5. Giữ kết nối suốt ca. Rút sạc quá 2 phút, hoặc 60 giây không thấy tủ -> tự tắt cho đỡ pin.
 */
@SuppressLint("MissingPermission")
class BleService : Service() {

    companion object {
        const val ACTION_UI = "com.example.focusdesk.UI_UPDATE"
        val SERVICE_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-567890abcdef")
        val NONCE_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-567890abcde1")
        val SIGN_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-567890abcde2")
        val PUBKEY_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-567890abcde3")
        val CMD_UUID: UUID = UUID.fromString("12345678-1234-5678-1234-567890abcde4")
        val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")   // ô "bật thông báo" chuẩn BLE
        private const val CHANNEL_ID = "FocusDeskChannel_Auto"
        private const val NOTIFICATION_ID = 2
        private const val NO_BOX_TIMEOUT_MS = 180_000L       // 3 phút không thấy tủ -> tắt (đủ cho phụ huynh ghép lại)
        private const val UNPLUG_STOP_MS = 120_000L          // rút sạc 2 phút -> tắt
        private const val TAG = "BleService"
    }

    private var gattServer: BluetoothGattServer? = null
    private var advertiser: BluetoothLeAdvertiser? = null
    private var signChar: BluetoothGattCharacteristic? = null
    private var cmdChar: BluetoothGattCharacteristic? = null

    @Volatile private var box: BluetoothDevice? = null       // tủ đang kết nối
    @Volatile private var verified = false                    // đã ký cho tủ trong lần kết nối này
    @Volatile private var lastSignature = ByteArray(0)
    private val main = Handler(Looper.getMainLooper())

    private val noBoxTimeout = Runnable {
        sendUi("READY", "Không thấy tủ trong 3 phút. Kiểm tra tủ đã cắm điện, máy tính đang mở FocusDesk, rồi bấm BẮT ĐẦU HỌC lại.")
        stopSelf()
    }
    private val unplugStop = Runnable {
        sendUi("READY", "Đã rút sạc — app ngừng kết nối với tủ.")
        stopSelf()
    }

    // Cắm / rút cáp sạc -> báo tủ ngay
    private val powerReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            when (intent?.action) {
                Intent.ACTION_POWER_CONNECTED -> { main.removeCallbacks(unplugStop); sendCharge(true) }
                Intent.ACTION_POWER_DISCONNECTED -> { sendCharge(false); main.postDelayed(unplugStop, UNPLUG_STOP_MS) }
            }
        }
    }

    // ------------------------------------------------------------------ vòng đời
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_POWER_CONNECTED)
            addAction(Intent.ACTION_POWER_DISCONNECTED)
        }
        registerReceiver(powerReceiver, filter)
        if (!initBleServer()) {
            sendUi("ERROR", "Bluetooth đang tắt. Hãy bật Bluetooth rồi bấm BẮT ĐẦU HỌC lại.")
            main.post { stopSelf() }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val notification: Notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("FocusDesk đang kết nối với tủ")
            .setContentText("Cất điện thoại vào tủ, cắm sạc và đóng cửa để bắt đầu ca học.")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setPriority(NotificationCompat.PRIORITY_MIN)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        if (gattServer != null && box == null) {
            sendUi("CONNECTING", "Đang tìm tủ… (để điện thoại gần tủ)")
            main.removeCallbacks(noBoxTimeout)
            main.postDelayed(noBoxTimeout, NO_BOX_TIMEOUT_MS)
        }
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        main.removeCallbacks(noBoxTimeout)
        main.removeCallbacks(unplugStop)
        try { unregisterReceiver(powerReceiver) } catch (e: Exception) { }
        stopBleServer()
        super.onDestroy()
    }

    // ------------------------------------------------------------------ tiện ích
    private fun sendUi(state: String, msg: String = "") {
        main.post {
            val intent = Intent(ACTION_UI)
            intent.setPackage(packageName)           // Android 13+: chỉ gửi trong nội bộ app
            intent.putExtra("STATE", state)
            intent.putExtra("MSG", msg)
            sendBroadcast(intent)
        }
    }

    /** Điện thoại có đang cắm nguồn sạc không (kể cả pin đã đầy). */
    private fun isCharging(): Boolean {
        val st = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        return (st?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
    }

    private fun chargeText(charging: Boolean) = if (charging) "CHARGED" else "UNCHARGED"

    private fun sendCharge(charging: Boolean) {
        notifyBox(cmdChar, chargeText(charging).toByteArray())
        if (!verified) return
        if (charging) sendUi("STUDYING", "Đã báo tủ: điện thoại đang sạc. Đóng cửa tủ + vào chế độ Học sinh trên máy tính là bắt đầu ca học.")
        else sendUi("WAITING_POWER", "Hãy cắm cáp sạc trong tủ cho điện thoại.")
    }

    /** Gửi thông báo (notify) giá trị mới tới tủ đang kết nối. */
    private fun notifyBox(ch: BluetoothGattCharacteristic?, value: ByteArray) {
        val c = ch ?: return
        val d = box ?: return
        val server = gattServer ?: return
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                server.notifyCharacteristicChanged(d, c, false, value)
            } else {
                legacyNotify(server, d, c, value)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Gửi tới tủ lỗi: ${e.message}")
        }
    }

    @Suppress("DEPRECATION")
    private fun legacyNotify(server: BluetoothGattServer, d: BluetoothDevice, c: BluetoothGattCharacteristic, v: ByteArray) {
        c.value = v
        server.notifyCharacteristicChanged(d, c, false)
    }

    private fun respond(device: BluetoothDevice, requestId: Int, ok: Boolean, offset: Int = 0, value: ByteArray? = null) {
        gattServer?.sendResponse(device, requestId, if (ok) BluetoothGatt.GATT_SUCCESS else BluetoothGatt.GATT_FAILURE,
            offset, value)
    }

    // ------------------------------------------------------------------ máy chủ BLE
    private fun initBleServer(): Boolean {
        val manager = getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager
        val adapter: BluetoothAdapter = manager.adapter ?: return false
        if (!adapter.isEnabled) return false

        gattServer = manager.openGattServer(this, gattCallback)
        val service = BluetoothGattService(SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY)

        val nonce = BluetoothGattCharacteristic(
            NONCE_UUID,
            BluetoothGattCharacteristic.PROPERTY_WRITE or BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
            BluetoothGattCharacteristic.PERMISSION_WRITE
        )
        val sign = BluetoothGattCharacteristic(
            SIGN_UUID,
            BluetoothGattCharacteristic.PROPERTY_READ or BluetoothGattCharacteristic.PROPERTY_NOTIFY,
            BluetoothGattCharacteristic.PERMISSION_READ
        )
        val pubKey = BluetoothGattCharacteristic(
            PUBKEY_UUID, BluetoothGattCharacteristic.PROPERTY_READ, BluetoothGattCharacteristic.PERMISSION_READ
        )
        val cmd = BluetoothGattCharacteristic(
            CMD_UUID,
            BluetoothGattCharacteristic.PROPERTY_READ or BluetoothGattCharacteristic.PROPERTY_NOTIFY,
            BluetoothGattCharacteristic.PERMISSION_READ
        )
        // Ô CCCD để tủ "đăng ký nhận thông báo" theo chuẩn BLE
        for (c in listOf(sign, cmd)) {
            c.addDescriptor(BluetoothGattDescriptor(CCCD_UUID,
                BluetoothGattDescriptor.PERMISSION_READ or BluetoothGattDescriptor.PERMISSION_WRITE))
        }
        signChar = sign
        cmdChar = cmd
        service.addCharacteristic(nonce)
        service.addCharacteristic(sign)
        service.addCharacteristic(pubKey)
        service.addCharacteristic(cmd)
        gattServer?.addService(service)

        advertiser = adapter.bluetoothLeAdvertiser
        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY)
            .setConnectable(true)
            .setTimeout(0)
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)   // phát mạnh -> tủ tìm thấy nhanh hơn
            .build()
        val data = AdvertiseData.Builder()
            .setIncludeDeviceName(false)
            .addServiceUuid(ParcelUuid(SERVICE_UUID))
            .build()
        advertiser?.startAdvertising(settings, data, advertiseCallback)
        return true
    }

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartFailure(errorCode: Int) {
            sendUi("ERROR", "Không phát sóng Bluetooth được (mã $errorCode). Tắt/bật Bluetooth rồi thử lại.")
        }
    }

    private fun stopBleServer() {
        try { advertiser?.stopAdvertising(advertiseCallback) } catch (e: Exception) { }
        try { box?.let { gattServer?.cancelConnection(it) } } catch (e: Exception) { }
        try { gattServer?.close() } catch (e: Exception) { }
        gattServer = null
        box = null
    }

    private val gattCallback = object : BluetoothGattServerCallback() {

        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                box = device
                verified = false
                main.removeCallbacks(noBoxTimeout)
                sendUi("CONNECTING", "Đã thấy tủ, đang xác minh bảo mật…")
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED && device.address == box?.address) {
                box = null
                verified = false
                sendUi("CONNECTING", "Mất kết nối với tủ — đang chờ tủ kết nối lại…")
                main.removeCallbacks(noBoxTimeout)
                main.postDelayed(noBoxTimeout, NO_BOX_TIMEOUT_MS)
            }
        }

        // Tủ ĐỌC dữ liệu: luôn phải trả lời (không trả lời là tủ bị treo chờ ~30 giây)
        override fun onCharacteristicReadRequest(device: BluetoothDevice, requestId: Int, offset: Int,
                                                 characteristic: BluetoothGattCharacteristic) {
            val full: ByteArray? = when (characteristic.uuid) {
                PUBKEY_UUID -> KeyHelper.publicKey()
                SIGN_UUID -> lastSignature
                CMD_UUID -> chargeText(isCharging()).toByteArray()
                else -> null
            }
            if (full == null) {
                respond(device, requestId, false, offset)
                return
            }
            val part = if (offset < full.size) full.copyOfRange(offset, full.size) else ByteArray(0)
            respond(device, requestId, true, offset, part)
        }

        // Tủ GHI mã ngẫu nhiên -> ký -> gửi chữ ký -> gửi luôn trạng thái sạc hiện tại
        override fun onCharacteristicWriteRequest(device: BluetoothDevice, requestId: Int,
                                                  characteristic: BluetoothGattCharacteristic, preparedWrite: Boolean,
                                                  responseNeeded: Boolean, offset: Int, value: ByteArray) {
            if (characteristic.uuid != NONCE_UUID) {
                if (responseNeeded) respond(device, requestId, false)
                return
            }
            val sig = KeyHelper.sign(value)
            if (sig == null) {
                if (responseNeeded) respond(device, requestId, false)
                sendUi("ERROR", "Không ký được: điện thoại chưa có khóa. Mở app, bấm \"Tạo khóa mới\" rồi ghép lại tủ.")
                return
            }
            if (responseNeeded) respond(device, requestId, true)
            box = device
            lastSignature = sig
            notifyBox(signChar, sig)
            verified = true
            // Sửa lỗi bản cũ: cắm sạc TRƯỚC khi ký thì tủ không bao giờ nhận được "CHARGED"
            main.postDelayed({ sendCharge(isCharging()) }, 300)
        }

        override fun onDescriptorWriteRequest(device: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
                                              preparedWrite: Boolean, responseNeeded: Boolean, offset: Int,
                                              value: ByteArray) {
            if (responseNeeded) respond(device, requestId, true)
        }

        override fun onDescriptorReadRequest(device: BluetoothDevice, requestId: Int, offset: Int,
                                             descriptor: BluetoothGattDescriptor) {
            respond(device, requestId, true, 0, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
        }
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(CHANNEL_ID, "Hệ thống FocusDesk", NotificationManager.IMPORTANCE_MIN)
            getSystemService(NotificationManager::class.java)?.createNotificationChannel(channel)
        }
    }
}
