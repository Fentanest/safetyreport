import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../models/agency_stats.dart';
import '../providers/report_provider.dart';
import '../services/api_service.dart';
import 'filtered_list_screen.dart';

class StatisticsScreen extends StatefulWidget {
  const StatisticsScreen({super.key});

  @override
  State<StatisticsScreen> createState() => _StatisticsScreenState();
}

class _StatisticsScreenState extends State<StatisticsScreen>
    with SingleTickerProviderStateMixin {
  late TabController _tab;
  AgencyStats? _stats;
  bool _loading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _tab = TabController(length: 18, vsync: this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  @override
  void dispose() {
    _tab.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() { _loading = true; _error = null; });
    try {
      final p = context.read<ReportProvider>();
      final api = ApiService(baseUrl: p.baseUrl, apiKey: p.apiKey);
      final stats = await api.getStats();
      if (mounted) setState(() { _stats = stats; _loading = false; });
    } catch (e) {
      if (mounted) setState(() { _error = e.toString(); _loading = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('통계'),
        bottom: _GroupedTabBar(controller: _tab),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? _buildError()
              : TabBarView(
                  controller: _tab,
                  children: [
                    _StatsTable(rows: _stats!.traffic.byAgency,        showPerson: false, category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.traffic.byPerson,        showPerson: true,  category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.traffic.policeByAgency,  showPerson: false, category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.traffic.policeByPerson,  showPerson: true,  category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.traffic.otherByAgency,   showPerson: false, category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.traffic.otherByPerson,   showPerson: true,  category: 'traffic',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.byAgency,        showPerson: false, category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.byPerson,        showPerson: true,  category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.policeByAgency,  showPerson: false, category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.policeByPerson,  showPerson: true,  category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.otherByAgency,   showPerson: false, category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.parking.otherByPerson,   showPerson: true,  category: 'parking',  onRefresh: _load),
                    _StatsTable(rows: _stats!.other.byAgency,          showPerson: false, category: 'other',    onRefresh: _load),
                    _StatsTable(rows: _stats!.other.byPerson,          showPerson: true,  category: 'other',    onRefresh: _load),
                    _StatsTable(rows: _stats!.other.policeByAgency,    showPerson: false, category: 'other',    onRefresh: _load),
                    _StatsTable(rows: _stats!.other.policeByPerson,    showPerson: true,  category: 'other',    onRefresh: _load),
                    _StatsTable(rows: _stats!.other.otherByAgency,     showPerson: false, category: 'other',    onRefresh: _load),
                    _StatsTable(rows: _stats!.other.otherByPerson,     showPerson: true,  category: 'other',    onRefresh: _load),
                  ],
                ),
    );
  }

  Widget _buildError() {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(mainAxisSize: MainAxisSize.min, children: [
          const Icon(Icons.error_outline, size: 48, color: Colors.red),
          const SizedBox(height: 12),
          Text(_error!, textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 16),
          FilledButton.icon(
              icon: const Icon(Icons.refresh),
              label: const Text('다시 시도'),
              onPressed: _load),
        ]),
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────
class _StatsTable extends StatelessWidget {
  final List<AgencyStatRow> rows;
  final bool showPerson;
  final String category; // 'traffic' or 'other'
  final Future<void> Function() onRefresh;

  const _StatsTable({required this.rows, required this.showPerson, required this.category, required this.onRefresh});

  @override
  Widget build(BuildContext context) {
    if (rows.isEmpty) {
      return const Center(
        child: Text('데이터가 없습니다.', style: TextStyle(color: Colors.grey)),
      );
    }

    return RefreshIndicator(
      onRefresh: onRefresh,
      child: ListView.builder(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 20),
        itemCount: rows.length,
        itemBuilder: (context, i) => _RowCard(
            row: rows[i], showPerson: showPerson, rank: i + 1, category: category),
      ),
    );
  }
}

class _RowCard extends StatelessWidget {
  final AgencyStatRow row;
  final bool showPerson;
  final int rank;
  final String category;

  const _RowCard(
      {required this.row,
      required this.showPerson,
      required this.rank,
      required this.category});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () {
          final agency = row.agency;
          final person = showPerson ? row.person : '';
          Navigator.push(
            context,
            MaterialPageRoute(
              builder: (_) => FilteredListScreen(
                title: (showPerson && person.isNotEmpty)
                    ? '$agency / $person'
                    : agency,
                category: category,
                filter: (r) {
                  final agencyMatch = r.agency == agency;
                  final personMatch =
                      person.isEmpty || r.manager == person;
                  return agencyMatch && personMatch;
                },
              ),
            ),
          );
        },
        child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 헤더: 순위 + 기관명 + 총건수
            Row(
              children: [
                Container(
                  width: 24, height: 24,
                  decoration: BoxDecoration(
                    color: rank <= 3
                        ? [Colors.amber, Colors.grey.shade400, Colors.brown.shade300][rank - 1]
                        : scheme.surfaceContainerHighest,
                    shape: BoxShape.circle,
                  ),
                  child: Center(
                    child: Text('$rank',
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            color: rank <= 3 ? Colors.white : scheme.onSurface)),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(row.agency,
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 14),
                          overflow: TextOverflow.ellipsis),
                      if (showPerson && row.person.isNotEmpty)
                        Text(row.person,
                            style: TextStyle(
                                fontSize: 12, color: Colors.grey.shade600),
                            overflow: TextOverflow.ellipsis),
                    ],
                  ),
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text('${row.total}건',
                        style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.bold,
                            color: scheme.primary)),
                    const Text('총 처리',
                        style: TextStyle(fontSize: 10, color: Colors.grey)),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 10),
            // 통계 바
            Row(
              children: [
                _statBadge('과태료', row.fines, row.finesPct, Colors.red),
                const SizedBox(width: 8),
                _statBadge('경고/범칙금', row.warnings, row.warningsPct, Colors.orange),
                const SizedBox(width: 8),
                _statBadge('불수용', row.rejects, row.rejectsPct, Colors.grey),
              ],
            ),
            const SizedBox(height: 8),
            // 진행 바 (과태료 비율)
            if (row.total > 0) ...[
              ClipRRect(
                borderRadius: BorderRadius.circular(4),
                child: Row(
                  children: [
                    if (row.fines > 0)
                      Flexible(
                        flex: row.fines,
                        child: Container(height: 6, color: Colors.red.shade400),
                      ),
                    if (row.warnings > 0)
                      Flexible(
                        flex: row.warnings,
                        child: Container(height: 6, color: Colors.orange.shade400),
                      ),
                    if (row.rejects > 0)
                      Flexible(
                        flex: row.rejects,
                        child: Container(height: 6, color: Colors.grey.shade400),
                      ),
                    Flexible(
                      flex: row.total - row.fines - row.warnings - row.rejects,
                      child: Container(height: 6, color: Colors.blue.shade100),
                    ),
                  ],
                ),
              ),
            ],
          ],
        ),
      ),
    ),
  );
  }

  Widget _statBadge(String label, int count, double pct, Color color) {
    return Expanded(
      child: Container(
        padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 4),
        decoration: BoxDecoration(
          color: color.withOpacity(0.08),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withOpacity(0.2)),
        ),
        child: Column(
          children: [
            Text('$count',
                style: TextStyle(
                    fontSize: 15,
                    fontWeight: FontWeight.bold,
                    color: color)),
            Text('$label ($pct%)',
                style: TextStyle(fontSize: 10, color: color.withOpacity(0.8)),
                textAlign: TextAlign.center),
          ],
        ),
      ),
    );
  }
}

// ──────────────────────────────────────────────────────────────
class _GroupedTabBar extends StatefulWidget implements PreferredSizeWidget {
  final TabController controller;
  const _GroupedTabBar({required this.controller});

  @override
  Size get preferredSize => const Size.fromHeight(132);

  @override
  State<_GroupedTabBar> createState() => _GroupedTabBarState();
}

class _GroupedTabBarState extends State<_GroupedTabBar> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onTabChange);
  }

  void _onTabChange() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onTabChange);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    const subTabs = ['기관별', '담당자별', '경찰 기관', '경찰 담당자', '비경찰 기관', '비경찰 담당자'];
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        _buildGroup('교통위반', 0,  subTabs),
        const Divider(height: 1, thickness: 1, color: Colors.white24),
        _buildGroup('주정차위반', 6, subTabs),
        const Divider(height: 1, thickness: 1, color: Colors.white24),
        _buildGroup('기타위반', 12, subTabs),
      ],
    );
  }

  Widget _buildGroup(String title, int startIdx, List<String> labels) {
    return SizedBox(
      height: 43,
      child: Row(
        children: [
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(title,
                style: const TextStyle(
                    color: Colors.white70,
                    fontSize: 11,
                    fontWeight: FontWeight.w500)),
          ),
          Container(width: 1, height: 18, color: Colors.white24),
          Expanded(
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: List.generate(labels.length, (i) {
                  final idx = startIdx + i;
                  final selected = widget.controller.index == idx;
                  return GestureDetector(
                    onTap: () => widget.controller.animateTo(idx),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                      decoration: BoxDecoration(
                        border: Border(
                          bottom: BorderSide(
                            color: selected ? Colors.white : Colors.transparent,
                            width: 3,
                          ),
                        ),
                      ),
                      alignment: Alignment.center,
                      height: 43,
                      child: Text(
                        labels[i],
                        style: TextStyle(
                          color: selected ? Colors.white : Colors.white70,
                          fontSize: 13,
                          fontWeight:
                              selected ? FontWeight.bold : FontWeight.normal,
                        ),
                      ),
                    ),
                  );
                }),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
