package com.fentanest.mysafetyreport

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import org.json.JSONObject
import java.util.concurrent.atomic.AtomicInteger
import java.util.regex.Pattern

class NotificationService : NotificationListenerService() {
    private val TAG = "SafetyReportNS"
    private val CHANNEL_ID = "safetyreport_results"
    private val notifIdCounter = AtomicInteger(1000)

    // 신고번호 패턴 (SPP-YYMM-NNNNNNN 형식, 예: SPP-2603-1434237)
    private val reportNoPattern = Pattern.compile("SPP-\\d{4}-\\d{6,8}")

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onNotificationPosted(sbn: StatusBarNotification) {
        val packageName = sbn.packageName
        val extras = sbn.notification.extras
        val title = extras.getString("android.title") ?: ""
        val text = extras.getCharSequence("android.text")?.toString() ?: ""

        Log.d(TAG, "알림 수신: $packageName")
        Log.d(TAG, "제목: $title, 내용: $text")

        if (packageName == "kr.go.safepeople" || packageName == "com.kakao.talk") {
            extractAndProcess("$title $text")
        }
    }

    private fun extractAndProcess(text: String) {
        val matcher = reportNoPattern.matcher(text)
        while (matcher.find()) {
            val reportNumber = matcher.group()
            Log.i(TAG, "신고번호 추출: $reportNumber")
            sendToBackend(reportNumber)
        }
    }

    private fun sendToBackend(reportNumber: String) {
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        val baseUrl = prefs.getString("flutter.baseUrl", "")?.trimEnd('/') ?: ""
        val apiKey = prefs.getString("flutter.apiKey", "") ?: ""

        if (baseUrl.isEmpty()) {
            Log.w(TAG, "baseUrl 미설정, 서버 전송 건너뜀")
            return
        }

        Thread {
            try {
                // 1. 크롤링 요청 전송
                val enqueueUrl = java.net.URL("$baseUrl/api/v1/crawl/enqueue")
                val conn = enqueueUrl.openConnection() as java.net.HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.setRequestProperty("X-API-Key", apiKey)
                conn.doOutput = true

                val body = "{\"report_number\": \"$reportNumber\"}"
                conn.outputStream.use { os ->
                    os.write(body.toByteArray(Charsets.UTF_8))
                }

                val enqueueStatus = conn.responseCode
                Log.i(TAG, "큐 전송 응답: $enqueueStatus")
                conn.disconnect()

                if (enqueueStatus == 200) {
                    // 2. 크롤링 완료 폴링 (30초 간격, 최대 10회 = 5분)
                    pollForCrawlResults(baseUrl, apiKey)
                }
            } catch (e: Exception) {
                Log.e(TAG, "서버 전송 오류: ${e.message}")
            }
        }.start()
    }

    private fun pollForCrawlResults(baseUrl: String, apiKey: String) {
        val maxAttempts = 10
        val intervalMs = 30_000L

        for (attempt in 1..maxAttempts) {
            Thread.sleep(intervalMs)
            try {
                val url = java.net.URL("$baseUrl/api/v1/crawl/results")
                val conn = url.openConnection() as java.net.HttpURLConnection
                conn.setRequestProperty("X-API-Key", apiKey)
                conn.connectTimeout = 8000
                conn.readTimeout = 8000

                val code = conn.responseCode
                if (code == 200) {
                    val responseText = conn.inputStream.bufferedReader().readText()
                    conn.disconnect()

                    val json = JSONObject(responseText)
                    val count = json.optInt("count", 0)
                    if (count > 0) {
                        val dataArr = json.optJSONArray("data")
                        if (dataArr != null) {
                            for (i in 0 until dataArr.length()) {
                                showReportNotification(dataArr.getJSONObject(i))
                            }
                        }
                        Log.i(TAG, "크롤링 결과 알림 표시: ${count}건")
                        return
                    }
                } else {
                    conn.disconnect()
                }
            } catch (e: Exception) {
                Log.w(TAG, "폴링 오류 (${attempt}회): ${e.message}")
            }
        }
        Log.w(TAG, "폴링 최대 횟수 초과")
    }

    private fun showReportNotification(record: JSONObject) {
        val id = record.optString("ID", "")
        val reportNumber = record.optString("신고번호", "")
        val name = record.optString("신고명", "신고")
        val status = record.optString("처리상태", "")
        val agency = record.optString("처리기관", "")
        val manager = record.optString("담당자", "")
        val fine = record.optString("범칙금_과태료", "")
        val penalty = record.optString("벌점", "")
        val responseDate = record.optString("답변일", "")
        val carNumber = record.optString("차량번호", "")
        val reportDate = record.optString("신고일", "")
        val law = record.optString("위반법규", "")
        val location = record.optString("위반장소", "")
        val occurDate = record.optString("발생일자", "")
        val occurTime = record.optString("발생시각", "")

        val bodyLines = mutableListOf<String>()
        if (reportNumber.isNotEmpty()) bodyLines.add("신고번호: $reportNumber")
        if (status.isNotEmpty()) bodyLines.add("처리상태: $status")
        if (agency.isNotEmpty()) bodyLines.add("처리기관: $agency")
        if (manager.isNotEmpty()) bodyLines.add("담당자: $manager")
        if (responseDate.isNotEmpty()) bodyLines.add("답변일: $responseDate")
        if (fine.isNotEmpty() && fine != "미확인" && fine != "null") bodyLines.add("범칙금/과태료: $fine")
        if (penalty.isNotEmpty() && penalty != "null") bodyLines.add("벌점: $penalty")
        if (carNumber.isNotEmpty()) bodyLines.add("차량번호: $carNumber")
        if (reportDate.isNotEmpty()) bodyLines.add("신고일: $reportDate")
        if (law.isNotEmpty()) bodyLines.add("위반법규: $law")
        if (location.isNotEmpty()) bodyLines.add("위반장소: $location")
        if (occurDate.isNotEmpty()) bodyLines.add("발생일자: $occurDate")
        if (occurTime.isNotEmpty()) bodyLines.add("발생시각: $occurTime")

        val bodyText = bodyLines.joinToString("\n")
        val notificationManager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

        // 안전신문고 앱 딥링크 액션 버튼
        val deepLinkUri = Uri.parse(
            "appsafetyreport://view?c_no=$id&ext_path=M_MY_01_S0002.html&mem_yn=Y"
        )
        val deepLinkIntent = Intent(Intent.ACTION_VIEW, deepLinkUri)
        val requestCode = (System.currentTimeMillis() % Int.MAX_VALUE).toInt()
        val pendingIntent = PendingIntent.getActivity(
            this, requestCode, deepLinkIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification = android.app.Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("📋 $name")
            .setContentText("처리상태: $status")
            .setStyle(android.app.Notification.BigTextStyle().bigText(bodyText))
            .setSmallIcon(R.drawable.ic_stat_logo)
            .setAutoCancel(true)
            .addAction(android.R.drawable.ic_menu_view, "안전신문고에서 보기", pendingIntent)
            .build()

        notificationManager.notify(notifIdCounter.getAndIncrement(), notification)
        saveToHistory("📋 $name", bodyText, reportNumber, record)
    }

    private fun saveToHistory(title: String, body: String, reportNumber: String, extraData: JSONObject? = null) {
        val prefs = getSharedPreferences("FlutterSharedPreferences", MODE_PRIVATE)
        val historyJson = prefs.getString("flutter.notifications_history", "[]") ?: "[]"
        try {
            val existing = JSONObject("{\"arr\":$historyJson}").getJSONArray("arr")
            val item = JSONObject().apply {
                put("id", System.currentTimeMillis().toString())
                put("title", title)
                put("body", body)
                put("reportNumber", reportNumber)
                put("timestamp", java.text.SimpleDateFormat(
                    "yyyy-MM-dd HH:mm:ss", java.util.Locale.KOREA
                ).format(java.util.Date()))
                put("isRead", false)
                if (extraData != null) put("extraData", extraData)
            }
            // 새 항목을 맨 앞에 추가, 최대 100개 유지
            val newArr = JSONObject().put("arr", org.json.JSONArray())
            val arr = newArr.getJSONArray("arr")
            arr.put(item)
            for (i in 0 until minOf(existing.length(), 99)) {
                arr.put(existing.getJSONObject(i))
            }
            prefs.edit().putString("flutter.notifications_history", arr.toString()).apply()
            Log.i(TAG, "알림 히스토리 저장: $title")
        } catch (e: Exception) {
            Log.e(TAG, "알림 히스토리 저장 오류: ${e.message}")
        }
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "안전신문고 처리 결과",
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = "크롤링 완료 후 처리 결과 알림"
        }
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(channel)
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification) {}
}
