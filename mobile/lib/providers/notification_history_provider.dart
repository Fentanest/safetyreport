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
      for (final r in serverData) {
        final rnum = r['신고번호']?.toString() ?? '';
        final name = r['신고명']?.toString() ?? '신고';
        final status = r['처리상태']?.toString() ?? '';
        final agency = r['처리기관']?.toString() ?? '';
        final fine = r['범칙금_과태료']?.toString() ?? '';
        final lines = <String>[];
        if (rnum.isNotEmpty) lines.add('신고번호: $rnum');
        if (status.isNotEmpty) lines.add('처리상태: $status');
        if (agency.isNotEmpty) lines.add('처리기관: $agency');
        if (fine.isNotEmpty && fine != '미확인' && fine != 'null') lines.add('범칙금/과태료: $fine');
        newItems.add(NotificationItem(
          id: '${now.millisecondsSinceEpoch}_$rnum',
          title: '📋 $name',
          body: lines.join('\n'),
          reportNumber: rnum,
          timestamp: ts,
          isRead: false,
        ));
      }
    }

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
