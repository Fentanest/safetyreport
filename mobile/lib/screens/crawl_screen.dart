import 'dart:async';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';

class CrawlScreen extends StatefulWidget {
  const CrawlScreen({super.key});

  @override
  State<CrawlScreen> createState() => _CrawlScreenState();
}

class _CrawlScreenState extends State<CrawlScreen> with WidgetsBindingObserver {
  // 옵션 상태
  String _loginMode = 'member';
  String _crawlType = 'api';
  String _crawlMode = 'full';
  int _maxEmptyPages = 3;
  final _queueController = TextEditingController();
  final _maxPagesController = TextEditingController(text: '3');

  // 크롤링 상태
  bool _isRunning = false;
  bool _loading = true;

  // 실시간 로그
  WebSocket? _ws;
  final List<String> _logLines = [];
  final ScrollController _logScroll = ScrollController();

  Timer? _statusTimer;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _init());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _statusTimer?.cancel();
    _ws?.close();
    _queueController.dispose();
    _maxPagesController.dispose();
    _logScroll.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _checkStatus();
    } else if (state == AppLifecycleState.paused) {
      _statusTimer?.cancel();
    }
  }

  ApiService? _api() {
    final p = context.read<ReportProvider>();
    if (p.baseUrl.isEmpty) return null;
    return ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
  }

  Future<void> _init() async {
    await _loadConfig();
    await _checkStatus();
    _startStatusPolling();
  }

  Future<void> _loadConfig() async {
    final api = _api();
    if (api == null) return;
    try {
      final cfg = await api.getCrawlConfig();
      setState(() {
        _crawlType = (cfg['crawl_type'] ?? 'api').toString();
        _crawlMode = (cfg['crawl_mode'] ?? 'full').toString();
        _maxEmptyPages = (cfg['max_empty_pages'] ?? 3) as int;
        _maxPagesController.text = _maxEmptyPages.toString();
        _loading = false;
      });
    } catch (_) {
      setState(() => _loading = false);
    }
  }

  Future<void> _checkStatus() async {
    final api = _api();
    if (api == null) return;
    try {
      final status = await api.getCrawlStatus();
      final running = status['running'] == true;
      if (running && !_isRunning) _connectWs(api);
      if (!running && _isRunning) {
        _ws?.close();
        _ws = null;
      }
      if (mounted) setState(() => _isRunning = running);
    } catch (_) {}
  }

  void _startStatusPolling() {
    _statusTimer?.cancel();
    _statusTimer = Timer.periodic(const Duration(seconds: 5), (_) => _checkStatus());
  }

  void _connectWs(ApiService api) async {
    if (_ws != null) return;
    try {
      _ws = await WebSocket.connect('${api.wsBaseUrl}/crawl/ws/logs');
      _ws!.listen(
        (data) {
          if (!mounted) return;
          final lines = data.toString()
              .split('\n')
              .where((l) => l.trim().isNotEmpty)
              .toList();
          setState(() {
            _logLines.addAll(lines);
            if (_logLines.length > 200) {
              _logLines.removeRange(0, _logLines.length - 200);
            }
          });
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (_logScroll.hasClients) {
              _logScroll.jumpTo(_logScroll.position.maxScrollExtent);
            }
          });
        },
        onDone: () {
          _ws = null;
          if (mounted) setState(() => _isRunning = false);
        },
        onError: (_) => _ws = null,
        cancelOnError: true,
      );
    } catch (_) {
      _ws = null;
    }
  }

  Future<void> _startCrawl() async {
    final api = _api();
    if (api == null) return;

    if (_crawlMode == 'reset') {
      final ok = await showDialog<bool>(
        context: context,
        builder: (_) => AlertDialog(
          title: const Text('DB 초기화 경고'),
          content: const Text('DB를 초기화하고 처음부터 새로 크롤링합니다.\n기존 데이터가 모두 삭제됩니다. 계속하시겠습니까?'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              style: FilledButton.styleFrom(backgroundColor: Colors.red),
              child: const Text('초기화 및 시작'),
            ),
          ],
        ),
      );
      if (ok != true) return;
    }

    setState(() {
      _isRunning = true;
      _logLines.clear();
      _logLines.add('크롤링 시작 중...');
    });

    try {
      final pages = int.tryParse(_maxPagesController.text) ?? 3;
      await api.startCrawl(
        loginMode: _loginMode,
        crawlType: _crawlType,
        crawlMode: _crawlMode,
        maxEmptyPages: pages,
        queueList: _queueController.text,
      );
      _connectWs(api);
    } catch (e) {
      setState(() {
        _isRunning = false;
        _logLines.add('오류: $e');
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.toString()), backgroundColor: Colors.red),
        );
      }
    }
  }

  Future<void> _killCrawl() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (_) => AlertDialog(
        title: const Text('강제 중지'),
        content: const Text('진행 중인 데이터는 저장되지 않습니다.\n정말 중지하시겠습니까?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('취소')),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            child: const Text('강제 중지'),
          ),
        ],
      ),
    );
    if (ok != true) return;

    final api = _api();
    if (api == null) return;
    try {
      await api.killCrawl();
      setState(() => _isRunning = false);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(e.toString()), backgroundColor: Colors.red),
        );
      }
    }
  }

  Future<void> _resumeCrawl() async {
    final api = _api();
    if (api == null) return;
    try {
      await api.resumeCrawl();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('재개 신호를 전송했습니다.')),
        );
      }
    } catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }

    final isMin = _crawlMode == 'min';

    return Scaffold(
      appBar: AppBar(
        title: const Text('크롤링 제어'),
      ),
      body: Column(
        children: [
          // ── 옵션 패널 ──
          Expanded(
            flex: _isRunning ? 2 : 5,
            child: RefreshIndicator(
              onRefresh: _init,
              child: SingleChildScrollView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(16),
                child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // 크롤링 방식 표시
                  Row(
                    children: [
                      Icon(Icons.sync_alt, size: 14, color: Colors.grey.shade600),
                      const SizedBox(width: 6),
                      Text('크롤링 방식:',
                          style: TextStyle(fontSize: 12, color: Colors.grey.shade600)),
                      const SizedBox(width: 6),
                      Chip(
                        label: Text(
                          _crawlType == 'api' ? 'API 방식' : '웹 크롤링 방식',
                          style: const TextStyle(fontSize: 11),
                        ),
                        padding: const EdgeInsets.symmetric(horizontal: 4),
                        visualDensity: VisualDensity.compact,
                        side: BorderSide.none,
                        backgroundColor: _crawlType == 'api'
                            ? Colors.blue.shade50
                            : Colors.orange.shade50,
                        labelStyle: TextStyle(
                          color: _crawlType == 'api'
                              ? Colors.blue.shade700
                              : Colors.orange.shade700,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),

                  // 1. 로그인 모드
                  _sectionTitle('1. 로그인 모드'),
                  _radioTile('회원 로그인', 'member', _loginMode,
                      '저장된 ID/PW로 자동 로그인',
                      onChanged: (v) => setState(() => _loginMode = v!)),
                  _radioTile('비회원(수동) 로그인', 'nonmember', _loginMode,
                      '브라우저에서 수동 로그인 후 재개 버튼 누르기',
                      onChanged: (v) => setState(() => _loginMode = v!)),

                  const SizedBox(height: 12),

                  // 2. 크롤링 범위
                  _sectionTitle('2. 크롤링 범위'),
                  _radioTile('전체 크롤링', 'full', _crawlMode, '',
                      onChanged: (v) => setState(() => _crawlMode = v!)),
                  _radioTile('최소 크롤링', 'min', _crawlMode,
                      '변경사항 감지된 곳까지만',
                      enabled: _crawlType != 'api',
                      onChanged: _crawlType == 'api' ? null : (v) => setState(() => _crawlMode = v!)),
                  if (_crawlType != 'api' && isMin)
                    Padding(
                      padding: const EdgeInsets.only(left: 32, top: 4, bottom: 4),
                      child: Row(children: [
                        const Text('탐색 페이지 한도:', style: TextStyle(fontSize: 13)),
                        const SizedBox(width: 8),
                        SizedBox(
                          width: 70,
                          child: TextField(
                            controller: _maxPagesController,
                            keyboardType: TextInputType.number,
                            style: const TextStyle(fontSize: 13),
                            decoration: const InputDecoration(
                                isDense: true,
                                contentPadding: EdgeInsets.symmetric(
                                    horizontal: 8, vertical: 6)),
                          ),
                        ),
                      ]),
                    ),
                  _radioTile('DB 초기화 후 새로 크롤링', 'reset', _crawlMode, '',
                      isRed: true,
                      onChanged: (v) => setState(() => _crawlMode = v!)),

                  const SizedBox(height: 12),

                  // 4. 큐
                  _sectionTitle('4. 큐 (선택사항)'),
                  TextField(
                    controller: _queueController,
                    maxLines: 3,
                    style: const TextStyle(fontSize: 12),
                    decoration: InputDecoration(
                      hintText: 'SPP-231120-1234567\nSPP-231121-7654321',
                      hintStyle: TextStyle(fontSize: 11, color: Colors.grey.shade400),
                    ),
                  ),

                  const SizedBox(height: 16),

                  // 버튼
                  Row(children: [
                    Expanded(
                      child: FilledButton.icon(
                        icon: const Icon(Icons.play_arrow),
                        label: const Text('크롤링 시작'),
                        onPressed: _isRunning ? null : _startCrawl,
                      ),
                    ),
                    if (_loginMode == 'nonmember') ...[
                      const SizedBox(width: 8),
                      FilledButton.icon(
                        icon: const Icon(Icons.play_circle_outline),
                        label: const Text('재개'),
                        style: FilledButton.styleFrom(backgroundColor: Colors.orange),
                        onPressed: _isRunning ? _resumeCrawl : null,
                      ),
                    ],
                    const SizedBox(width: 8),
                    FilledButton.icon(
                      icon: const Icon(Icons.stop),
                      label: const Text('강제 중지'),
                      style: FilledButton.styleFrom(backgroundColor: Colors.red),
                      onPressed: _isRunning ? _killCrawl : null,
                    ),
                  ]),
                ],
              ),
            ),
          ),
          ),

          // ── 실시간 로그 ──
          const Divider(height: 1),
          Expanded(
            flex: _isRunning ? 3 : 2,
            child: Container(
              color: const Color(0xFF1E1E1E),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
                    child: Row(children: [
                      if (_isRunning) ...[
                        const SizedBox(
                          width: 12, height: 12,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: Colors.greenAccent),
                        ),
                        const SizedBox(width: 8),
                      ],
                      Text(
                        _isRunning ? '실행 중' : '대기 중',
                        style: TextStyle(
                            color: _isRunning ? Colors.greenAccent : Colors.grey,
                            fontSize: 11,
                            fontWeight: FontWeight.bold),
                      ),
                    ]),
                  ),
                  Expanded(
                    child: _logLines.isEmpty
                        ? Center(
                            child: Text('로그 없음',
                                style: TextStyle(
                                    color: Colors.grey.shade700, fontSize: 12)))
                        : ListView.builder(
                            controller: _logScroll,
                            padding: const EdgeInsets.fromLTRB(12, 0, 12, 8),
                            itemCount: _logLines.length,
                            itemBuilder: (_, i) => Text(
                              _logLines[i],
                              style: const TextStyle(
                                  color: Colors.greenAccent,
                                  fontSize: 10.5,
                                  fontFamily: 'monospace',
                                  height: 1.4),
                            ),
                          ),
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _sectionTitle(String text) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: Text(text,
            style: TextStyle(
                fontWeight: FontWeight.bold,
                fontSize: 13,
                color: Theme.of(context).colorScheme.primary)),
      );

  Widget _radioTile(
    String title,
    String value,
    String groupValue,
    String subtitle, {
    bool enabled = true,
    bool isRed = false,
    void Function(String?)? onChanged,
  }) {
    return RadioListTile<String>(
      title: Text(title,
          style: TextStyle(
              fontSize: 13,
              color: isRed ? Colors.red : (enabled ? null : Colors.grey),
              fontWeight: isRed ? FontWeight.bold : FontWeight.normal)),
      subtitle: subtitle.isNotEmpty
          ? Text(subtitle,
              style: TextStyle(
                  fontSize: 11, color: Colors.grey.shade600))
          : null,
      value: value,
      groupValue: groupValue,
      dense: true,
      contentPadding: EdgeInsets.zero,
      onChanged: enabled ? onChanged : null,
    );
  }
}
