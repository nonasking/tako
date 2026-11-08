"""append 모드 ADF 보존, site 정규화, 토큰 파일 권한, 응답 타임아웃 재시도 정책."""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

import requests

from tako.auth import Credentials, write_credentials
from tako.cmd_edit import append_adf
from tako.config import normalize_site
from tako.jira_client import JiraApiError
from tests.test_jira_client import FakeResponse, _client


class AppendAdfTest(unittest.TestCase):
    def test_existing_nodes_kept_verbatim(self) -> None:
        table = {"type": "table", "content": [{"type": "tableRow", "content": []}]}
        task = {"type": "taskList", "attrs": {"localId": "x"}, "content": []}
        current = {"version": 1, "type": "doc", "content": [table, task]}
        addition = {"version": 1, "type": "doc", "content": [{"type": "heading", "attrs": {"level": 2}}]}

        merged = append_adf(current, addition)

        self.assertIs(merged["content"][0], table)
        self.assertIs(merged["content"][1], task)
        self.assertEqual(merged["content"][2]["type"], "heading")
        self.assertEqual(len(current["content"]), 2)  # 원본은 건드리지 않는다

    def test_empty_current_returns_addition(self) -> None:
        addition = {"version": 1, "type": "doc", "content": [{"type": "paragraph"}]}
        self.assertIs(append_adf(None, addition), addition)
        self.assertIs(append_adf({"type": "doc", "content": []}, addition), addition)


class NormalizeSiteTest(unittest.TestCase):
    def test_strips_scheme_and_path(self) -> None:
        for raw in [
            "mycompany.atlassian.net",
            "https://mycompany.atlassian.net",
            "https://mycompany.atlassian.net/jira/software/projects",
            "  http://mycompany.atlassian.net/  ",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_site(raw), "mycompany.atlassian.net")


class NormalizeSiteEmptyTest(unittest.TestCase):
    def test_scheme_only_yields_empty(self) -> None:
        self.assertEqual(normalize_site("https://"), "")


@unittest.skipIf(os.name == "nt", "POSIX 권한 비트는 Windows에서 의미가 없다")
class WriteCredentialsModeTest(unittest.TestCase):
    def test_new_file_created_0600(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "creds.json"
            old = os.umask(0o022)
            try:
                write_credentials(Credentials(email="a@b.com", api_token="t"), path)
            finally:
                os.umask(old)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_existing_loose_file_tightened(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "creds.json"
            path.write_text("{}", encoding="utf-8")
            os.chmod(path, 0o644)
            write_credentials(Credentials(email="a@b.com", api_token="t"), path)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


class ReadTimeoutRetryTest(unittest.TestCase):
    def test_read_timeout_on_create_is_not_retried(self) -> None:
        # 응답 대기 중 타임아웃은 요청이 이미 처리됐을 수 있다. 생성은 재시도하면 중복.
        client, session = _client([requests.ReadTimeout("slow"), FakeResponse(201, payload={"key": "WL-9"})])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_read_timeout_on_get_is_retried(self) -> None:
        ok = FakeResponse(200, payload={"key": "WL-1"})
        client, session = _client([requests.ReadTimeout("slow"), ok])
        self.assertEqual(client.get_issue("WL-1")["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)

    def test_connect_timeout_on_create_is_retried(self) -> None:
        # 연결 단계 타임아웃은 서버에 닿기 전이라 생성도 재시도해도 안전하다.
        client, session = _client([requests.ConnectTimeout("no route"), FakeResponse(201, payload={"key": "WL-9"})])
        self.assertEqual(client.create_issue({"summary": "x"}).key, "WL-9")
        self.assertEqual(len(session.calls), 2)


if __name__ == "__main__":
    unittest.main()


class CmdUpdateWiringTest(unittest.TestCase):
    """cmd_update가 실제로 PUT하는 description을 확인한다."""

    EXISTING_ADF = {
        "version": 1,
        "type": "doc",
        "content": [
            {"type": "table", "content": [{"type": "tableRow", "content": []}]},
            {"type": "taskList", "attrs": {"localId": "t1"}, "content": []},
        ],
    }

    class _Client:
        def __init__(self, description):
            self.description = description
            self.sent = None

        def get_issue(self, key, fields=None):
            return {"key": key, "fields": {"summary": "제목", "description": self.description}}

        def update_issue_fields(self, key, fields):
            self.sent = fields

    def _run(self, client, **over):
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch

        from tako.cmd_edit import cmd_update
        from tests.test_cmd_new import _cfg

        args = argparse.Namespace(
            key="WL-1", summary=None, body="새 내용", mode="append", section="작업 결과",
            yes=True, credentials=None,
        )
        for k, v in over.items():
            setattr(args, k, v)
        with patch("tako.cmd_edit.build_client", return_value=client), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return cmd_update(args, _cfg())

    def test_append_keeps_existing_adf_nodes(self) -> None:
        client = self._Client(self.EXISTING_ADF)
        self.assertEqual(self._run(client), 0)
        content = client.sent["description"]["content"]
        self.assertEqual(content[:2], self.EXISTING_ADF["content"])
        self.assertGreater(len(content), 2)

    def test_overwrite_replaces_body(self) -> None:
        client = self._Client(self.EXISTING_ADF)
        self.assertEqual(self._run(client, mode="overwrite"), 0)
        types = [n["type"] for n in client.sent["description"]["content"]]
        self.assertNotIn("table", types)
        self.assertNotIn("taskList", types)

    def test_summary_only_leaves_description_alone(self) -> None:
        client = self._Client(self.EXISTING_ADF)
        self.assertEqual(self._run(client, body=None, summary="새 제목"), 0)
        self.assertEqual(client.sent, {"summary": "새 제목"})


class RetryAfterRequestSentTest(unittest.TestCase):
    """requests.ConnectionError 중 요청을 보낸 뒤 끊긴 경우는 생성 호출을 재시도하지 않는다."""

    @staticmethod
    def _refused() -> requests.ConnectionError:
        from urllib3.exceptions import MaxRetryError, NewConnectionError

        reason = NewConnectionError(None, "Failed to establish a new connection: [Errno 61] Connection refused")
        return requests.ConnectionError(MaxRetryError(None, "/rest/api/3/issue", reason=reason))

    @staticmethod
    def _aborted() -> requests.ConnectionError:
        from http.client import RemoteDisconnected

        from urllib3.exceptions import ProtocolError

        return requests.ConnectionError(
            ProtocolError("Connection aborted.", RemoteDisconnected("Remote end closed connection without response"))
        )

    def test_create_retried_when_connection_refused(self) -> None:
        client, session = _client([self._refused(), FakeResponse(201, payload={"key": "WL-9"})])
        self.assertEqual(client.create_issue({"summary": "x"}).key, "WL-9")
        self.assertEqual(len(session.calls), 2)

    def test_create_not_retried_when_connection_aborted_after_send(self) -> None:
        client, session = _client([self._aborted(), FakeResponse(201, payload={"key": "WL-9"})])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_get_still_retried_when_connection_aborted(self) -> None:
        ok = FakeResponse(200, payload={"key": "WL-1"})
        client, session = _client([self._aborted(), ok])
        self.assertEqual(client.get_issue("WL-1")["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)

    def test_tls_error_on_create_is_not_retried(self) -> None:
        # SSLError 는 핸드셰이크 실패와, 요청을 보낸 뒤 응답을 읽다 난 TLS 오류를 구분할 수
        # 없다. 뒤쪽이면 티켓이 이미 만들어졌을 수 있으므로 생성은 재시도하지 않는다.
        client, session = _client([requests.exceptions.SSLError("bad record mac"), FakeResponse(201, payload={"key": "WL-9"})])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_tls_error_on_get_is_still_retried(self) -> None:
        ok = FakeResponse(200, payload={"key": "WL-1"})
        client, session = _client([requests.exceptions.SSLError("bad record mac"), ok])
        self.assertEqual(client.get_issue("WL-1")["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)

    @staticmethod
    def _proxy_error(original: Exception) -> requests.exceptions.ProxyError:
        """requests 가 실제로 던지는 사슬: ProxyError(MaxRetryError(reason=urllib3 ProxyError(원래 오류)))."""
        from urllib3.exceptions import MaxRetryError
        from urllib3.exceptions import ProxyError as Urllib3ProxyError

        reason = Urllib3ProxyError("Unable to connect to proxy", original)
        return requests.exceptions.ProxyError(MaxRetryError(None, "/rest/api/3/issue", reason=reason))

    def test_unreachable_proxy_on_create_is_retried(self) -> None:
        from urllib3.exceptions import NewConnectionError

        refused = self._proxy_error(NewConnectionError(None, "Connection refused"))
        client, session = _client([refused, FakeResponse(201, payload={"key": "WL-9"})])
        self.assertEqual(client.create_issue({"summary": "x"}).key, "WL-9")
        self.assertEqual(len(session.calls), 2)

    def test_connection_dropped_behind_proxy_on_create_is_not_retried(self) -> None:
        # urllib3 는 터널을 지나 요청을 보낸 뒤 끊긴 연결도 "Unable to connect to proxy" 로 감싼다.
        # 요청은 Jira 에 닿았을 수 있으므로 생성은 재시도하지 않는다.
        from http.client import RemoteDisconnected

        dropped = self._proxy_error(RemoteDisconnected("Remote end closed connection without response"))
        client, session = _client([dropped, FakeResponse(201, payload={"key": "WL-9"})])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_certificate_verification_failure_is_pre_send(self) -> None:
        # 인증서 검증은 핸드셰이크 단계라 요청이 나가지 않았다. "처리됐을 수 있음" 안내를 붙이지 않는다.
        import ssl

        from urllib3.exceptions import MaxRetryError
        from urllib3.exceptions import SSLError as Urllib3SSLError

        cert = ssl.SSLCertVerificationError(1, "certificate verify failed: unable to get local issuer certificate")
        exc = requests.exceptions.SSLError(MaxRetryError(None, "/rest/api/3/issue", reason=Urllib3SSLError(cert)))
        client, session = _client(exc)
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertNotIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 3)


class TruncatedResponseTest(unittest.TestCase):
    """응답 본문이 중간에 끊긴 경우 (ChunkedEncodingError 는 ConnectionError 의 하위 클래스가 아니다)."""

    def test_truncated_response_on_create_is_reported_not_retried(self) -> None:
        client, session = _client([
            requests.exceptions.ChunkedEncodingError("Response ended prematurely"),
            FakeResponse(201, payload={"key": "WL-9"}),
        ])
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))
        self.assertEqual(len(session.calls), 1)

    def test_truncated_response_on_get_is_retried(self) -> None:
        ok = FakeResponse(200, payload={"key": "WL-1"})
        client, session = _client([requests.exceptions.ChunkedEncodingError("Response ended prematurely"), ok])
        self.assertEqual(client.get_issue("WL-1")["key"], "WL-1")
        self.assertEqual(len(session.calls), 2)

    def test_unreadable_201_body_warns_ticket_may_exist(self) -> None:
        class BadJson(FakeResponse):
            def json(self):
                raise ValueError("Expecting value")

        client, _ = _client(BadJson(201))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))

    def test_201_without_key_warns_ticket_may_exist(self) -> None:
        client, _ = _client(FakeResponse(201, payload=["unexpected"]))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))


class MaybeAppliedHintTest(unittest.TestCase):
    """상태를 바꾸는 호출이 서버에 닿았을 수 있는 채로 실패하면 확인 안내를 붙인다."""

    def test_put_exhausted_after_sent_errors_has_hint(self) -> None:
        # PUT 은 재시도하지만 세 번 모두 응답을 못 받으면 반영됐는지 알 수 없다.
        client, session = _client(requests.ReadTimeout("slow"))
        with self.assertRaises(JiraApiError) as ctx:
            client.update_issue_fields("WL-1", {"summary": "x"})
        self.assertEqual(len(session.calls), 3)
        self.assertIn("확인 후", str(ctx.exception))

    def test_put_exhausted_before_reaching_server_has_no_hint(self) -> None:
        client, _ = _client(requests.ConnectTimeout("no route"))
        with self.assertRaises(JiraApiError) as ctx:
            client.update_issue_fields("WL-1", {"summary": "x"})
        self.assertNotIn("확인 후", str(ctx.exception))

    def test_5xx_on_create_and_update_has_hint(self) -> None:
        client, _ = _client(FakeResponse(503, text="unavailable"))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))

        client, _ = _client(FakeResponse(503, text="unavailable"))
        with self.assertRaises(JiraApiError) as ctx:
            client.update_issue_fields("WL-1", {"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))

    def test_read_only_failures_have_no_hint(self) -> None:
        client, _ = _client(requests.ReadTimeout("slow"))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_issue("WL-1")
        self.assertNotIn("확인 후", str(ctx.exception))

        # 검색은 POST 지만 조회다.
        client, _ = _client(requests.ReadTimeout("slow"))
        with self.assertRaises(JiraApiError) as ctx:
            client.search_issues("project = WL")
        self.assertNotIn("확인 후", str(ctx.exception))

        client, _ = _client(FakeResponse(503, text="unavailable"))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_issue("WL-1")
        self.assertNotIn("확인 후", str(ctx.exception))

    def test_hint_remembers_earlier_attempts(self) -> None:
        # 마지막 실패만 보면 안 된다. 앞선 시도가 서버에 닿았을 수 있으면 안내가 붙어야 한다.
        cases = [
            [requests.ReadTimeout("slow"), requests.ReadTimeout("slow"), requests.ConnectTimeout("no route")],
            [FakeResponse(503, text="x"), requests.ConnectTimeout("no route"), requests.ConnectTimeout("no route")],
            [FakeResponse(503, text="x"), FakeResponse(503, text="x"), FakeResponse(429, text="slow down")],
        ]
        for responses in cases:
            with self.subTest(responses=[type(r).__name__ for r in responses]):
                client, session = _client(responses)
                with self.assertRaises(JiraApiError) as ctx:
                    client.update_issue_fields("WL-1", {"summary": "x"})
                self.assertEqual(len(session.calls), 3)
                self.assertIn("확인 후", str(ctx.exception))

    def test_success_after_earlier_timeout_is_plain_success(self) -> None:
        client, session = _client([requests.ReadTimeout("slow"), FakeResponse(204)])
        client.update_issue_fields("WL-1", {"summary": "x"})  # 예외 없으면 성공
        self.assertEqual(len(session.calls), 2)

    def test_4xx_on_create_has_no_hint(self) -> None:
        client, _ = _client(FakeResponse(400, text="bad field"))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertNotIn("확인 후", str(ctx.exception))


class CmdUpdateDoesNotConvertUnneededBodyTest(unittest.TestCase):
    """제목만 바꾸거나 append 할 때는 기존 본문을 마크다운으로 변환하지 않는다."""

    def _run(self, **over):
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch

        from tako.cmd_edit import cmd_update
        from tests.test_cmd_new import _cfg

        client = CmdUpdateWiringTest._Client(CmdUpdateWiringTest.EXISTING_ADF)
        args = argparse.Namespace(
            key="WL-1", summary=None, body="새 내용", mode="append", section="작업 결과",
            yes=True, credentials=None,
        )
        for k, v in over.items():
            setattr(args, k, v)
        with patch("tako.cmd_edit.build_client", return_value=client), \
                patch("tako.cmd_edit.adf_to_markdown", side_effect=AssertionError("변환 호출 금지")) as conv, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = cmd_update(args, _cfg())
        return rc, client, conv

    def test_summary_only(self) -> None:
        rc, client, _ = self._run(body=None, summary="새 제목")
        self.assertEqual(rc, 0)
        self.assertEqual(client.sent, {"summary": "새 제목"})

    def test_append(self) -> None:
        rc, client, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(client.sent["description"]["content"][:2], CmdUpdateWiringTest.EXISTING_ADF["content"])


class NonRetryableRequestErrorTest(unittest.TestCase):
    """재시도해도 같은 결과인 실패는 트레이스백이 아니라 한 줄 오류로 끝난다."""

    def test_invalid_site_url(self) -> None:
        client, session = _client(requests.exceptions.InvalidURL("Failed to parse: https://a..b/"))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("확인 후", str(ctx.exception))  # 요청이 나가지 않았다

    def test_redirect_loop_on_read(self) -> None:
        client, session = _client(requests.exceptions.TooManyRedirects("Exceeded 30 redirects."))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_issue("WL-1")
        self.assertEqual(len(session.calls), 1)
        self.assertNotIn("확인 후", str(ctx.exception))

    def test_redirect_loop_on_create_warns(self) -> None:
        # 서버가 응답은 했으므로 보수적으로 확인을 권한다.
        client, _ = _client(requests.exceptions.TooManyRedirects("Exceeded 30 redirects."))
        with self.assertRaises(JiraApiError) as ctx:
            client.create_issue({"summary": "x"})
        self.assertIn("확인 후", str(ctx.exception))

    def test_non_latin_credentials(self) -> None:
        client, _ = _client(UnicodeEncodeError("latin-1", "토큰", 0, 2, "ordinal not in range(256)"))
        with self.assertRaises(JiraApiError) as ctx:
            client.get_myself()
        self.assertIn("tako init", str(ctx.exception))

    def test_html_body_with_200_is_reported(self) -> None:
        class Html(FakeResponse):
            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        for call in (lambda c: c.get_issue("WL-1"), lambda c: c.get_myself(), lambda c: c.search_issues("project = WL")):
            client, _ = _client(Html(200, text="<html>SSO</html>"))
            with self.assertRaises(JiraApiError) as ctx:
                call(client)
            self.assertIn("JSON 이 아님", str(ctx.exception))


class LinkFailureMessageTest(unittest.TestCase):
    def test_link_failure_says_ticket_already_exists(self) -> None:
        import argparse
        import io
        from contextlib import redirect_stderr, redirect_stdout
        from unittest.mock import patch

        from tako import cmd_new
        from tests.test_cmd_new import FakeClient, _args, _cfg

        class LinkFails(FakeClient):
            def create_issue_link(self, **_kw):
                raise JiraApiError("503 서버 오류. body: x (요청이 처리됐을 수 있으니 Jira에서 확인 후 다시 시도)")

        out, err = io.StringIO(), io.StringIO()
        args = _args(summary="제목", description="본문", link=["WL-100"], yes=True)
        with patch("tako.cmd_new.build_client", return_value=LinkFails()), \
                patch("tako.cmd_new.stdin_is_tty", return_value=False), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                rc = cmd_new.cmd_new(args, _cfg())
            except SystemExit as exc:
                rc = exc.code
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue().strip(), "WL-1")
        self.assertIn("이미 만들어졌다", err.getvalue())
        self.assertIn("다시 실행하지 말고", err.getvalue())
        del argparse

