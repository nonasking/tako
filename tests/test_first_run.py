"""첫 실행(설정 없음) 경로 단위 테스트.

지키려는 계약 두 가지:
  - 비TTY 에서는 절대 입력을 기다리지 않는다 (슬래시 커맨드·CI 가 멈추면 안 됨)
  - 안내문이 저장소 파일을 전제하지 않는다 (PyPI 설치 사용자는 그 파일이 없음)

실행: python -m unittest tests.test_first_run
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from tako.browser import open_url
from tako.cmd_common import load_config_or_guide
from tako.config import first_run_guide


def _missing_path() -> str:
    with TemporaryDirectory() as tmp:
        return str(Path(tmp) / "config.yaml")  # 블록을 빠져나가며 사라진다


class NonTtyTest(unittest.TestCase):
    def test_non_tty_exits_without_prompting(self) -> None:
        # confirm 이 호출되면 자동화가 입력을 기다리며 멈춘다 — 절대 불려선 안 된다.
        with patch("tako.prompts.stdin_is_tty", return_value=False), \
             patch("tako.prompts.confirm") as confirm_mock, \
             redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                load_config_or_guide(_missing_path())

        self.assertEqual(ctx.exception.code, 2)
        confirm_mock.assert_not_called()
        self.assertIn("tako init", err.getvalue())


class TtyTest(unittest.TestCase):
    def test_declining_init_shows_guide_and_exits(self) -> None:
        with patch("tako.prompts.stdin_is_tty", return_value=True), \
             patch("tako.prompts.confirm", return_value=False), \
             redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                load_config_or_guide(_missing_path())

        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("tako init", err.getvalue())

    def test_accepting_init_runs_wizard(self) -> None:
        with patch("tako.prompts.stdin_is_tty", return_value=True), \
             patch("tako.prompts.confirm", return_value=True), \
             patch("tako.cmd_common.interactive_init") as init_mock, \
             redirect_stderr(io.StringIO()):
            # 마법사는 파일을 안 만드는 mock 이므로 재로드는 실패한다 — 그 실패가 곱게 끝나는지까지 본다.
            with self.assertRaises(SystemExit) as ctx:
                load_config_or_guide(_missing_path())

        init_mock.assert_called_once()
        self.assertEqual(ctx.exception.code, 2)

    def test_custom_credentials_path_reaches_the_wizard(self) -> None:
        # --credentials 를 안 넘기면 인증 파일만 기본 경로에 생겨 바로 뒤 로드가 엇갈린다.
        with patch("tako.prompts.stdin_is_tty", return_value=True), \
             patch("tako.prompts.confirm", return_value=True), \
             patch("tako.cmd_common.interactive_init") as init_mock, \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                load_config_or_guide(_missing_path(), "/tmp/custom-creds.json")

        _, kwargs = init_mock.call_args
        self.assertEqual(kwargs["credentials_target"], Path("/tmp/custom-creds.json"))


class GuideTest(unittest.TestCase):
    def test_guide_does_not_assume_a_cloned_repo(self) -> None:
        text = first_run_guide(Path("/tmp/x/config.yaml"))
        # 예전 안내는 `cp config.example.yaml` 이었다 — PyPI 설치자에겐 없는 파일.
        self.assertNotIn("cp config.example.yaml", text)
        self.assertIn("jira:", text)
        self.assertIn("default_project:", text)

    def test_guide_mentions_the_config_path(self) -> None:
        self.assertIn("/tmp/x/config.yaml", first_run_guide(Path("/tmp/x/config.yaml")))


class OpenUrlTest(unittest.TestCase):
    def test_rejects_non_http_schemes(self) -> None:
        # file:// 나 커스텀 스킴을 그대로 넘기면 OS 핸들러가 뭘 열지 알 수 없다.
        for url in ("file:///etc/passwd", "javascript:alert(1)", "", "id.atlassian.com"):
            with self.subTest(url=url):
                self.assertFalse(open_url(url))


if __name__ == "__main__":
    unittest.main()
