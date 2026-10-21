"""Cross-client G1–G11 decisions; inputs are authoritative status or persisted G1 evidence."""
def offline_grace(last_g1, now, same_member=True):
    return type(last_g1) in (int,float) and same_member and 0 <= now-last_g1 <= 72*3600


def gate_policy(v):
    if not v['owner']: return 'owner_mismatch'
    if not v['binding']: return 'binding_mismatch'
    if v['reauth']: return 'reauth_required'
    if not v['session']: return 'login_required'
    if not v['confirmed']:
        return 'local_only' if offline_grace(v['last_g1'],v['now'],v['same_member']) else 'connection_required'
    if v['contributor']=='suspended': return 'suspended'
    prior=v['last_g1'] is not None and v['same_member']
    if v.get('kakao') is not True:
        explicit=v.get('kakao') is False and 'kakao_missing' in v.get('reasons',[])
        if not explicit:return 'connection_required'
        return 'soft_reset' if prior else 'login_required'
    if v['contributor']=='none' and prior: return 'soft_reset'
    if v['consent']=='revoked': return 'soft_reset' if prior else 'consent_required'
    if v['consent']!='active': return 'consent_required'
    return 'use_upload' if v['contributor']=='active' else 'connection_required'
