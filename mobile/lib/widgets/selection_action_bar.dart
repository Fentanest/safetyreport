import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';

/// 신고 카드 선택 모드에서 하단에 표시되는 액션 바
class SelectionActionBar extends StatefulWidget {
  final List<Report> selectedReports;
  final VoidCallback onCancel;
  final VoidCallback? onActionDone;

  const SelectionActionBar({
    super.key,
    required this.selectedReports,
    required this.onCancel,
    this.onActionDone,
  });

  @override
  State<SelectionActionBar> createState() => _SelectionActionBarState();
}

class _SelectionActionBarState extends State<SelectionActionBar> {
  bool _busy = false;

  List<Report> get reports => widget.selectedReports;
  int get count => reports.length;

  Future<void> _copyNumbers() async {
    final text = reports.map((r) => r.reportNumber).join('\n');
    await Clipboard.setData(ClipboardData(text: text));
    if (!mounted) return;
    _snack('신고번호 $count건 복사됨', icon: Icons.copy);
  }

  Future<void> _crawl() async {
    if (_busy) return;
    setState(() => _busy = true);
    final provider = context.read<ReportProvider>();
    try {
      final reportNumbers = reports.map((r) => r.reportNumber).toList();
      await provider.startCrawlQueue(reportNumbers);
      if (!mounted) return;
      _snack('크롤링 요청 완료: ${reportNumbers.length}건', icon: Icons.refresh);
    } catch (e) {
      if (!mounted) return;
      _snack('크롤링 요청 실패: $e', icon: Icons.warning_amber, error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
    widget.onActionDone?.call();
  }

  Future<void> _watchlistAction(bool add) async {
    if (_busy) return;
    setState(() => _busy = true);
    final provider = context.read<ReportProvider>();
    final numbers = reports.map((r) => r.reportNumber).toList();
    try {
      if (add) {
        await provider.addToWatchlist(numbers);
        if (!mounted) return;
        _snack('감시 목록에 $count건 추가됨', icon: Icons.bookmark_added);
      } else {
        await provider.removeFromWatchlist(numbers);
        if (!mounted) return;
        _snack('감시 목록에서 $count건 해제됨', icon: Icons.bookmark_remove);
      }
      widget.onActionDone?.call();
    } catch (e) {
      if (!mounted) return;
      _snack('오류: $e', error: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String msg, {IconData? icon, bool error = false}) {
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Row(children: [
        if (icon != null) ...[
          Icon(icon, size: 16, color: Colors.white),
          const SizedBox(width: 8),
        ],
        Text(msg),
      ]),
      backgroundColor: error ? Colors.red.shade700 : null,
      behavior: SnackBarBehavior.floating,
    ));
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final watchlistNums = context.watch<ReportProvider>().watchlistNumbers;
    final allInWatchlist = reports.every((r) => watchlistNums.contains(r.reportNumber));
    final noneInWatchlist = reports.every((r) => !watchlistNums.contains(r.reportNumber));

    return Material(
      elevation: 12,
      color: cs.surface,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 상태 행
              Row(
                children: [
                  IconButton(
                    icon: const Icon(Icons.close),
                    tooltip: '선택 취소',
                    onPressed: widget.onCancel,
                    visualDensity: VisualDensity.compact,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    '$count개 선택됨',
                    style: const TextStyle(
                        fontWeight: FontWeight.bold, fontSize: 15),
                  ),
                  const Spacer(),
                  if (_busy)
                    const SizedBox(
                      width: 20, height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              // 액션 버튼 행
              Row(
                children: [
                  _ActionBtn(
                    icon: Icons.copy,
                    label: '번호 복사',
                    onTap: _busy ? null : _copyNumbers,
                  ),
                  const SizedBox(width: 8),
                  _ActionBtn(
                    icon: Icons.refresh,
                    label: '크롤링',
                    onTap: _busy ? null : _crawl,
                    color: Colors.blue,
                  ),
                  const SizedBox(width: 8),
                  if (!allInWatchlist)
                    _ActionBtn(
                      icon: Icons.bookmark_add_outlined,
                      label: '감시 추가',
                      onTap: _busy ? null : () => _watchlistAction(true),
                      color: Colors.green,
                    ),
                  if (!allInWatchlist && !noneInWatchlist)
                    const SizedBox(width: 8),
                  if (!noneInWatchlist)
                    _ActionBtn(
                      icon: Icons.bookmark_remove_outlined,
                      label: '감시 해제',
                      onTap: _busy ? null : () => _watchlistAction(false),
                      color: Colors.red,
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _ActionBtn extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback? onTap;
  final Color? color;

  const _ActionBtn({
    required this.icon,
    required this.label,
    required this.onTap,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final c = color ?? Theme.of(context).colorScheme.onSurface;
    return Expanded(
      child: Material(
        color: (color ?? Colors.grey).withOpacity(0.08),
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, size: 20, color: onTap == null ? Colors.grey : c),
                const SizedBox(height: 3),
                Text(
                  label,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    color: onTap == null ? Colors.grey : c,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
