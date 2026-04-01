import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';
import '../widgets/report_detail_sheet.dart';
import '../widgets/search_filter_sheet.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final provider = context.read<ReportProvider>();
      if (provider.trafficReports.isEmpty && provider.otherReports.isEmpty) {
        provider.fetchTrafficReports();
        provider.fetchOtherReports();
      }
    });
  }

  void _openSearchPopup() {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (_) =>
          SearchFilterSheet(provider: context.read<ReportProvider>()),
    );
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ReportProvider>();
    final allFiltered = [
      ...provider.filteredTrafficReports,
      ...provider.filteredOtherReports,
    ];
    final hasFilter = provider.hasFilter;
    final labels = provider.filter.activeLabels;

    return Scaffold(
      appBar: AppBar(
        title: const Text('검색'),
        actions: [
          if (hasFilter)
            TextButton(
              onPressed: provider.clearFilter,
              child: const Text('초기화', style: TextStyle(color: Colors.white)),
            ),
          IconButton(
            icon: Badge(
              isLabelVisible: hasFilter,
              child: const Icon(Icons.tune),
            ),
            tooltip: '검색 조건',
            onPressed: _openSearchPopup,
          ),
        ],
      ),
      body: Column(
        children: [
          // 활성 필터 요약 바
          if (hasFilter && labels.isNotEmpty)
            Container(
              color: Colors.blue.shade50,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              child: SingleChildScrollView(
                scrollDirection: Axis.horizontal,
                child: Row(
                  children: labels.map((l) => Padding(
                    padding: const EdgeInsets.only(right: 6),
                    child: Chip(
                      label: Text(l, style: const TextStyle(fontSize: 11)),
                      backgroundColor: Colors.blue.shade100,
                      padding: EdgeInsets.zero,
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                  )).toList(),
                ),
              ),
            ),
          // 결과 헤더
          if (hasFilter)
            Container(
              color: Theme.of(context).colorScheme.surfaceContainerHighest,
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Row(children: [
                const Icon(Icons.format_list_bulleted, size: 16),
                const SizedBox(width: 6),
                Text('검색 결과: ${allFiltered.length}건',
                    style: const TextStyle(fontWeight: FontWeight.bold)),
              ]),
            ),
          Expanded(
            child: provider.isLoading && allFiltered.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : !hasFilter
                    ? Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.search,
                                size: 72, color: Colors.grey.shade300),
                            const SizedBox(height: 16),
                            const Text('검색 조건을 설정하세요.',
                                style: TextStyle(
                                    color: Colors.grey, fontSize: 15)),
                            const SizedBox(height: 12),
                            FilledButton.icon(
                              icon: const Icon(Icons.tune),
                              label: const Text('검색 조건 설정'),
                              onPressed: _openSearchPopup,
                            ),
                          ],
                        ),
                      )
                    : allFiltered.isEmpty
                        ? const Center(child: Text('검색 결과가 없습니다.'))
                        : ListView.builder(
                            padding:
                                const EdgeInsets.fromLTRB(12, 8, 12, 20),
                            itemCount: allFiltered.length,
                            itemBuilder: (context, index) =>
                                _buildReportCard(allFiltered[index]),
                          ),
          ),
        ],
      ),
      floatingActionButton: !hasFilter
          ? FloatingActionButton.extended(
              onPressed: _openSearchPopup,
              icon: const Icon(Icons.search),
              label: const Text('검색'),
            )
          : null,
    );
  }

  Widget _buildReportCard(Report r) {
    final color = _statusColor(r.status);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.grey.shade200),
      ),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => showReportDetailSheet(context, r),
        child: Padding(
          padding: const EdgeInsets.all(14),
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
                    style: const TextStyle(color: Colors.grey, fontSize: 12)),
              ]),
              const Divider(height: 14),
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _metaRow(Icons.calendar_today, r.date),
                        const SizedBox(height: 3),
                        _metaRow(Icons.business, r.agency),
                        if (r.fineInfo.isNotEmpty) ...[
                          const SizedBox(height: 3),
                          _metaRow(Icons.monetization_on_outlined, r.fineInfo),
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
                        border: Border.all(color: Colors.blueGrey.shade200),
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
      ),
    );
  }

  Widget _metaRow(IconData icon, String text) => Row(children: [
        Icon(icon, size: 12, color: Colors.grey),
        const SizedBox(width: 4),
        Text(text, style: const TextStyle(color: Colors.grey, fontSize: 12)),
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
