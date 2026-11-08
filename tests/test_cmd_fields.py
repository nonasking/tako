"""tako fields detect 안내 문구 단위 테스트 (stdlib unittest — 네트워크 호출 없음).

--save 없이 끝날 때 보여주는 "어디에 추가하라" 안내가 실제로 쓰는 config 경로를
가리키는지 본다. 클라이언트는 build_client 를 가짜로 바꿔 끼운다.
실행: python -m unittest tests.test_cmd_fields
"""

from __future__ import annotations

import argparse
import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from tako.cmd_fields import cmd_fields_detect
from tako.config import DEFAULT_CONFIG_PATH, ENV_OVERRIDE_VAR, AutoFillRules, JiraSite, TakoConfig


CFG = TakoConfig(
    jira=JiraSite(site="x.atlassian.net", default_project="WL", default_issue_type="Task"),
    allowed_issue_types=(),
    auto_fill=AutoFillRules(),
)


class FakeClient:
    def list_fields(self) -> list[dict]:
        return [{"id": "customfield_10016", "name": "Story Points"}]


def _detect(config: str | None) -> tuple[int, str, str]:
    args = argparse.Namespace(name="story_points", save=False, config=config, credentials=None)
    out, err = io.StringIO(), io.StringIO()
    with patch("tako.cmd_fields.build_client", return_value=FakeClient()), \
         redirect_stdout(out), redirect_stderr(err):
        rc = cmd_fields_detect(args, CFG)
    return rc, out.getvalue(), err.getvalue()


class DetectHintPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev_env = os.environ.pop(ENV_OVERRIDE_VAR, None)

    def tearDown(self) -> None:
        os.environ.pop(ENV_OVERRIDE_VAR, None)
        if self._prev_env is not None:
            os.environ[ENV_OVERRIDE_VAR] = self._prev_env

    def test_default_path_without_override(self) -> None:
        rc, out, err = _detect(None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "customfield_10016")
        self.assertIn(f"다음을 {DEFAULT_CONFIG_PATH} 의 jira 블록에", err)
        self.assertIn("tako fields set story_points customfield_10016", err)

    def test_config_option_reflected_in_path_and_commands(self) -> None:
        _, _, err = _detect("/tmp/my tako.yaml")
        self.assertIn("다음을 /tmp/my tako.yaml 의 jira 블록에", err)
        # 재실행 힌트가 같은 파일을 가리켜야 한다 — 공백 경로는 셸 인용.
        self.assertIn("tako --config '/tmp/my tako.yaml' fields set story_points", err)
        self.assertIn("tako --config '/tmp/my tako.yaml' fields detect story_points --save", err)

    def test_env_override_reflected_in_path(self) -> None:
        os.environ[ENV_OVERRIDE_VAR] = "/tmp/env-tako.yaml"
        _, _, err = _detect(None)
        self.assertIn("다음을 /tmp/env-tako.yaml 의 jira 블록에", err)
        # 환경변수는 다음 실행에도 그대로 적용되므로 명령에 --config 를 덧붙이지 않는다.
        self.assertIn("tako fields set story_points", err)
        self.assertNotIn("--config", err)


if __name__ == "__main__":
    unittest.main()
