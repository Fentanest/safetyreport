"""동일 기관명·담당자명 충돌 재현용 합성 자료. 운영 데이터에는 사용하지 않는다."""
from core.database import models


def seed_collision_rows(engine):
    rows = []
    for index, code, status, fine, day in (
        (1, "99999998", "수용", "과태료 40000원", "02"),
        (2, "99999999", "불수용", "미확인", "04"),
    ):
        rows.append({"ID": f"99100{index}", "신고번호": f"SR-REVIEW-{index}",
                     "처리기관": "검수 동일명 기관", "처리기관코드": code,
                     "담당자": "검수 담당자", "상태": "답변완료", "처리상태": status,
                     "범칙금_과태료": fine, "신고일": "2026-09-01", "답변일": f"2026-09-{day}",
                     "위반법규": "검수 법규", "위도": 37.56 - index * .01,
                     "경도": 126.83 + index * .01, "위반장소": f"검수주소{index}"})
    with engine.begin() as conn:
        conn.execute(models.merge_traffic_table.insert().prefix_with('OR IGNORE'), rows)
