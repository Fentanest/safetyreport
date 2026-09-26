"""계약 벡터 테스트 — contracts/community-ingest/vectors/*.

observations 전 case(build_payload/canonical_json/sha/eligible),
event_decisions(decide_event), canonical-json, schedule(keys/decisions).
기대값을 코드에 맞춰 고치지 않는다 — 불일치는 실패로 보고한다.
"""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from services import community_capture as cap
from services import community_schedule as sched

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "contracts" / "community-ingest" / "vectors"


def load(name):
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


class ObservationVectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load("observations.json")
        cls.by_name = {c["name"]: c for c in cls.data["cases"]}

    def test_all_cases(self):
        self.assertGreaterEqual(len(self.data["cases"]), 1)
        for case in self.data["cases"]:
            with self.subTest(case=case["name"]):
                payload = cap.build_payload(dict(case["input"]))
                self.assertEqual(payload, case["expected_payload"])
                self.assertEqual(cap.is_eligible(payload), case["eligible"])
                self.assertEqual(cap.canonical_json(payload), case["canonical_json"])
                self.assertEqual(cap.payload_sha256(payload), case["payload_sha256"])

    def test_event_decisions(self):
        for item in self.data["event_decisions"]:
            with self.subTest(case=item["name"]):
                payload = cap.build_payload(dict(self.by_name[item["observation"]]["input"]))
                prev = None
                if item.get("prev") is not None:
                    prev = {"payload_sha256": item["prev"]["payload_sha256"],
                            "eligible": item["prev"]["eligible"]}
                elif item.get("server_completed"):
                    prev = {"payload_sha256": None, "eligible": True}
                self.assertEqual(cap.decide_event(prev, payload), item["expect"])

    def test_build_adapter_input_maps_pc_columns(self):
        detail = {"처리상태": "수용", "범칙금_과태료": "과태료: 40,000원", "답변일": "2026-09-10",
                  "처리기관": "서울특별시 중구청", "담당자": "홍길동", "차량번호": "12가3456",
                  "위반장소": "서울특별시 중구 세종대로 110", "벌점": ""}
        adapter = cap.build_adapter_input(detail, {"신고일": "2026-09-01"}, "불법주정차신고",
                                          {"위도": 37.5, "경도": 127.0, "지오코딩상태": "ok"}, "답변완료")
        self.assertEqual(adapter["processing_status"], "수용")
        self.assertEqual(adapter["report_date"], "2026-09-01")
        self.assertEqual(adapter["entry_value"], "불법주정차신고")
        self.assertEqual(adapter["geocode"], {"status": "ok", "lat": 37.5, "lng": 127.0})
        self.assertEqual(adapter["progress_status"], "답변완료")
        payload = cap.build_payload(adapter)
        self.assertNotIn("progress_status", cap.canonical_json(payload))
        self.assertEqual(payload["status"], "accepted")


class CanonicalJsonVectorTest(unittest.TestCase):
    def test_all_cases(self):
        import hashlib
        data = load("canonical-json.json")
        for case in data["cases"]:
            with self.subTest(case=case["name"]):
                self.assertEqual(cap.canonical_json(case["value"]), case["canonical_json"])
                self.assertEqual(hashlib.sha256(case["canonical_json"].encode()).hexdigest(), case["sha256"])


class ScheduleVectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load("schedule.json")

    def test_keys(self):
        for item in self.data["keys"]:
            with self.subTest(case=item["name"]):
                now = datetime.fromisoformat(item["now"].replace("Z", "+00:00"))
                self.assertEqual(sched.due_key(now), item["due_key"])
                self.assertEqual(sched.next_due_at(now), datetime.fromisoformat(
                    item["next_due_at"].replace("Z", "+00:00")))

    def test_decisions(self):
        for item in self.data["decisions"]:
            with self.subTest(case=item["name"]):
                now = datetime.fromisoformat(item["now"].replace("Z", "+00:00"))
                key = item.get("key") or sched.due_key(now)
                run = None
                if key in item.get("runs", {}):
                    run = dict(item["runs"][key])
                self.assertEqual(sched.should_run(now, run), item["should_run"], key)

    def test_list_refetch_rule_loads(self):
        data = load("list_refetch.json")
        self.assertTrue(data["cases"])
        self.assertIn("refetch", data["rule"])


if __name__ == "__main__":
    unittest.main()
