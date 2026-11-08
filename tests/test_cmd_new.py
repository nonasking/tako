"""tako new 흐름 단위 테스트 (stdlib unittest — 네트워크 호출 없음).

인자 → payload 조립, --draft-file 충돌 판정, 담당자/보고자 해석 순서를 본다.
클라이언트는 build_client 를 가짜로 바꿔 끼워 REST 없이 돈다.
실행: python -m unittest tests.test_cmd_new
"""

from __future__ import annotations

import argparse
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tako.cmd_new import _apply_pendings, _payload_with_defaults, _prefilled_from_args, cmd_new
from tako.config import AutoFillRules, JiraSite, TakoConfig
from tako.jira_client import CreatedIssue


ACC_ME = "557058:aaaaaaaa-bbbb"
ACC_OTHER = "557058:cccccccc-dddd"


def _cfg(**jira_over) -> TakoConfig:
    # auto_copy_url 은 꺼 둔다 — 켜면 테스트가 실제 클립보드를 건드린다.
    jira = dict(site="x.atlassian.net", default_project="WL", default_issue_type="Task", auto_copy_url=False)
    jira.update(jira_over)
    return TakoConfig(
        jira=JiraSite(**jira),
        allowed_issue_types=("Task", "Bug"),
        auto_fill=AutoFillRules(),
        epic_aliases={"infra": "WL-9200"},
    )


def _args(**over) -> argparse.Namespace:
    base = dict(
        credentials=None, project=None, issue_type=None, summary=None, description=None,
        parent=None, assignee=None, reporter=None, label=[], story_points=None,
        duedate=None, link=[], yes=False, edit=False, draft_file=None,
    )
    base.update(over)
    return argparse.Namespace(**base)


class FakeClient:
    def __init__(self, *, can_modify_reporter: bool = True) -> None:
        self.calls: list[str] = []
        self.created: dict | None = None
        self._can_modify_reporter = can_modify_reporter

    def get_myself(self) -> dict:
        self.calls.append("myself")
        return {"accountId": ACC_ME, "displayName": "나"}

    def search_users(self, query: str) -> list[dict]:
        self.calls.append(f"search:{query}")
        return [{"accountId": ACC_OTHER, "displayName": "동료", "emailAddress": query}]

    def check_project_permission(self, permission: str, *, project_key: str) -> bool:
        self.calls.append(f"perm:{project_key}")
        return self._can_modify_reporter

    def create_issue(self, fields: dict) -> CreatedIssue:
        self.created = fields
        return CreatedIssue(key="WL-1", url="https://x.atlassian.net/browse/WL-1", raw={})


def _run_new(args: argparse.Namespace, cfg: TakoConfig, client: FakeClient) -> tuple[int, str, str]:
    """cmd_new 실행 → (종료 코드, stdout, stderr). 중간 SystemExit 도 종료 코드로 받는다."""
    out, err = io.StringIO(), io.StringIO()
    with patch("tako.cmd_new.build_client", return_value=client), \
         patch("tako.cmd_new.stdin_is_tty", return_value=False), \
         redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cmd_new(args, cfg)
        except SystemExit as exc:
            rc = exc.code
    return rc, out.getvalue(), err.getvalue()


class PrefilledTest(unittest.TestCase):
    def test_only_given_args_are_kept(self) -> None:
        pre = _prefilled_from_args(_args(summary="제목", label=["a"], story_points=0))
        self.assertEqual(pre, {"summary": "제목", "labels": ["a"], "story_points": 0})

    def test_empty_description_counts_as_given(self) -> None:
        # 빈 본문도 명시 입력 — None 과 구분해야 인터랙티브로 빠지지 않는다.
        self.assertEqual(_prefilled_from_args(_args(description="")), {"description": ""})

    def test_defaults_fill_from_config(self) -> None:
        payload = _payload_with_defaults({"parent_epic": "infra"}, _cfg(default_assignee="me"))
        self.assertEqual(payload["project"], "WL")
        self.assertEqual(payload["issue_type"], "Task")
        self.assertEqual(payload["parent_epic"], "WL-9200")
        self.assertEqual(payload["assignee_pending"], "me")

    def test_explicit_assignee_beats_default(self) -> None:
        payload = _payload_with_defaults({"assignee_pending": "a@b.c"}, _cfg(default_assignee="me"))
        self.assertEqual(payload["assignee_pending"], "a@b.c")


class DraftFileConflictTest(unittest.TestCase):
    def test_content_arg_with_draft_file_is_rejected(self) -> None:
        for over in ({"summary": "x"}, {"description": ""}, {"label": ["a"]}, {"story_points": 0}):
            with self.subTest(over=over):
                rc, _, err = _run_new(_args(draft_file="d.md", yes=True, **over), _cfg(), FakeClient())
                self.assertEqual(rc, 2)
                self.assertIn("--draft-file 은 내용 인자", err)

    def test_issue_type_alone_is_allowed_past_conflict_check(self) -> None:
        rc, _, err = _run_new(
            _args(draft_file="/nonexistent/d.md", issue_type="Bug", yes=True), _cfg(), FakeClient()
        )
        self.assertEqual(rc, 2)
        self.assertNotIn("내용 인자", err)
        self.assertIn("파일 못 읽음", err)


class ApplyPendingsTest(unittest.TestCase):
    def test_both_roles_resolved_assignee_first(self) -> None:
        client = FakeClient()
        payload = {"project": "WL", "assignee_pending": "me", "reporter_pending": "a@b.c"}
        with redirect_stderr(io.StringIO()):
            rc = _apply_pendings(payload, client, _cfg())
        self.assertIsNone(rc)
        self.assertEqual(client.calls, ["myself", "perm:WL", "search:a@b.c"])
        self.assertEqual(payload["assignee"], ACC_ME)
        self.assertEqual(payload["reporter"], ACC_OTHER)
        self.assertNotIn("assignee_pending", payload)
        self.assertNotIn("reporter_pending", payload)

    def test_reporter_without_permission_keeps_pending(self) -> None:
        payload = {"project": "WL", "reporter_pending": "me"}
        with redirect_stderr(io.StringIO()):
            rc = _apply_pendings(payload, FakeClient(can_modify_reporter=False), _cfg())
        self.assertEqual(rc, 2)
        self.assertEqual(payload["reporter_pending"], "me")  # 편집 라운드가 원래 입력을 다시 보여준다

    def test_unresolvable_input_reports_role(self) -> None:
        err = io.StringIO()
        with redirect_stderr(err):
            rc = _apply_pendings({"assignee_pending": "홍길동"}, FakeClient(), _cfg())
        self.assertEqual(rc, 2)
        self.assertIn("[담당자] 형식 미지원", err.getvalue())


class AutoModeTest(unittest.TestCase):
    def test_args_only_creates_with_resolved_fields(self) -> None:
        client = FakeClient()
        rc, out, _ = _run_new(
            _args(summary="제목", description="본문", parent="infra", yes=True),
            _cfg(default_assignee="me"),
            client,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "WL-1")
        self.assertEqual(client.created["parent"], {"key": "WL-9200"})
        self.assertEqual(client.created["assignee"], {"accountId": ACC_ME})
        self.assertEqual(client.created["issuetype"], {"name": "Task"})

    def test_disallowed_issue_type_stops_before_rest(self) -> None:
        client = FakeClient()
        rc, _, err = _run_new(
            _args(summary="제목", description="본문", issue_type="Epic", yes=True), _cfg(), client
        )
        self.assertEqual(rc, 2)
        self.assertIn("허용 안 된 이슈 타입", err)
        self.assertIsNone(client.created)


if __name__ == "__main__":
    unittest.main()
