package com.fentanest.mysafetyreport

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.provider.Settings
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.util.concurrent.atomic.AtomicInteger

class MainActivity : FlutterActivity() {
    private val CHANNEL = "com.fentanest.mysafetyreport/permissions"
    private val notifIdGen = AtomicInteger(3000)
    private val NOTIF_CHANNEL_APP = "app_push_v2"
    private var methodChannel: MethodChannel? = null

    override fun onCreate(savedInstanceState: android.os.Bundle?) {
        super.onCreate(savedInstanceState)
        createAppNotifChannel()
        // 앱이 종료 상태에서 알림 탭으로 실행된 경우 처리
        intent?.let { handleNavIntent(it) }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleNavIntent(intent)
    }

    private fun handleNavIntent(intent: Intent) {
        val navTab = intent.getIntExtra("nav_tab", -1)
        val eventType = intent.getStringExtra("nav_event_type") ?: ""
        if (navTab >= 0) {
            // MethodChannel이 준비되기 전(앱 콜드 스타트) 처리를 위해 약간 지연
            android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
                methodChannel?.invokeMethod("navigateToTab", mapOf(
                    "tab" to navTab,
                    "event_type" to eventType
                ))
            }, 500)
        }
    }

    private fun createAppNotifChannel() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(NOTIF_CHANNEL_APP) == null) {
            val ch = NotificationChannel(
                NOTIF_CHANNEL_APP, "앱 알림", NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "크롤링 완료 등 앱 이벤트 알림"
                enableVibration(true)
            }
            nm.createNotificationChannel(ch)
        }
    }

    private fun showLocalNotification(title: String, body: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val openIntent = packageManager.getLaunchIntentForPackage(packageName)
        val pi = PendingIntent.getActivity(
            this, notifIdGen.get(), openIntent ?: Intent(),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        val notif = Notification.Builder(this, NOTIF_CHANNEL_APP)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setSmallIcon(R.drawable.ic_stat_logo)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()
        nm.notify(notifIdGen.getAndIncrement(), notif)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)

        val channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL)
        methodChannel = channel
        channel.setMethodCallHandler { call, result ->
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

                    "showNotification" -> {
                        val title = call.argument<String>("title") ?: "알림"
                        val body  = call.argument<String>("body")  ?: ""
                        showLocalNotification(title, body)
                        result.success(null)
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
