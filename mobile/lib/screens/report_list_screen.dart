import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';
import 'package:font_awesome_flutter/font_awesome_flutter.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';
import 'settings_screen.dart';

class ReportListScreen extends StatefulWidget {
  const ReportListScreen({super.key});

  @override
  State<ReportListScreen> createState() => _ReportListScreenState();
}

class _ReportListScreenState extends State<ReportListScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<ReportProvider>().fetchTrafficReports();
      context.read<ReportProvider>().fetchOtherReports();
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ReportProvider>();

    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('신고 내역'),
          actions: [
            IconButton(
              icon: Badge(
                isLabelVisible: provider.hasFilter,
                child: const Icon(Icons.filter_list),
              ),
              tooltip: '검색/필터',
              onPressed: () => _showSearchPopup(context),
            ),
            IconButton(
              icon: const Icon(FontAwesomeIcons.wordpress),
              tooltip: '제작자 블로그',
              onPressed: () async {
                final url = Uri.parse('https://hb.worklazy.net/mysafetyreport/');
                await launchUrl(url, mode: LaunchMode.externalApplication);
              },
            ),
            IconButton(
              icon: const Icon(Icons.settings),
              tooltip: '설정',
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              ),
            ),
          ],
          bottom: const TabBar(
            labelColor: Colors.white,
            unselectedLabelColor: Colors.white70,
            indicatorColor: Colors.white,
            indicatorWeight: 3,
            tabs: [
              Tab(text: '교통위반'),
              Tab(text: '기타위반'),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            _buildTab(provider, provider.filteredTrafficReports,
                provider.fetchTrafficReports),
            _buildTab(provider, provider.filteredOtherReports,
                provider.fetchOtherReports),
          ],
        ),
      ),
    );
  }

  Widget _buildTab(ReportProvider provider, List<Report> reports,
      Future<void> Function() onRefresh) {
    return RefreshIndicator(
      onRefresh: onRefresh,
      child: provider.isLoading && reports.isEmpty
          ? const Center(child: CircularProgressIndicator())
          : reports.isEmpty
              ? Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.inbox_rounded,
                          size: 56, color: Colors.grey.shade400),
                      const SizedBox(height: 12),
                      Text(
                        provider.hasFilter ? '검색 결과가 없습니다.' : '신고 내역이 없습니다.',
                        style:
                            const TextStyle(color: Colors.grey, fontSize: 15),
                      ),
                    ],
                  ),
                )
              : ListView.builder(
                  padding: const EdgeInsets.fromLTRB(12, 10, 12, 20),
                  itemCount: reports.length,
                  itemBuilder: (context, index) =>
                      _buildReportCard(reports[index]),
                ),
    );
  }

  void _showSearchPopup(BuildContext context) {
    final provider = context.read<ReportProvider>();
    final queryCtrl = TextEditingController();
    String selectedStatus = '';
    String startDate = '';
    String endDate = '';

    const statusOptions = ['', '수용', '일부수용', '불수용', '처리중', '취하'];

    Future<String?> pickDate(BuildContext ctx) async {
      final now = DateTime.now();
      final picked = await showDatePicker(
        context: ctx,
        initialDate: now,
        firstDate: DateTime(2020),
        lastDate: now,
      );
      if (picked == null) return null;
      return '${picked.year}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
    }

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheet) => Padding(
          padding: EdgeInsets.only(
            bottom: MediaQuery.of(ctx).viewInsets.bottom,
            left: 16,
            right: 16,
            top: 20,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // 핸들
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 16),
                  decoration: BoxDecoration(
                    color: Colors.grey.shade300,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  const Text('상세 검색',
                      style:
                          TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                  if (provider.hasFilter)
                    TextButton(
                      onPressed: () {
                        provider.clearFilter();
                        Navigator.pop(ctx);
                      },
                      child: const Text('초기화'),
                    ),
                ],
              ),
              const SizedBox(height: 14),
              TextField(
                controller: queryCtrl,
                decoration: const InputDecoration(
                  labelText: '신고명 / 신고번호 / 기관명 / 차량번호',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.search),
                  isDense: true,
                ),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: selectedStatus,
                decoration: const InputDecoration(
                  labelText: '처리 상태',
                  border: OutlineInputBorder(),
                  prefixIcon: Icon(Icons.task_alt),
                  isDense: true,
                ),
                items: statusOptions
                    .map((s) => DropdownMenuItem(
                          value: s,
                          child: Text(s.isEmpty ? '전체' : s),
                        ))
                    .toList(),
                onChanged: (v) => setSheet(() => selectedStatus = v ?? ''),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.calendar_today, size: 15),
                      label: Text(startDate.isEmpty ? '시작일' : startDate,
                          style: const TextStyle(fontSize: 13)),
                      onPressed: () async {
                        final d = await pickDate(ctx);
                        if (d != null) setSheet(() => startDate = d);
                      },
                    ),
                  ),
                  const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 8),
                      child: Text('~')),
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.calendar_today, size: 15),
                      label: Text(endDate.isEmpty ? '종료일' : endDate,
                          style: const TextStyle(fontSize: 13)),
                      onPressed: () async {
                        final d = await pickDate(ctx);
                        if (d != null) setSheet(() => endDate = d);
                      },
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 16),
              FilledButton.icon(
                icon: const Icon(Icons.search, size: 18),
                label: const Text('검색 적용'),
                style: FilledButton.styleFrom(
                    padding: const EdgeInsets.symmetric(vertical: 14)),
                onPressed: () {
                  provider.setFilter(
                    query: queryCtrl.text.trim(),
                    status: selectedStatus,
                    startDate: startDate,
                    endDate: endDate,
                  );
                  Navigator.pop(ctx);
                },
              ),
              const SizedBox(height: 20),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildReportCard(Report report) {
    final color = _statusColor(report.status);
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: BorderSide(color: Colors.grey.shade200),
      ),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    report.name,
                    style: const TextStyle(
                        fontWeight: FontWeight.bold, fontSize: 14),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                _statusChip(report.status, color),
              ],
            ),
            const SizedBox(height: 6),
            Row(
              children: [
                const Icon(Icons.tag, size: 13, color: Colors.grey),
                const SizedBox(width: 3),
                Text(report.reportNumber,
                    style: const TextStyle(color: Colors.grey, fontSize: 12)),
              ],
            ),
            const Divider(height: 14),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _metaRow(Icons.calendar_today, report.date),
                    const SizedBox(height: 3),
                    _metaRow(Icons.business, report.agency),
                  ],
                ),
                if (report.carNumber.isNotEmpty)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: Colors.blueGrey.shade50,
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: Colors.blueGrey.shade200),
                    ),
                    child: Text(
                      report.carNumber,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 13,
                          letterSpacing: 0.5),
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _metaRow(IconData icon, String text) {
    return Row(
      children: [
        Icon(icon, size: 12, color: Colors.grey),
        const SizedBox(width: 4),
        Text(text, style: const TextStyle(color: Colors.grey, fontSize: 12)),
      ],
    );
  }

  Widget _statusChip(String status, Color color) {
    return Container(
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
  }

  Color _statusColor(String status) {
    if (status.contains('수용') && !status.contains('불')) return Colors.green;
    if (status.contains('불수용')) return Colors.red;
    if (status.contains('처리') || status.contains('진행')) return Colors.orange;
    return Colors.grey;
  }
}
