package com.example.focusdesk

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.content.ContextCompat

class PowerConnectionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_POWER_CONNECTED) {
            Log.d("FocusDesk_Power", "Đã cắm sạc! Đang tự động đánh thức hệ thống bảo mật...")

            // Tự động khởi chạy BleService ngầm để trao đổi chữ ký với ESP32
            val serviceIntent = Intent(context, BleService::class.java)
            ContextCompat.startForegroundService(context, serviceIntent)
        } else if (intent.action == Intent.ACTION_POWER_DISCONNECTED) {
            Log.d("FocusDesk_Power", "Đã rút sạc.")
            // Tùy chọn: Dừng BleService nếu muốn tiết kiệm pin tối đa
            // context.stopService(Intent(context, BleService::class.java))
        }
    }
}