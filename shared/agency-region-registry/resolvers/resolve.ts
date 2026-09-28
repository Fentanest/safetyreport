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
  resolution_status: 'resolved' | 'resolved_as_of_date' | 'unresolved';
  registry_version: string;
}

export interface RegionResolution {
  status: 'resolved' | 'historical' | 'gap' | 'unresolved' | 'current_then';
  current_codes: string[];
  display: string | null;
  relation: string | null;
  aggregate_note?: string;
  registry_version: string;
}

function isSevenAlnum(code: string | null | undefined): code is string {
  return typeof code === 'string' && code.length === 7 && /^[0-9A-Za-z]{7}$/.test(code);
}

/** Source agency (code/name from one official answer) → identity + display. */
export function resolveAgency(
  code: string | null | undefined,
  name: string | null | undefined,
  answeredAt: string | null | undefined,
  links: Array<Record<string, string>>,
  registryVersion: string,
): AgencyResolution {
  const trimmed = (name ?? '').trim();
  const displayName = trimmed === '' ? null : trimmed;
  const byFrom = new Map<string, Array<Record<string, string>>>();
  const byTo = new Map<string, Array<Record<string, string>>>();
  for (const link of links) {
    const list = byFrom.get(link.from_code) ?? [];
    list.push(link);
    byFrom.set(link.from_code, list);
    const rlist = byTo.get(link.to_code) ?? [];
    rlist.push(link);
    byTo.set(link.to_code, rlist);
  }
  const chain: Array<Record<string, string>> = [];
  if (isSevenAlnum(code ?? null)) {
    const seen = new Set<string>([code as string]);
    for (;;) {
      const from = chain.length === 0 ? (code as string) : chain[chain.length - 1].to_code;
      const outgoing = byFrom.get(from) ?? [];
      if (outgoing.length !== 1) break;
      const next = outgoing[0];
      if (seen.has(next.to_code)) break;
      chain.push(next);
      seen.add(next.to_code);
    }
    if (chain.length === 0) {
      // The code starts no forward chain: it may be a post-change code received
      // after a verified rename (e.g. 1815198 after 1812314 → 1815198). Walk back
      // over unique incoming links so both sides resolve to the same institution.
      // Several incoming links (a merge target) stay ambiguous → unresolved.
      const back: Array<Record<string, string>> = [];
      let cursor = code as string;
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
  }
  if (chain.length === 0) {
    return {
      institution_id: null,
      agency_stat_key: `src:${code ?? '-'}:${displayName ?? '-'}`,
      current_agency_code: null,
      current_agency_name: displayName,
      resolution_status: 'unresolved',
      registry_version: registryVersion,
    };
  }
  const institutionId = chain[0].institution_id;
  const horizon = answeredAt ?? '9999-12-31';
  const applied = chain.filter((link) => horizon >= link.effective_date);
  if (applied.length === chain.length) {
    const current = chain[chain.length - 1];
    return {
      institution_id: institutionId,
      agency_stat_key: `inst:${institutionId}`,
      current_agency_code: current.to_code,
      current_agency_name: current.to_name,
      resolution_status: 'resolved',
      registry_version: registryVersion,
    };
  }
  const anchor = applied.length === 0 ? null : applied[applied.length - 1];
  return {
    institution_id: institutionId,
    agency_stat_key: `inst:${institutionId}`,
    current_agency_code: anchor === null ? (code as string) : anchor.to_code,
    current_agency_name: anchor === null ? displayName : anchor.to_name,
    resolution_status: 'resolved_as_of_date',
    registry_version: registryVersion,
  };
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

/** Display text: resolved → current name; historical/gap → (구) source;
 *  unresolved → source verbatim (never invents the suffix). */
export function displayAgency(
  sourceName: string | null | undefined,
  resolution: AgencyResolution,
): string | null {
  if (resolution.resolution_status === 'resolved' || resolution.resolution_status === 'resolved_as_of_date') {
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
