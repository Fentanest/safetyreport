import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/notification_item.dart';
import '../providers/notification_history_provider.dart';

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
    }
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
                style: FilledButton.styleFrom(
                    backgroundColor: Colors.red),
                child: const Text('삭제'),
              ),
            ],
          ),
        ) ??
        false;
  }

  void _showDetail(BuildContext context, NotificationItem item) {
    context.read<NotificationHistoryProvider>().markRead(item.id);
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20))),
      builder: (_) => DraggableScrollableSheet(
        initialChildSize: 0.5,
        minChildSize: 0.3,
        maxChildSize: 0.85,
        expand: false,
        builder: (_, controller) => ListView(
          controller: controller,
          padding: const EdgeInsets.fromLTRB(20, 8, 20, 32),
          children: [
            Center(
              child: Container(
                  width: 36,
                  height: 4,
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
            if (item.reportNumber.isNotEmpty) _detailRow('신고번호', item.reportNumber),
            _detailRow('수신 시각', item.timestamp),
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
                    style: const TextStyle(
                        color: Colors.grey, fontSize: 13))),
            Expanded(
                child: Text(value,
                    style: const TextStyle(fontSize: 13))),
          ],
        ),
      );

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<NotificationHistoryProvider>();
    final items = provider.items;

    return Scaffold(
      appBar: AppBar(
        title: const Text('알림 기록'),
        actions: [
          if (items.isNotEmpty && provider.unreadCount > 0)
            TextButton.icon(
              icon: const Icon(Icons.done_all, size: 18),
              label: const Text('모두 읽음'),
              style: TextButton.styleFrom(foregroundColor: Colors.white),
              onPressed: provider.markAllRead,
            ),
          if (items.isNotEmpty)
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
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: '새로고침',
            onPressed: provider.load,
          ),
        ],
      ),
      body: items.isEmpty
          ? Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.notifications_none,
                      size: 72, color: Colors.grey.shade300),
                  const SizedBox(height: 16),
                  const Text('알림 기록이 없습니다.',
                      style: TextStyle(color: Colors.grey, fontSize: 15)),
                  const SizedBox(height: 8),
                  const Text(
                    '카카오톡·안전신문고 알림이 감지되면\n크롤링 결과가 여기에 기록됩니다.',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: Colors.grey, fontSize: 12, height: 1.5),
                  ),
                ],
              ),
            )
          : ListView.separated(
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
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 읽음 표시 점
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
            // 아이콘
            Container(
              width: 40,
              height: 40,
              decoration: BoxDecoration(
                color: Colors.blue.shade50,
                borderRadius: BorderRadius.circular(10),
              ),
              child: Icon(Icons.notifications_active,
                  color: Colors.blue.shade700, size: 20),
            ),
            const SizedBox(width: 12),
            // 내용
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
