class AgencyStatRow {
  final String agency;
  final String person;
  final int total;
  final int fines;
  final double finesPct;
  final int warnings;
  final double warningsPct;
  final int rejects;
  final double rejectsPct;

  const AgencyStatRow({
    required this.agency,
    required this.person,
    required this.total,
    required this.fines,
    required this.finesPct,
    required this.warnings,
    required this.warningsPct,
    required this.rejects,
    required this.rejectsPct,
  });

  factory AgencyStatRow.fromJson(Map<String, dynamic> json) {
    return AgencyStatRow(
      agency: json['agency']?.toString() ?? '',
      person: json['person']?.toString() ?? '',
      total: (json['total'] as num?)?.toInt() ?? 0,
      fines: (json['fines'] as num?)?.toInt() ?? 0,
      finesPct: (json['fines_pct'] as num?)?.toDouble() ?? 0.0,
      warnings: (json['warnings'] as num?)?.toInt() ?? 0,
      warningsPct: (json['warnings_pct'] as num?)?.toDouble() ?? 0.0,
      rejects: (json['rejects'] as num?)?.toInt() ?? 0,
      rejectsPct: (json['rejects_pct'] as num?)?.toDouble() ?? 0.0,
    );
  }
}

class CategoryStats {
  final List<AgencyStatRow> byAgency;
  final List<AgencyStatRow> byPerson;
  final List<AgencyStatRow> policeByAgency;
  final List<AgencyStatRow> policeByPerson;
  final List<AgencyStatRow> otherByAgency;
  final List<AgencyStatRow> otherByPerson;

  const CategoryStats({
    required this.byAgency,
    required this.byPerson,
    required this.policeByAgency,
    required this.policeByPerson,
    required this.otherByAgency,
    required this.otherByPerson,
  });

  static List<AgencyStatRow> _parse(Map<String, dynamic> json, String key) =>
      (json[key] as List? ?? [])
          .map((i) => AgencyStatRow.fromJson(i as Map<String, dynamic>))
          .toList();

  factory CategoryStats.fromJson(Map<String, dynamic> json) {
    return CategoryStats(
      byAgency:      _parse(json, 'by_agency'),
      byPerson:      _parse(json, 'by_person'),
      policeByAgency: _parse(json, 'police_by_agency'),
      policeByPerson: _parse(json, 'police_by_person'),
      otherByAgency:  _parse(json, 'other_by_agency'),
      otherByPerson:  _parse(json, 'other_by_person'),
    );
  }
}

class AgencyStats {
  final CategoryStats traffic;
  final CategoryStats parking;
  final CategoryStats other;

  const AgencyStats({required this.traffic, required this.parking, required this.other});

  factory AgencyStats.fromJson(Map<String, dynamic> json) {
    return AgencyStats(
      traffic: CategoryStats.fromJson(
          (json['traffic'] as Map<String, dynamic>?) ?? {}),
      parking: CategoryStats.fromJson(
          (json['parking'] as Map<String, dynamic>?) ?? {}),
      other: CategoryStats.fromJson(
          (json['other'] as Map<String, dynamic>?) ?? {}),
    );
  }
}
