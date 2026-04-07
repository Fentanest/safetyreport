import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'screens/dashboard_screen.dart';
import 'screens/report_list_screen.dart';
import 'screens/statistics_screen.dart';
import 'screens/setup_screen.dart';
import 'screens/file_browser_screen.dart';
import 'screens/notifications_screen.dart';
import 'screens/crawl_screen.dart';
import 'providers/report_provider.dart';
import 'providers/notification_history_provider.dart';

void main() {
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => ReportProvider()..init()),
        ChangeNotifierProvider(
            create: (_) => NotificationHistoryProvider()..load()),
      ],
      child: const SafetyReportApp(),
    ),
  );
}

class SafetyReportApp extends StatelessWidget {
  const SafetyReportApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '나만의 안전신문고',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1A73E8),
          brightness: Brightness.light,
        ),
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          backgroundColor: Color(0xFF1A73E8),
          foregroundColor: Colors.white,
          elevation: 0,
          titleTextStyle: TextStyle(
            color: Colors.white,
            fontSize: 18,
            fontWeight: FontWeight.bold,
          ),
        ),
        cardTheme: CardThemeData(
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(14)),
            side: BorderSide(color: Color(0xFFE8EAED)),
          ),
        ),
        inputDecorationTheme: InputDecorationTheme(
          contentPadding: EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          border: OutlineInputBorder(
            borderRadius: BorderRadius.all(Radius.circular(10)),
          ),
        ),
        navigationBarTheme: NavigationBarThemeData(
          backgroundColor: Colors.white,
          elevation: 8,
          shadowColor: Colors.black12,
          indicatorColor: Color(0xFFE3EEFF),
          labelTextStyle: WidgetStatePropertyAll(
            TextStyle(fontSize: 11, fontWeight: FontWeight.w600),
          ),
        ),
      ),
      darkTheme: ThemeData(
        useMaterial3: true,
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1A73E8),
          brightness: Brightness.dark,
        ),
        appBarTheme: const AppBarTheme(
          centerTitle: false,
          backgroundColor: Color(0xFF1E1E2E),
          foregroundColor: Colors.white,
          elevation: 0,
          titleTextStyle: TextStyle(
            color: Colors.white,
            fontSize: 18,
            fontWeight: FontWeight.bold,
          ),
        ),
        cardTheme: const CardThemeData(
          elevation: 0,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.all(Radius.circular(14)),
          ),
        ),
      ),
      home: Consumer<ReportProvider>(
        builder: (context, provider, child) {
          if (!provider.isInitialized) {
            return const Scaffold(
              body: Center(child: CircularProgressIndicator()),
            );
          }
          if (!provider.isConfigured) {
            return const SetupScreen();
          }
          return const MainNavigationScreen();
        },
      ),
    );
  }
}

class MainNavigationScreen extends StatefulWidget {
  const MainNavigationScreen({super.key});

  @override
  State<MainNavigationScreen> createState() => _MainNavigationScreenState();
}

const _permChannel = MethodChannel('com.fentanest.mysafetyreport/permissions');

class _MainNavigationScreenState extends State<MainNavigationScreen>
    with WidgetsBindingObserver {
  int _selectedIndex = 0;

  final List<Widget> _screens = [
    const DashboardScreen(),
    const ReportListScreen(),
    const StatisticsScreen(),
    const NotificationsScreen(),
    const FileBrowserScreen(),
    const CrawlScreen(),
  ];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Native에서 navigateToTab 호출 수신
    _permChannel.setMethodCallHandler(_handleNativeCall);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<NotificationHistoryProvider>().load();
      _checkPendingChanges();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) {
      _checkPendingChanges();
      _checkForegroundEvent();
    }
  }

  Future<dynamic> _handleNativeCall(MethodCall call) async {
    if (call.method == 'navigateToTab') {
      final args = call.arguments as Map?;
      final tab = (args?['tab'] as num?)?.toInt() ?? 3;
      if (mounted) setState(() => _selectedIndex = tab);
    }
  }

  Future<void> _checkForegroundEvent() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final raw = prefs.getString('foreground_event');
    if (raw == null) return;
    await prefs.remove('foreground_event');
    if (!mounted) return;
    try {
      final event = jsonDecode(raw) as Map<String, dynamic>;
      final title = event['title']?.toString() ?? '';
      final body = event['body']?.toString() ?? '';
      if (title.isEmpty) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(title, style: const TextStyle(fontWeight: FontWeight.bold)),
              if (body.isNotEmpty)
                Text(body, style: const TextStyle(fontSize: 12)),
            ],
          ),
          duration: const Duration(seconds: 4),
          action: SnackBarAction(
            label: '알림 보기',
            onPressed: () => setState(() => _selectedIndex = 3),
          ),
        ),
      );
    } catch (_) {}
  }

  Future<void> _checkPendingChanges() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.reload();
    final raw = prefs.getString('pending_crawl_changes');
    if (raw == null || raw.isEmpty) return;
    await prefs.remove('pending_crawl_changes');

    List<dynamic> changes;
    try {
      changes = jsonDecode(raw) as List<dynamic>;
    } catch (_) {
      return;
    }
    if (changes.isEmpty) return;
    if (!mounted) return;

    // 알림 히스토리에 extraData 포함해서 저장 (신고 결과 탭에서 상세 조회 가능하도록)
    await context.read<NotificationHistoryProvider>()
        .addFromServerResults(changes.cast<Map<String, dynamic>>());

    // 알림 탭으로 이동
    setState(() => _selectedIndex = 3);

    // 변경 신고건 카드 뷰 표시
    await Future.delayed(const Duration(milliseconds: 200));
    if (!mounted) return;
    _showChangesBottomSheet(changes);
  }

  void _showChangesBottomSheet(List<dynamic> changes) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.6,
        minChildSize: 0.35,
        maxChildSize: 0.92,
        expand: false,
        builder: (_, controller) {
          final newCount = changes
              .where((r) => (r as Map)['change_type'] == '신규')
              .length;
          final changedCount = changes.length - newCount;
          return Column(
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: Column(
                children: [
                  Container(
                    width: 36, height: 4,
                    decoration: BoxDecoration(
                      color: Colors.grey.shade300,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Icon(Icons.sync_alt, color: Colors.blue, size: 20),
                      const SizedBox(width: 8),
                      Text(
                        '신고 변경 ${changes.length}건',
                        style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.bold,
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 6),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      if (newCount > 0) _changeBadge('신규', newCount, Colors.teal),
                      if (newCount > 0 && changedCount > 0) const SizedBox(width: 6),
                      if (changedCount > 0) _changeBadge('처리변경', changedCount, Colors.orange),
                    ],
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: ListView.separated(
                controller: controller,
                padding: const EdgeInsets.all(12),
                itemCount: changes.length,
                separatorBuilder: (_, __) => const SizedBox(height: 8),
                itemBuilder: (ctx, i) {
                  final r = changes[i] as Map<String, dynamic>;
                  final changeType = r['change_type']?.toString() ?? '변경';
                  final isNew = changeType == '신규';
                  final reportNo = r['신고번호']?.toString() ?? '';
                  final name = r['신고명']?.toString() ?? '신고';
                  final status = r['처리상태']?.toString() ?? '';
                  final agency = r['처리기관']?.toString() ?? '';
                  final fine = r['범칙금_과태료']?.toString() ?? '';

                  Color statusColor = Colors.grey;
                  if (status == '수용') statusColor = Colors.green;
                  else if (status == '불수용') statusColor = Colors.red;
                  else if (status == '처리중') statusColor = Colors.orange;
                  else if (status.contains('완료')) statusColor = Colors.blue;

                  return Card(
                    child: Padding(
                      padding: const EdgeInsets.all(14),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Container(
                                margin: const EdgeInsets.only(right: 6),
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 6, vertical: 2),
                                decoration: BoxDecoration(
                                  color: isNew
                                      ? Colors.teal.withOpacity(0.12)
                                      : Colors.orange.withOpacity(0.12),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(
                                    color: isNew
                                        ? Colors.teal.withOpacity(0.5)
                                        : Colors.orange.withOpacity(0.5),
                                  ),
                                ),
                                child: Text(
                                  isNew ? '신규' : '처리변경',
                                  style: TextStyle(
                                    fontSize: 10,
                                    fontWeight: FontWeight.bold,
                                    color: isNew ? Colors.teal : Colors.orange,
                                  ),
                                ),
                              ),
                              Expanded(
                                child: Text(
                                  name,
                                  style: const TextStyle(
                                    fontWeight: FontWeight.bold, fontSize: 14,
                                  ),
                                ),
                              ),
                              Container(
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 8, vertical: 3),
                                decoration: BoxDecoration(
                                  color: statusColor.withOpacity(0.12),
                                  borderRadius: BorderRadius.circular(20),
                                  border: Border.all(
                                      color: statusColor.withOpacity(0.4)),
                                ),
                                child: Text(
                                  status,
                                  style: TextStyle(
                                    fontSize: 12, color: statusColor,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            ],
                          ),
                          if (reportNo.isNotEmpty) ...[
                            const SizedBox(height: 6),
                            Row(children: [
                              Icon(Icons.tag, size: 13,
                                  color: Colors.grey.shade500),
                              const SizedBox(width: 4),
                              Text(reportNo,
                                  style: TextStyle(
                                      fontSize: 12,
                                      color: Colors.grey.shade600)),
                            ]),
                          ],
                          if (agency.isNotEmpty) ...[
                            const SizedBox(height: 4),
                            Row(children: [
                              Icon(Icons.business, size: 13,
                                  color: Colors.grey.shade500),
                              const SizedBox(width: 4),
                              Text(agency,
                                  style: TextStyle(
                                      fontSize: 12,
                                      color: Colors.grey.shade600)),
                            ]),
                          ],
                          if (fine.isNotEmpty &&
                              fine != '미확인' &&
                              fine != 'null') ...[
                            const SizedBox(height: 4),
                            Row(children: [
                              Icon(Icons.receipt_long, size: 13,
                                  color: Colors.grey.shade500),
                              const SizedBox(width: 4),
                              Text(fine,
                                  style: TextStyle(
                                      fontSize: 12,
                                      color: Colors.grey.shade600)),
                            ]),
                          ],
                        ],
                      ),
                    ),
                  );
                },
              ),
            ),
          ],
        );},
      ),
    );
  }

  Widget _changeBadge(String label, int count, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withOpacity(0.4)),
      ),
      child: Text(
        '$label $count건',
        style: TextStyle(
          fontSize: 12, color: color, fontWeight: FontWeight.w600,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final unread =
        context.watch<NotificationHistoryProvider>().unreadCount;

    return Scaffold(
      body: IndexedStack(
        index: _selectedIndex,
        children: _screens,
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _selectedIndex,
        onDestinationSelected: (index) {
          setState(() => _selectedIndex = index);
          // 알림 탭(index 3) 선택 시 새로고침
          if (index == 3) {
            context.read<NotificationHistoryProvider>().load();
          }
        },
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.dashboard_outlined),
            selectedIcon: Icon(Icons.dashboard),
            label: '대시보드',
          ),
          const NavigationDestination(
            icon: Icon(Icons.list_alt_outlined),
            selectedIcon: Icon(Icons.list_alt),
            label: '신고리스트',
          ),
          const NavigationDestination(
            icon: Icon(Icons.bar_chart_outlined),
            selectedIcon: Icon(Icons.bar_chart),
            label: '통계',
          ),
          NavigationDestination(
            icon: Badge(
              isLabelVisible: unread > 0,
              label: Text('$unread'),
              child: const Icon(Icons.notifications_outlined),
            ),
            selectedIcon: Badge(
              isLabelVisible: unread > 0,
              label: Text('$unread'),
              child: const Icon(Icons.notifications),
            ),
            label: '알림',
          ),
          const NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            selectedIcon: Icon(Icons.folder),
            label: '파일',
          ),
          const NavigationDestination(
            icon: Icon(Icons.sync_outlined),
            selectedIcon: Icon(Icons.sync),
            label: '크롤링',
          ),
        ],
      ),
    );
  }
}
