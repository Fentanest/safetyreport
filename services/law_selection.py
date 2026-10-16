"""Case-basis selection supplement for the existing law parser.

A warning is not a negative finding. General guidance and case decisions are
kept separate. Recognition evidence is retained for regression diagnostics.
"""
import re
import unicodedata
from services import law_citations as base

_SPACE = base.SPACE_CLASS
_SPACES = re.compile(_SPACE + '+')


def norm(s):
    # Same entity rule (';' required) and whitespace set as Dart normalizeLawText.
    return _SPACES.sub('', unicodedata.normalize('NFKC', base.decode_entities(s)))
BOUNDARY = re.compile('(?<=[다요])[.。!?](?!' + _SPACE + '*\\()|\\n' + _SPACE + '*\\n|(?<=\\n)(?=' + _SPACE + '*(?:[가-하]|[0-9]+)[.)]' + _SPACE + ')|(?=그러나|다만(?:,|' + _SPACE + ')|추가로|※)')
FINDING = re.compile('위반행위로판단|에저촉되어|위반에따라|위반되는사항|적합하지않다고판단|해당하는사실을확인|위반(?:한)?(?:사실|사항|행위)(?:이|가|을|를|은|는)?확인|위반(?:으로)?확인|위반한(?:사실|사항|불법|행위)|위반(?:으로|한행위로)판단|위반(?:사항|행위)?에해당(?:되어|하여|하므로|되나)|위반하였|위반하여|위반사항을통지|위반을확인|위반,|위반으로원상복구')
SANCTION = re.compile('과태료(?:를)?부과(?:할예정|예정|처분|처리|하였|합니다|하겠습니다|대상)|과태료[0-9,.]+원을부과하고자|과태료처분(?:예정|대상)|과태료(?:를)?부과절차(?:를)?진행|행정처분하겠')
REMEDY = re.compile('(?:원상복구|시정|정비)(?:명령|조치|요청)|원상복구(?:및[^.]{0,20})?(?:하도록|명령|의견진술)|번호판교체에대하여안내|행정조치하였|계도조치|행정계도|점검하여안전운행하도록행정지도')
CASE = re.compile('귀하|해당차량|신고차량|신고된차량|차량소유|차주|피신고|신고하신|신고해주신|제보(?:하신|해주신)|소유주|민원신고사항|보내주신|첨부파일|첨부해주신|제출하신')
DENIAL = re.compile('위반(?:한)?(?:사실|사항|행위)(?:이|가|은|는)?없(?:습니다|음|어)|위반(?:한)?(?:사실|사항|행위)(?:을|를)?확인(?:할수없|하지못)|해당하지않|위반(?:한)?(?:사실|사항|행위)?(?:이|을|를|가)?(?:명확히)?(?:확인되지않|확정할수없)|위반이있었다고확정할수없|명확(?:하게|히)판단하기어려|식별이곤란한정도가아닌|대상(?:은|이)?(?:아니|아닙|아님)|해당되지않|적용하지않|위반으로의율하기는어렵|철거는못')
ALLOW = re.compile('주차가가능한곳|주차가능함을|주차가가능함을|주차가능표지를발급받은차량')
REJECT = re.compile('과태료(?:를)?부과(?:는|가|하기|에)?(?:어려|곤란|불가)|과태료처분(?:은|이)?(?:어려|곤란|불가)|과태료(?:를)?부과할수없|단속(?:이|은)?어려|과태료를부과하지아니')
FUTURE = re.compile('(?:위반사항|위반행위)에해당하는경우|검토후조치|위반사항확인후|단속검토예정|승인내역이없음|위반으로의심')
PROC_TITLE = re.compile('사전통지|의견제출|자진납부|과태료경감|경고처분|적용배제|특례')
PROC_TEXT = re.compile('신고포상금|포상금을지급|포상금을수령|신고는육하원칙|증거자료를객관적|영상에의해위반사실이명백히확인될것|신청권이나자료요청권한|촬영을삼가')
SANCTION_TITLE = re.compile('과태료|부과기준')
REMEDY_TITLE = re.compile('점검및정비|정비명령|점검및정비명령')

def segments(s):
    out = []
    pos = 0
    for m in BOUNDARY.finditer(s):
        if m.start() > pos:
            out.append((pos, m.start(), s[pos:m.start()]))
        pos = m.end()
    if pos < len(s):
        out.append((pos, len(s), s[pos:]))
    return out

def choose(answer, old_key=''):
    refs, pending = base.parse_laws(answer)
    segs = segments(answer)
    annotated = [dict(r, evidence=[]) for r in refs]
    all_groups = []
    group_by_segment = {}
    for si, (lo, hi, raw) in enumerate(segs):
        occ = []
        for ri, r in enumerate(refs):
            for o in r['occurrences']:
                if lo <= o['source_span'][0] < hi:
                    occ.append({'ri': ri, 'o': o})
        occ.sort(key=lambda x: x['o']['source_span'][0])
        groups = []
        for item in occ:
            if not groups:
                groups.append([item])
                continue
            end = groups[-1][-1]['o']['source_span'][1]
            start = item['o']['source_span'][0]
            masks = [item['o']['name_span']] + [p['span'] for p in pending if p.get('type') == 'unbound_article' and end <= p['span'][0] < p['span'][1] <= start]
            cursor = end
            parts = []
            for a, z in sorted(masks):
                if a < end or z > start:
                    continue
                parts.append(answer[cursor:a])
                cursor = z
            parts.append(answer[cursor:start])
            gap = norm(''.join(parts))
            residue = re.sub('(?:및|또는|동법|같은법|시행령|시행규칙|와|과)|[「」『』【】\\[\\]"“”‘’(),、]', '', gap)
            if not residue:
                groups[-1].append(item)
            else:
                groups.append([item])
        group_by_segment[si] = []
        for gi, items in enumerate(groups):
            first = items[0]['o']
            last = items[-1]['o']
            start = first['source_span'][0]
            name_start = first['name_span'][0]
            if lo <= name_start < start and (not any((x['o']['source_span'][0] < start for g in groups[:gi] for x in g))):
                start = name_start
            if gi + 1 < len(groups):
                nxt = groups[gi + 1][0]['o']
                end = nxt['source_span'][0]
                na, nb = nxt['name_span']
                if last['source_span'][1] <= na < end:
                    end = na
            else:
                end = hi
            a = lo if gi == 0 else start
            g = {'id': len(all_groups), 'segment': si, 'span': [a, end], 'text': answer[a:end], 'items': items, 'tail_start': last['source_span'][1]}
            all_groups.append(g)
            group_by_segment[si].append(g)
    for g in all_groups:
        q = norm(g['text'])
        case = bool(CASE.search(q) or re.search('(?:행위|사실|위반사항)(?:이|가|을|를|은|는)?(?:확인되|확인하|파악되|판단되)', q) or FINDING.search(q))
        local_ids = {x['ri'] for x in g['items']}
        after_group = norm(answer[g['tail_start']:g['span'][1]])
        generic = bool(re.search('(?:가입하지아니한자|위반자|차량소유자)는과태료(?:가)?부과(?:대상|됩니다)', q))
        policy = bool(re.search('(?:경고처리|계도처분|경고처분)', q) and re.search('경우|개선안|지침|기준', q) and (not FINDING.search(q)))
        proof = bool(FINDING.search(q) or re.match('^(?:을|를)위반한', after_group) or re.match('^(?:\\([^)]*\\))?["“‘「][^"”’」]*["”’」](?:에해당되어|로판단되어)', after_group))
        sanction = bool(SANCTION.search(q))
        remedy = bool(REMEDY.search(q))
        for item in g['items']:
            ri = item['ri']
            r = refs[ri]
            o = item['o']
            title = norm(o['source_text'])
            tail = norm(answer[o['source_span'][1]:g['span'][1]])
            definition = bool(re.match('^(?:\\([^)]*\\))?(?:에서|는|은)["“‘\'「]', tail))
            if PROC_TITLE.search(title) or PROC_TEXT.search(q) or policy:
                role = 'procedure'
                reason = 'procedure_or_policy_context'
            elif definition:
                role = 'general'
                reason = 'quoted_statutory_explanation'
            elif generic:
                role = 'general'
                reason = 'generic_liability_without_case_finding'
            elif ALLOW.search(q):
                role = 'excluded'
                reason = 'case_permission'
            elif DENIAL.search(q):
                role = 'excluded'
                reason = 'case_denial'
            elif FUTURE.search(q):
                role = 'unresolved'
                reason = 'unconfirmed_finding'
            elif REJECT.search(q) and (not proof):
                role = 'unresolved'
                reason = 'enforcement_rejection_without_violation_finding'
            elif REMEDY_TITLE.search(title):
                role = 'remedy' if case and remedy else 'unknown'
                reason = 'remedy_basis'
            elif SANCTION_TITLE.search(title):
                role = 'sanction' if case and sanction else 'unknown'
                reason = 'sanction_basis'
            elif proof:
                role = 'violation'
                reason = 'case_violation_finding'
            elif case and sanction:
                role = 'sanction'
                reason = 'case_sanction_with_named_basis'
            elif case and remedy:
                role = 'remedy'
                reason = 'case_remedy_with_named_basis'
            else:
                role = 'unknown'
                reason = 'no_verified_event_link'
            annotated[ri]['evidence'].append({'role': role, 'reason': reason, 'group': g['id'], 'span': g['span'], 'text': g['text'], 'citation': o})
    for si, (lo, hi, raw) in enumerate(segs):
        if group_by_segment.get(si) or si == 0:
            continue
        prev = group_by_segment.get(si - 1, [])
        if len(prev) != 1:
            continue
        g = prev[0]
        ids = {x['ri'] for x in g['items']}
        if len(ids) != 1:
            continue
        ri = next(iter(ids))
        q = norm(raw)
        nt, nm = base.normalized_law_copy(raw)
        next_name = base.names_re.search(nt)
        if next_name:
            cut = nm[next_name.start()][0]
            raw = raw[:cut]
            hi = lo + cut
            q = norm(raw)
        if not CASE.search(q) or not REMEDY.search(q) or DENIAL.search(q) or FUTURE.search(q) or re.search('경우|미이행|추후', q):
            continue
        roles = {e['role'] for e in annotated[ri]['evidence']}
        if roles.intersection({'excluded', 'procedure', 'unresolved'}):
            continue
        annotated[ri]['evidence'].append({'role': 'remedy', 'reason': 'adjacent_case_action_single_basis', 'group': g['id'], 'span': [lo, hi], 'text': raw, 'citation': None})
    rank = {'violation': 0, 'sanction': 1, 'remedy': 2}
    for r in annotated:
        roles = {e['role'] for e in r['evidence']}
        accepted = roles.intersection(rank)
        if accepted and 'excluded' in roles:
            r['decision'] = 'conflict'
            r['rank'] = None
        elif accepted:
            r['rank'] = min((rank[x] for x in accepted))
            r['decision'] = min(accepted, key=lambda x: rank[x])
        elif 'excluded' in roles:
            r['decision'] = 'excluded'
            r['rank'] = None
        elif roles.intersection({'unresolved'}):
            r['decision'] = 'unresolved'
            r['rank'] = None
        elif roles == {'procedure'}:
            r['decision'] = 'procedure'
            r['rank'] = None
        elif roles and roles <= {'general', 'procedure'}:
            r['decision'] = 'general'
            r['rank'] = None
        else:
            r['decision'] = 'unknown'
            r['rank'] = None
    candidates = [r for r in annotated if r['rank'] is not None and r['kind'] == 'article']
    legacy = False
    if not candidates and old_key:
        candidates = [r for r in annotated if r['decision'] == 'unknown' and (r['key'] == old_key or r['key'].startswith(old_key + '제')) and (r['kind'] == 'article')]
        legacy = bool(candidates)
        for r in candidates:
            r['rank'] = 3
    candidates.sort(key=lambda r: (r['rank'], not (r['key'] == old_key or (old_key and r['key'].startswith(old_key + '제'))), r['occurrences'][0]['source_span'][0]))
    return {'selected': candidates[0]['key'] if candidates else '', 'refs': annotated, 'pending': pending, 'legacy_fallback': legacy, 'groups': [{'id': g['id'], 'span': g['span'], 'text': g['text'], 'refs': [refs[x['ri']]['key'] for x in g['items']]} for g in all_groups]}
