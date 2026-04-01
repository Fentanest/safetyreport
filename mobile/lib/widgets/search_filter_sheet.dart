import 'package:flutter/material.dart';
import '../providers/report_provider.dart';

/// 신고 리스트 / 검색탭 공용 상세검색 팝업
class SearchFilterSheet extends StatefulWidget {
  final ReportProvider provider;
  const SearchFilterSheet({super.key, required this.provider});

  @override
  State<SearchFilterSheet> createState() => _SearchFilterSheetState();
}

class _SearchFilterSheetState extends State<SearchFilterSheet> {
  late final TextEditingController _nameCtrl;
  late final TextEditingController _numCtrl;
  late final TextEditingController _agencyCtrl;
  late final TextEditingController _managerCtrl;
  late final TextEditingController _carCtrl;
  late final TextEditingController _lawCtrl;
  late final TextEditingController _locationCtrl;
  late final TextEditingController _fineCtrl;
  late final TextEditingController _reportContentCtrl;
  late final TextEditingController _processContentCtrl;
  late final TextEditingController _occurTimeStartCtrl;
  late final TextEditingController _occurTimeEndCtrl;

  late String _status;
  late String _reportDateStart;
  late String _reportDateEnd;
  late String _occurDateStart;
  late String _occurDateEnd;
  late String _responseDateStart;
  late String _responseDateEnd;
  late bool _excludePolice;
  late bool _onlyPolice;

  static const _statusOptions = [
    '', '수용', '일부수용', '불수용', '처리중', '취하', '기타',
  ];

  @override
  void initState() {
    super.initState();
    final f = widget.provider.filter;
    _nameCtrl            = TextEditingController(text: f.name);
    _numCtrl             = TextEditingController(text: f.reportNumber);
    _agencyCtrl          = TextEditingController(text: f.agency);
    _managerCtrl         = TextEditingController(text: f.manager);
    _carCtrl             = TextEditingController(text: f.carNumber);
    _lawCtrl             = TextEditingController(text: f.law);
    _locationCtrl        = TextEditingController(text: f.location);
    _fineCtrl            = TextEditingController(text: f.fine);
    _reportContentCtrl   = TextEditingController(text: f.reportContent);
    _processContentCtrl  = TextEditingController(text: f.processContent);
    _occurTimeStartCtrl  = TextEditingController(text: f.occurTimeStart);
    _occurTimeEndCtrl    = TextEditingController(text: f.occurTimeEnd);
    _status              = f.status;
    _reportDateStart     = f.reportDateStart;
    _reportDateEnd       = f.reportDateEnd;
    _occurDateStart      = f.occurDateStart;
    _occurDateEnd        = f.occurDateEnd;
    _responseDateStart   = f.responseDateStart;
    _responseDateEnd     = f.responseDateEnd;
    _excludePolice       = f.excludePolice;
    _onlyPolice          = f.onlyPolice;
  }

  @override
  void dispose() {
    for (final c in [
      _nameCtrl, _numCtrl, _agencyCtrl, _managerCtrl, _carCtrl,
      _lawCtrl, _locationCtrl, _fineCtrl, _reportContentCtrl,
      _processContentCtrl, _occurTimeStartCtrl, _occurTimeEndCtrl,
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<String?> _pickDate(String current) async {
    final now = DateTime.now();
    final init = current.isNotEmpty
        ? DateTime.tryParse(current) ?? now
        : now;
    final picked = await showDatePicker(
      context: context,
      initialDate: init,
      firstDate: DateTime(2020),
      lastDate: now,
    );
    if (picked == null) return null;
    return '${picked.year}-${picked.month.toString().padLeft(2, '0')}-'
        '${picked.day.toString().padLeft(2, '0')}';
  }

  void _apply() {
    widget.provider.setFilter(ReportFilter(
      name:              _nameCtrl.text.trim(),
      reportNumber:      _numCtrl.text.trim(),
      agency:            _agencyCtrl.text.trim(),
      manager:           _managerCtrl.text.trim(),
      carNumber:         _carCtrl.text.trim(),
      law:               _lawCtrl.text.trim(),
      location:          _locationCtrl.text.trim(),
      fine:              _fineCtrl.text.trim(),
      reportContent:     _reportContentCtrl.text.trim(),
      processContent:    _processContentCtrl.text.trim(),
      status:            _status,
      reportDateStart:   _reportDateStart,
      reportDateEnd:     _reportDateEnd,
      occurDateStart:    _occurDateStart,
      occurDateEnd:      _occurDateEnd,
      responseDateStart: _responseDateStart,
      responseDateEnd:   _responseDateEnd,
      occurTimeStart:    _occurTimeStartCtrl.text.trim(),
      occurTimeEnd:      _occurTimeEndCtrl.text.trim(),
      excludePolice:     _excludePolice,
      onlyPolice:        _onlyPolice,
    ));
    Navigator.pop(context);
  }

  void _clear() {
    widget.provider.clearFilter();
    Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
        left: 20, right: 20, top: 16,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // 핸들
            Center(
              child: Container(
                width: 40, height: 4,
                margin: const EdgeInsets.only(bottom: 12),
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
                    style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
                TextButton(onPressed: _clear, child: const Text('전체 초기화')),
              ],
            ),
            const SizedBox(height: 12),

            // ── 신고 기본 정보 ──────────────────────────
            _sectionLabel('신고 기본 정보'),
            _input(_nameCtrl, '신고명', Icons.description_outlined),
            const SizedBox(height: 8),
            _input(_numCtrl, '신고번호', Icons.tag),
            const SizedBox(height: 8),
            _input(_carCtrl, '차량번호', Icons.directions_car_outlined),
            const SizedBox(height: 8),
            _input(_locationCtrl, '위반장소', Icons.location_on_outlined),
            const SizedBox(height: 8),
            _input(_lawCtrl, '위반법규', Icons.gavel_outlined),
            const SizedBox(height: 8),
            _input(_reportContentCtrl, '신고내용', Icons.article_outlined),

            const SizedBox(height: 16),

            // ── 처리 정보 ───────────────────────────────
            _sectionLabel('처리 정보'),
            _input(_agencyCtrl, '처리기관', Icons.business_outlined),
            const SizedBox(height: 8),
            _input(_managerCtrl, '담당자', Icons.person_outline),
            const SizedBox(height: 8),
            _input(_fineCtrl, '과태료/범칙금', Icons.monetization_on_outlined),
            const SizedBox(height: 8),
            _input(_processContentCtrl, '처리내용', Icons.task_alt_outlined),
            const SizedBox(height: 8),
            DropdownButtonFormField<String>(
              value: _status,
              decoration: const InputDecoration(
                labelText: '처리상태',
                border: OutlineInputBorder(),
                prefixIcon: Icon(Icons.checklist_outlined),
                isDense: true,
              ),
              items: _statusOptions
                  .map((s) => DropdownMenuItem(
                        value: s,
                        child: Text(s.isEmpty ? '전체' : s),
                      ))
                  .toList(),
              onChanged: (v) => setState(() => _status = v ?? ''),
            ),
            const SizedBox(height: 10),
            // 경찰기관 토글
            Row(children: [
              Expanded(
                child: _toggleChip(
                  '경찰기관 제외',
                  _excludePolice,
                  () => setState(() {
                    _excludePolice = !_excludePolice;
                    if (_excludePolice) _onlyPolice = false;
                  }),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: _toggleChip(
                  '경찰기관만',
                  _onlyPolice,
                  () => setState(() {
                    _onlyPolice = !_onlyPolice;
                    if (_onlyPolice) _excludePolice = false;
                  }),
                ),
              ),
            ]),

            const SizedBox(height: 16),

            // ── 날짜/시각 범위 ───────────────────────────
            _sectionLabel('날짜 / 시각 범위'),
            _dateRange('신고일', _reportDateStart, _reportDateEnd,
                Colors.blue,
                (d) => setState(() => _reportDateStart = d),
                (d) => setState(() => _reportDateEnd = d)),
            const SizedBox(height: 8),
            _dateRange('발생일', _occurDateStart, _occurDateEnd,
                Colors.green,
                (d) => setState(() => _occurDateStart = d),
                (d) => setState(() => _occurDateEnd = d)),
            const SizedBox(height: 8),
            _dateRange('답변일', _responseDateStart, _responseDateEnd,
                Colors.orange,
                (d) => setState(() => _responseDateStart = d),
                (d) => setState(() => _responseDateEnd = d)),
            const SizedBox(height: 8),
            // 발생시각
            Row(children: [
              Container(
                width: 56,
                padding: const EdgeInsets.only(right: 8),
                child: Text('발생시각',
                    style: TextStyle(
                        fontSize: 12,
                        color: Colors.grey.shade600,
                        fontWeight: FontWeight.w600)),
              ),
              Expanded(
                child: TextField(
                  controller: _occurTimeStartCtrl,
                  decoration: const InputDecoration(
                    hintText: '14:30',
                    border: OutlineInputBorder(),
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 10, vertical: 10),
                  ),
                ),
              ),
              const Padding(
                  padding: EdgeInsets.symmetric(horizontal: 6),
                  child: Text('~')),
              Expanded(
                child: TextField(
                  controller: _occurTimeEndCtrl,
                  decoration: const InputDecoration(
                    hintText: '15:00',
                    border: OutlineInputBorder(),
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 10, vertical: 10),
                  ),
                ),
              ),
            ]),

            const SizedBox(height: 20),
            FilledButton.icon(
              icon: const Icon(Icons.search, size: 18),
              label: const Text('검색 적용'),
              style: FilledButton.styleFrom(
                  padding: const EdgeInsets.symmetric(vertical: 14)),
              onPressed: _apply,
            ),
            const SizedBox(height: 20),
          ],
        ),
      ),
    );
  }

  Widget _sectionLabel(String label) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(label,
          style: TextStyle(
              fontSize: 12,
              fontWeight: FontWeight.bold,
              color: Colors.grey.shade600,
              letterSpacing: 0.5)),
    );
  }

  Widget _input(TextEditingController ctrl, String label, IconData icon) {
    return TextField(
      controller: ctrl,
      decoration: InputDecoration(
        labelText: label,
        border: const OutlineInputBorder(),
        prefixIcon: Icon(icon, size: 18),
        isDense: true,
      ),
    );
  }

  Widget _toggleChip(String label, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(vertical: 10),
        decoration: BoxDecoration(
          color: active ? Colors.blue.shade50 : Colors.grey.shade100,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
              color: active ? Colors.blue : Colors.grey.shade300, width: 1.5),
        ),
        child: Center(
          child: Text(label,
              style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w600,
                  color: active ? Colors.blue : Colors.grey.shade600)),
        ),
      ),
    );
  }

  Widget _dateRange(
    String label,
    String start,
    String end,
    Color color,
    ValueChanged<String> onStart,
    ValueChanged<String> onEnd,
  ) {
    return Row(children: [
      Container(
        width: 40,
        padding: const EdgeInsets.only(right: 8),
        child: Text(label,
            style: TextStyle(
                fontSize: 12,
                color: color,
                fontWeight: FontWeight.w600)),
      ),
      Expanded(
        child: _dateTap(start.isEmpty ? '시작일' : start, color,
            () async {
          final d = await _pickDate(start);
          if (d != null) onStart(d);
        }, start.isNotEmpty ? () => onStart('') : null),
      ),
      const Padding(
          padding: EdgeInsets.symmetric(horizontal: 6),
          child: Text('~')),
      Expanded(
        child: _dateTap(end.isEmpty ? '종료일' : end, color,
            () async {
          final d = await _pickDate(end);
          if (d != null) onEnd(d);
        }, end.isNotEmpty ? () => onEnd('') : null),
      ),
    ]);
  }

  Widget _dateTap(String label, Color color, VoidCallback onTap,
      VoidCallback? onClear) {
    final hasValue = label != '시작일' && label != '종료일';
    return OutlinedButton(
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
        side: BorderSide(color: hasValue ? color : Colors.grey.shade300),
        foregroundColor: hasValue ? color : Colors.grey,
      ),
      onPressed: onTap,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Expanded(
            child: Text(label,
                style: const TextStyle(fontSize: 12),
                overflow: TextOverflow.ellipsis,
                textAlign: TextAlign.center),
          ),
          if (onClear != null)
            GestureDetector(
              onTap: onClear,
              child: Icon(Icons.close, size: 14, color: color),
            ),
        ],
      ),
    );
  }
}
