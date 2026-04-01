package com.fentanest.mysafetyreport

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.util.Log
import java.util.regex.Pattern
import org.json.JSONObject

class NotificationService : NotificationListenerService() {
    private val TAG = "SafetyReportNS"
    private val CHANNEL_ID = "safetyreport_results"

    // 신고번호 패턴 (202x로 시작하는 10~14자리)
    private val reportNoPattern = Pattern.compile("202[0-9]{7,11}")

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

        if (packageName == "kr.go.mss.safetyreport" || packageName == "com.kakao.talk") {
            extractAndProcess(text)
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
        val baseUrl = prefs.getString("flutter.baseUrl", "")?.trimEnd('/')
        val apiKey = prefs.getString("flutter.apiKey", "") ?: ""

        if (baseUrl.isNullOrEmpty()) {
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
                    pollForResult(baseUrl, apiKey, reportNumber)
                }
            } catch (e: Exception) {
                Log.e(TAG, "서버 전송 오류: ${e.message}")
            }
        }.start()
    }

    private fun pollForResult(baseUrl: String, apiKey: String, reportNumber: String) {
        val maxAttempts = 10
        val intervalMs = 30_000L

        for (attempt in 1..maxAttempts) {
            Thread.sleep(intervalMs)
            try {
                val summaryUrl = java.net.URL("$baseUrl/api/v1/summary")
                val conn = summaryUrl.openConnection() as java.net.HttpURLConnection
                conn.setRequestProperty("X-API-Key", apiKey)
                conn.connectTimeout = 8000
                conn.readTimeout = 8000

                val code = conn.responseCode
                if (code == 200) {
                    val responseText = conn.inputStream.bufferedReader().readText()
                    conn.disconnect()

                    val json = JSONObject(responseText)
                    val data = json.optJSONObject("data") ?: continue

                    // recent_answers에서 해당 신고번호 검색
                    val recentList = data.optJSONArray("recent_answers")
                    if (recentList != null) {
                        for (i in 0 until recentList.length()) {
                            val item = recentList.getJSONObject(i)
                            if (item.optString("신고번호") == reportNumber) {
                                val name = item.optString("신고명", "신고")
                                val status = item.optString("처리상태", "")
                                val agency = item.optString("처리기관", "")
                                showLocalNotification(
                                    title = "📋 신고 처리 완료",
                                    text = "$name\n처리상태: $status | $agency",
                                    reportNumber = reportNumber
                                )
                                Log.i(TAG, "크롤링 완료 알림 표시: $reportNumber")
                                return
                            }
                        }
                    }

                    // recent_answers에 없으면 total 증가로 판단
                    if (attempt >= 3) {
                        val total = data.optInt("total", 0)
                        if (total > 0) {
                            showLocalNotification(
                                title = "📋 안전신문고 업데이트",
                                text = "신고번호 $reportNumber 처리 결과가 업데이트되었습니다.",
                                reportNumber = reportNumber
                            )
                            return
                        }
                    }
                } else {
                    conn.disconnect()
                }
            } catch (e: Exception) {
                Log.w(TAG, "폴링 오류 (${attempt}회): ${e.message}")
            }
        }
        Log.w(TAG, "폴링 최대 횟수 초과: $reportNumber")
    }

    private fun showLocalNotification(title: String, text: String, reportNumber: String = "") {
        val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notification = android.app.Notification.Builder(this, CHANNEL_ID)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(android.app.Notification.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setAutoCancel(true)
            .build()
        manager.notify(System.currentTimeMillis().toInt(), notification)
        saveToHistory(title, text, reportNumber)
    }

    private fun saveToHistory(title: String, body: String, reportNumber: String) {
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
