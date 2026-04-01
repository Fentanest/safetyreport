import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:http/http.dart' as http;
import '../providers/report_provider.dart';
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

  @override
  void initState() {
    super.initState();
    final provider = context.read<ReportProvider>();
    _urlController.text = provider.baseUrl;
    _apiController.text = provider.apiKey;
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

            // ── 권한 설정 카드 ──────────────────────────────
            Card(
              child: InkWell(
                borderRadius: BorderRadius.circular(12),
                onTap: () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => const PermissionScreen(),
                  ),
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
                              '알림 접근, 배터리 최적화 제외 등 앱 권한을 관리합니다.',
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
