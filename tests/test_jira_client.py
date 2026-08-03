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
    """requests.Response 중 _format_error / 재시도 판단이 실제로 보는 부분만."""

    def __init__(self, status_code: int, text: str = "", payload=None, headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    """JiraSiteClient._session 자리에 꽂는 스텁. 호출 인자를 기록만 하고 정해진 응답 반환.

    응답 리스트를 주면 호출 순서대로 하나씩 소비 (재시도 검증용). 마지막 응답은 반복.
    """

    def __init__(self, response):
        self._responses = list(response) if isinstance(response, list) else [response]
        self.calls: list[tuple[str, str]] = []
        self.headers: dict[str, str] = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        resp = self._responses[0]
        if len(self._responses) > 1:
            self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def _client(response) -> tuple[JiraSiteClient, FakeSession]:
    client = JiraSiteClient(
        site="x.atlassian.net", creds=Credentials(email="a@b.com", api_token="t")
    )
    session = FakeSession(response)
    client._session = session  # type: ignore[attr-defined]
    client._sleep = lambda _s: None  # 재시도 대기 없이 즉시 — 테스트 시간 절약
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

    def test_get_issue_fields_param_narrows_request(self) -> None:
        client, session = _client(FakeResponse(200, payload={"key": "WL-1"}))
        client.get_issue("WL-1", fields=["summary", "issuetype"])
        _, url = session.calls[0]
        self.assertTrue(url.endswith("issue/WL-1?fields=summary,issuetype"))

    def test_update_issue_fields_accepts_204(self) -> None:
        client, session = _client(FakeResponse(204))
        client.update_issue_fields("WL-1", {"summary": "new"})  # 예외 없으면 성공
        self.assertEqual(session.calls, [("PUT", "https://x.atlassian.net/rest/api/3/issue/WL-1")])


class RetryPolicyTest(unittest.TestCase):
    """_request 재시도 정책 — 429 는 항상, 5xx 는 멱등 호출만, POST /issue 는 제외."""

    def test_429_retried_then_succeeds(self) -> None:
        ok = FakeResponse(200, payload={"issues": [], "isLast": True})
        client, session = _client([FakeResponse(429, text="slow"), ok])
        result = client.search_issues("project = WL")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(session.calls), 2)

    def test_429_respects_retry_after_header(self) -> None:
        sleeps: list[float] = []
        ok = FakeResponse(200, payload={"issues": []})
        client, _ = _client([FakeResponse(429, headers={"Retry-After": "3"}), ok])
        client._sleep = sleeps.append
        client.search_issues("project = WL")
        self.assertEqual(sleeps, [3.0])

    def test_429_gives_up_after_max_attempts(self) -> None:
        client, session = _client(FakeResponse(429, text="still limited"))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_issue("WL-1")
        self.assertIn("429", str(ctx.exception))
        self.assertEqual(len(session.calls), 3)  # max_attempts 기본 3

    def test_5xx_on_get_is_retried(self) -> None:
        ok = FakeResponse(200, payload={"key": "WL-1", "fields": {}})
        client, session = _client([FakeResponse(500, text="boom"), ok])
        issue = client.get_issue("WL-1")
        self.assertEqual(issue["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)

    def test_5xx_on_search_is_retried(self) -> None:
        # POST 지만 조회라 retry_responses=True 를 명시하는 경로.
        ok = FakeResponse(200, payload={"issues": []})
        client, session = _client([FakeResponse(502, text="bad gateway"), ok])
        client.search_issues("project = WL")
        self.assertEqual(len(session.calls), 2)

    def test_5xx_on_create_is_not_retried(self) -> None:
        # 서버가 처리를 마쳤을 수도 있는 생성 호출 — 재시도하면 중복 티켓 위험.
        client, session = _client([FakeResponse(500, text="boom"), FakeResponse(201, payload={"key": "WL-9"})])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("500", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_network_error_retried_then_succeeds(self) -> None:
        import requests
        ok = FakeResponse(200, payload={"key": "WL-1"})
        client, session = _client([requests.ConnectionError("reset"), ok])
        issue = client.get_issue("WL-1")
        self.assertEqual(issue["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)


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
