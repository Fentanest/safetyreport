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

/// Runtime snapshot bundle. index rows follow data/agency_index.json cols:
/// [name|null, agg, type|null, created8|null, lookupName|null] keyed by code.
class AgencySnapshot {
  AgencySnapshot({
    required this.links,
    required this.index,
    this.compact = const {},
    required this.forward,
    required this.multi,
    required this.institutions,
    required this.registryVersion,
    required this.asOfDate,
  });

  final List<dynamic> links;
  final Map<String, dynamic> index;
  final Map<String, dynamic> compact;
  final Map<String, dynamic> forward;
  final Map<String, dynamic> multi;
  final Map<String, dynamic> institutions;
  final String registryVersion;
  final String? asOfDate;

  /// 스냅샷별 별칭 캐시: 8만 행 스캔을 매 신고마다 반복하지 않는다.
  Map<String, List<String>>? _aliasAll;

  List<String> aliasAll(String name) {
    var table = _aliasAll;
    if (table == null) {
      table = <String, List<String>>{};
      (index as Map).forEach((code, row) {
        final r = row as List;
        for (final key in [r[0], if (r.length > 4) r[4]]) {
          if (key != null) {
            final codes = table![key as String] ??= [];
            if (!codes.contains(code)) codes.add(code as String);
          }
        }
      });
      (multi as Map).forEach((old, oldName) {
        final list = table![oldName as String] ??= [];
        if (!list.contains(old)) list.add(old as String);
      });
      _aliasAll = table;
    }
    return table[name] ?? const [];
  }
}

List<dynamic> _walkChain(String start, AgencySnapshot snap) {
  final byFrom = <String, List<dynamic>>{};
  final byTo = <String, List<dynamic>>{};
  for (final l in snap.links) {
    (byFrom[l['from_code'] as String] ??= []).add(l);
    (byTo[l['to_code'] as String] ??= []).add(l);
  }
  final chain = <dynamic>[];
  final seen = <String>{start};
  while (true) {
    final from = chain.isEmpty ? start : chain.last['to_code'] as String;
    final outgoing = byFrom[from] ?? const [];
    if (outgoing.length != 1) break;
    final next = outgoing.first;
    if (seen.contains(next['to_code'])) break;
    chain.add(next);
    seen.add(next['to_code'] as String);
  }
  if (chain.isEmpty) {
    final back = <dynamic>[];
    var cursor = start;
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
  return chain;
}

String? _boundaryName(String boundary, AgencySnapshot snap) {
  final row = snap.index[boundary] as List?;
  final name = row == null ? null : row[0] as String?;
  return (name == null || name.isEmpty) ? null : name;
}

Map<String, dynamic> _resolveBoundary(
  String boundary,
  String? name,
  String? answeredAt,
  AgencySnapshot snap,
  String? asWasCode,
) {
  final chain = _walkChain(boundary, snap);
  final mapped = snap.institutions[boundary] as String?;
  if (chain.isEmpty) {
    final institutionId = mapped ?? 'ag-c${boundary.toLowerCase()}';
    return {
      'institution_id': institutionId,
      'agency_stat_key': 'inst:$institutionId',
      'current_agency_code': boundary,
      'current_agency_name': _boundaryName(boundary, snap) ?? name,
      'resolution_status': 'resolved',
      'registry_version': snap.registryVersion,
    };
  }
  final institutionId =
      mapped ?? (chain.first['institution_id'] as String?) ?? 'ag-c${boundary.toLowerCase()}';
  final horizon = answeredAt ?? '9999-12-31';
  final applied = chain
      .where((l) => horizon.compareTo(l['effective_date'] as String) >= 0)
      .toList();
  if (applied.length == chain.length) {
    final head = chain.last['to_code'] as String;
    return {
      'institution_id': institutionId,
      'agency_stat_key': 'inst:$institutionId',
      'current_agency_code': head,
      'current_agency_name':
          _boundaryName(head, snap) ?? (chain.last['to_name'] as String?) ?? name,
      'resolution_status': 'resolved',
      'registry_version': snap.registryVersion,
    };
  }
  final anchor = applied.isEmpty ? null : applied.last;
  if (anchor == null) {
    return {
      'institution_id': institutionId,
      'agency_stat_key': 'inst:$institutionId',
      'current_agency_code': _isSevenAlnum(asWasCode) ? asWasCode : null,
      'current_agency_name': name,
      'resolution_status': 'resolved_as_of_date',
      'registry_version': snap.registryVersion,
    };
  }
  return {
    'institution_id': institutionId,
    'agency_stat_key': 'inst:$institutionId',
    'current_agency_code': anchor['to_code'],
    'current_agency_name': (anchor['to_name'] as String?) ?? name,
    'resolution_status': 'resolved_as_of_date',
    'registry_version': snap.registryVersion,
  };
}

List<String> _aliasCandidates(String name, String? answeredAt, AgencySnapshot snap) {
  final digits = (answeredAt ?? '').replaceAll('-', '');
  final ans8 = digits.length >= 8 ? digits.substring(0, 8) : null;
  final found = <String>[];
  for (final code in snap.aliasAll(name)) {
    final row = snap.index[code] as List?;
    final created = (row == null ? null : row[3] as String?) ?? '';
    if (ans8 != null && created.isNotEmpty && created.compareTo(ans8) > 0) {
      continue;
    }
    found.add(code);
  }
  return found;
}

/// Source agency (code/name from one official answer) → identity + display.
Map<String, dynamic> resolveAgency(
  String? code,
  String? name,
  String? answeredAt,
  AgencySnapshot snap,
) {
  final cleanName = (name ?? '').trim();
  final displayName = cleanName.isEmpty ? null : cleanName;
  if (_isSevenAlnum(code)) {
    final c = code!;
    final row = snap.index[c] as List?;
    if (row != null) {
      return _resolveBoundary(row[1] as String, displayName, answeredAt, snap, c);
    }
    final compactAgg = snap.compact[c] as String?;
    if (compactAgg != null) {
      return _resolveBoundary(compactAgg, displayName, answeredAt, snap, c);
    }
    final target = snap.forward[c] as String?;
    if (target != null) {
      return _resolveBoundary(target, displayName, answeredAt, snap, c);
    }
    if ((snap.multi as Map).containsKey(c)) {
      final display = displayName != null ? '(구)$displayName' : '(구)${snap.multi[c]}';
      return {
        'institution_id': null,
        'agency_stat_key': 'src:$c:${displayName ?? '-'}',
        'current_agency_code': null,
        'current_agency_name': display,
        'resolution_status': 'historical',
        'registry_version': snap.registryVersion,
      };
    }
    return {
      'institution_id': null,
      'agency_stat_key': 'src:$c:${displayName ?? '-'}',
      'current_agency_code': null,
      'current_agency_name': displayName,
      'resolution_status': 'unresolved',
      'registry_version': snap.registryVersion,
    };
  }
  if (displayName != null) {
    final candidates = _aliasCandidates(displayName, answeredAt, snap);
    if (candidates.length == 1) {
      final got = Map<String, dynamic>.from(
          resolveAgency(candidates.first, displayName, answeredAt, snap));
      got['code_derived'] = true;
      return got;
    }
  }
  return {
    'institution_id': null,
    'agency_stat_key': 'src:${code ?? '-'}:${displayName ?? '-'}',
    'current_agency_code': null,
    'current_agency_name': displayName,
    'resolution_status': 'unresolved',
    'registry_version': snap.registryVersion,
  };
}

/// 현행 표시용: registry as_of_date 기준으로 체인 전체를 적용한다.
Map<String, dynamic> resolveCurrentAgency(
  String? code,
  String? name,
  AgencySnapshot snap,
) {
  return resolveAgency(code, name, snap.asOfDate ?? '9999-12-31', snap);
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

/// Display text: resolved → current name; historical/gap → (구) value;
/// unresolved → source verbatim (never invents the suffix).
String? displayAgency(String? sourceName, Map<String, dynamic> resolution) {
  final status = resolution['resolution_status'];
  if (status == 'resolved' ||
      status == 'resolved_as_of_date' ||
      status == 'historical') {
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
