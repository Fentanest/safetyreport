import 'dart:convert';
import 'package:http/http.dart' as http;
import '../models/report.dart';
import '../models/file_item.dart';
import '../models/agency_stats.dart';

class ApiService {
  final String baseUrl;
  final String apiKey;

  ApiService({required this.baseUrl, required this.apiKey});

  Map<String, String> get _headers => {
    'Content-Type': 'application/json',
    'X-API-Key': apiKey,
  };

  Future<DashboardStats> getSummary() async {
    final response = await http.get(Uri.parse('$baseUrl/api/v1/summary'), headers: _headers);
    if (response.statusCode == 200) {
      final json = jsonDecode(response.body);
      return DashboardStats.fromJson(json['data']);
    } else {
      throw Exception('Failed to load summary');
    }
  }

  Future<List<Report>> getReports(String category) async {
    final response = await http.get(Uri.parse('$baseUrl/api/v1/reports/$category'), headers: _headers);
    if (response.statusCode == 200) {
      final json = jsonDecode(response.body);
      var list = json['data'] as List? ?? [];
      return list.map((i) => Report.fromJson(i)).toList();
    } else {
      throw Exception('Failed to load reports');
    }
  }

  Future<List<FileItem>> getFiles(String path) async {
    final uri = Uri.parse('$baseUrl/api/v1/files').replace(
      queryParameters: path.isNotEmpty ? {'path': path} : null,
    );
    final response = await http.get(uri, headers: _headers);
    if (response.statusCode == 200) {
      final json = jsonDecode(response.body);
      final list = json['data'] as List? ?? [];
      return list.map((i) => FileItem.fromJson(i)).toList();
    } else {
      throw Exception('파일 목록 로드 실패: ${response.statusCode}');
    }
  }

  Future<AgencyStats> getStats() async {
    final response =
        await http.get(Uri.parse('$baseUrl/api/v1/stats'), headers: _headers);
    if (response.statusCode == 200) {
      final json = jsonDecode(response.body);
      return AgencyStats.fromJson(json['data'] as Map<String, dynamic>);
    } else {
      throw Exception('통계 로드 실패: ${response.statusCode}');
    }
  }

  Future<List<Report>> getWatchlist() async {
    final response = await http.get(
        Uri.parse('$baseUrl/api/v1/watchlist'), headers: _headers);
    if (response.statusCode == 200) {
      final json = jsonDecode(response.body);
      final list = json['data'] as List? ?? [];
      return list.map((i) => Report.fromJson(i)).toList();
    } else {
      throw Exception('감시 목록 로드 실패: ${response.statusCode}');
    }
  }

  Future<void> updateWatchlist(List<String> reportNumbers,
      {bool add = false}) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/watchlist'),
      headers: _headers,
      body: jsonEncode({
        'report_numbers': reportNumbers,
        'action': add ? 'add' : 'remove',
      }),
    );
    if (response.statusCode != 200) {
      throw Exception('감시 목록 업데이트 실패: ${response.statusCode}');
    }
  }

  Future<void> enqueueCrawl(String reportNumber) async {
    final response = await http.post(
      Uri.parse('$baseUrl/api/v1/crawl/enqueue'),
      headers: _headers,
      body: jsonEncode({'report_number': reportNumber}),
    );
    if (response.statusCode != 200) {
      throw Exception('Failed to enqueue crawl');
    }
  }
}
