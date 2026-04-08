import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/report.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';
import '../widgets/report_detail_sheet.dart';

class WatchlistScreen extends StatefulWidget {
  const WatchlistScreen({super.key});

  @override
  State<WatchlistScreen> createState() => _WatchlistScreenState();
}

class _WatchlistScreenState extends State<WatchlistScreen> {
  List<Report> _items = [];
  bool _loading = true;
  String? _error;
  late ApiService _api;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final p = context.read<ReportProvider>();
      _api = ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
      _load();
    });
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final items = await _api.getWatchlist();
      if (mounted) setState(() { _items = items; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  Future<void> _remove(Report r) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('감시 목록에서 제거'),
        content: Text('${r.name}\n\n감시 목록에서 제거하시겠습니까?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            child: const Text('제거'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      await _api.updateWatchlist([r.reportNumber], add: false);
      setState(() => _items.removeWhere((i) => i.reportNumber == r.reportNumber));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('감시 목록에서 제거되었습니다.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('오류: $e')));
      }
    }
  }

  Future<void> _removeAll() async {
    if (_items.isEmpty) return;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('전체 해제'),
        content: Text('감시 목록 ${_items.length}건을 모두 해제하시겠습니까?'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('취소')),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: FilledButton.styleFrom(backgroundColor: Colors.red),
            child: const Text('전체 해제'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final rnums = _items.map((r) => r.reportNumber).toList();
      await _api.updateWatchlist(rnums, add: false);
      setState(() => _items.clear());
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('감시 목록이 모두 해제되었습니다.')));
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text('오류: $e')));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text('감시 목록${_items.isNotEmpty ? " (${_items.length})" : ""}'),
        actions: [
          if (_items.isNotEmpty)
            TextButton.icon(
              icon: const Icon(Icons.delete_sweep, size: 18),
              label: const Text('전체 해제'),
              style: TextButton.styleFrom(foregroundColor: Colors.white),
              onPressed: _removeAll,
            ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    const Icon(Icons.error_outline, size: 48, color: Colors.red),
                    const SizedBox(height: 12),
                    Text(_error!, textAlign: TextAlign.center),
                    const SizedBox(height: 16),
                    FilledButton.icon(
                      icon: const Icon(Icons.refresh),
                      label: const Text('다시 시도'),
                      onPressed: _load,
                    ),
                  ]),
                )
              : _items.isEmpty
                  ? Center(
                      child: Column(mainAxisSize: MainAxisSize.min, children: [
                        Icon(Icons.bookmark_border,
                            size: 64, color: Colors.grey.shade300),
                        const SizedBox(height: 16),
                        const Text('감시 목록이 비어 있습니다.',
                            style: TextStyle(color: Colors.grey, fontSize: 15)),
                        const SizedBox(height: 8),
                        const Text(
                          '웹에서 신고를 감시 목록에 추가하면\n여기서 상태를 확인할 수 있습니다.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: Colors.grey, fontSize: 12, height: 1.5),
                        ),
                      ]),
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView.separated(
                        padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 12),
                        itemCount: _items.length,
                        separatorBuilder: (_, __) => const SizedBox(height: 6),
                        itemBuilder: (context, i) => _WatchCard(
                          report: _items[i],
                          onRemove: () => _remove(_items[i]),
                        ),
                      ),
                    ),
    );
  }
}

class _WatchCard extends StatelessWidget {
  final Report report;
  final VoidCallback onRemove;

  const _WatchCard({required this.report, required this.onRemove});

  Color _statusColor(String s) {
    if (s == '일부수용') return const Color(0xFF43A047);
    if (s.contains('수용') && !s.contains('불')) return Colors.green;
    if (s.contains('불수용')) return Colors.red;
    if (s.contains('처리') || s.contains('진행')) return Colors.orange;
    return Colors.grey;
  }

  @override
  Widget build(BuildContext context) {
    final color = _statusColor(report.status);
    return Card(
      margin: EdgeInsets.zero,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => showReportDetailSheet(context, report),
        child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.bookmark, size: 16, color: Colors.blue),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(report.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          fontWeight: FontWeight.w600, fontSize: 14)),
                ),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.1),
                    borderRadius: BorderRadius.circular(20),
                    border: Border.all(color: color.withOpacity(0.4)),
                  ),
                  child: Text(report.status,
                      style: TextStyle(
                          color: color, fontSize: 11, fontWeight: FontWeight.bold)),
                ),
              ],
            ),
            const SizedBox(height: 8),
            if (report.carNumber.isNotEmpty)
              _row(Icons.directions_car_outlined, '차량번호', report.carNumber),
            if (report.reportNumber.isNotEmpty)
              _row(Icons.tag, '신고번호', report.reportNumber),
            if (report.date.isNotEmpty)
              _row(Icons.calendar_today, '신고일', report.date),
            if (report.responseDate.isNotEmpty)
              _row(Icons.check_circle_outline, '답변일', report.responseDate),
            if (report.agency.isNotEmpty)
              _row(Icons.business, '처리기관', report.agency),
            if (report.fineInfo.isNotEmpty)
              _row(Icons.monetization_on_outlined, '과태료/범칙금', report.fineInfo),
            const SizedBox(height: 8),
            Align(
              alignment: Alignment.centerRight,
              child: TextButton.icon(
                icon: const Icon(Icons.bookmark_remove, size: 16),
                label: const Text('감시 해제'),
                style: TextButton.styleFrom(
                    foregroundColor: Colors.red,
                    padding: EdgeInsets.zero,
                    minimumSize: const Size(0, 32)),
                onPressed: onRemove,
              ),
            ),
          ],
        ),
      ),
    ),
    );
  }

  Widget _row(IconData icon, String label, String value) => Padding(
        padding: const EdgeInsets.only(top: 3),
        child: Row(children: [
          Icon(icon, size: 12, color: Colors.grey),
          const SizedBox(width: 4),
          Text('$label ', style: const TextStyle(fontSize: 11, color: Colors.grey)),
          Expanded(
              child: Text(value,
                  style: const TextStyle(fontSize: 11),
                  overflow: TextOverflow.ellipsis)),
        ]),
      );
}
