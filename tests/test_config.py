"""tako.config 검증 단위 테스트 (stdlib unittest — 파일은 임시 디렉터리에만 씀).

실행: python -m unittest tests.test_config
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tako.config import ConfigError, load_config


VALID = """
jira:
  site: mycompany.atlassian.net
  default_project: WL
  default_issue_type: 기능변경
  default_assignee: me
  fields:
    story_points: customfield_10016
issue_types:
  기능변경: {}
  버그수정: {}
epic_aliases:
  infra: WL-9200
"""


class ConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "config.yaml"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _load(self, text: str):
        self.path.write_text(text, encoding="utf-8")
        return load_config(self.path)

    def _err(self, text: str) -> str:
        self.path.write_text(text, encoding="utf-8")
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.path)
        return str(ctx.exception)

    def test_valid_config(self) -> None:
        cfg = self._load(VALID)
        self.assertEqual(cfg.jira.site, "mycompany.atlassian.net")
        self.assertEqual(cfg.jira.default_project, "WL")
        self.assertEqual(cfg.jira.custom_fields, {"story_points": "customfield_10016"})
        self.assertEqual(cfg.jira.default_assignee, "me")
        self.assertTrue(cfg.jira.auto_copy_url)  # 기본값 True
        self.assertEqual(cfg.allowed_issue_types, ("기능변경", "버그수정"))
        self.assertEqual(cfg.resolve_epic("infra"), "WL-9200")
        self.assertEqual(cfg.resolve_epic("WL-1"), "WL-1")  # 별칭 아니면 그대로
        self.assertIsNone(cfg.resolve_epic(None))

    def test_missing_file(self) -> None:
        missing = Path(self._tmp.name) / "nope.yaml"
        with self.assertRaises(ConfigError) as ctx:
            load_config(missing)
        self.assertTrue(str(ctx.exception).startswith("설정 파일이 없습니다"))

    def test_broken_yaml_and_non_mapping_root(self) -> None:
        self.assertIn("YAML 파싱 실패", self._err("jira: [unclosed\n"))
        self.assertIn("최상위가 매핑이 아님", self._err("- just\n- a list\n"))

    def test_missing_jira_block(self) -> None:
        self.assertIn("jira 블록 누락", self._err("issue_types:\n  Task: {}\n"))

    def test_required_strings_blank(self) -> None:
        self.assertIn(
            "jira.site 비었음.",
            self._err("jira:\n  site: '  '\n  default_project: WL\n  default_issue_type: Task\n"),
        )
        self.assertIn(
            "jira.default_project 비었음.",
            self._err("jira:\n  site: x.atlassian.net\n  default_issue_type: Task\n"),
        )
        self.assertIn(
            "jira.default_issue_type 비었음.",
            self._err("jira:\n  site: x.atlassian.net\n  default_project: WL\n"),
        )

    def test_default_issue_type_must_be_declared(self) -> None:
        msg = self._err(
            "jira:\n  site: x.atlassian.net\n  default_project: WL\n  default_issue_type: 기능변경\n"
            "issue_types:\n  Task: {}\n"
        )
        self.assertIn("default_issue_type", msg)
        self.assertIn("Task", msg)  # 정의된 타입을 안내

    def test_fields_validation(self) -> None:
        base = "jira:\n  site: x.atlassian.net\n  default_project: WL\n  default_issue_type: Task\n"
        self.assertIn("jira.fields 가 매핑이 아님", self._err(base + "  fields: [a, b]\n"))
        self.assertIn(
            "jira.fields.story_points 값이 비었거나 문자열이 아님",
            self._err(base + "  fields:\n    story_points: 10016\n"),
        )
        self.assertIn(
            "jira.fields.story_points 값이 비었거나 문자열이 아님",
            self._err(base + "  fields:\n    story_points: ''\n"),
        )

    def test_scalar_type_validation(self) -> None:
        base = "jira:\n  site: x.atlassian.net\n  default_project: WL\n  default_issue_type: Task\n"
        self.assertIn(
            "jira.default_assignee 가 문자열이 아니거나 비었음",
            self._err(base + "  default_assignee: 12345\n"),
        )
        self.assertIn(
            "jira.auto_copy_url 은 true / false 여야 함",
            self._err(base + "  auto_copy_url: 'yes please'\n"),
        )
        self.assertIn("issue_types 가 매핑이 아님", self._err(base + "issue_types:\n  - Task\n"))

    def test_epic_aliases_validation(self) -> None:
        base = "jira:\n  site: x.atlassian.net\n  default_project: WL\n  default_issue_type: Task\n"
        self.assertIn("epic_aliases 가 매핑이 아님", self._err(base + "epic_aliases:\n  - infra\n"))
        self.assertIn(
            "epic_aliases.infra 값이 문자열이 아님",
            self._err(base + "epic_aliases:\n  infra: 9200\n"),
        )


if __name__ == "__main__":
    unittest.main()
