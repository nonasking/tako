"""tako retype 의 유형 매칭 단위 테스트 (stdlib unittest).

실행: python -m unittest tests.test_retype
"""

from __future__ import annotations

import unittest

from tako.main import _match_issue_type


# editmeta fields.issuetype.allowedValues 모양 일부.
ALLOWED = [
    {"id": "10001", "name": "Task", "subtask": False},
    {"id": "10002", "name": "Story", "subtask": False},
    {"id": "10003", "name": "기능변경", "subtask": False},
]


class MatchIssueTypeTest(unittest.TestCase):
    def test_name_exact(self) -> None:
        self.assertEqual(_match_issue_type("Story", ALLOWED)["id"], "10002")

    def test_name_case_insensitive(self) -> None:
        self.assertEqual(_match_issue_type("story", ALLOWED)["id"], "10002")
        self.assertEqual(_match_issue_type("  TASK ", ALLOWED)["id"], "10001")

    def test_korean_name(self) -> None:
        self.assertEqual(_match_issue_type("기능변경", ALLOWED)["id"], "10003")

    def test_id_fallback(self) -> None:
        self.assertEqual(_match_issue_type("10003", ALLOWED)["name"], "기능변경")

    def test_no_match_returns_none(self) -> None:
        # 허용 목록에 없는 유형 (계층 경계 변환 등은 애초에 목록에 없어 여기서 걸러짐)
        self.assertIsNone(_match_issue_type("하위작업", ALLOWED))
        self.assertIsNone(_match_issue_type("Bug", ALLOWED))

    def test_empty_allowed(self) -> None:
        self.assertIsNone(_match_issue_type("Story", []))


if __name__ == "__main__":
    unittest.main()
