import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/report_provider.dart';
import '../models/report.dart';

class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key});

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _queryCtrl = TextEditingController();
  String _selectedStatus = '';
  String _startDate = '';
  String _endDate = '';
  bool _searched = false;

  static const List<String> _statusOptions = [
    '',
    '수용',
    '일부수용',
    '불수용',
    '처리중',
    '취하',
  ];

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

  @override
  void dispose() {
    _queryCtrl.dispose();
    super.dispose();
  }

  void _applySearch() {
    context.read<ReportProvider>().setFilter(
          query: _queryCtrl.text.trim(),
          status: _selectedStatus,
          startDate: _startDate,
          endDate: _endDate,
        );
    setState(() => _searched = true);
    FocusScope.of(context).unfocus();
  }

  void _clearSearch() {
    _queryCtrl.clear();
    setState(() {
      _selectedStatus = '';
      _startDate = '';
      _endDate = '';
      _searched = false;
    });
    context.read<ReportProvider>().clearFilter();
  }

  Future<void> _pickDate(bool isStart) async {
    final now = DateTime.now();
    final initial = isStart
        ? (_startDate.isNotEmpty ? DateTime.parse(_startDate) : now)
        : (_endDate.isNotEmpty ? DateTime.parse(_endDate) : now);
    final picked = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: DateTime(2020),
      lastDate: now,
    );
    if (picked != null) {
      final formatted =
          '${picked.year}-${picked.month.toString().padLeft(2, '0')}-${picked.day.toString().padLeft(2, '0')}';
      setState(() {
        if (isStart) {
          _startDate = formatted;
        } else {
          _endDate = formatted;
        }
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final provider = context.watch<ReportProvider>();
    final allFiltered = [
      ...provider.filteredTrafficReports,
      ...provider.filteredOtherReports,
    ];

    return Scaffold(
      appBar: AppBar(
        title: const Text('상세 검색'),
        actions: [
          if (provider.hasFilter)
            TextButton(
              onPressed: _clearSearch,
              child: const Text('초기화', style: TextStyle(color: Colors.white)),
            ),
        ],
      ),
      body: Column(
        children: [
          _buildFilterPanel(),
          const Divider(height: 1),
          if (_searched) _buildResultHeader(allFiltered.length),
          Expanded(
            child: provider.isLoading && allFiltered.isEmpty
                ? const Center(child: CircularProgressIndicator())
                : !_searched
                    ? const Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Icon(Icons.search, size: 64, color: Colors.grey),
                            SizedBox(height: 16),
                            Text('검색 조건을 입력 후 검색하세요.',
                                style: TextStyle(color: Colors.grey)),
                          ],
                        ),
                      )
                    : allFiltered.isEmpty
                        ? const Center(child: Text('검색 결과가 없습니다.'))
                        : ListView.builder(
                            padding: const EdgeInsets.all(8),
                            itemCount: allFiltered.length,
                            itemBuilder: (context, index) =>
                                _buildReportCard(allFiltered[index]),
                          ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilterPanel() {
    return Container(
      color: Theme.of(context).colorScheme.surface,
      padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
      child: Column(
        children: [
          // 신고명 검색어
          TextField(
            controller: _queryCtrl,
            decoration: const InputDecoration(
              labelText: '신고명 / 신고번호 / 기관명 / 차량번호',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.search),
              isDense: true,
            ),
            onSubmitted: (_) => _applySearch(),
          ),
          const SizedBox(height: 10),
          // 처리상태 + 검색 버튼
          Row(
            children: [
              Expanded(
                child: DropdownButtonFormField<String>(
                  value: _selectedStatus,
                  decoration: const InputDecoration(
                    labelText: '처리상태',
                    border: OutlineInputBorder(),
                    isDense: true,
                  ),
                  items: _statusOptions
                      .map((s) => DropdownMenuItem(
                            value: s,
                            child: Text(s.isEmpty ? '전체' : s),
                          ))
                      .toList(),
                  onChanged: (v) => setState(() => _selectedStatus = v ?? ''),
                ),
              ),
              const SizedBox(width: 8),
              ElevatedButton.icon(
                onPressed: _applySearch,
                icon: const Icon(Icons.search, size: 18),
                label: const Text('검색'),
                style: ElevatedButton.styleFrom(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 13),
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          // 날짜 범위
          Row(
            children: [
              Expanded(
                child: _DatePickerField(
                  label: '시작일',
                  value: _startDate,
                  onTap: () => _pickDate(true),
                  onClear: () => setState(() => _startDate = ''),
                ),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 8),
                child: Text('~', style: TextStyle(fontSize: 18)),
              ),
              Expanded(
                child: _DatePickerField(
                  label: '종료일',
                  value: _endDate,
                  onTap: () => _pickDate(false),
                  onClear: () => setState(() => _endDate = ''),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }

  Widget _buildResultHeader(int count) {
    return Container(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: [
          const Icon(Icons.format_list_bulleted, size: 16),
          const SizedBox(width: 6),
          Text(
            '검색 결과: $count건',
            style: const TextStyle(fontWeight: FontWeight.bold),
          ),
        ],
      ),
    );
  }

  Widget _buildReportCard(Report report) {
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 5, horizontal: 4),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Text(
                    report.name,
                    style: const TextStyle(
                        fontWeight: FontWeight.bold, fontSize: 15),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                _buildStatusChip(report.status),
              ],
            ),
            const SizedBox(height: 6),
            Row(
              children: [
                const Icon(Icons.numbers, size: 14, color: Colors.grey),
                const SizedBox(width: 4),
                Text(report.reportNumber,
                    style: const TextStyle(color: Colors.grey, fontSize: 12)),
              ],
            ),
            const Divider(height: 16),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text('신고일: ${report.date}',
                        style: const TextStyle(fontSize: 12)),
                    Text('처리기관: ${report.agency}',
                        style: const TextStyle(fontSize: 12)),
                  ],
                ),
                if (report.carNumber.isNotEmpty)
                  Container(
                    padding: const EdgeInsets.symmetric(
                        horizontal: 8, vertical: 4),
                    decoration: BoxDecoration(
                      color: Colors.blueGrey.withOpacity(0.1),
                      borderRadius: BorderRadius.circular(4),
                    ),
                    child: Text(
                      report.carNumber,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 12),
                    ),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStatusChip(String status) {
    Color color = Colors.grey;
    if (status.contains('수용') && !status.contains('불')) {
      color = Colors.green;
    }
    if (status.contains('불수용')) color = Colors.red;
    if (status.contains('처리')) color = Colors.orange;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(0.1),
        border: Border.all(color: color.withOpacity(0.5)),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Text(
        status,
        style: TextStyle(
            color: color, fontSize: 11, fontWeight: FontWeight.bold),
      ),
    );
  }
}

class _DatePickerField extends StatelessWidget {
  final String label;
  final String value;
  final VoidCallback onTap;
  final VoidCallback onClear;

  const _DatePickerField({
    required this.label,
    required this.value,
    required this.onTap,
    required this.onClear,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: InputDecorator(
        decoration: InputDecoration(
          labelText: label,
          border: const OutlineInputBorder(),
          isDense: true,
          suffixIcon: value.isNotEmpty
              ? IconButton(
                  icon: const Icon(Icons.clear, size: 16),
                  onPressed: onClear,
                )
              : const Icon(Icons.calendar_today, size: 16),
        ),
        child: Text(
          value.isEmpty ? '날짜 선택' : value,
          style: TextStyle(
            color: value.isEmpty ? Colors.grey : null,
            fontSize: 14,
          ),
        ),
      ),
    );
  }
}
