"""이전 DB 업데이트 로직 보관(비활성, EO R-06에서 upgrade_schema 안의 주석을 여기로 옮겼다).

2026-09-26 초기화 크롤링 릴리스(`source-rebuild-2026-09-26.1`)부터 서버는 이전 버전 DB 를 고치지 않는다:
낮은 버전 DB 는 서버 시작 때 reset_legacy_database() 가 백업 뒤 비우고 초기화 크롤링이 다시 채운다. 복원은 거절한다.
다음 스키마 변경 때 업데이트 로직을 다시 켜려면(사용자 요청 예정) 아래 각 조각을 `core/database/database.py`
upgrade_schema 의 같은 번호 자리에 되돌리고, 모바일 LocalDbService 의 같은 단계와 함께 바꾼다(PROJECT_RULES §3-1, 양쪽 레포·왕복 시험).

조각이 부르는 함수(backup_before_upgrade, _apply_versioned_migrations·_migration_N, migrate_by_entry_value, backfill_synced_at,
_normalize_processing_layers, merge_final)는 database.py 에 그대로 있다. backfill_synced_at 은 fixture 가 지금도 쓴다.
이 파일에는 실행되는 코드가 없다.
"""

# ① upgrade_schema 시작(_refuse_legacy_schema 뒤):
# [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스]
# if backup_dir:
#     backup_before_upgrade(engine, backup_dir)

# ② 새로 만든 표가 mysafety_watchlist 일 때(표 만들기 직후):
# [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 옛 merge 표의 감시목록 열 → 감시목록 표 이관
# if table.name == 'mysafety_watchlist':
#     if settings.table_merge_traffic in existing_tables and settings.table_merge_other in existing_tables:
#         try:
#             migrate_query = text(f"""
#                 INSERT OR IGNORE INTO mysafety_watchlist (신고번호)
#                 SELECT 신고번호 FROM {settings.table_merge_traffic} WHERE 감시목록 = 'Y'
#                 UNION
#                 SELECT 신고번호 FROM {settings.table_merge_other} WHERE 감시목록 = 'Y'
#             """)
#             connection.execute(migrate_query)
#             logger.LoggerFactory.logbot.info("기존 감시목록 데이터를 완벽하게 이관했습니다.")
#         except Exception as e:
#             logger.LoggerFactory.logbot.error(f"감시목록 데이터 이관 중 오류 발생: {e}")

# ③ 이미 있는 표의 빠진 열 추가(표 만들기 루프의 else):
# [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 있는 표에 빠진 열 추가
# else:
#     existing_columns = [col['name'] for col in inspector.get_columns(table.name)]
#     for column in table.columns:
#         if column.name not in existing_columns:
#             logger.LoggerFactory.logbot.warning(f"'{table.name}' 테이블에 '{column.name}' 컬럼을 추가합니다.")
#             column_type = column.type.compile(engine.dialect)
#             alter_query = text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}')
#             try:
#                 connection.execute(alter_query)
#             except Exception as e:
#                 logger.LoggerFactory.logbot.error(f"스키마 업그레이드 오류: {e}")

# ④ 인덱스·버전 기록 뒤:
# [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스]
# _apply_versioned_migrations(engine)

# ⑤ maintenance=True 일 때 중복군 재계산 앞:
# [이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스] 옛 형식 자료 정리(재분류·synced_at 백필·상태 정규화)
# migrate_by_entry_value(engine)
# backfill_synced_at(engine)
# normalized_rows = _normalize_processing_layers(engine)
# if normalized_rows:
#     merge_final(engine)
# else:
#     _refresh_duplicate_groups(engine)
