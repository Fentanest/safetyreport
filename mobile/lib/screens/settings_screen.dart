import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:http/http.dart' as http;
import 'package:url_launcher/url_launcher.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';
import '../services/permission_service.dart';
import 'permission_screen.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _urlController = TextEditingController();
  final _apiController = TextEditingController();
  bool _obscureKey = true;
  bool _testing = false;
  _TestResult? _testResult;
  bool _wsRunning = false;
  bool _wsToggling = false;

  // 기타 데이터 필터 세팅
  bool _excludeWithdraw = true;
  bool _normalizePolice = true;
  bool _autoExportExcel = true;
  bool _autoExportSheet = true;
  bool _filterLoading = false;

  @override
  void initState() {
    super.initState();
    final provider = context.read<ReportProvider>();
    _urlController.text = provider.baseUrl;
    _apiController.text = provider.apiKey;
    _checkWsStatus();
    _loadFilterSettings();
  }

  ApiService? _buildApi() {
    final p = context.read<ReportProvider>();
    if (p.baseUrl.isEmpty) return null;
    return ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
  }

  Future<void> _loadFilterSettings() async {
    final api = _buildApi();
    if (api == null) return;
    try {
      final cfg = await api.getAppConfig();
      if (mounted) {
        setState(() {
          _excludeWithdraw = cfg['exclude_withdraw'] as bool? ?? true;
          _normalizePolice = cfg['normalize_police'] as bool? ?? true;
          _autoExportExcel = cfg['auto_export_excel'] as bool? ?? true;
          _autoExportSheet = cfg['auto_export_sheet'] as bool? ?? true;
        });
      }
    } catch (_) {}
  }

  Future<void> _toggleFilter(String key, bool value) async {
    setState(() => _filterLoading = true);
    final api = _buildApi();
    if (api != null) {
      try {
        await api.updateSettings({key: value});
        // 데이터 새로고침
        if (mounted) context.read<ReportProvider>().refreshAll();
      } catch (_) {}
    }
    if (mounted) setState(() => _filterLoading = false);
  }

  Future<void> _checkWsStatus() async {
    final running = await PermissionService.isWsServiceRunning();
    if (mounted) setState(() => _wsRunning = running);
  }

  Future<void> _toggleWsService() async {
    setState(() => _wsToggling = true);
    if (_wsRunning) {
      await PermissionService.stopWsService();
    } else {
      await PermissionService.startWsService();
    }
    await Future.delayed(const Duration(seconds: 1));
    await _checkWsStatus();
    setState(() => _wsToggling = false);
  }

  @override
  void dispose() {
    _urlController.dispose();
    _apiController.dispose();
    super.dispose();
  }

  Future<void> _testConnection() async {
    final url = _urlController.text.trim();
    final key = _apiController.text.trim();
    if (url.isEmpty || key.isEmpty) {
      setState(() {
        _testResult = _TestResult.error('URL과 API 키를 모두 입력해주세요.');
      });
      return;
    }

    setState(() {
      _testing = true;
      _testResult = null;
    });

    final cleanUrl = url.endsWith('/') ? url.substring(0, url.length - 1) : url;

    try {
      final response = await http
          .get(
            Uri.parse('$cleanUrl/api/v1/summary'),
            headers: {'X-API-Key': key, 'Content-Type': 'application/json'},
          )
          .timeout(const Duration(seconds: 10));

      final status = response.statusCode;
      String body = response.body;
      if (body.length > 300) body = '${body.substring(0, 300)}...';

      if (status == 200) {
        try {
          final json = jsonDecode(response.body);
          final total = json['data']?['total'] ?? '?';
          setState(() {
            _testResult = _TestResult.success('연결 성공! 총 $total건 조회됨');
          });
        } catch (_) {
          setState(() {
            _testResult = _TestResult.warn(
              '상태 $status 응답 수신, JSON 파싱 실패\n응답: $body',
            );
          });
        }
      } else if (status == 401) {
        setState(() {
          _testResult = _TestResult.error(
            'API 키 인증 실패 (401)\nAPI 키를 확인해주세요.\n응답: $body',
          );
        });
      } else if (status == 302 || (status == 200 && body.contains('<html'))) {
        setState(() {
          _testResult = _TestResult.error(
            '로그인 페이지로 리다이렉트됨 ($status)\n서버의 /api/v1/ 경로가 세션 인증을 우회하도록 설정되어 있는지 확인하세요.\n응답: $body',
          );
        });
      } else {
        setState(() {
          _testResult = _TestResult.warn(
            '예상치 못한 응답: $status\n$body',
          );
        });
      }
    } on Exception catch (e) {
      setState(() {
        _testResult = _TestResult.error('연결 실패: $e');
      });
    } finally {
      setState(() => _testing = false);
    }
  }

  Future<void> _save() async {
    final url = _urlController.text.trim();
    final key = _apiController.text.trim();
    if (url.isEmpty || key.isEmpty) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('모든 필드를 입력해주세요.')),
      );
      return;
    }
    final provider = context.read<ReportProvider>();
    await provider.setConfig(url, key);
    // 설정 변경 후 모든 데이터 새로고침
    provider.refreshAll();
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('설정이 저장되었습니다. 데이터를 불러오는 중...'),
          backgroundColor: Colors.green,
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;

    return Scaffold(
      appBar: AppBar(title: const Text('앱 설정')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // ── 서버 연결 카드 ─────────────────────────────
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.dns_rounded, color: cs.primary),
                        const SizedBox(width: 8),
                        Text(
                          '서버 연결',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: cs.primary,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      'Cloudflare Tunnel 또는 서버 주소를 입력하세요.',
                      style: TextStyle(color: Colors.grey, fontSize: 13),
                    ),
                    const SizedBox(height: 16),
                    TextField(
                      controller: _urlController,
                      decoration: InputDecoration(
                        labelText: '서버 URL',
                        hintText: 'https://example.com',
                        border: const OutlineInputBorder(),
                        prefixIcon: const Icon(Icons.link),
                        suffixIcon: IconButton(
                          icon: const Icon(Icons.clear, size: 18),
                          onPressed: () => _urlController.clear(),
                        ),
                      ),
                      keyboardType: TextInputType.url,
                      autocorrect: false,
                    ),
                    const SizedBox(height: 14),
                    TextField(
                      controller: _apiController,
                      decoration: InputDecoration(
                        labelText: 'API Key',
                        hintText: 'sk-...',
                        border: const OutlineInputBorder(),
                        prefixIcon: const Icon(Icons.vpn_key),
                        suffixIcon: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            IconButton(
                              icon: Icon(
                                _obscureKey
                                    ? Icons.visibility_off
                                    : Icons.visibility,
                                size: 20,
                              ),
                              onPressed: () =>
                                  setState(() => _obscureKey = !_obscureKey),
                            ),
                            IconButton(
                              icon: const Icon(Icons.copy, size: 18),
                              tooltip: '복사',
                              onPressed: () {
                                Clipboard.setData(
                                  ClipboardData(text: _apiController.text),
                                );
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(
                                      content: Text('API 키가 복사되었습니다.')),
                                );
                              },
                            ),
                          ],
                        ),
                      ),
                      obscureText: _obscureKey,
                      autocorrect: false,
                    ),
                    const SizedBox(height: 16),
                    // 연결 테스트 결과
                    if (_testResult != null) _buildTestResult(_testResult!),
                    if (_testResult != null) const SizedBox(height: 12),
                    // 버튼 행
                    Row(
                      children: [
                        Expanded(
                          child: OutlinedButton.icon(
                            icon: _testing
                                ? const SizedBox(
                                    width: 16,
                                    height: 16,
                                    child: CircularProgressIndicator(
                                        strokeWidth: 2),
                                  )
                                : const Icon(Icons.wifi_find, size: 18),
                            label: Text(_testing ? '테스트 중...' : '연결 테스트'),
                            onPressed: _testing ? null : _testConnection,
                          ),
                        ),
                        const SizedBox(width: 12),
                        Expanded(
                          child: FilledButton.icon(
                            icon: const Icon(Icons.save, size: 18),
                            label: const Text('저장'),
                            onPressed: _save,
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            // ── 기타 데이터 필터 세팅 카드 ───────────────────
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.filter_list, color: cs.primary),
                        const SizedBox(width: 8),
                        Text(
                          '기타 데이터 필터 세팅',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: cs.primary,
                          ),
                        ),
                        if (_filterLoading) ...[
                          const SizedBox(width: 8),
                          const SizedBox(
                            width: 14, height: 14,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      '웹앱 설정과 동기화됩니다. 변경 시 데이터가 즉시 갱신됩니다.',
                      style: TextStyle(color: Colors.grey, fontSize: 12),
                    ),
                    const SizedBox(height: 12),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('취하 데이터 숨기기', style: TextStyle(fontSize: 14)),
                      subtitle: const Text('처리상태가 취하인 신고를 목록에서 제외합니다.', style: TextStyle(fontSize: 12)),
                      value: _excludeWithdraw,
                      onChanged: _filterLoading ? null : (v) {
                        setState(() => _excludeWithdraw = v);
                        _toggleFilter('exclude_withdraw', v);
                      },
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('경찰 기관명 정규화', style: TextStyle(fontSize: 14)),
                      subtitle: const Text('처리기관명을 "XX경찰서" 형태로 통일합니다.', style: TextStyle(fontSize: 12)),
                      value: _normalizePolice,
                      onChanged: _filterLoading ? null : (v) {
                        setState(() => _normalizePolice = v);
                        _toggleFilter('normalize_police', v);
                      },
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            // ── 크롤링 자동 저장 카드 ──────────────────────
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.save_outlined, color: cs.primary),
                        const SizedBox(width: 8),
                        Text(
                          '크롤링 자동 저장',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: cs.primary,
                          ),
                        ),
                        if (_filterLoading) ...[
                          const SizedBox(width: 8),
                          const SizedBox(
                            width: 14, height: 14,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        ],
                      ],
                    ),
                    const SizedBox(height: 4),
                    const Text(
                      '크롤링 완료 후 자동으로 내보내기를 실행합니다.',
                      style: TextStyle(color: Colors.grey, fontSize: 12),
                    ),
                    const SizedBox(height: 12),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('엑셀 자동 저장', style: TextStyle(fontSize: 14)),
                      subtitle: const Text('크롤링 완료 후 서버에 Excel 파일을 자동 생성합니다.', style: TextStyle(fontSize: 12)),
                      value: _autoExportExcel,
                      onChanged: _filterLoading ? null : (v) {
                        setState(() => _autoExportExcel = v);
                        _toggleFilter('auto_export_excel', v);
                      },
                    ),
                    SwitchListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text('구글 스프레드시트 자동 업로드', style: TextStyle(fontSize: 14)),
                      subtitle: const Text('크롤링 완료 후 구글 시트에 자동 업로드합니다.', style: TextStyle(fontSize: 12)),
                      value: _autoExportSheet,
                      onChanged: _filterLoading ? null : (v) {
                        setState(() => _autoExportSheet = v);
                        _toggleFilter('auto_export_sheet', v);
                      },
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            // ── 앱 정보 카드 ──────────────────────────────
            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(Icons.info_outline, color: cs.secondary),
                        const SizedBox(width: 8),
                        Text(
                          '앱 정보',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: cs.secondary,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 12),
                    const _InfoRow(label: '앱 버전', value: 'v1.0.0'),
                    const _InfoRow(label: '플랫폼', value: 'Android / iOS'),
                    const SizedBox(height: 8),
                    const Text(
                      '※ 인터넷 권한(INTERNET)은 Android 일반 권한으로 설치 시 별도 요청 없이 자동 부여됩니다.',
                      style: TextStyle(fontSize: 11, color: Colors.grey),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            Card(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          Icons.wifi_tethering,
                          color: _wsRunning ? Colors.green : Colors.grey,
                        ),
                        const SizedBox(width: 8),
                        const Expanded(
                          child: Text(
                            '백그라운드 서버 연결',
                            style: TextStyle(
                                fontSize: 16, fontWeight: FontWeight.bold),
                          ),
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: _wsRunning
                                ? Colors.green.shade50
                                : Colors.red.shade50,
                            borderRadius: BorderRadius.circular(20),
                            border: Border.all(
                              color: _wsRunning
                                  ? Colors.green.shade300
                                  : Colors.red.shade200,
                            ),
                          ),
                          child: Text(
                            _wsRunning ? '● 실행 중' : '○ 중지됨',
                            style: TextStyle(
                              fontSize: 12,
                              fontWeight: FontWeight.bold,
                              color: _wsRunning
                                  ? Colors.green.shade700
                                  : Colors.red.shade700,
                            ),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 6),
                    const Text(
                      '앱 종료 후에도 크롤링 시작·완료 이벤트를 실시간으로 알림으로 받습니다.\n상단 상태바에 지속 알림이 표시됩니다.',
                      style: TextStyle(color: Colors.grey, fontSize: 12, height: 1.5),
                    ),
                    const SizedBox(height: 14),
                    SizedBox(
                      width: double.infinity,
                      child: _wsToggling
                          ? const Center(
                              child: Padding(
                                padding: EdgeInsets.all(8),
                                child: CircularProgressIndicator(strokeWidth: 2),
                              ),
                            )
                          : OutlinedButton.icon(
                              icon: Icon(
                                _wsRunning ? Icons.stop_circle_outlined : Icons.play_circle_outline,
                                size: 18,
                              ),
                              label: Text(_wsRunning ? '서비스 중지' : '서비스 시작'),
                              style: OutlinedButton.styleFrom(
                                foregroundColor:
                                    _wsRunning ? Colors.red : Colors.green,
                                side: BorderSide(
                                  color: _wsRunning ? Colors.red : Colors.green,
                                ),
                              ),
                              onPressed: _toggleWsService,
                            ),
                    ),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 16),

            Card(
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(builder: (_) => const PermissionScreen()),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Row(
                    children: [
                      Icon(Icons.security, color: cs.tertiary ?? cs.primary),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '권한 설정',
                              style: TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.bold,
                                color: cs.tertiary ?? cs.primary,
                              ),
                            ),
                            const SizedBox(height: 2),
                            const Text(
                              '알림 접근, 배터리 최적화 제외, 백그라운드 서비스 등 권한을 관리합니다.',
                              style: TextStyle(color: Colors.grey, fontSize: 13),
                            ),
                          ],
                        ),
                      ),
                      const Icon(Icons.chevron_right, color: Colors.grey),
                    ],
                  ),
                ),
              ),
            ),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                icon: const Icon(Icons.language, size: 18),
                label: const Text('홈페이지 바로가기'),
                onPressed: () async {
                  final url = Uri.parse('https://hb.worklazy.net/mysafetyreport/');
                  await launchUrl(url, mode: LaunchMode.externalApplication);
                },
              ),
            ),
            const SizedBox(height: 16),
          ],
        ),
      ),
    );
  }

  Widget _buildTestResult(_TestResult result) {
    Color bg, fg;
    IconData icon;
    switch (result.type) {
      case _ResultType.success:
        bg = Colors.green.shade50;
        fg = Colors.green.shade800;
        icon = Icons.check_circle;
        break;
      case _ResultType.warn:
        bg = Colors.orange.shade50;
        fg = Colors.orange.shade800;
        icon = Icons.warning;
        break;
      case _ResultType.error:
        bg = Colors.red.shade50;
        fg = Colors.red.shade800;
        icon = Icons.error;
        break;
    }

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: fg.withOpacity(0.3)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: fg, size: 20),
          const SizedBox(width: 8),
          Expanded(
            child: SelectableText(
              result.message,
              style: TextStyle(color: fg, fontSize: 12, height: 1.5),
            ),
          ),
        ],
      ),
    );
  }
}

class _TestResult {
  final _ResultType type;
  final String message;
  const _TestResult.success(this.message) : type = _ResultType.success;
  const _TestResult.warn(this.message) : type = _ResultType.warn;
  const _TestResult.error(this.message) : type = _ResultType.error;
}

enum _ResultType { success, warn, error }

class _InfoRow extends StatelessWidget {
  final String label;
  final String value;
  const _InfoRow({required this.label, required this.value});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          SizedBox(
            width: 80,
            child: Text(label,
                style: const TextStyle(color: Colors.grey, fontSize: 13)),
          ),
          Text(value,
              style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13)),
        ],
      ),
    );
  }
}
