import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../models/notification_item.dart';
import '../models/report.dart';
import '../providers/notification_history_provider.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';
import '../widgets/report_detail_sheet.dart';

const _permChannel = MethodChannel('com.fentanest.mysafetyreport/permissions');

class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key});

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen>
    with WidgetsBindingObserver {

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<NotificationHistoryProvider>().load();
      _fetchServerResults();
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
      context.read<NotificationHistoryProvider>().load();
      _fetchServerResults();
    }
  }

  ApiService? _getApi() {
    final p = context.read<ReportProvider>();
    if (p.baseUrl.isEmpty) return null;
    return ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
  }

  Future<void> _fetchServerResults() async {
    final api = _getApi();
    if (api == null) return;
    try {
      final done = await api.getCrawlDone();
      if (done['done'] == true) {
        final changedCount = (done['changed_count'] as num?)?.toInt() ?? 0;
        final results = await api.fetchCrawlResults();
        if (mounted) {
          await context.read<NotificationHistoryProvider>()
              .addFromServerResults(results);
        }
        _showPushNotif(changedCount);
      }
    } catch (_) {}
  }

  Future<void> _showPushNotif(int changedCount) async {
    try {
      final body = changedCount > 0
          ? '크롤링이 완료되었습니다. ${changedCount}건의 변경사항이 있습니다.'
          : '크롤링이 완료되었습니다. 변경사항이 없습니다.';
      await _permChannel.invokeMethod('showNotification', {
        'title': '✅ 크롤링 완료',
        'body': body,
      });
    } catch (_) {}
  }

  Future<bool> _confirmClear(BuildContext context) async {
    return await showDialog<bool>(
          context: context,
          builder: (ctx) => AlertDialog(
            title: const Text('알림 기록 삭제'),
            content: const Text('모든 알림 기록을 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.'),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('취소'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(ctx, true),
                style: FilledButton.styleFrom(backgroundColor: Colors.red),
                child: const Text('삭제'),
              ),
            ],
          ),
        ) ??
        false;
  }

  void _showDetail(BuildContext context, NotificationItem item) {
    context.read<NotificationHistoryProvider>().markRead(item.id);

    if (item.extraData != null && item.extraData!.isNotEmpty) {
      final report = Report.fromJson(item.extraData!);
      showReportDetailSheet(context, report);
      return;
    }

    final tabController = DefaultTabController.of(context);
    final hasChanges = item.body.contains('변경사항이 있습니다');
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (sheetCtx) => DraggableScrollableSheet(
        initialChildSize: hasChanges ? 0.5 : 0.4,
        minChildSize: 0.3,
        maxChildSize: 0.7,
        expand: false,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
          children: [
            Center(
              child: Container(
                  width: 36, height: 4,
                  margin: const EdgeInsets.only(bottom: 20),
                  decoration: BoxDecoration(
                      color: Colors.grey.shade300,
                      borderRadius: BorderRadius.circular(2))),
            ),
            Row(children: [
              const Icon(Icons.notifications_active, color: Colors.blue),
              const SizedBox(width: 10),
              Expanded(
                  child: Text(item.title,
                      style: const TextStyle(
                          fontSize: 17, fontWeight: FontWeight.bold))),
            ]),
            const Divider(height: 24),
            Text(item.body,
                style: const TextStyle(fontSize: 14, height: 1.6)),
            const SizedBox(height: 20),
            if (item.reportNumber.isNotEmpty)
              _detailRow('신고번호', item.reportNumber),
            _detailRow('수신 시각', item.timestamp),
            if (hasChanges) ...[
              const SizedBox(height: 16),
              FilledButton.icon(
                icon: const Icon(Icons.assignment_outlined),
                label: const Text('신고 결과 보기'),
                onPressed: () {
                  Navigator.pop(sheetCtx);
                  tabController.animateTo(1);
                },
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _detailRow(String label, String value) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 4),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SizedBox(
                width: 72,
                child: Text(label,
                    style: const TextStyle(color: Colors.grey, fontSize: 13))),
            Expanded(
                child: Text(value, style: const TextStyle(fontSize: 13))),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<NotificationHistoryProvider>();
    final allItems = provider.items;

    final crawlItems = allItems.where((i) => i.extraData == null).toList();
    final reportItems = allItems.where((i) => i.extraData != null).toList();
    final crawlUnread = crawlItems.where((i) => !i.isRead).length;
    final reportUnread = reportItems.where((i) => !i.isRead).length;

    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('알림 기록'),
          actions: [
            if (allItems.isNotEmpty && provider.unreadCount > 0)
              TextButton.icon(
                icon: const Icon(Icons.done_all, size: 18),
                label: const Text('모두 읽음'),
                style: TextButton.styleFrom(foregroundColor: Colors.white),
                onPressed: provider.markAllRead,
              ),
            if (allItems.isNotEmpty)
              IconButton(
                icon: const Icon(Icons.delete_sweep_outlined),
                tooltip: '모두 비우기',
                onPressed: () async {
                  if (await _confirmClear(context)) {
                    if (context.mounted) {
                      context.read<NotificationHistoryProvider>().clearAll();
                    }
                  }
                },
              ),
          ],
          bottom: TabBar(
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white70,
            indicatorColor: Colors.white,
            indicatorWeight: 3,
            tabs: [
              Tab(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text('크롤링 현황'),
                    if (crawlUnread > 0) ...[
                      const SizedBox(width: 6),
                      _unreadBadge(crawlUnread),
                    ],
                  ],
                ),
              ),
              Tab(
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    const Text('신고 결과'),
                    if (reportUnread > 0) ...[
                      const SizedBox(width: 6),
                      _unreadBadge(reportUnread),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            _buildList(
              items: crawlItems,
              emptyMessage: '크롤링 알림이 없습니다.',
              emptySubMessage: '크롤링 시작/완료 알림이 여기에 기록됩니다.',
            ),
            _buildList(
              items: reportItems,
              emptyMessage: '신고 결과가 없습니다.',
              emptySubMessage: '크롤링 후 변경된 신고건이 여기에 기록됩니다.\n각 항목을 눌러 상세 정보를 확인하세요.',
            ),
          ],
        ),
      ),
    );
  }

  Widget _unreadBadge(int count) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        decoration: BoxDecoration(
          color: Colors.red,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Text('$count',
            style: const TextStyle(
                fontSize: 11, color: Colors.white, fontWeight: FontWeight.bold)),
      );

  Future<void> _refresh() async {
    context.read<NotificationHistoryProvider>().load();
    await _fetchServerResults();
  }

  Widget _buildList({
    required List<NotificationItem> items,
    required String emptyMessage,
    required String emptySubMessage,
  }) {
    if (items.isEmpty) {
      return RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          children: [
            SizedBox(
              height: 300,
              child: Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.notifications_none,
                        size: 72, color: Colors.grey.shade300),
                    const SizedBox(height: 16),
                    Text(emptyMessage,
                        style: const TextStyle(color: Colors.grey, fontSize: 15)),
                    const SizedBox(height: 8),
                    Text(
                      emptySubMessage,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: Colors.grey, fontSize: 12, height: 1.5),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.separated(
        padding: const EdgeInsets.symmetric(vertical: 8),
        itemCount: items.length,
        separatorBuilder: (_, __) => const Divider(height: 1, indent: 16),
        itemBuilder: (context, index) {
          final item = items[index];
          return _NotifTile(
            item: item,
            onTap: () => _showDetail(context, item),
          );
        },
      ),
    );
  }
}

class _NotifTile extends StatelessWidget {
  final NotificationItem item;
  final VoidCallback onTap;

  const _NotifTile({required this.item, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final unread = !item.isRead;
    final hasDetail = item.extraData != null && item.extraData!.isNotEmpty;
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 5, right: 10),
              child: Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: unread ? Colors.blue : Colors.transparent,
                ),
              ),
            ),
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: hasDetail
                    ? Colors.orange.shade50
                    : Colors.blue.shade50,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(
                hasDetail
                    ? Icons.assignment_outlined
                    : Icons.notifications_active,
                color: hasDetail
                    ? Colors.orange.shade700
                    : Colors.blue.shade700,
                size: 20,
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(item.title,
                      style: TextStyle(
                          fontSize: 13,
                          fontWeight: unread
                              ? FontWeight.bold
                              : FontWeight.w500)),
                  const SizedBox(height: 3),
                  Text(item.body,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(
                          fontSize: 12,
                          color: Colors.grey.shade600,
                          height: 1.4)),
                  const SizedBox(height: 4),
                  Row(children: [
                    if (item.reportNumber.isNotEmpty) ...[
                      Icon(Icons.tag, size: 11, color: Colors.grey.shade400),
                      const SizedBox(width: 2),
                      Text(item.reportNumber,
                          style: TextStyle(
                              fontSize: 11, color: Colors.grey.shade400)),
                      const SizedBox(width: 8),
                    ],
                    Icon(Icons.access_time,
                        size: 11, color: Colors.grey.shade400),
                    const SizedBox(width: 2),
                    Text(item.timestamp,
                        style: TextStyle(
                            fontSize: 11, color: Colors.grey.shade400)),
                  ]),
                ],
              ),
            ),
            Icon(Icons.chevron_right, size: 16, color: Colors.grey.shade400),
          ],
        ),
      ),
    );
  }
}
