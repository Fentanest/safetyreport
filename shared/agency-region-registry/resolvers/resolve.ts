// Agency/region registry resolver (TypeScript port of resolvers/resolve.py).
//
// The Python file is canonical: this port must return equal results for every
// case in vectors/resolve_cases.json (tests/product/agencyRegistry.test.ts
// checks it). No network, no parsing of handling notes at runtime.

export interface AgencyResolution {
  institution_id: string | null;
  agency_stat_key: string;
  current_agency_code: string | null;
  current_agency_name: string | null;
  resolution_status: 'resolved' | 'resolved_as_of_date' | 'unresolved' | 'historical';
  registry_version: string;
  code_derived?: true;
}

export interface RegionResolution {
  status: 'resolved' | 'historical' | 'gap' | 'unresolved' | 'current_then';
  current_codes: string[];
  display: string | null;
  relation: string | null;
  aggregate_note?: string;
  registry_version: string;
}

/** Runtime snapshot bundle. index rows follow data/agency_index.json cols:
 *  [name|null, agg, type|null, created8|null, lookupName|null] keyed by code. */
export interface AgencySnapshot {
  links: Array<Record<string, string>>;
  index: Record<string, Array<string | null>>;
  compact?: Record<string, string>;
  forward: Record<string, string>;
  multi: Record<string, string>;
  institutions: Record<string, string>;
  registryVersion: string;
  asOfDate: string | null;
}

function isSevenAlnum(code: string | null | undefined): code is string {
  return typeof code === 'string' && code.length === 7 && /^[0-9A-Za-z]{7}$/.test(code);
}

function walkChain(start: string, snap: AgencySnapshot): Array<Record<string, string>> {
  const byFrom = new Map<string, Array<Record<string, string>>>();
  const byTo = new Map<string, Array<Record<string, string>>>();
  for (const link of snap.links) {
    const list = byFrom.get(link.from_code) ?? [];
    list.push(link);
    byFrom.set(link.from_code, list);
    const rlist = byTo.get(link.to_code) ?? [];
    rlist.push(link);
    byTo.set(link.to_code, rlist);
  }
  const chain: Array<Record<string, string>> = [];
  const seen = new Set<string>([start]);
  for (;;) {
    const from = chain.length === 0 ? start : chain[chain.length - 1].to_code;
    const outgoing = byFrom.get(from) ?? [];
    if (outgoing.length !== 1) break;
    const next = outgoing[0];
    if (seen.has(next.to_code)) break;
    chain.push(next);
    seen.add(next.to_code);
  }
  if (chain.length === 0) {
    const back: Array<Record<string, string>> = [];
    let cursor = start;
    for (;;) {
      const incoming = byTo.get(cursor) ?? [];
      if (incoming.length !== 1) break;
      const link = incoming[0];
      if (seen.has(link.from_code)) break;
      back.push(link);
      seen.add(link.from_code);
      cursor = link.from_code;
    }
    chain.push(...back.reverse());
  }
  return chain;
}

function boundaryName(boundary: string, snap: AgencySnapshot): string | null {
  const row = snap.index[boundary];
  const name = row?.[0] ?? null;
  return name === null || name === '' ? null : name;
}

function resolveBoundary(
  boundary: string,
  name: string | null,
  answeredAt: string | null | undefined,
  snap: AgencySnapshot,
  asWasCode: string | null,
): AgencyResolution {
  const chain = walkChain(boundary, snap);
  const mapped = snap.institutions[boundary] ?? null;
  if (chain.length === 0) {
    const institutionId = mapped ?? `ag-c${boundary.toLowerCase()}`;
    return {
      institution_id: institutionId,
      agency_stat_key: `inst:${institutionId}`,
      current_agency_code: boundary,
      current_agency_name: boundaryName(boundary, snap) ?? name,
      resolution_status: 'resolved',
      registry_version: snap.registryVersion,
    };
  }
  const institutionId = mapped ?? chain[0].institution_id ?? `ag-c${boundary.toLowerCase()}`;
  const horizon = answeredAt ?? '9999-12-31';
  const applied = chain.filter((link) => horizon >= link.effective_date);
  if (applied.length === chain.length) {
    const head = chain[chain.length - 1].to_code;
    return {
      institution_id: institutionId,
      agency_stat_key: `inst:${institutionId}`,
      current_agency_code: head,
      current_agency_name: boundaryName(head, snap) ?? chain[chain.length - 1].to_name ?? name,
      resolution_status: 'resolved',
      registry_version: snap.registryVersion,
    };
  }
  const anchor = applied.length === 0 ? null : applied[applied.length - 1];
  if (anchor === null) {
    return {
      institution_id: institutionId,
      agency_stat_key: `inst:${institutionId}`,
      current_agency_code: isSevenAlnum(asWasCode) ? asWasCode : null,
      current_agency_name: name,
      resolution_status: 'resolved_as_of_date',
      registry_version: snap.registryVersion,
    };
  }
  return {
    institution_id: institutionId,
    agency_stat_key: `inst:${institutionId}`,
    current_agency_code: anchor.to_code,
    current_agency_name: anchor.to_name ?? name,
    resolution_status: 'resolved_as_of_date',
    registry_version: snap.registryVersion,
  };
}

function aliasCandidates(
  name: string,
  answeredAt: string | null | undefined,
  snap: AgencySnapshot,
): string[] {
  const ans8 = answeredAt != null ? answeredAt.replace(/-/g, '').slice(0, 8) : null;
  const found: string[] = [];
  for (const code of aliasAll(name, snap)) {
    const row = snap.index[code];
    const created = (row?.[3] as string | null) ?? '';
    if (ans8 !== null && ans8 !== '' && created !== '' && created > ans8) continue;
    found.push(code);
  }
  return found;
}

/** 스냅샷별 별칭 캐시: 8만 행 스캔을 매 신고마다 반복하지 않는다. */
const aliasCache = new WeakMap<AgencySnapshot, Map<string, string[]>>();

function aliasAll(name: string, snap: AgencySnapshot): string[] {
  let table = aliasCache.get(snap);
  if (table === undefined) {
    table = new Map<string, string[]>();
    for (const [code, row] of Object.entries(snap.index)) {
      for (const key of [row[0], row[4]]) {
        if (key != null) {
          const list = table.get(key) ?? [];
          if (!list.includes(code)) list.push(code);
          table.set(key, list);
        }
      }
    }
    for (const [old, oldName] of Object.entries(snap.multi)) {
      const list = table.get(oldName) ?? [];
      if (!list.includes(old)) list.push(old);
      table.set(oldName, list);
    }
    aliasCache.set(snap, table);
  }
  return table.get(name) ?? [];
}

/** Source agency (code/name from one official answer) → identity + display. */
export function resolveAgency(
  code: string | null | undefined,
  name: string | null | undefined,
  answeredAt: string | null | undefined,
  snap: AgencySnapshot,
): AgencyResolution {
  const trimmed = (name ?? '').trim();
  const displayName = trimmed === '' ? null : trimmed;
  if (isSevenAlnum(code ?? null)) {
    const c = code as string;
    const row = snap.index[c];
    if (row != null) {
      return resolveBoundary(row[1] as string, displayName, answeredAt, snap, c);
    }
    const compactAgg = snap.compact?.[c];
    if (compactAgg != null) {
      return resolveBoundary(compactAgg, displayName, answeredAt, snap, c);
    }
    const target = snap.forward[c];
    if (target != null) {
      return resolveBoundary(target, displayName, answeredAt, snap, c);
    }
    if (c in snap.multi) {
      const display = displayName !== null ? `(구)${displayName}` : `(구)${snap.multi[c]}`;
      return {
        institution_id: null,
        agency_stat_key: `src:${c}:${displayName ?? '-'}`,
        current_agency_code: null,
        current_agency_name: display,
        resolution_status: 'historical',
        registry_version: snap.registryVersion,
      };
    }
    return {
      institution_id: null,
      agency_stat_key: `src:${c}:${displayName ?? '-'}`,
      current_agency_code: null,
      current_agency_name: displayName,
      resolution_status: 'unresolved',
      registry_version: snap.registryVersion,
    };
  }
  if (displayName !== null) {
    const candidates = aliasCandidates(displayName, answeredAt, snap);
    if (candidates.length === 1) {
      const got = resolveAgency(candidates[0], displayName, answeredAt, snap);
      return { ...got, code_derived: true as const };
    }
  }
  return {
    institution_id: null,
    agency_stat_key: `src:${code ?? '-'}:${displayName ?? '-'}`,
    current_agency_code: null,
    current_agency_name: displayName,
    resolution_status: 'unresolved',
    registry_version: snap.registryVersion,
  };
}

/** 현행 표시용: registry as_of_date 기준으로 체인 전체를 적용한다. */
export function resolveCurrentAgency(
  code: string | null | undefined,
  name: string | null | undefined,
  snap: AgencySnapshot,
): AgencyResolution {
  return resolveAgency(code, name, snap.asOfDate ?? '9999-12-31', snap);
}

function findEvent(events: Array<Record<string, unknown>>, code: string): Record<string, unknown> | null {
  for (const event of events) {
    if (event.old_code === code) return event;
  }
  return null;
}

/** Region code at a date (answer date for facts, as_of for display). */
export function resolveRegion(
  code: string | null | undefined,
  date: string | null | undefined,
  events: Array<Record<string, unknown>>,
  registryVersion: string,
): RegionResolution {
  const clean = (code ?? '').trim();
  const event = clean === '' ? null : findEvent(events, clean);
  if (event === null) {
    return { status: 'unresolved', current_codes: [], display: null, relation: null, registry_version: registryVersion };
  }
  const at = date ?? '9999-12-31';
  const relation = event.relation as string;
  if (at < (event.effective_date as string)) {
    return {
      status: 'current_then', current_codes: [clean], display: event.old_name as string,
      relation, registry_version: registryVersion,
    };
  }
  const codes = event.new_codes as string[];
  const names = event.new_names as string[];
  if (relation === 'rename' || relation === 'rename_under_merge' || relation === 'transfer' || relation === 'merge') {
    return {
      status: 'resolved', current_codes: [...codes], display: names.length === 0 ? null : names[0],
      relation, registry_version: registryVersion,
    };
  }
  if (relation === 'split') {
    return {
      status: 'historical', current_codes: [...codes], display: `(구)${event.old_name as string}`,
      relation, aggregate_note: 'successors are not assigned; aggregate once under the common parent',
      registry_version: registryVersion,
    };
  }
  if (relation === 'merge_parent') {
    return {
      status: 'historical', current_codes: [...codes], display: `(구)${event.old_name as string}`,
      relation, aggregate_note: 'kept as a history row under the current parent total',
      registry_version: registryVersion,
    };
  }
  if (relation === 'reestablished') {
    return {
      status: 'resolved', current_codes: [...codes], display: names.length === 0 ? null : names[0],
      relation, aggregate_note: 'region lineage only; institution continuity is decided separately',
      registry_version: registryVersion,
    };
  }
  return { status: 'unresolved', current_codes: [], display: null, relation, registry_version: registryVersion };
}

export function resolveRegionGap(
  code: string | null | undefined,
  date: string | null | undefined,
  events: Array<Record<string, unknown>>,
  registryVersion: string,
): RegionResolution {
  const clean = (code ?? '').trim();
  const event = clean === '' ? null : findEvent(events, clean);
  const at = date ?? '9999-12-31';
  if (
    event !== null && event.relation === 'reestablished' && typeof event.old_closed === 'string' &&
    (event.old_closed as string) <= at && at < (event.effective_date as string)
  ) {
    return {
      status: 'gap', current_codes: [], display: `(구)${event.old_name as string}`,
      relation: 'reestablished',
      aggregate_note: 'no district existed then; never back-allocate to the reestablished districts',
      registry_version: registryVersion,
    };
  }
  return resolveRegion(code, date, events, registryVersion);
}

/** Display text: resolved → current name; historical/gap → (구) value;
 *  unresolved → source verbatim (never invents the suffix). */
export function displayAgency(
  sourceName: string | null | undefined,
  resolution: AgencyResolution,
): string | null {
  if (
    resolution.resolution_status === 'resolved' ||
    resolution.resolution_status === 'resolved_as_of_date' ||
    resolution.resolution_status === 'historical'
  ) {
    return resolution.current_agency_name;
  }
  const clean = (sourceName ?? '').trim();
  return clean === '' ? null : clean;
}

export function displayRegion(
  sourceName: string | null | undefined,
  resolution: RegionResolution,
): string | null {
  if (
    resolution.status === 'resolved' || resolution.status === 'current_then' ||
    resolution.status === 'historical' || resolution.status === 'gap'
  ) {
    return resolution.display;
  }
  const clean = (sourceName ?? '').trim();
  return clean === '' ? null : clean;
}
