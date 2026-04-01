import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

class PermissionService {
  static const _channel =
      MethodChannel('com.fentanest.mysafetyreport/permissions');

  // ── 알림 리스너 권한 ──────────────────────────────────────────────────────
  static Future<bool> isNotificationListenerEnabled() async {
    try {
      return await _channel.invokeMethod<bool>('isNotificationListenerEnabled') ?? false;
    } on PlatformException {
      return false;
    }
  }

  static Future<void> openNotificationListenerSettings() async {
    await _channel.invokeMethod('openNotificationListenerSettings');
  }

  // ── 배터리 최적화 제외 ─────────────────────────────────────────────────────
  static Future<bool> isBatteryOptimizationIgnored() async {
    return await Permission.ignoreBatteryOptimizations.isGranted;
  }

  static Future<void> requestIgnoreBatteryOptimizations() async {
    await Permission.ignoreBatteryOptimizations.request();
  }

  // ── 알림 표시 권한 (Android 13+) ─────────────────────────────────────────
  static Future<bool> isNotificationPermissionGranted() async {
    return await Permission.notification.isGranted;
  }

  static Future<void> requestNotificationPermission() async {
    await Permission.notification.request();
  }

  // ── WsService 제어 ────────────────────────────────────────────────────────
  /// 백그라운드 WebSocket 서비스를 시작합니다.
  static Future<bool> startWsService() async {
    try {
      return await _channel.invokeMethod<bool>('startWsService') ?? false;
    } on PlatformException catch (e) {
      // ignore: avoid_print
      print('WsService 시작 오류: ${e.message}');
      return false;
    }
  }

  /// 백그라운드 WebSocket 서비스를 중지합니다.
  static Future<bool> stopWsService() async {
    try {
      return await _channel.invokeMethod<bool>('stopWsService') ?? false;
    } on PlatformException catch (e) {
      // ignore: avoid_print
      print('WsService 중지 오류: ${e.message}');
      return false;
    }
  }

  /// 백그라운드 WebSocket 서비스 실행 여부를 반환합니다.
  static Future<bool> isWsServiceRunning() async {
    try {
      return await _channel.invokeMethod<bool>('isWsServiceRunning') ?? false;
    } on PlatformException {
      return false;
    }
  }
}
