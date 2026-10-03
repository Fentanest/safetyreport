"""Real fixture app with loopback fake central authentication; no gate bypass."""
import argparse
import sqlite3
import sys
from pathlib import Path

from fixture_server import activate_environment, prepare_data_dir, seed, serve


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir',required=True)
    p.add_argument('--port',type=int,default=18703)
    p.add_argument('--review-fixture',action='store_true',help='동일명 기관 충돌 합성 자료 추가')
    args=p.parse_args()
    root=prepare_data_dir(args.data_dir,reset=False)
    activate_environment(root)
    seed(root)
    sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'tests'))
    from test_community_auth import FakeSupabase, USER_A
    from test_community_gate import FakeAccount
    from services import community_auth_service as cas, community_gate, community_rebuild as rebuild
    from services.community_store import CommunityStore
    import settings.settings as settings
    from core.database.engine import get_engine
    from core.database import models
    if args.review_fixture:
        from review_regression_fixture import seed_collision_rows
        seed_collision_rows(get_engine())
    from sqlalchemy import update
    fake=FakeSupabase()
    account=FakeAccount(fake)
    account.grant(USER_A)
    cfg=cas.CommunityConfig(enabled=True,supabase_url=fake.url,publishable_key='sb_publishable_fixturepublic123',site_url='http://127.0.0.1:8480/')
    service=cas.CommunityAuthService(str(root),lambda:cfg)
    session=fake.issue_session(USER_A)
    record=service._session_record(session,USER_A)
    service.store.save({'current':record})
    cas._default=service
    settings._instance.update_config('LOGIN','username','fixture-official')
    settings._instance.load()
    community_gate.refresh_now()
    CommunityStore.open()
    version,dataset,namespace=rebuild._scope()
    rebuild._record_fresh_baseline(version,dataset,namespace)
    with get_engine().begin() as c:
        for table in (models.merge_traffic_table,models.merge_parking_table):
            c.execute(update(table).where(table.c.ID.in_(['90000001','90000011','90000012','90000101'])).values(위도=37.56, 경도=126.83))
    for name,version in [('legacy.db',4),('future.db',6)]:
        path=root/name
        connection=sqlite3.connect(path)
        connection.execute('CREATE TABLE IF NOT EXISTS mysafety (ID TEXT)')
        connection.execute('CREATE TABLE IF NOT EXISTS mysafetymerge_traffic (ID TEXT)')
        connection.execute('PRAGMA user_version='+str(version))
        connection.commit(); connection.close()
    try: serve(root,'127.0.0.1',args.port)
    finally: service.shutdown(); fake.close()


if __name__=='__main__': main()
