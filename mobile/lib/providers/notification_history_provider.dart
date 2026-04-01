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

  Future<void> _save() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        _key, jsonEncode(_items.map((i) => i.toJson()).toList()));
  }
}
