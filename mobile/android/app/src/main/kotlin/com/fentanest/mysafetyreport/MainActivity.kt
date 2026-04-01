package com.fentanest.mysafetyreport

import android.content.Intent
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private val CHANNEL = "com.fentanest.mysafetyreport/permissions"

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
            .setMethodCallHandler { call, result ->
                when (call.method) {

                    // ── 알림 리스너 권한 ────────────────────────────────────
                    "isNotificationListenerEnabled" -> {
                        val flat = Settings.Secure.getString(
                            contentResolver,
                            "enabled_notification_listeners"
                        )
                        result.success(flat != null && flat.contains(packageName))
                    }
                    "openNotificationListenerSettings" -> {
                        startActivity(
                            Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        )
                        result.success(null)
                    }

                    // ── WsService 제어 ─────────────────────────────────────
                    "startWsService" -> {
                        try {
                            val intent = Intent(this, WsService::class.java).apply {
                                action = WsService.ACTION_START
                            }
                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                startForegroundService(intent)
                            } else {
                                startService(intent)
                            }
                            result.success(true)
                        } catch (e: Exception) {
                            result.error("WS_START_FAILED", e.message, null)
                        }
                    }
                    "stopWsService" -> {
                        try {
                            val intent = Intent(this, WsService::class.java).apply {
                                action = WsService.ACTION_STOP
                            }
                            startService(intent)
                            result.success(true)
                        } catch (e: Exception) {
                            result.error("WS_STOP_FAILED", e.message, null)
                        }
                    }
                    "isWsServiceRunning" -> {
                        val am = getSystemService(ACTIVITY_SERVICE) as android.app.ActivityManager
                        @Suppress("DEPRECATION")
                        val running = am.getRunningServices(Int.MAX_VALUE).any {
                            it.service.className == WsService::class.java.name
                        }
                        result.success(running)
                    }

                    else -> result.notImplemented()
                }
            }
    }

    override fun onResume() {
        super.onResume()
        // 앱이 포그라운드로 돌아올 때 WsService 자동 시작 (설정이 완료된 경우)
        autoStartWsServiceIfConfigured()
    }

    private fun autoStartWsServiceIfConfigured() {
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        val baseUrl = prefs.getString("flutter.baseUrl", "") ?: ""
        val apiKey  = prefs.getString("flutter.apiKey",  "") ?: ""
        if (baseUrl.isNotEmpty() && apiKey.isNotEmpty()) {
            val intent = Intent(this, WsService::class.java).apply {
                action = WsService.ACTION_START
            }
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    startForegroundService(intent)
                } else {
                    startService(intent)
                }
            } catch (_: Exception) {}
        }
    }
}
