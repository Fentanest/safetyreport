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
        const val NOTIF_CHANNEL_PUSH = "ws_push_v2"       // 이벤트 알림 채널 (heads-up)
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
                stopForeground(true)
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
                    try { Thread.sleep(10_000) } catch (_: InterruptedException) { break }
                    continue
                }

                // http → ws, https → wss 변환
                val wsUrl = baseUrl
                    .replace(Regex("^https://"), "wss://")
                    .replace(Regex("^http://"), "ws://") +
                    "/ws/events?api_key=$apiKey"

                Log.i(TAG, "WS 연결 시도 #$attempt: $wsUrl")
                updateForegroundNotif("서버 연결 중... (#$attempt)")

                val connected = connectAndBlock(wsUrl)

                if (!running.get()) break

                // 재연결 대기
                val delay = BACKOFF_MS[attempt.coerceAtMost(BACKOFF_MS.size - 1)]
                Log.i(TAG, "WS 연결 종료. ${delay}ms 후 재연결.")
                updateForegroundNotif("서버 연결 대기 중...")
                attempt = if (connected) 0 else (attempt + 1).coerceAtMost(BACKOFF_MS.size - 1)
                try { Thread.sleep(delay) } catch (_: InterruptedException) { break }
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

        try { latch.await() } catch (_: InterruptedException) { Thread.currentThread().interrupt() }
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

        // Flutter가 앱 포어그라운드 복귀 시 카드 뷰로 표시할 수 있도록 저장
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        prefs.edit().putString("flutter.pending_crawl_changes", changes.toString()).apply()

        // 알림 히스토리에도 extraData 포함해서 저장 (신고 결과 탭 표시용)
        saveCrawlChangesToHistory(changes, prefs)

        for (i in 0 until changes.length()) {
            val record     = changes.getJSONObject(i)
            val changeType = record.optString("change_type", "변경")
            val reportNo   = record.optString("신고번호", "")
            val name       = record.optString("신고명", "신고")
            val status     = record.optString("처리상태", "")
            val agency     = record.optString("처리기관", "")
            val fine       = record.optString("범칙금_과태료", "")

            val titlePrefix = if (changeType == "신규") "🆕 신규 신고" else "🔄 처리 변경"

            val bodyLines = mutableListOf<String>()
            if (reportNo.isNotEmpty())                        bodyLines.add("신고번호: $reportNo")
            if (status.isNotEmpty())                          bodyLines.add("처리상태: $status")
            if (agency.isNotEmpty())                          bodyLines.add("처리기관: $agency")
            if (fine.isNotEmpty() && fine != "null")          bodyLines.add("범칙금/과태료: $fine")

            showPushNotif(
                title = "$titlePrefix — $name",
                body  = bodyLines.joinToString("\n"),
                type  = "crawl_changes"
            )
        }
    }

    private fun saveCrawlChangesToHistory(changes: org.json.JSONArray, prefs: android.content.SharedPreferences) {
        try {
            val historyJson = prefs.getString("flutter.notifications_history", "[]") ?: "[]"
            val existing = JSONObject("{\"arr\":$historyJson}").getJSONArray("arr")
            val ts = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.KOREA)
                .format(java.util.Date())
            val newArr = org.json.JSONArray()
            val baseMs = System.currentTimeMillis()

            for (i in 0 until changes.length()) {
                val record     = changes.getJSONObject(i)
                val changeType = record.optString("change_type", "변경")
                val reportNo   = record.optString("신고번호", "")
                val name       = record.optString("신고명", "신고")
                val status     = record.optString("처리상태", "")
                val agency     = record.optString("처리기관", "")
                val fine       = record.optString("범칙금_과태료", "")

                val title = if (changeType == "신규") "🆕 $name" else "🔄 $name"
                val bodyLines = mutableListOf<String>()
                if (changeType.isNotEmpty()) bodyLines.add("[$changeType]")
                if (reportNo.isNotEmpty())   bodyLines.add("신고번호: $reportNo")
                if (status.isNotEmpty())     bodyLines.add("처리상태: $status")
                if (agency.isNotEmpty())     bodyLines.add("처리기관: $agency")
                if (fine.isNotEmpty() && fine != "null") bodyLines.add("범칙금/과태료: $fine")

                val item = JSONObject().apply {
                    put("id",           "${baseMs}_$reportNo")
                    put("title",        title)
                    put("body",         bodyLines.joinToString("\n"))
                    put("reportNumber", reportNo)
                    put("timestamp",    ts)
                    put("isRead",       false)
                    put("extraData",    record)  // 전체 신고 데이터 보존
                }
                newArr.put(item)
            }
            // 기존 항목 이어붙이기 (최대 200개 유지)
            for (i in 0 until minOf(existing.length(), 200 - changes.length())) {
                newArr.put(existing.getJSONObject(i))
            }
            prefs.edit().putString("flutter.notifications_history", newArr.toString()).apply()
        } catch (e: Exception) {
            Log.e(TAG, "crawl_changes 히스토리 저장 오류: ${e.message}")
        }
    }

    private fun showPushNotif(title: String, body: String, type: String) {
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // 앱 열기 인텐트 — SINGLE_TOP으로 onNewIntent 트리거, 앱 재시작 방지
        // nav_event_type 엑스트라로 MainActivity가 알림 탭으로 자동 이동
        val openIntent = packageManager.getLaunchIntentForPackage(packageName)
            ?.apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
                putExtra("nav_tab", 3)        // 알림 탭 인덱스
                putExtra("nav_event_type", type)
            }
        val notifId = pushIdGen.get()
        val pi = PendingIntent.getActivity(
            this, notifId,
            openIntent ?: Intent(),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notif = Notification.Builder(this, NOTIF_CHANNEL_PUSH)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setSmallIcon(R.drawable.ic_stat_logo)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        nm.notify(pushIdGen.getAndIncrement(), notif)

        // 앱이 포그라운드일 때 in-app SnackBar 표시용 — SharedPreferences에 기록
        try {
            val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
            val event = org.json.JSONObject().apply {
                put("title", title)
                put("body", body)
                put("type", type)
            }
            prefs.edit().putString("flutter.foreground_event", event.toString()).apply()
        } catch (_: Exception) {}
    }

    // ─────────────────────────────────────────────────────────────────────────
    // 히스토리 저장 (Flutter SharedPreferences와 공유)
    // ─────────────────────────────────────────────────────────────────────────

    private fun saveToHistory(type: String, data: JSONObject) {
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        val historyJson = prefs.getString("flutter.notifications_history", "[]") ?: "[]"
        try {
            val existing = JSONObject("{\"arr\":$historyJson}").getJSONArray("arr")
            val count = data.optInt("changed_count", 0)
            val title = when (type) {
                "crawl_started"  -> "🔄 크롤링 시작"
                "crawl_finished" -> "✅ 크롤링 완료"
                else             -> type
            }
            val body = when (type) {
                "crawl_started"  -> {
                    val source = data.optString("source", "")
                    val sourceLabel = if (source.startsWith("mobile")) "📱 모바일" else "🖥️ 웹"
                    "$sourceLabel 에서 크롤링이 시작되었습니다."
                }
                "crawl_finished" -> if (count > 0)
                    "크롤링이 완료되었습니다. ${count}건의 변경사항이 있습니다."
                else
                    "크롤링이 완료되었습니다. 변경사항이 없습니다."
                else -> ""
            }
            val item = JSONObject().apply {
                put("id",          System.currentTimeMillis().toString())
                put("title",       title)
                put("body",        body)
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
        // 알림 탭 시 앱 실행
        val openIntent = packageManager.getLaunchIntentForPackage(packageName)
            ?.apply { flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP }
        val openPi = PendingIntent.getActivity(
            this, 0, openIntent ?: Intent(),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        // 서비스 중지 버튼
        val stopIntent = Intent(this, WsService::class.java).apply { action = ACTION_STOP }
        val stopPi = PendingIntent.getService(
            this, 1, stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        return Notification.Builder(this, NOTIF_CHANNEL_WS)
            .setContentTitle("나만의 안전신문고")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_stat_logo)
            .setOngoing(true)
            .setContentIntent(openPi)
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

        // 이벤트 푸시 알림 채널 (높은 중요도 — heads-up 팝업 표시)
        val pushChannel = NotificationChannel(
            NOTIF_CHANNEL_PUSH,
            "크롤링 이벤트 알림",
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = "크롤링 시작/완료 및 변경사항 알림"
            enableVibration(true)
        }
        nm.createNotificationChannel(pushChannel)
    }
}
