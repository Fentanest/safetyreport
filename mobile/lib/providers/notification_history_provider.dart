import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/notification_item.dart';

class NotificationHistoryProvider with ChangeNotifier {
  static const _key = 'notifications_history';

  List<NotificationItem> _items = [];

  List<NotificationItem> get items => List.unmodifiable(_items);
  int get unreadCount => _items.where((i) => !i.isRead).length;

  Future<void> load() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload(); // WsService가 직접 쓴 내용 반영
    final raw = prefs.getString(_key);
    if (raw != null && raw.isNotEmpty) {
      try {
        final list = jsonDecode(raw) as List;
        _items = list
            .map((i) => NotificationItem.fromJson(i as Map<String, dynamic>))
            .toList();
      } catch (_) {
        _items = [];
      }
    }
    notifyListeners();
  }

  Future<void> markRead(String id) async {
    final idx = _items.indexWhere((i) => i.id == id);
    if (idx >= 0 && !_items[idx].isRead) {
      _items[idx] = _items[idx].copyWith(isRead: true);
      await _save();
      notifyListeners();
    }
  }

  Future<void> markAllRead() async {
    _items = _items.map((i) => i.copyWith(isRead: true)).toList();
    await _save();
    notifyListeners();
  }

  Future<void> clearAll() async {
    _items = [];
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
    notifyListeners();
  }

  Future<void> addFromServerResults(List<Map<String, dynamic>> serverData, {bool isMobileTriggered = false}) async {
    final now = DateTime.now();
    final ts = '${now.year}-${now.month.toString().padLeft(2,'0')}-${now.day.toString().padLeft(2,'0')} ${now.hour.toString().padLeft(2,'0')}:${now.minute.toString().padLeft(2,'0')}:${now.second.toString().padLeft(2,'0')}';
    final List<NotificationItem> newItems = [];

    if (serverData.isEmpty) {
      newItems.add(NotificationItem(
        id: '${now.millisecondsSinceEpoch}',
        title: isMobileTriggered ? '📱 크롤링 완료' : '🖥️ 크롤링 완료',
        body: '변경된 신고건이 없습니다.',
        reportNumber: '',
        timestamp: ts,
        isRead: false,
      ));
    } else {
      // 이미 history에 있는 신고번호는 중복 추가 방지
      final existingRnums = _items
          .where((i) => i.reportNumber.isNotEmpty)
          .map((i) => i.reportNumber)
          .toSet();

      for (final r in serverData) {
        final rnum = r['신고번호']?.toString() ?? '';
        if (rnum.isNotEmpty && existingRnums.contains(rnum)) continue;
        final name = r['신고명']?.toString() ?? '신고';
        final status = r['처리상태']?.toString() ?? '';
        final agency = r['처리기관']?.toString() ?? '';
        final fine = r['범칙금_과태료']?.toString() ?? '';
        final changeType = r['change_type']?.toString() ?? '';
        final lines = <String>[];
        if (changeType.isNotEmpty) lines.add('[$changeType]');
        if (rnum.isNotEmpty) lines.add('신고번호: $rnum');
        if (status.isNotEmpty) lines.add('처리상태: $status');
        if (agency.isNotEmpty) lines.add('처리기관: $agency');
        if (fine.isNotEmpty && fine != 'null') lines.add('범칙금/과태료: $fine');
        newItems.add(NotificationItem(
          id: '${now.millisecondsSinceEpoch}_$rnum',
          title: changeType == '신규' ? '🆕 $name' : '🔄 $name',
          body: lines.join('\n'),
          reportNumber: rnum,
          timestamp: ts,
          isRead: false,
          extraData: Map<String, dynamic>.from(r),
        ));
      }
    }

    if (newItems.isEmpty) return;
    _items.insertAll(0, newItems);
    await _save();
    notifyListeners();
  }

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        _key, jsonEncode(_items.map((i) => i.toJson()).toList()));
  }
}
