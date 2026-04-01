import 'package:flutter/services.dart';
import 'package:permission_handler/permission_handler.dart';

class PermissionService {
  static const _channel =
      MethodChannel('com.fentanest.mysafetyreport/permissions');

  /// 알림 리스너 서비스 활성화 여부
  static Future<bool> isNotificationListenerEnabled() async {
    try {
      return await _channel.invokeMethod('isNotificationListenerEnabled');
    } catch (_) {
      return false;
    }
  }

  /// 알림 리스너 설정 화면 열기
  static Future<void> openNotificationListenerSettings() async {
    try {
      await _channel.invokeMethod('openNotificationListenerSettings');
    } catch (_) {}
  }

  /// 배터리 최적화 제외 요청
  static Future<bool> requestIgnoreBatteryOptimizations() async {
    final status = await Permission.ignoreBatteryOptimizations.request();
    return status.isGranted;
  }

  /// 배터리 최적화 제외 여부
  static Future<bool> isBatteryOptimizationIgnored() async {
    return await Permission.ignoreBatteryOptimizations.isGranted;
  }

  /// 알림 표시 권한 요청 (Android 13+)
  static Future<bool> requestNotificationPermission() async {
    final status = await Permission.notification.request();
    return status.isGranted;
  }

  /// 알림 표시 권한 여부
  static Future<bool> isNotificationPermissionGranted() async {
    return await Permission.notification.isGranted;
  }
}
