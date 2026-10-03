"""Deterministic full-population performance check; only isolated fixture data."""
import argparse
import json
import resource
import time
from pathlib import Path

from fixture_server import activate_environment, prepare_data_dir


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', required=True)
    p.add_argument('--count', type=int, required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--baseline', action='store_true')
    args = p.parse_args()
    root = prepare_data_dir(args.data_dir, reset=False)
    activate_environment(root)
    from sqlalchemy import event, text
    from core.database import database, models
    from core.database.engine import get_engine
    from core.utils import logger
    from services import report_stats_service as stats
    if args.baseline:
        import subprocess
        source = subprocess.check_output(['git', 'show', 'a35b7d2:services/report_stats_service.py'], text=True)
        exec(compile(source, '<baseline-report-stats>', 'exec'), stats.__dict__)
    logger.LoggerFactory.create_logger(mode='crawl')
    engine = get_engine()
    database.upgrade_schema(engine)
    with engine.begin() as c:
        n = c.execute(text('SELECT count(*) FROM mysafetymerge_traffic')).scalar()
        if n != args.count:
            if n:
                raise RuntimeError('Use a new fixture directory for each population')
            for start in range(0, args.count, 5000):
                rows = []
                titles = []
                for i in range(start, min(start + 5000, args.count)):
                    rid = str(800000000 + i)
                    titles.append(dict(ID=rid, 신고번호='FIXTURE-' + rid, 신고명='합성 신고', 신고일='2026-01-01'))
                    rows.append(dict(ID=rid, 신고번호='FIXTURE-' + rid, 신고명='합성 신고', 신고일='2026-01-01',
                        답변일='2026-01-03', 처리상태=['수용','일부수용','불수용','처리중'][i % 4],
                        범칙금_과태료='과태료 40,000원' if i % 4 < 2 else '',
                        처리기관='합성 경찰서 ' + str(i % 20), 담당자='합성 담당 ' + str(i % 100),
                        위반법규='도로교통법 제5조' if i % 2 else '', 위반장소='합성 주소 ' + str(i % 3000),
                        주소정규화='합성 주소 ' + str(i % 3000), 위도=33 + (i % 3000) / 1000,
                        경도=126 + (i % 3000) / 1000))
                c.execute(models.title_table.insert(), titles)
                c.execute(models.merge_traffic_table.insert(), rows)
    calls = []
    sql_times = []
    def sql(*a):
        calls.append(a[2]); a[4]._user_report_started=time.perf_counter()
    def sql_done(*a): sql_times.append(time.perf_counter()-a[4]._user_report_started)
    event.listen(engine, 'before_cursor_execute', sql)
    event.listen(engine, 'after_cursor_execute', sql_done)
    results = {'count': args.count, 'measurements': []}
    for name, fn in [('dashboard', lambda: stats.get_dashboard_stats(engine, mode='raw')),
                     ('statistics', lambda: stats.get_stats_page(engine, {}, mode='raw')),
                     ('map', lambda: stats.get_report_map_stats(engine, mode='raw'))] + ([] if args.baseline else
                     [('map_bounded', lambda: stats.get_report_map_stats(engine, mode='raw', max_points=1200)),
                      ('map_viewport', lambda: stats.get_report_map_stats(engine, mode='raw', max_points=1200, bounds=(33,126,34.5,127.5), zoom=7))]):
        for temperature in ('cold', 'warm'):
            calls.clear(); sql_times.clear()
            t = time.perf_counter()
            payload = fn()
            elapsed = time.perf_counter() - t
            if name == 'statistics':
                assert payload[1]['all']['total'] == args.count
            elif name.startswith('map'):
                assert payload['meta']['total_reports'] == args.count
                assert sum(p['total'] for p in payload['points']) == (payload['meta']['viewport_reports'] if name == 'map_viewport' else args.count)
                if name in ('map_bounded','map_viewport'):
                    assert len(payload['points']) <= 1200
            else:
                assert payload['total'] == args.count
            serialized_at=time.perf_counter()
            encoded=json.dumps(payload, ensure_ascii=False).encode()
            serialize_seconds=time.perf_counter()-serialized_at
            row = dict(name=name, cache=temperature, seconds=round(elapsed, 4), sql=len(calls),
                       sql_cursor_seconds=round(sum(sql_times),6), serialize_seconds=round(serialize_seconds,6), bytes=len(encoded),
                       max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            results['measurements'].append(row)
            print(json.dumps(row), flush=True)
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()
