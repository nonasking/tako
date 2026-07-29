"""보고자(reporter) 지정 경로 단위 테스트 (stdlib unittest — 네트워크 호출 없음).

페이로드 조립 / 미리보기 / 권한 사전 확인 세 갈래를 본다.
실행: python -m unittest tests.test_reporter
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from tako.auth import Credentials
from tako.issue_draft import DraftError, IssueDraft, build_payload, render_preview
from tako.jira_client import JiraApiError, JiraSiteClient
from tako.main import _can_modify_reporter


BASE = {
    "project": "WL",
    "issue_type": "Task",
    "summary": "제목",
    "description": "본문",
}


class ReporterDraftTest(unittest.TestCase):
    def test_absent_reporter_stays_out_of_payload(self) -> None:
        # 미지정이 정상 경로 — Jira 가 인증 사용자를 알아서 넣는다.
        fields = build_payload(IssueDraft.from_payload(BASE))["payload"]["fields"]
        self.assertNotIn("reporter", fields)

    def test_reporter_becomes_account_id_object(self) -> None:
        draft = IssueDraft.from_payload({**BASE, "reporter": "5b10a2844c20165700ede21g"})
        fields = build_payload(draft)["payload"]["fields"]
        self.assertEqual(fields["reporter"], {"accountId": "5b10a2844c20165700ede21g"})

    def test_label_is_display_only(self) -> None:
        draft = IssueDraft.from_payload(
            {**BASE, "reporter": "acc-1", "reporter_label": "이주열 (jy@example.com)"}
        )
        fields = build_payload(draft)["payload"]["fields"]
        self.assertEqual(fields["reporter"], {"accountId": "acc-1"})
        self.assertNotIn("reporter_label", fields)
        self.assertIn("보고자:   이주열 (jy@example.com)", render_preview(draft))

    def test_preview_falls_back_to_account_id(self) -> None:
        draft = IssueDraft.from_payload({**BASE, "reporter": "acc-1"})
        self.assertIn("보고자:   acc-1", render_preview(draft))

    def test_preview_omits_line_when_unset(self) -> None:
        self.assertNotIn("보고자", render_preview(IssueDraft.from_payload(BASE)))

    def test_non_string_rejected(self) -> None:
        with self.assertRaises(DraftError):
            IssueDraft.from_payload({**BASE, "reporter": 12345})
        with self.assertRaises(DraftError):
            IssueDraft.from_payload({**BASE, "reporter_label": []})

    def test_blank_reporter_treated_as_unset(self) -> None:
        draft = IssueDraft.from_payload({**BASE, "reporter": "   "})
        self.assertIsNone(draft.reporter)


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple[str, str]] = []
        self.headers: dict[str, str] = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def _client(response) -> tuple[JiraSiteClient, FakeSession]:
    client = JiraSiteClient(
        site="x.atlassian.net", creds=Credentials(email="a@b.com", api_token="t")
    )
    session = FakeSession(response)
    client._session = session  # type: ignore[attr-defined]
    return client, session


def _permission_body(have: bool) -> dict:
    return {
        "permissions": {
            "MODIFY_REPORTER": {
                "key": "MODIFY_REPORTER",
                "name": "Modify Reporter",
                "type": "PROJECT",
                "havePermission": have,
            }
        }
    }


class CheckProjectPermissionTest(unittest.TestCase):
    def test_reads_have_permission(self) -> None:
        for have in (True, False):
            client, session = _client(FakeResponse(200, payload=_permission_body(have)))
            self.assertIs(
                client.check_project_permission("MODIFY_REPORTER", project_key="WL"), have
            )
            method, url = session.calls[0]
            self.assertEqual(method, "GET")
            self.assertIn("mypermissions?projectKey=WL", url)
            self.assertIn("permissions=MODIFY_REPORTER", url)

    def test_project_key_is_url_quoted(self) -> None:
        client, session = _client(FakeResponse(200, payload=_permission_body(True)))
        client.check_project_permission("MODIFY_REPORTER", project_key="A B")
        self.assertIn("projectKey=A%20B", session.calls[0][1])

    def test_missing_entry_is_undecidable(self) -> None:
        for payload in ({}, {"permissions": {}}, {"permissions": {"MODIFY_REPORTER": {}}}, None):
            client, _ = _client(FakeResponse(200, payload=payload))
            self.assertIsNone(
                client.check_project_permission("MODIFY_REPORTER", project_key="WL")
            )

    def test_error_status_raises(self) -> None:
        client, _ = _client(FakeResponse(404, text="no such project"))
        with self.assertRaises(JiraApiError):
            client.check_project_permission("MODIFY_REPORTER", project_key="NOPE")


class CanModifyReporterTest(unittest.TestCase):
    def _run(self, response) -> tuple[bool, str]:
        client, _ = _client(response)
        err = io.StringIO()
        with redirect_stderr(err):
            return _can_modify_reporter(client, "WL"), err.getvalue()

    def test_permission_granted_is_silent(self) -> None:
        allowed, err = self._run(FakeResponse(200, payload=_permission_body(True)))
        self.assertTrue(allowed)
        self.assertEqual(err, "")

    def test_permission_denied_explains_and_blocks(self) -> None:
        allowed, err = self._run(FakeResponse(200, payload=_permission_body(False)))
        self.assertFalse(allowed)
        self.assertIn("WL 프로젝트에서 보고자를 지정할 권한이 없음", err)
        self.assertIn("Modify Reporter", err)
        self.assertIn("--reporter 를 빼면", err)

    def test_undecidable_response_does_not_block(self) -> None:
        # 확인용 호출이 본 작업을 가로막지 않는다.
        allowed, err = self._run(FakeResponse(200, payload={"permissions": {}}))
        self.assertTrue(allowed)
        self.assertIn("판정 불가", err)

    def test_lookup_failure_does_not_block(self) -> None:
        allowed, err = self._run(FakeResponse(500, text="boom"))
        self.assertTrue(allowed)
        self.assertIn("권한 확인 실패", err)


if __name__ == "__main__":
    unittest.main()
