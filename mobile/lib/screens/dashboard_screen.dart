import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:fl_chart/fl_chart.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';
import '../widgets/report_detail_sheet.dart';
import 'settings_screen.dart';
import 'watchlist_screen.dart';
import 'filtered_list_screen.dart';

class DashboardScreen extends StatefulWidget {
  const DashboardScreen({super.key});

  @override
  State<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends State<DashboardScreen> {
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<ReportProvider>().fetchSummary();
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ReportProvider>();
    final stats = provider.stats;

    return Scaffold(
      appBar: AppBar(
        title: const Text('대시보드'),
        actions: [
          IconButton(
            icon: const Icon(Icons.settings),
            tooltip: '설정',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const SettingsScreen()),
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: provider.fetchSummary,
        child: provider.isLoading && stats == null
            ? const Center(child: CircularProgressIndicator())
            : stats == null
                ? _buildErrorState(context, provider)
                : _buildContent(context, stats),
      ),
    );
  }

  // ── 에러 상태 ──────────────────────────────────────
  Widget _buildErrorState(BuildContext context, ReportProvider provider) {
    final error = provider.errorMessage;
    return LayoutBuilder(
      builder: (context, constraints) => SingleChildScrollView(
        physics: const AlwaysScrollableScrollPhysics(),
        child: ConstrainedBox(
          constraints: BoxConstraints(minHeight: constraints.maxHeight),
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(32),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.cloud_off_rounded,
                      size: 72, color: Colors.grey.shade400),
                  const SizedBox(height: 20),
                  const Text('서버에 연결할 수 없습니다',
                      style: TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  const Text('아래로 당겨 다시 시도하세요.',
                      style: TextStyle(color: Colors.grey, fontSize: 13)),
                  if (error != null) ...[
                    const SizedBox(height: 16),
                    Container(
                      width: double.infinity,
                      padding: const EdgeInsets.all(14),
                      decoration: BoxDecoration(
                        color: Colors.red.shade50,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(color: Colors.red.shade200),
                      ),
                      child: SelectableText(error,
                          style: TextStyle(
                              fontSize: 12,
                              color: Colors.red.shade800,
                              height: 1.6,
                              fontFamily: 'monospace')),
                    ),
                  ],
                  const SizedBox(height: 24),
                  FilledButton.icon(
                    icon: const Icon(Icons.refresh),
                    label: const Text('다시 시도'),
                    onPressed: provider.fetchSummary,
                  ),
                  const SizedBox(height: 12),
                  OutlinedButton.icon(
                    icon: const Icon(Icons.settings),
                    label: const Text('설정 확인'),
                    onPressed: () => Navigator.push(
                      context,
                      MaterialPageRoute(builder: (_) => const SettingsScreen()),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  // ── 메인 콘텐츠 ────────────────────────────────────
  Widget _buildContent(BuildContext context, DashboardStats stats) {
    final trafficTotal = stats.tFineCount + stats.tPenaltyCount +
        stats.tRejectCount + stats.tUnconfirmedCount;
    return SingleChildScrollView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildSummaryGrid(stats),
          const SizedBox(height: 16),
          if (trafficTotal > 0) ...[
            _buildTrafficCard(stats),
            const SizedBox(height: 16),
          ],
          _buildChartCard(stats),
          const SizedBox(height: 16),
          _buildWatchlistSection(context, stats.watchlist),
          const SizedBox(height: 16),
          _buildSectionHeader(
              '최근 답변 완료 (3일)', Icons.notifications_active, Colors.green),
          const SizedBox(height: 8),
          _buildRecentList(stats.recentAnswers),
        ],
      ),
    );
  }

  Widget _buildSectionHeader(String title, IconData icon, Color color) {
    return Row(
      children: [
        Icon(icon, size: 18, color: color),
        const SizedBox(width: 6),
        Text(title,
            style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
      ],
    );
  }

  // ── 요약 그리드 (6칸) ─────────────────────────────
  Widget _buildSummaryGrid(DashboardStats stats) {
    return GridView.count(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      crossAxisCount: 2,
      childAspectRatio: 1.65,
      mainAxisSpacing: 10,
      crossAxisSpacing: 10,
      children: [
        _buildStatCard('전체', stats.total, Colors.blue, Icons.assignment_rounded,
            filter: (r) => true),
        _buildStatCard('수용', stats.acceptCount, Colors.green,
            Icons.check_circle_rounded,
            filter: (r) =>
                r.status.contains('수용') && !r.status.contains('불')),
        _buildStatCard('일부수용', stats.partialCount, const Color(0xFF43A047),
            Icons.check_circle_outline_rounded,
            filter: (r) => r.status == '일부수용'),
        _buildStatCard('불수용/기타', stats.rejectCount, Colors.red,
            Icons.cancel_rounded,
            filter: (r) =>
                r.status.contains('불수용') || r.status == '기타'),
        _buildStatCard('처리 중', stats.processingCount, Colors.orange,
            Icons.pending_rounded,
            filter: (r) =>
                r.status.contains('처리') || r.status.contains('진행')),
        _buildStatCard('취하', stats.withdrawCount, Colors.grey,
            Icons.remove_circle_outline_rounded,
            filter: (r) => r.status == '취하'),
      ],
    );
  }

  Widget _buildStatCard(String label, int value, Color color, IconData icon,
      {required bool Function(Report) filter}) {
    return Card(
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(14),
        side: BorderSide(color: color.withOpacity(0.2)),
      ),
      color: color.withOpacity(0.06),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: value > 0
            ? () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => FilteredListScreen(
                      title: label,
                      category: 'all',
                      filter: filter,
                    ),
                  ),
                )
            : null,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Text(label,
                      style: TextStyle(
                          fontSize: 12,
                          fontWeight: FontWeight.w600,
                          color: color)),
                  Icon(icon, color: color.withOpacity(0.7), size: 20),
                ],
              ),
              Row(
                children: [
                  Text('$value건',
                      style: TextStyle(
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          color: color)),
                  if (value > 0) ...[
                    const Spacer(),
                    Icon(Icons.chevron_right,
                        size: 16, color: color.withOpacity(0.5)),
                  ],
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }

  // ── 교통위반 카드 ──────────────────────────────────
  Widget _buildTrafficCard(DashboardStats stats) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(children: [
              const Icon(Icons.directions_car, size: 18, color: Colors.blueGrey),
              const SizedBox(width: 6),
              const Text('교통위반 처리 현황',
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
            ]),
            const SizedBox(height: 14),
            Row(
              children: [
                _miniStat('과태료', stats.tFineCount, Colors.red,
                    filter: (r) => r.fineInfo.contains('과태료')),
                _miniStat('경고/범칙금', stats.tPenaltyCount, Colors.orange,
                    filter: (r) =>
                        r.fineInfo.contains('경고') ||
                        r.fineInfo.contains('범칙금')),
                _miniStat('불수용', stats.tRejectCount, Colors.grey,
                    filter: (r) =>
                        r.status.contains('불수용') || r.status == '기타'),
                _miniStat('미확인', stats.tUnconfirmedCount, Colors.blueGrey,
                    filter: (r) =>
                        r.fineInfo == '미확인' &&
                        !r.status.contains('불수용') &&
                        r.status != '기타'),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _miniStat(String label, int value, Color color,
      {required bool Function(Report) filter}) {
    return Expanded(
      child: InkWell(
        borderRadius: BorderRadius.circular(8),
        onTap: value > 0
            ? () => Navigator.push(
                  context,
                  MaterialPageRoute(
                    builder: (_) => FilteredListScreen(
                      title: '교통위반 — $label',
                      category: 'traffic',
                      filter: filter,
                    ),
                  ),
                )
            : null,
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 4),
          child: Column(
            children: [
              Text('$value',
                  style: TextStyle(
                      fontSize: 20, fontWeight: FontWeight.bold, color: color)),
              const SizedBox(height: 2),
              Text(label,
                  style: const TextStyle(fontSize: 11, color: Colors.grey),
                  textAlign: TextAlign.center),
            ],
          ),
        ),
      ),
    );
  }

  // ── 파이 차트 ──────────────────────────────────────
  Widget _buildChartCard(DashboardStats stats) {
    final total = stats.total;
    if (total == 0) return const SizedBox.shrink();

    final sections = [
      (stats.acceptCount, Colors.green, '수용'),
      (stats.partialCount, const Color(0xFF43A047), '일부수용'),
      (stats.rejectCount, Colors.red, '불수용'),
      (stats.processingCount, Colors.orange, '처리중'),
      (stats.withdrawCount, Colors.grey, '취하'),
    ].where((e) => e.$1 > 0).toList();

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Row(children: [
              const Icon(Icons.pie_chart, size: 18, color: Colors.blueGrey),
              const SizedBox(width: 6),
              const Text('처리 현황',
                  style:
                      TextStyle(fontSize: 15, fontWeight: FontWeight.bold)),
              const Spacer(),
              Text('총 $total건',
                  style: const TextStyle(color: Colors.grey, fontSize: 13)),
            ]),
            const SizedBox(height: 16),
            Row(
              children: [
                SizedBox(
                  height: 160,
                  width: 160,
                  child: PieChart(PieChartData(
                    sections: sections
                        .map((e) => PieChartSectionData(
                              value: e.$1.toDouble(),
                              color: e.$2,
                              title:
                                  '${(e.$1 / total * 100).toStringAsFixed(0)}%',
                              radius: 48,
                              titleStyle: const TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                  color: Colors.white),
                            ))
                        .toList(),
                    centerSpaceRadius: 32,
                    sectionsSpace: 2,
                  )),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: sections
                        .map((e) => Padding(
                              padding:
                                  const EdgeInsets.symmetric(vertical: 4),
                              child: Row(children: [
                                Container(
                                    width: 10,
                                    height: 10,
                                    decoration: BoxDecoration(
                                        color: e.$2,
                                        shape: BoxShape.circle)),
                                const SizedBox(width: 6),
                                Text(e.$3,
                                    style: const TextStyle(fontSize: 12)),
                                const Spacer(),
                                Text('${e.$1}건',
                                    style: const TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.w600)),
                              ]),
                            ))
                        .toList(),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ── 감시 목록 섹션 ────────────────────────────────
  Widget _buildWatchlistSection(BuildContext context, List<Report> items) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.bookmark, size: 18, color: Colors.blue),
            const SizedBox(width: 6),
            const Expanded(
              child: Text('감시 목록',
                  style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold)),
            ),
            TextButton.icon(
              icon: const Icon(Icons.open_in_new, size: 14),
              label: const Text('관리'),
              style: TextButton.styleFrom(
                  padding: EdgeInsets.zero,
                  minimumSize: const Size(0, 28)),
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const WatchlistScreen()),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (items.isEmpty)
          Container(
            width: double.infinity,
            padding: const EdgeInsets.symmetric(vertical: 16),
            decoration: BoxDecoration(
              color: Colors.grey.shade50,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.grey.shade200),
            ),
            child: Column(children: [
              Icon(Icons.bookmark_border, size: 32, color: Colors.grey.shade300),
              const SizedBox(height: 6),
              const Text('감시 중인 신고가 없습니다.',
                  style: TextStyle(color: Colors.grey, fontSize: 12)),
            ]),
          )
        else
          ...items.take(5).map((r) => _buildWatchItem(r)),
        if (items.length > 5)
          Center(
            child: TextButton(
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const WatchlistScreen()),
              ),
              child: Text('+ ${items.length - 5}건 더 보기'),
            ),
          ),
      ],
    );
  }

  Widget _buildWatchItem(Report r) {
    return Card(
      margin: const EdgeInsets.only(bottom: 6),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: () => showReportDetailSheet(context, r),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.bookmark, color: Colors.blue, size: 16),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(r.name,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontWeight: FontWeight.w600, fontSize: 14)),
                  ),
                  const SizedBox(width: 8),
                  _statusChip(r.status),
                ],
              ),
              const SizedBox(height: 8),
              if (r.reportNumber.isNotEmpty)
                _metaRow(Icons.tag, '신고번호', r.reportNumber),
              if (r.date.isNotEmpty)
                _metaRow(Icons.calendar_today, '신고일', r.date),
              if (r.responseDate.isNotEmpty)
                _metaRow(Icons.check_circle_outline, '답변일', r.responseDate),
              if (r.agency.isNotEmpty)
                _metaRow(Icons.business, '처리기관', r.agency),
              if (r.manager.isNotEmpty)
                _metaRow(Icons.person_outline, '담당자', r.manager),
              if (r.fineInfo.isNotEmpty && r.fineInfo != '미확인')
                _metaRow(Icons.monetization_on_outlined, '과태료/범칙금', r.fineInfo),
              if (r.carNumber.isNotEmpty)
                _metaRow(Icons.directions_car_outlined, '차량번호', r.carNumber),
            ],
          ),
        ),
      ),
    );
  }

  // ── 최근 답변 완료 리스트 ───────────────────────────
  Widget _buildRecentList(List<Report> reports) {
    if (reports.isEmpty) {
      return const Center(
        child: Padding(
          padding: EdgeInsets.all(24),
          child: Text('3일 내 답변 완료된 신고가 없습니다.',
              style: TextStyle(color: Colors.grey)),
        ),
      );
    }

    return ListView.separated(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      itemCount: reports.length,
      separatorBuilder: (_, __) => const SizedBox(height: 6),
      itemBuilder: (context, index) {
        final r = reports[index];
        return Card(
          margin: EdgeInsets.zero,
          child: InkWell(
            borderRadius: BorderRadius.circular(12),
            onTap: () => showReportDetailSheet(context, r),
            child: Padding(
              padding: const EdgeInsets.all(14),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(
                        child: Text(r.name,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                fontWeight: FontWeight.w600, fontSize: 14)),
                      ),
                      const SizedBox(width: 8),
                      _statusChip(r.status),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (r.reportNumber.isNotEmpty)
                    _metaRow(Icons.tag, '신고번호', r.reportNumber),
                  if (r.date.isNotEmpty)
                    _metaRow(Icons.calendar_today, '신고일', r.date),
                  if (r.responseDate.isNotEmpty)
                    _metaRow(Icons.check_circle_outline, '답변일', r.responseDate),
                  if (r.agency.isNotEmpty)
                    _metaRow(Icons.business, '처리기관', r.agency),
                  if (r.manager.isNotEmpty)
                    _metaRow(Icons.person_outline, '담당자', r.manager),
                  if (r.fineInfo.isNotEmpty)
                    _metaRow(Icons.monetization_on_outlined, '과태료/범칙금', r.fineInfo),
                  if (r.carNumber.isNotEmpty)
                    _metaRow(Icons.directions_car_outlined, '차량번호', r.carNumber),
                ],
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _metaRow(IconData icon, String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(top: 3),
      child: Row(children: [
        Icon(icon, size: 12, color: Colors.grey),
        const SizedBox(width: 4),
        Text('$label ', style: const TextStyle(fontSize: 11, color: Colors.grey)),
        Expanded(
          child: Text(value,
              style: const TextStyle(fontSize: 11),
              overflow: TextOverflow.ellipsis),
        ),
      ]),
    );
  }

  Widget _statusChip(String status) {
    final color = _statusColor(status);
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: color.withOpacity(0.4)),
      ),
      child: Text(status,
          style: TextStyle(
              color: color, fontSize: 11, fontWeight: FontWeight.bold)),
    );
  }

  Color _statusColor(String status) {
    if (status == '일부수용') return const Color(0xFF43A047);
    if (status.contains('수용') && !status.contains('불')) return Colors.green;
    if (status.contains('불수용')) return Colors.red;
    if (status.contains('처리') || status.contains('진행')) return Colors.orange;
    return Colors.grey;
  }
}
