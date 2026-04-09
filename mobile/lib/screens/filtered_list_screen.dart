import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';
import '../widgets/report_detail_sheet.dart';
import '../widgets/selection_action_bar.dart';

/// 대시보드/통계 카드 탭 시 해당 조건에 맞는 신고만 보여주는 화면
class FilteredListScreen extends StatefulWidget {
  final String title;
  /// 'all', 'traffic', 'parking', 'other'
  final String category;
  final bool Function(Report) filter;

  const FilteredListScreen({
    super.key,
    required this.title,
    required this.category,
    required this.filter,
  });

  @override
  State<FilteredListScreen> createState() => _FilteredListScreenState();
}

class _FilteredListScreenState extends State<FilteredListScreen> {
  final Set<String> _selected = {};
  bool get _selectionMode => _selected.isNotEmpty;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final p = context.read<ReportProvider>();
      if (p.trafficReports.isEmpty) p.fetchTrafficReports();
      if (p.parkingReports.isEmpty) p.fetchParkingReports();
      if (p.otherReports.isEmpty) p.fetchOtherReports();
    });
  }

  void _toggleSelect(String rn) => setState(() {
        _selected.contains(rn) ? _selected.remove(rn) : _selected.add(rn);
      });

  void _clearSelection() => setState(() => _selected.clear());

  List<Report> _getReports(ReportProvider provider) {
    final List<Report> base;
    if (widget.category == 'traffic') {
      base = provider.trafficReports;
    } else if (widget.category == 'parking') {
      base = provider.parkingReports;
    } else if (widget.category == 'other') {
      base = provider.otherReports;
    } else {
      base = [...provider.trafficReports, ...provider.parkingReports, ...provider.otherReports];
    }
    return base.where(widget.filter).toList();
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ReportProvider>();
    final reports = _getReports(provider);
    final selectedReports =
        reports.where((r) => _selected.contains(r.reportNumber)).toList();

    return Scaffold(
      appBar: _selectionMode
          ? AppBar(
              leading: IconButton(
                icon: const Icon(Icons.close),
                onPressed: _clearSelection,
              ),
              title: Text('${_selected.length}개 선택됨'),
              backgroundColor:
                  Theme.of(context).colorScheme.primaryContainer,
              foregroundColor:
                  Theme.of(context).colorScheme.onPrimaryContainer,
            )
          : AppBar(
              title: Text(widget.title),
              actions: [
                Padding(
                  padding: const EdgeInsets.only(right: 14),
                  child: Center(
                    child: Text('${reports.length}건',
                        style: const TextStyle(fontWeight: FontWeight.bold)),
                  ),
                ),
              ],
            ),
      body: Stack(
        children: [
          provider.isLoading && reports.isEmpty
              ? const Center(child: CircularProgressIndicator())
              : reports.isEmpty
                  ? const Center(
                      child: Text('해당하는 신고가 없습니다.',
                          style: TextStyle(color: Colors.grey)))
                  : ListView.builder(
                      padding: EdgeInsets.fromLTRB(
                          12, 8, 12, _selectionMode ? 100 : 20),
                      itemCount: reports.length,
                      itemBuilder: (ctx, i) => _buildCard(reports[i]),
                    ),
          if (_selectionMode)
            Positioned(
              bottom: 0, left: 0, right: 0,
              child: SelectionActionBar(
                selectedReports: selectedReports,
                onCancel: _clearSelection,
                onActionDone: _clearSelection,
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildCard(Report r) {
    final color = _statusColor(r.status);
    final isSelected = _selected.contains(r.reportNumber);

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(
          color: isSelected
              ? Theme.of(context).colorScheme.primary
              : Colors.grey.shade200,
          width: isSelected ? 2 : 1,
        ),
      ),
      color: isSelected
          ? Theme.of(context).colorScheme.primaryContainer.withOpacity(0.3)
          : null,
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: _selectionMode
            ? () => _toggleSelect(r.reportNumber)
            : () => showReportDetailSheet(context, r),
        onLongPress: () {
          if (!_selectionMode) setState(() => _selected.add(r.reportNumber));
        },
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              if (_selectionMode)
                Padding(
                  padding: const EdgeInsets.only(right: 10, top: 2),
                  child: Icon(
                    isSelected
                        ? Icons.check_circle
                        : Icons.radio_button_unchecked,
                    size: 20,
                    color: isSelected
                        ? Theme.of(context).colorScheme.primary
                        : Colors.grey.shade400,
                  ),
                ),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: [
                      Expanded(
                        child: Text(r.name,
                            style: const TextStyle(
                                fontWeight: FontWeight.bold, fontSize: 14),
                            overflow: TextOverflow.ellipsis),
                      ),
                      const SizedBox(width: 8),
                      _statusChip(r.status, color),
                    ]),
                    const SizedBox(height: 6),
                    Row(children: [
                      const Icon(Icons.tag, size: 13, color: Colors.grey),
                      const SizedBox(width: 3),
                      Text(r.reportNumber,
                          style: const TextStyle(
                              color: Colors.grey, fontSize: 12)),
                    ]),
                    const Divider(height: 14),
                    Row(
                      children: [
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              _metaRow(Icons.calendar_today, r.date.isNotEmpty ? '신고: ${r.date}' : ''),
                              if (r.responseDate.isNotEmpty) ...[
                                const SizedBox(height: 3),
                                _metaRow(Icons.event_available, '답변: ${r.responseDate}'),
                              ],
                              const SizedBox(height: 3),
                              _metaRow(Icons.business, r.agency),
                              if (r.manager.isNotEmpty) ...[
                                const SizedBox(height: 3),
                                _metaRow(Icons.person_outline, r.manager),
                              ],
                              if (r.fineInfo.isNotEmpty) ...[
                                const SizedBox(height: 3),
                                _metaRow(
                                    Icons.monetization_on_outlined, r.fineInfo),
                              ],
                            ],
                          ),
                        ),
                        if (r.carNumber.isNotEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 10, vertical: 5),
                            decoration: BoxDecoration(
                              color: Colors.blueGrey.shade50,
                              borderRadius: BorderRadius.circular(6),
                              border:
                                  Border.all(color: Colors.blueGrey.shade200),
                            ),
                            child: Text(r.carNumber,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    letterSpacing: 0.5)),
                          ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _metaRow(IconData icon, String text) => Row(children: [
        Icon(icon, size: 12, color: Colors.grey),
        const SizedBox(width: 4),
        Expanded(
          child: Text(text,
              style: const TextStyle(color: Colors.grey, fontSize: 12),
              overflow: TextOverflow.ellipsis),
        ),
      ]);

  Widget _statusChip(String status, Color color) => Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: color.withOpacity(0.1),
          border: Border.all(color: color.withOpacity(0.4)),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Text(status,
            style: TextStyle(
                color: color, fontSize: 11, fontWeight: FontWeight.bold)),
      );

  Color _statusColor(String s) {
    if (s == '일부수용') return const Color(0xFF43A047);
    if (s.contains('수용') && !s.contains('불')) return Colors.green;
    if (s.contains('불수용')) return Colors.red;
    if (s.contains('처리') || s.contains('진행')) return Colors.orange;
    return Colors.grey;
  }
}
