package com.fentanest.mysafetyreport

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.IBinder
import android.util.Log
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

/**
 * WsService — 백그라운드에서 서버의 WebSocket 이벤트를 수신하는 Foreground Service
 *
 * - 앱이 완전히 종료되어도 OS에 의해 살아있음 (Foreground Service의 지속 알림으로 보장)
 * - OkHttp WebSocket 클라이언트로 ws://<baseUrl>/ws/events?api_key=<key> 연결 유지
 * - 네트워크 오류/서버 재시작 시 지수 백오프로 자동 재연결
 * - 수신 이벤트:
 *   - crawl_started  → "크롤링 시작됨" 알림
 *   - crawl_finished → "크롤링 완료, N건 변경" 알림
 *   - crawl_changes  → 개별 신고 변경 상세 알림
 *   - ping           → pong 응답 (연결 유지)
 */
class WsService : Service() {

    companion object {
        const val TAG = "WsService"
        const val NOTIF_CHANNEL_WS   = "ws_service"       // 서비스 지속 알림 채널
        const val NOTIF_CHANNEL_PUSH = "ws_push"          // 이벤트 알림 채널
        const val FOREGROUND_NOTIF_ID = 1001              // 지속 알림 ID (고정)
        const val ACTION_START = "ACTION_WS_START"
        const val ACTION_STOP  = "ACTION_WS_STOP"

        // 재연결 지연: 3초 → 6초 → 12초 → … 최대 60초
        private val BACKOFF_MS = longArrayOf(3_000, 6_000, 12_000, 24_000, 60_000)
    }

    private val running   = AtomicBoolean(false)
    private val pushIdGen = AtomicInteger(2000)

    private var okClient: OkHttpClient? = null
    private var activeWs: WebSocket? = null
    private var reconnectThread: Thread? = null

    // ─────────────────────────────────────────────────────────────────────────
    // Service 생명주기
    // ─────────────────────────────────────────────────────────────────────────

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            else -> {
                if (!running.get()) {
                    startForeground(FOREGROUND_NOTIF_ID, buildForegroundNotif("서버에 연결 중..."))
                    running.set(true)
                    startWsLoop()
                }
            }
        }
        return START_STICKY   // 시스템이 강제 종료해도 재시작
    }

    override fun onDestroy() {
        running.set(false)
        activeWs?.cancel()
        okClient?.dispatcher?.executorService?.shutdown()
        reconnectThread?.interrupt()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    // ─────────────────────────────────────────────────────────────────────────
    // WebSocket 연결 루프 (지수 백오프 재연결)
    // ─────────────────────────────────────────────────────────────────────────

    private fun startWsLoop() {
        reconnectThread = Thread {
            var attempt = 0
            while (running.get()) {
                val prefs   = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
                val baseUrl = prefs.getString("flutter.baseUrl", "")?.trimEnd('/') ?: ""
                val apiKey  = prefs.getString("flutter.apiKey",  "") ?: ""

                if (baseUrl.isEmpty() || apiKey.isEmpty()) {
                    Log.w(TAG, "baseUrl/apiKey 미설정. 10초 후 재시도.")
                    Thread.sleep(10_000)
                    continue
                }

                // http → ws, https → wss 변환
                val wsUrl = baseUrl
                    .replace(Regex("^https://"), "wss://")
                    .replace(Regex("^http://"),  "ws://")
                    + "/ws/events?api_key=$apiKey"

                Log.i(TAG, "WS 연결 시도 #$attempt: $wsUrl")
                updateForegroundNotif("서버 연결 중... (#$attempt)")

                val connected = connectAndBlock(wsUrl)

                if (!running.get()) break

                // 재연결 대기
                val delay = BACKOFF_MS[attempt.coerceAtMost(BACKOFF_MS.size - 1)]
                Log.i(TAG, "WS 연결 종료. ${delay}ms 후 재연결.")
                updateForegroundNotif("연결 끊김. ${delay / 1000}초 후 재연결...")
                attempt = if (connected) 0 else (attempt + 1).coerceAtMost(BACKOFF_MS.size - 1)
                Thread.sleep(delay)
            }
        }.also { it.isDaemon = true; it.start() }
    }

    /**
     * 단일 WebSocket 연결 시도. 연결이 끊어질 때까지 블로킹.
     * @return 정상 연결 후 종료되었으면 true, 연결 실패면 false
     */
    private fun connectAndBlock(url: String): Boolean {
        val latch = java.util.concurrent.CountDownLatch(1)
        var connected = false

        val client = OkHttpClient.Builder()
            .pingInterval(25, TimeUnit.SECONDS)    // 서버가 죽었을 때 감지
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.SECONDS)      // 무한 대기 (스트리밍)
            .build()
        okClient = client

        val request = Request.Builder().url(url).build()
        val ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                connected = true
                activeWs = webSocket
                Log.i(TAG, "WS 연결 성공")
                updateForegroundNotif("서버 연결됨 ✓")
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                handleEvent(text, webSocket)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS 종료 중: $code $reason")
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(TAG, "WS 닫힘: $code")
                latch.countDown()
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(TAG, "WS 오류: ${t.message}")
                latch.countDown()
            }
        })
        activeWs = ws

        latch.await()
        client.dispatcher.executorService.shutdown()
        return connected
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 이벤트 처리
    // ─────────────────────────────────────────────────────────────────────────

    private fun handleEvent(text: String, ws: WebSocket) {
        try {
            val json = JSONObject(text)
            val type = json.optString("type", "")
            val data = json.optJSONObject("data") ?: JSONObject()

            Log.d(TAG, "WS 이벤트: $type")

            when (type) {
                "ping"           -> ws.send("pong")
                "connected"      -> Log.i(TAG, "서버 연결 확인: ${data.optString("message")}")
                "crawl_started"  -> showCrawlStartedNotif(data)
                "crawl_finished" -> showCrawlFinishedNotif(data)
                "crawl_changes"  -> showCrawlChangesNotif(data)
                else             -> Log.d(TAG, "알 수 없는 이벤트: $type")
            }

            // 알림 히스토리 저장 (crawl_started, crawl_finished만)
            if (type in listOf("crawl_started", "crawl_finished")) {
                saveToHistory(type, data)
            }
        } catch (e: Exception) {
            Log.e(TAG, "이벤트 파싱 오류: ${e.message}")
        }
    }

    private fun showCrawlStartedNotif(data: JSONObject) {
        val mode = data.optString("crawl_mode", "full")
        val type = data.optString("crawl_type", "api")
        val source = data.optString("source", "")
        val sourceLabel = if (source.startsWith("mobile")) "📱 모바일" else "🖥️ 웹"
        showPushNotif(
            title = "🔄 크롤링 시작",
            body  = "$sourceLabel 에서 크롤링이 시작되었습니다.\n모드: $mode / 방식: $type",
            type  = "crawl_started"
        )
    }

    private fun showCrawlFinishedNotif(data: JSONObject) {
        val count = data.optInt("changed_count", 0)
        val body = if (count > 0) {
            "크롤링이 완료되었습니다. ${count}건의 변경사항이 있습니다."
        } else {
            "크롤링이 완료되었습니다. 변경사항이 없습니다."
        }
        showPushNotif(
            title = "✅ 크롤링 완료",
            body  = body,
            type  = "crawl_finished"
        )
    }

    private fun showCrawlChangesNotif(data: JSONObject) {
        val changes = data.optJSONArray("changes") ?: return
        for (i in 0 until changes.length()) {
            val record = changes.getJSONObject(i)
            val reportNo = record.optString("신고번호", "")
            val name     = record.optString("신고명", "신고")
            val status   = record.optString("처리상태", "")
            showPushNotif(
                title = "📋 $name",
                body  = "신고번호: $reportNo\n처리상태: $status",
                type  = "crawl_changes"
            )
        }
    }

    private fun showPushNotif(title: String, body: String, type: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // 앱 열기 인텐트
        val openIntent = packageManager.getLaunchIntentForPackage(packageName)
        val pi = PendingIntent.getActivity(
            this, pushIdGen.get(),
            openIntent ?: Intent(),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notif = Notification.Builder(this, NOTIF_CHANNEL_PUSH)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        nm.notify(pushIdGen.getAndIncrement(), notif)
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 히스토리 저장 (Flutter SharedPreferences와 공유)
    // ─────────────────────────────────────────────────────────────────────────

    private fun saveToHistory(type: String, data: JSONObject) {
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        val historyJson = prefs.getString("flutter.notifications_history", "[]") ?: "[]"
        try {
            val existing = JSONObject("{\"arr\":$historyJson}").getJSONArray("arr")
            val title = when (type) {
                "crawl_started"  -> "🔄 크롤링 시작"
                "crawl_finished" -> "✅ 크롤링 완료 (${data.optInt("changed_count", 0)}건 변경)"
                else             -> type
            }
            val item = JSONObject().apply {
                put("id",          System.currentTimeMillis().toString())
                put("title",       title)
                put("body",        data.toString())
                put("reportNumber","")
                put("timestamp",   java.text.SimpleDateFormat(
                    "yyyy-MM-dd HH:mm:ss", java.util.Locale.KOREA
                ).format(java.util.Date()))
                put("isRead", false)
            }
            val newArr = org.json.JSONArray()
            newArr.put(item)
            for (i in 0 until minOf(existing.length(), 99)) {
                newArr.put(existing.getJSONObject(i))
            }
            prefs.edit().putString("flutter.notifications_history", newArr.toString()).apply()
        } catch (e: Exception) {
            Log.e(TAG, "히스토리 저장 오류: ${e.message}")
        }
    }

    // ─────────────────────────────────────────────────────────────────────────
    // Foreground 알림 관리
    // ─────────────────────────────────────────────────────────────────────────

    private fun buildForegroundNotif(text: String): Notification {
        // 서비스 중지 버튼
        val stopIntent = Intent(this, WsService::class.java).apply { action = ACTION_STOP }
        val stopPi = PendingIntent.getService(
            this, 0, stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return Notification.Builder(this, NOTIF_CHANNEL_WS)
            .setContentTitle("나만의 안전신문고")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setOngoing(true)
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "중지", stopPi)
            .build()
    }

    private fun updateForegroundNotif(text: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(FOREGROUND_NOTIF_ID, buildForegroundNotif(text))
    }

    private fun createNotificationChannels() {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // 서비스 지속 알림 채널 (낮은 중요도 — 소리 없음)
        val wsChannel = NotificationChannel(
            NOTIF_CHANNEL_WS,
            "안전신문고 백그라운드 연결",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "서버와의 WebSocket 연결 상태를 표시합니다."
            setShowBadge(false)
        }
        nm.createNotificationChannel(wsChannel)

        // 이벤트 푸시 알림 채널 (기본 중요도)
        val pushChannel = NotificationChannel(
            NOTIF_CHANNEL_PUSH,
            "크롤링 이벤트 알림",
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = "크롤링 시작/완료 및 변경사항 알림"
        }
        nm.createNotificationChannel(pushChannel)
    }
}
