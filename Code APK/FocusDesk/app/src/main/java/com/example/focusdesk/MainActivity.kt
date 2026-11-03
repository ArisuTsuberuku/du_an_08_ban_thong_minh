package com.example.focusdesk

import android.Manifest
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import com.example.focusdesk.ui.theme.FocusDeskTheme

class MainActivity : ComponentActivity() {

    private val requestPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions -> Log.d("FocusDesk", "Quyền: $permissions") }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        requestRuntimePermissions()
        // Kiểm tra KHÓA THẬT trong Keystore (không tin cờ "đã cài" — cài lại app / đổi máy sẽ mất khóa)
        val hasKey = KeyHelper.hasKey()

        setContent {
            FocusDeskTheme {
                Scaffold(modifier = Modifier.fillMaxSize()) { innerPadding ->
                    MainScreen(
                        hasKeyAtStart = hasKey,
                        modifier = Modifier.padding(innerPadding),
                        onSetup = { setupFirstTime() },
                        onStart = { startBle() },
                        onNewKey = { newKey() },
                        onHide = { moveTaskToBack(true) },
                        onStop = { stopService(Intent(this, BleService::class.java)) }
                    )
                }
            }
        }
    }

    private fun requestRuntimePermissions() {
        val list = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            list.add(Manifest.permission.BLUETOOTH_CONNECT)
            list.add(Manifest.permission.BLUETOOTH_ADVERTISE)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) list.add(Manifest.permission.POST_NOTIFICATIONS)
        if (list.isNotEmpty()) requestPermissionLauncher.launch(list.toTypedArray())
    }

    /** Lần đầu: bỏ tối ưu pin + tạo khóa. Trả về true nếu tạo khóa được. */
    private fun setupFirstTime(): Boolean {
        requestIgnoreBatteryOptimizations()
        val ok = KeyHelper.ensureKey()
        if (ok) startBle()
        return ok
    }

    private fun startBle() {
        ContextCompat.startForegroundService(this, Intent(this, BleService::class.java))
    }

    /** Tạo khóa mới (đổi máy / nghi lộ khóa). Tủ phải được phụ huynh bấm "Ghép điện thoại mới" lại. */
    private fun newKey(): Boolean {
        stopService(Intent(this, BleService::class.java))
        KeyHelper.deleteKey()
        return KeyHelper.generate()
    }

    @Suppress("BatteryLife")
    private fun requestIgnoreBatteryOptimizations() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            try {
                startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                    data = Uri.parse("package:$packageName")
                })
            } catch (e: Exception) {
                Log.e("FocusDesk", "Không mở được cài đặt pin: ${e.message}")
            }
        }
    }
}

@Composable
fun MainScreen(
    hasKeyAtStart: Boolean,
    modifier: Modifier = Modifier,
    onSetup: () -> Boolean,
    onStart: () -> Unit,
    onNewKey: () -> Boolean,
    onHide: () -> Unit,
    onStop: () -> Unit
) {
    val context = LocalContext.current
    var state by remember { mutableStateOf(if (hasKeyAtStart) "READY" else "SETUP") }
    var msg by remember { mutableStateOf("") }
    var code by remember { mutableStateOf(if (hasKeyAtStart) KeyHelper.pairCodePretty() else "") }
    var askNewKey by remember { mutableStateOf(false) }

    // Nhận trạng thái từ BleService
    DisposableEffect(context) {
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                intent?.getStringExtra("STATE")?.let { state = it }
                msg = intent?.getStringExtra("MSG") ?: ""
            }
        }
        val filter = IntentFilter(BleService.ACTION_UI)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            context.registerReceiver(receiver, filter)
        }
        onDispose { context.unregisterReceiver(receiver) }
    }

    if (askNewKey) {
        AlertDialog(
            onDismissRequest = { askNewKey = false },
            title = { Text("Tạo khóa mới?") },
            text = { Text("Mã ghép sẽ đổi. Phụ huynh phải vào web → Tủ điện thoại → \"Ghép điện thoại mới\" và nhập mã mới thì tủ mới nhận điện thoại này.") },
            confirmButton = {
                TextButton(onClick = {
                    askNewKey = false
                    if (onNewKey()) {
                        code = KeyHelper.pairCodePretty(); state = "READY"; msg = "Đã tạo khóa mới. Gửi mã ghép cho phụ huynh."
                    } else {
                        state = "ERROR"; msg = "Không tạo được khóa trên máy này."
                    }
                }) { Text("Tạo khóa mới") }
            },
            dismissButton = { TextButton(onClick = { askNewKey = false }) { Text("Hủy") } }
        )
    }

    Column(
        modifier = modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text("FocusDesk", style = MaterialTheme.typography.headlineMedium, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(8.dp))
        if (code.isNotEmpty()) {
            Text("Mã ghép của điện thoại", style = MaterialTheme.typography.bodyMedium)
            Text(code, fontSize = 40.sp, fontWeight = FontWeight.Bold, color = Color(0xFF1565C0))
            Text("Phụ huynh nhập mã này ở web: Tủ điện thoại → Ghép điện thoại mới",
                style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
            Spacer(Modifier.height(24.dp))
        }

        when (state) {
            "SETUP" -> {
                Text("Chào mừng! Bấm để tạo khóa bảo mật cho điện thoại này.", textAlign = TextAlign.Center)
                Spacer(Modifier.height(16.dp))
                Button(onClick = {
                    if (onSetup()) {
                        code = KeyHelper.pairCodePretty(); state = "CONNECTING"; msg = "Đang tìm tủ…"
                    } else {
                        state = "ERROR"; msg = "Không tạo được khóa bảo mật trên máy này."
                    }
                }) { Text("Liên kết bàn học lần đầu") }
            }

            "READY", "ERROR" -> {
                if (state == "ERROR") Text(msg, color = Color(0xFFD32F2F), textAlign = TextAlign.Center)
                else if (msg.isNotEmpty()) Text(msg, textAlign = TextAlign.Center)
                Spacer(Modifier.height(16.dp))
                Button(onClick = { state = "CONNECTING"; msg = "Đang tìm tủ…"; onStart() },
                    modifier = Modifier.size(width = 220.dp, height = 60.dp)) { Text("BẮT ĐẦU HỌC") }
                Spacer(Modifier.height(12.dp))
                OutlinedButton(onClick = { state = "CONNECTING"; msg = "Đang chờ tủ đọc khóa…"; onStart() }) {
                    Text("Liên kết lại tủ")
                }
                TextButton(onClick = { askNewKey = true }) { Text("Tạo khóa mới…") }
            }

            "CONNECTING" -> {
                CircularProgressIndicator()
                Spacer(Modifier.height(16.dp))
                Text(msg.ifEmpty { "Đang kết nối & xác minh bảo mật…" }, textAlign = TextAlign.Center)
                Spacer(Modifier.height(16.dp))
                TextButton(onClick = { onStop(); state = "READY"; msg = "" }) { Text("Dừng") }
            }

            "WAITING_POWER" -> {
                Text("✅ Xác minh thành công!\n\nHãy cắm cáp sạc trong tủ cho điện thoại rồi đóng cửa tủ.",
                    color = Color(0xFFD32F2F), textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
            }

            "STUDYING" -> {
                Text("📚 Đã sẵn sàng học!\n\nĐiện thoại đang sạc trong tủ. Đóng cửa tủ là bắt đầu ca học — lấy điện thoại ra trước giờ sẽ bị tính vi phạm.",
                    color = Color(0xFF388E3C), textAlign = TextAlign.Center, style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.height(16.dp))
                Button(onClick = onHide) { Text("Ẩn app") }
            }
        }
    }
}
