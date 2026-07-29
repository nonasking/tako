"""jira_client 의 에러 매핑 단위 테스트 (stdlib unittest — 네트워크 호출 없음).

응답은 status_code / text / json() 만 흉내내는 가짜 객체로 대체.

실행: python -m unittest tests.test_jira_client
"""

from __future__ import annotations

import unittest

from tako.auth import Credentials
from tako.jira_client import (
    JiraApiError,
    JiraSiteClient,
    _format_error,
    markdown_to_adf,
)


class FakeResponse:
    """requests.Response 중 _format_error 가 실제로 보는 부분만."""

    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """JiraSiteClient._session 자리에 꽂는 스텁. 호출 인자를 기록만 하고 정해진 응답 반환."""

    def __init__(self, response: FakeResponse):
        self._response = response
        self.calls: list[tuple[str, str]] = []
        self.headers: dict[str, str] = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        return self._response


def _client(response: FakeResponse) -> tuple[JiraSiteClient, FakeSession]:
    client = JiraSiteClient(
        site="x.atlassian.net", creds=Credentials(email="a@b.com", api_token="t")
    )
    session = FakeSession(response)
    client._session = session  # type: ignore[attr-defined]
    return client, session


class FormatErrorTest(unittest.TestCase):
    def test_auth_codes(self) -> None:
        self.assertEqual(_format_error(FakeResponse(401)), "401 인증 실패. 이메일/토큰 확인.")
        self.assertEqual(
            _format_error(FakeResponse(403)), "403 권한 없음. 프로젝트에 생성 권한 있는지 확인."
        )

    def test_input_rejected_includes_body(self) -> None:
        for code in (400, 422):
            msg = _format_error(FakeResponse(code, text='{"errors":{"issuetype":"invalid"}}'))
            self.assertTrue(msg.startswith(f"{code} 입력 거부."))
            self.assertIn("issuetype", msg)

    def test_rate_limit_and_server_errors(self) -> None:
        self.assertTrue(_format_error(FakeResponse(429, text="slow down")).startswith("429 한도 초과."))
        self.assertTrue(_format_error(FakeResponse(500, text="boom")).startswith("500 서버 오류."))
        self.assertTrue(_format_error(FakeResponse(503, text="down")).startswith("503 서버 오류."))

    def test_unmapped_codes_fall_through(self) -> None:
        # 404 는 호출부(get_issue 등)가 따로 처리 — _format_error 자체는 '예상치 못한 응답'.
        self.assertTrue(_format_error(FakeResponse(404, text="nope")).startswith("404 예상치 못한 응답."))
        self.assertTrue(_format_error(FakeResponse(418)).startswith("418 예상치 못한 응답."))

    def test_body_truncation_and_empty_text(self) -> None:
        long_body = "x" * 2000
        self.assertEqual(len(_format_error(FakeResponse(400, text=long_body))) - len("400 입력 거부. body: "), 500)
        self.assertEqual(len(_format_error(FakeResponse(429, text=long_body))) - len("429 한도 초과. body: "), 300)
        self.assertEqual(_format_error(FakeResponse(400, text="  ")), "400 입력 거부. body: ")


class ClientErrorMappingTest(unittest.TestCase):
    def test_create_issue_success(self) -> None:
        client, session = _client(FakeResponse(201, payload={"key": "WL-1", "id": "1"}))
        result = client.create_issue({"summary": "x"})
        self.assertEqual(result.key, "WL-1")
        self.assertEqual(result.url, "https://x.atlassian.net/browse/WL-1")
        self.assertEqual(session.calls, [("POST", "https://x.atlassian.net/rest/api/3/issue")])

    def test_create_issue_maps_status_code(self) -> None:
        client, _ = _client(FakeResponse(403))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("403 권한 없음", str(ctx.exception))

    def test_create_issue_response_without_key(self) -> None:
        client, _ = _client(FakeResponse(201, payload={"id": "1"}))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("응답에 키 없음", str(ctx.exception))

    def test_get_issue_404_is_friendly(self) -> None:
        # 404 만은 '예상치 못한 응답' 대신 키를 짚어주는 메시지.
        client, _ = _client(FakeResponse(404, text="not found"))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_issue("WL-9999")
        self.assertEqual(str(ctx.exception), "이슈를 찾을 수 없음: WL-9999")

    def test_update_issue_fields_accepts_204(self) -> None:
        client, session = _client(FakeResponse(204))
        client.update_issue_fields("WL-1", {"summary": "new"})  # 예외 없으면 성공
        self.assertEqual(session.calls, [("PUT", "https://x.atlassian.net/rest/api/3/issue/WL-1")])


class MarkdownToAdfTest(unittest.TestCase):
    def test_empty_text_yields_empty_doc(self) -> None:
        for empty in ("", "   \n"):
            doc = markdown_to_adf(empty)
            self.assertEqual(doc["type"], "doc")
            self.assertEqual(doc["version"], 1)
            self.assertEqual(doc["content"], [{"type": "paragraph", "content": []}])

    def test_converts_markdown_to_adf_doc(self) -> None:
        doc = markdown_to_adf("## 제목\n본문")
        self.assertEqual(doc["type"], "doc")
        self.assertTrue(doc["content"])


if __name__ == "__main__":
    unittest.main()
