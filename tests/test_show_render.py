"""tako show 텍스트 렌더 중 하위 이슈 / 연결 이슈 줄 단위 테스트 (stdlib unittest).

실행: python -m unittest tests.test_show_render
"""

from __future__ import annotations

import unittest

from tako.main import _child_lines, _related_lines, _render_issue_text


def _issue(key: str, summary: str, status: str | None) -> dict:
    fields: dict = {"summary": summary}
    if status:
        fields["status"] = {"name": status}
    return {"key": key, "fields": fields}


class ChildLinesTest(unittest.TestCase):
    def test_key_status_summary(self) -> None:
        rows = _child_lines([_issue("WL-101", "로그인 API", "진행중")])
        self.assertEqual(rows, ["  WL-101 [진행중] 로그인 API"])

    def test_status_missing_drops_bracket(self) -> None:
        rows = _child_lines([_issue("WL-102", "정산 배치", None)])
        self.assertEqual(rows, ["  WL-102 정산 배치"])

    def test_absent_or_wrong_shape(self) -> None:
        self.assertEqual(_child_lines(None), [])
        self.assertEqual(_child_lines([]), [])
        self.assertEqual(_child_lines("WL-1"), [])
        self.assertEqual(_child_lines([None, "x"]), [])


class RelatedLinesTest(unittest.TestCase):
    def test_outward_uses_outward_phrase(self) -> None:
        links = [{
            "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            "outwardIssue": _issue("WL-200", "결제 모듈", "완료"),
        }]
        self.assertEqual(_related_lines(links), ["  blocks → WL-200 [완료] 결제 모듈"])

    def test_inward_uses_inward_phrase(self) -> None:
        links = [{
            "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            "inwardIssue": _issue("WL-300", "스키마 확정", "진행중"),
        }]
        self.assertEqual(_related_lines(links), ["  is blocked by → WL-300 [진행중] 스키마 확정"])

    def test_falls_back_to_type_name(self) -> None:
        # inward/outward 문구가 없는 링크 타입도 있다 — 이름으로 대체.
        links = [{"type": {"name": "Relates"}, "outwardIssue": _issue("WL-400", "회고", None)}]
        self.assertEqual(_related_lines(links), ["  Relates → WL-400 회고"])

    def test_link_without_target_skipped(self) -> None:
        # 권한 없는 프로젝트의 이슈면 타깃이 통째로 빠져 올 수 있다.
        links = [{"type": {"name": "Relates", "outward": "relates to"}}]
        self.assertEqual(_related_lines(links), [])

    def test_absent_or_wrong_shape(self) -> None:
        self.assertEqual(_related_lines(None), [])
        self.assertEqual(_related_lines({"type": {}}), [])


class RenderIssueTextTest(unittest.TestCase):
    def test_sections_appear_only_when_present(self) -> None:
        issue = {
            "key": "WL-1",
            "fields": {
                "summary": "제목",
                "issuetype": {"name": "Task"},
                "status": {"name": "진행중"},
                "subtasks": [_issue("WL-2", "하위", "대기")],
                "issuelinks": [],
            },
        }
        text = _render_issue_text(issue, [], site="ex.atlassian.net")
        self.assertIn("--- 하위 이슈 (1) ---", text)
        self.assertIn("WL-2 [대기] 하위", text)
        self.assertNotIn("연결된 이슈", text)

    def test_bare_issue_renders_without_new_sections(self) -> None:
        issue = {"key": "WL-9", "fields": {"summary": "제목"}}
        text = _render_issue_text(issue, [], site="ex.atlassian.net")
        self.assertNotIn("하위 이슈", text)
        self.assertNotIn("연결된 이슈", text)
        self.assertIn("--- 설명 ---", text)


if __name__ == "__main__":
    unittest.main()
