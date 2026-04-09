import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/report.dart';
import '../services/api_service.dart';

class ReportFilter {
  final String name;
  final String reportNumber;
  final String agency;
  final String manager;
  final String carNumber;
  final String law;
  final String location;
  final String fine;
  final String reportContent;
  final String processContent;
  final String status;
  final String reportDateStart;
  final String reportDateEnd;
  final String occurDateStart;
  final String occurDateEnd;
  final String responseDateStart;
  final String responseDateEnd;
  final String occurTimeStart;
  final String occurTimeEnd;
  final bool excludePolice;
  final bool onlyPolice;

  const ReportFilter({
    this.name = '',
    this.reportNumber = '',
    this.agency = '',
    this.manager = '',
    this.carNumber = '',
    this.law = '',
    this.location = '',
    this.fine = '',
    this.reportContent = '',
    this.processContent = '',
    this.status = '',
    this.reportDateStart = '',
    this.reportDateEnd = '',
    this.occurDateStart = '',
    this.occurDateEnd = '',
    this.responseDateStart = '',
    this.responseDateEnd = '',
    this.occurTimeStart = '',
    this.occurTimeEnd = '',
    this.excludePolice = false,
    this.onlyPolice = false,
  });

  bool get isEmpty =>
      name.isEmpty &&
      reportNumber.isEmpty &&
      agency.isEmpty &&
      manager.isEmpty &&
      carNumber.isEmpty &&
      law.isEmpty &&
      location.isEmpty &&
      fine.isEmpty &&
      reportContent.isEmpty &&
      processContent.isEmpty &&
      status.isEmpty &&
      reportDateStart.isEmpty &&
      reportDateEnd.isEmpty &&
      occurDateStart.isEmpty &&
      occurDateEnd.isEmpty &&
      responseDateStart.isEmpty &&
      responseDateEnd.isEmpty &&
      occurTimeStart.isEmpty &&
      occurTimeEnd.isEmpty &&
      !excludePolice &&
      !onlyPolice;

  /// 활성 필터 항목 요약 (Chip 표시용)
  List<String> get activeLabels {
    final list = <String>[];
    if (name.isNotEmpty) list.add('신고명: $name');
    if (reportNumber.isNotEmpty) list.add('신고번호: $reportNumber');
    if (agency.isNotEmpty) list.add('기관: $agency');
    if (manager.isNotEmpty) list.add('담당자: $manager');
    if (carNumber.isNotEmpty) list.add('차량: $carNumber');
    if (law.isNotEmpty) list.add('위반법규: $law');
    if (location.isNotEmpty) list.add('위반장소: $location');
    if (fine.isNotEmpty) list.add('과태료: $fine');
    if (reportContent.isNotEmpty) list.add('신고내용: $reportContent');
    if (processContent.isNotEmpty) list.add('처리내용: $processContent');
    if (status.isNotEmpty) list.add('상태: $status');
    if (reportDateStart.isNotEmpty || reportDateEnd.isNotEmpty)
      list.add('신고일: $reportDateStart~$reportDateEnd');
    if (occurDateStart.isNotEmpty || occurDateEnd.isNotEmpty)
      list.add('발생일: $occurDateStart~$occurDateEnd');
    if (responseDateStart.isNotEmpty || responseDateEnd.isNotEmpty)
      list.add('답변일: $responseDateStart~$responseDateEnd');
    if (occurTimeStart.isNotEmpty || occurTimeEnd.isNotEmpty)
      list.add('발생시각: $occurTimeStart~$occurTimeEnd');
    if (excludePolice) list.add('경찰기관 제외');
    if (onlyPolice) list.add('경찰기관만');
    return list;
  }
}

class ReportProvider with ChangeNotifier {
  String _baseUrl = '';
  String _apiKey = '';
  bool _isLoading = false;
  bool _isInitialized = false;
  String? _errorMessage;
  DashboardStats? _stats;
  List<Report> _trafficReports = [];
  List<Report> _parkingReports = [];
  List<Report> _otherReports = [];
  List<Report> _duplicateReports = [];
  Set<String> _watchlistNumbers = {};

  ReportFilter _filter = const ReportFilter();

  String get baseUrl => _baseUrl;
  String get apiKey => _apiKey;
  bool get isLoading => _isLoading;
  bool get isInitialized => _isInitialized;
  bool get isConfigured => _baseUrl.isNotEmpty && _apiKey.isNotEmpty;
  String? get errorMessage => _errorMessage;
  DashboardStats? get stats => _stats;
  List<Report> get trafficReports => _trafficReports;
  List<Report> get parkingReports => _parkingReports;
  List<Report> get otherReports => _otherReports;
  List<Report> get duplicateReports => _duplicateReports;
  Set<String> get watchlistNumbers => _watchlistNumbers;
  bool isInWatchlist(String reportNumber) => _watchlistNumbers.contains(reportNumber);
  ReportFilter get filter => _filter;
  bool get hasFilter => !_filter.isEmpty;

  List<Report> get filteredTrafficReports => _applyFilter(_trafficReports);
  List<Report> get filteredParkingReports => _applyFilter(_parkingReports);
  List<Report> get filteredOtherReports => _applyFilter(_otherReports);
  // 중복차량은 서버에서 이미 그룹/정렬되므로 필터 미적용
  List<Report> get filteredDuplicateReports => _duplicateReports;

  bool _contains(String source, String query) =>
      query.isEmpty || source.toLowerCase().contains(query.toLowerCase());

  bool _dateGte(String value, String bound) =>
      bound.isEmpty || value.isEmpty || value.compareTo(bound) >= 0;

  bool _dateLte(String value, String bound) =>
      bound.isEmpty || value.isEmpty || value.compareTo(bound) <= 0;

  List<Report> _applyFilter(List<Report> reports) {
    final f = _filter;
    return reports.where((r) {
      if (!_contains(r.name, f.name)) return false;
      if (!_contains(r.reportNumber, f.reportNumber)) return false;
      if (!_contains(r.agency, f.agency)) return false;
      if (!_contains(r.manager, f.manager)) return false;
      if (!_contains(r.carNumber, f.carNumber)) return false;
      if (!_contains(r.law, f.law)) return false;
      if (!_contains(r.location, f.location)) return false;
      if (!_contains(r.fineInfo, f.fine)) return false;
      if (!_contains(r.reportContent, f.reportContent)) return false;
      if (!_contains(r.processContent, f.processContent)) return false;
      if (f.status.isNotEmpty && r.status != f.status) return false;
      if (!_dateGte(r.date, f.reportDateStart)) return false;
      if (!_dateLte(r.date, f.reportDateEnd)) return false;
      if (!_dateGte(r.occurrenceDate, f.occurDateStart)) return false;
      if (!_dateLte(r.occurrenceDate, f.occurDateEnd)) return false;
      if (!_dateGte(r.responseDate, f.responseDateStart)) return false;
      if (!_dateLte(r.responseDate, f.responseDateEnd)) return false;
      if (!_dateGte(r.occurrenceTime, f.occurTimeStart)) return false;
      if (!_dateLte(r.occurrenceTime, f.occurTimeEnd)) return false;
      if (f.excludePolice && r.agency.contains('경찰')) return false;
      if (f.onlyPolice && !r.agency.contains('경찰')) return false;
      return true;
    }).toList();
  }

  void setFilter(ReportFilter filter) {
    _filter = filter;
    notifyListeners();
  }

  void clearFilter() {
    _filter = const ReportFilter();
    notifyListeners();
  }

  Future<void> init() async {
    final prefs = await SharedPreferences.getInstance();
    _baseUrl = prefs.getString('baseUrl') ?? '';
    _apiKey = prefs.getString('apiKey') ?? '';
    _isInitialized = true;
    notifyListeners();
    if (isConfigured) fetchWatchlistNumbers();
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

  Future<void> fetchParkingReports() async {
    if (!isConfigured) return;
    _isLoading = true;
    notifyListeners();
    try {
      _parkingReports = await _api.getReports('parking');
    } catch (e) {
      _errorMessage = '주정차위반 내역 로드 실패: $e';
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

  Future<void> fetchDuplicateReports() async {
    if (!isConfigured) return;
    _isLoading = true;
    notifyListeners();
    try {
      _duplicateReports = await _api.getReports('duplicates');
    } catch (e) {
      _errorMessage = '중복차량 내역 로드 실패: $e';
    } finally {
      _isLoading = false;
      notifyListeners();
    }
  }

  Future<void> fetchWatchlistNumbers() async {
    if (!isConfigured) return;
    try {
      final reports = await _api.getWatchlist();
      _watchlistNumbers = reports.map((r) => r.reportNumber).toSet();
      notifyListeners();
    } catch (_) {}
  }

  Future<void> addToWatchlist(List<String> reportNumbers) async {
    await _api.updateWatchlist(reportNumbers, add: true);
    _watchlistNumbers.addAll(reportNumbers);
    notifyListeners();
  }

  Future<void> removeFromWatchlist(List<String> reportNumbers) async {
    await _api.updateWatchlist(reportNumbers, add: false);
    _watchlistNumbers.removeAll(reportNumbers);
    notifyListeners();
  }

  Future<void> enqueueCrawl(String reportNumber) async {
    await _api.enqueueCrawl(reportNumber);
  }

  Future<void> startCrawlQueue(List<String> reportNumbers) async {
    String crawlType = 'api';
    String crawlMode = 'full';
    int maxEmptyPages = 3;
    try {
      final config = await _api.getCrawlConfig();
      crawlType = config['crawl_type']?.toString() ?? 'api';
      crawlMode = config['crawl_mode']?.toString() ?? 'full';
      maxEmptyPages = (config['max_empty_pages'] as num?)?.toInt() ?? 3;
    } catch (_) {}
    await _api.startCrawl(
      loginMode: 'member',
      crawlType: crawlType,
      crawlMode: crawlMode,
      maxEmptyPages: maxEmptyPages,
      queueList: reportNumbers.join('\n'),
    );
  }

  Future<void> refreshAll() async {
    if (!isConfigured) return;
    _errorMessage = null;
    await Future.wait([
      fetchSummary(),
      fetchTrafficReports(),
      fetchParkingReports(),
      fetchOtherReports(),
      fetchWatchlistNumbers(),
    ]);
  }
}
