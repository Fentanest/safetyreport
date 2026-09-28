// Agency/region registry resolver (Dart port of resolvers/resolve.py).
//
// The Python file is canonical: this port must return equal results for every
// case in vectors/resolve_cases.json (contract_vectors_test.dart checks it).
// No network, no parsing of handling notes at runtime.

bool _isSevenAlnum(String? code) {
  if (code == null || code.length != 7) return false;
  for (var i = 0; i < 7; i++) {
    final c = code.codeUnitAt(i);
    final ok = (c >= 48 && c <= 57) ||
        (c >= 65 && c <= 90) ||
        (c >= 97 && c <= 122);
    if (!ok) return false;
  }
  return true;
}

/// Source agency (code/name from one official answer) → identity + display.
Map<String, dynamic> resolveAgency(
  String? code,
  String? name,
  String? answeredAt,
  List<dynamic> links,
  String registryVersion,
) {
  final cleanName = (name ?? '').trim();
  final displayName = cleanName.isEmpty ? null : cleanName;
  final byFrom = <String, List<dynamic>>{};
  final byTo = <String, List<dynamic>>{};
  for (final l in links) {
    (byFrom[l['from_code'] as String] ??= []).add(l);
    (byTo[l['to_code'] as String] ??= []).add(l);
  }
  final chain = <dynamic>[];
  if (_isSevenAlnum(code)) {
    final seen = <String>{code!};
    while (true) {
      final from = chain.isEmpty ? code : chain.last['to_code'] as String;
      final outgoing = byFrom[from] ?? const [];
      if (outgoing.length != 1) break;
      final next = outgoing.first;
      if (seen.contains(next['to_code'])) break;
      chain.add(next);
      seen.add(next['to_code'] as String);
    }
    if (chain.isEmpty) {
      // The code starts no forward chain: it may be a post-change code received
      // after a verified rename (e.g. 1815198 after 1812314 → 1815198). Walk back
      // over unique incoming links so both sides resolve to the same institution.
      // Several incoming links (a merge target) stay ambiguous → unresolved.
      final back = <dynamic>[];
      var cursor = code;
      while (true) {
        final incoming = byTo[cursor] ?? const [];
        if (incoming.length != 1) break;
        final link = incoming.first;
        if (seen.contains(link['from_code'])) break;
        back.add(link);
        seen.add(link['from_code'] as String);
        cursor = link['from_code'] as String;
      }
      chain.addAll(back.reversed);
    }
  }
  if (chain.isEmpty) {
    return {
      'institution_id': null,
      'agency_stat_key': 'src:${code ?? '-'}:${displayName ?? '-'}',
      'current_agency_code': null,
      'current_agency_name': displayName,
      'resolution_status': 'unresolved',
      'registry_version': registryVersion,
    };
  }
  final institutionId = chain.first['institution_id'];
  final horizon = answeredAt ?? '9999-12-31';
  final applied = chain
      .where((l) => horizon.compareTo(l['effective_date'] as String) >= 0)
      .toList();
  if (applied.length == chain.length) {
    final current = chain.last;
    return {
      'institution_id': institutionId,
      'agency_stat_key': 'inst:$institutionId',
      'current_agency_code': current['to_code'],
      'current_agency_name': current['to_name'],
      'resolution_status': 'resolved',
      'registry_version': registryVersion,
    };
  }
  final anchor = applied.isEmpty ? null : applied.last;
  return {
    'institution_id': institutionId,
    'agency_stat_key': 'inst:$institutionId',
    'current_agency_code': anchor == null ? code : anchor['to_code'],
    'current_agency_name': anchor == null ? displayName : anchor['to_name'],
    'resolution_status': 'resolved_as_of_date',
    'registry_version': registryVersion,
  };
}

/// Region code at a date (answer date for facts, as_of for display).
Map<String, dynamic> resolveRegionGap(
  String? code,
  String? date,
  List<dynamic> events,
  String registryVersion,
) {
  final clean = (code ?? '').trim();
  final result = resolveRegion(code, date, events, registryVersion);
  dynamic event;
  for (final e in events) {
    if (e['old_code'] == clean) {
      event = e;
      break;
    }
  }
  final at = date ?? '9999-12-31';
  if (event != null &&
      event['relation'] == 'reestablished' &&
      (event['old_closed'] as String?) != null &&
      (event['old_closed'] as String).compareTo(at) <= 0 &&
      at.compareTo(event['effective_date'] as String) < 0) {
    return {
      'status': 'gap',
      'current_codes': <String>[],
      'display': '(구)${event['old_name']}',
      'relation': 'reestablished',
      'aggregate_note':
          'no district existed then; never back-allocate to the reestablished districts',
      'registry_version': registryVersion,
    };
  }
  return result;
}

Map<String, dynamic> resolveRegion(
  String? code,
  String? date,
  List<dynamic> events,
  String registryVersion,
) {
  final clean = (code ?? '').trim();
  dynamic event;
  for (final e in events) {
    if (e['old_code'] == clean) {
      event = e;
      break;
    }
  }
  if (event == null || clean.isEmpty) {
    return {
      'status': 'unresolved',
      'current_codes': <String>[],
      'display': null,
      'relation': null,
      'registry_version': registryVersion,
    };
  }
  final at = date ?? '9999-12-31';
  final relation = event['relation'] as String;
  if (at.compareTo(event['effective_date'] as String) < 0) {
    return {
      'status': 'current_then',
      'current_codes': [clean],
      'display': event['old_name'],
      'relation': relation,
      'registry_version': registryVersion,
    };
  }
  List<String> codes(List<dynamic> xs) => xs.cast<String>().toList();
  if (relation == 'rename' ||
      relation == 'rename_under_merge' ||
      relation == 'transfer' ||
      relation == 'merge') {
    final names = codes(event['new_names'] as List);
    return {
      'status': 'resolved',
      'current_codes': codes(event['new_codes'] as List),
      'display': names.isEmpty ? null : names.first,
      'relation': relation,
      'registry_version': registryVersion,
    };
  }
  if (relation == 'split') {
    return {
      'status': 'historical',
      'current_codes': codes(event['new_codes'] as List),
      'display': '(구)${event['old_name']}',
      'relation': relation,
      'aggregate_note':
          'successors are not assigned; aggregate once under the common parent',
      'registry_version': registryVersion,
    };
  }
  if (relation == 'merge_parent') {
    return {
      'status': 'historical',
      'current_codes': codes(event['new_codes'] as List),
      'display': '(구)${event['old_name']}',
      'relation': relation,
      'aggregate_note': 'kept as a history row under the current parent total',
      'registry_version': registryVersion,
    };
  }
  if (relation == 'reestablished') {
    final names = codes(event['new_names'] as List);
    return {
      'status': 'resolved',
      'current_codes': codes(event['new_codes'] as List),
      'display': names.isEmpty ? null : names.first,
      'relation': relation,
      'aggregate_note':
          'region lineage only; institution continuity is decided separately',
      'registry_version': registryVersion,
    };
  }
  return {
    'status': 'unresolved',
    'current_codes': <String>[],
    'display': null,
    'relation': relation,
    'registry_version': registryVersion,
  };
}

/// Display text: resolved → current name; historical/gap → (구) source;
/// unresolved → source verbatim (never invents the suffix).
String? displayAgency(String? sourceName, Map<String, dynamic> resolution) {
  final status = resolution['resolution_status'];
  if (status == 'resolved' || status == 'resolved_as_of_date') {
    return resolution['current_agency_name'] as String?;
  }
  final clean = (sourceName ?? '').trim();
  return clean.isEmpty ? null : clean;
}

String? displayRegion(String? sourceName, Map<String, dynamic> resolution) {
  final status = resolution['status'];
  if (status == 'resolved' ||
      status == 'current_then' ||
      status == 'historical' ||
      status == 'gap') {
    return resolution['display'] as String?;
  }
  final clean = (sourceName ?? '').trim();
  return clean.isEmpty ? null : clean;
}
