import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import 'permission_screen.dart';

class SetupScreen extends StatefulWidget {
  const SetupScreen({super.key});

  @override
  State<SetupScreen> createState() => _SetupScreenState();
}

class _SetupScreenState extends State<SetupScreen> {
  final _urlController = TextEditingController();
  final _apiController = TextEditingController();
  bool _loading = false;
  String? _errorMessage;

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

  Future<void> _connect() async {
    final url = _urlController.text.trim();
    final key = _apiController.text.trim();
    if (url.isEmpty || key.isEmpty) {
      setState(() => _errorMessage = '서버 URL과 API Key를 모두 입력해주세요.');
      return;
    }

    setState(() {
      _loading = true;
      _errorMessage = null;
    });

    final cleanUrl = url.endsWith('/') ? url.substring(0, url.length - 1) : url;
    try {
      final response = await http.get(
        Uri.parse('$cleanUrl/api/v1/summary'),
        headers: {'X-API-Key': key, 'Content-Type': 'application/json'},
      ).timeout(const Duration(seconds: 10));

      if (response.statusCode == 200) {
        try {
          final json = jsonDecode(response.body);
          final total = json['data']?['total'] ?? '?';
          if (!mounted) return;
          await context.read<ReportProvider>().setConfig(cleanUrl, key);
          if (!mounted) return;
          Navigator.pushReplacement(
            context,
            MaterialPageRoute(
              builder: (_) => const PermissionScreen(isSetup: true),
            ),
          );
          return;
        } catch (_) {
          setState(() => _errorMessage = '서버 응답 파싱 실패. 올바른 서버인지 확인해주세요.');
        }
      } else if (response.statusCode == 401) {
        setState(() => _errorMessage = 'API Key 인증 실패 (401)\nAPI Key를 다시 확인해주세요.');
      } else {
        setState(() => _errorMessage = '서버 오류: HTTP ${response.statusCode}\nURL을 다시 확인해주세요.');
      }
    } catch (e) {
      setState(() => _errorMessage = '서버에 연결할 수 없습니다.\nURL을 확인하고 서버가 실행 중인지 확인해주세요.\n\n$e');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('초기 설정')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const SizedBox(height: 32),
            const Icon(Icons.settings_suggest, size: 64, color: Colors.blue),
            const SizedBox(height: 24),
            const Text(
              '안전신문고 서버 연결',
              textAlign: TextAlign.center,
              style: TextStyle(fontSize: 24, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            const Text(
              '앱을 시작하려면 서버 URL과 API Key가 필요합니다.',
              textAlign: TextAlign.center,
              style: TextStyle(color: Colors.grey),
            ),
            const SizedBox(height: 32),
            TextField(
              controller: _urlController,
              decoration: const InputDecoration(
                labelText: '서버 URL',
                hintText: 'https://...',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.link),
              ),
              keyboardType: TextInputType.url,
              autocorrect: false,
              enabled: !_loading,
            ),
            const SizedBox(height: 16),
            TextField(
              controller: _apiController,
              decoration: const InputDecoration(
                labelText: 'API Key',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.vpn_key),
              ),
              obscureText: true,
              autocorrect: false,
              enabled: !_loading,
            ),
            if (_errorMessage != null) ...[
              const SizedBox(height: 16),
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: Colors.red.shade50,
                  borderRadius: BorderRadius.circular(10),
                  border: Border.all(color: Colors.red.shade200),
                ),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Icon(Icons.error_outline, color: Colors.red.shade700, size: 18),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        _errorMessage!,
                        style: TextStyle(color: Colors.red.shade800, fontSize: 13, height: 1.5),
                      ),
                    ),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 24),
            FilledButton.icon(
              icon: _loading
                  ? const SizedBox(
                      width: 18, height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white),
                    )
                  : const Icon(Icons.wifi_find, size: 18),
              label: Text(_loading ? '연결 확인 중...' : '연결 확인 후 시작하기'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 16),
              ),
              onPressed: _loading ? null : _connect,
            ),
          ],
        ),
      ),
    );
  }
}
