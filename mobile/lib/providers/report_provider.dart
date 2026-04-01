import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/report.dart';
import '../services/api_service.dart';

class ReportProvider with ChangeNotifier {
  String _baseUrl = '';
  String _apiKey = '';
  bool _isLoading = false;
  bool _isInitialized = false;
  String? _errorMessage;
  DashboardStats? _stats;
  List<Report> _trafficReports = [];
  List<Report> _otherReports = [];

  // 필터 상태
  String _filterQuery = '';
  String _filterStatus = '';
  String _filterStartDate = '';
  String _filterEndDate = '';

  String get baseUrl => _baseUrl;
  String get apiKey => _apiKey;
  bool get isLoading => _isLoading;
  bool get isInitialized => _isInitialized;
  bool get isConfigured => _baseUrl.isNotEmpty && _apiKey.isNotEmpty;
  String? get errorMessage => _errorMessage;
  DashboardStats? get stats => _stats;
  List<Report> get trafficReports => _trafficReports;
  List<Report> get otherReports => _otherReports;
  bool get hasFilter =>
      _filterQuery.isNotEmpty ||
      _filterStatus.isNotEmpty ||
      _filterStartDate.isNotEmpty ||
      _filterEndDate.isNotEmpty;

  List<Report> get filteredTrafficReports => _applyFilter(_trafficReports);
  List<Report> get filteredOtherReports => _applyFilter(_otherReports);

  List<Report> _applyFilter(List<Report> reports) {
    return reports.where((r) {
      final q = _filterQuery.toLowerCase();
      final queryMatch = q.isEmpty ||
          r.name.toLowerCase().contains(q) ||
          r.reportNumber.toLowerCase().contains(q) ||
          r.agency.toLowerCase().contains(q) ||
          r.carNumber.toLowerCase().contains(q);

      final statusMatch =
          _filterStatus.isEmpty || r.status == _filterStatus;

      bool dateMatch = true;
      if (_filterStartDate.isNotEmpty && r.date.isNotEmpty) {
        dateMatch = dateMatch && r.date.compareTo(_filterStartDate) >= 0;
      }
      if (_filterEndDate.isNotEmpty && r.date.isNotEmpty) {
        dateMatch = dateMatch && r.date.compareTo(_filterEndDate) <= 0;
      }

      return queryMatch && statusMatch && dateMatch;
    }).toList();
  }

  void setFilter({
    String query = '',
    String status = '',
    String startDate = '',
    String endDate = '',
  }) {
    _filterQuery = query;
    _filterStatus = status;
    _filterStartDate = startDate;
    _filterEndDate = endDate;
    notifyListeners();
  }

  void clearFilter() {
    _filterQuery = '';
    _filterStatus = '';
    _filterStartDate = '';
    _filterEndDate = '';
    notifyListeners();
  }

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _baseUrl = prefs.getString('baseUrl') ?? '';
    _apiKey = prefs.getString('apiKey') ?? '';
    _isInitialized = true;
    notifyListeners();
  }

  Future<void> setConfig(String url, String key) async {
    final cleanUrl =
        url.endsWith('/') ? url.substring(0, url.length - 1) : url;
    _baseUrl = cleanUrl;
    _apiKey = key;
    _errorMessage = null;

    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('baseUrl', _baseUrl);
    await prefs.setString('apiKey', _apiKey);

    notifyListeners();
  }

  ApiService get _api => ApiService(baseUrl: _baseUrl, apiKey: _apiKey);

  Future<void> fetchSummary() async {
    if (!isConfigured) return;
    _isLoading = true;
    _errorMessage = null;
    notifyListeners();
    try {
      _stats = await _api.getSummary();
    } catch (e) {
      _errorMessage = '서버 연결 실패: $e';
      _stats = null;
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> fetchTrafficReports() async {
    if (!isConfigured) return;
    _isLoading = true;
    notifyListeners();
    try {
      _trafficReports = await _api.getReports('traffic');
    } catch (e) {
      _errorMessage = '교통위반 내역 로드 실패: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> fetchOtherReports() async {
    if (!isConfigured) return;
    _isLoading = true;
    notifyListeners();
    try {
      _otherReports = await _api.getReports('other');
    } catch (e) {
      _errorMessage = '기타위반 내역 로드 실패: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }
}
