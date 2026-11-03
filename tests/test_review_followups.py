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
