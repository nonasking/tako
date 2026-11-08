"""draft_file 왕복(render → parse) 단위 테스트.

에디터 왕복의 핵심 계약: 사용자가 손대지 않고 저장만 해도 내용이 보존되고,
잘못 고친 부분은 조용히 사라지는 게 아니라 한국어 에러로 걸린다.

실행: python -m unittest tests.test_draft_file
"""

from __future__ import annotations

import unittest
from unittest import mock

from tako.config import AutoFillRules, JiraSite, TakoConfig
from tako.draft_file import DraftFileError, parse_draft, render_draft, resolve_editor


def _cfg(**overrides) -> TakoConfig:
    return TakoConfig(
        jira=JiraSite(site="x.atlassian.net", default_project="WL", default_issue_type="기능변경"),
        allowed_issue_types=("기능변경", "버그수정"),
        auto_fill=AutoFillRules(),
        **overrides,
    )


class RenderParseRoundTripTest(unittest.TestCase):
    def test_round_trip_preserves_fields_and_body(self) -> None:
        payload = {
            "project": "WL",
            "issue_type": "기능변경",
            "summary": "제목: 콜론 포함",
            "description": "## 내가 한 일\n- 첫 줄\n\n---\n\n구분선 아래",
            "parent_epic": "WL-9200",
            "labels": ["backend", "긴급"],
            "duedate": "2026-08-25",
            "story_points": 3,
            "links": [("WL-100", "Relates"), ("WL-200", "Blocks")],
        }
        fields, body = parse_draft(render_draft(payload))
        self.assertEqual(fields["project"], "WL")
        self.assertEqual(fields["summary"], "제목: 콜론 포함")
        self.assertEqual(fields["parent"], "WL-9200")
        self.assertEqual(fields["labels"], ["backend", "긴급"])
        # 따옴표 없는 YAML 날짜도 문자열로 돌아와야 한다
        self.assertEqual(fields["duedate"], "2026-08-25")
        self.assertEqual(fields["story_points"], 3)
        # Relates 는 축약, 그 외 타입은 KEY:TYPE 유지
        self.assertEqual(fields["links"], ["WL-100", "WL-200:Blocks"])
        self.assertEqual(body, payload["description"])

    def test_render_shows_pending_assignee(self) -> None:
        text = render_draft({"issue_type": "기능변경", "summary": "s", "assignee_pending": "me"})
        fields, _ = parse_draft(text)
        self.assertEqual(fields["assignee"], "me")

    def test_render_appends_assignee_label_comment(self) -> None:
        # accountId 만으로는 누군지 못 알아본다 — 라벨을 주석으로 병기, 값은 그대로
        text = render_draft(
            {"issue_type": "기능변경", "summary": "s", "assignee": "ACC-1", "assignee_label": "강민성 (a@b.c)"}
        )
        self.assertIn("# 강민성 (a@b.c)", text)
        fields, _ = parse_draft(text)
        self.assertEqual(fields["assignee"], "ACC-1")

    def test_long_summary_stays_on_one_line(self) -> None:
        long = "fix the refund flow for virtual account so that the balance does not go negative " * 2
        fields, _ = parse_draft(render_draft({"issue_type": "기능변경", "summary": long, "description": "b"}))
        self.assertEqual(fields["summary"], long)

    def test_issue_type_line_is_comment_only(self) -> None:
        text = render_draft({"issue_type": "기능변경", "summary": "s", "description": "b"})
        fields, _ = parse_draft(text)
        self.assertNotIn("issue_type", fields)
        self.assertIn("기능변경", text)  # 표시는 되지만 (주석)


class ParseRejectionTest(unittest.TestCase):
    def test_rejects_issue_type_key(self) -> None:
        with self.assertRaises(DraftFileError) as ctx:
            parse_draft("---\nissue_type: Bug\nsummary: s\n---\nb")
        self.assertIn("이슈 유형", str(ctx.exception))

    def test_rejects_unknown_key(self) -> None:
        with self.assertRaises(DraftFileError) as ctx:
            parse_draft("---\nsummry: 오타\n---\nb")
        self.assertIn("summry", str(ctx.exception))

    def test_rejects_missing_frontmatter(self) -> None:
        with self.assertRaises(DraftFileError):
            parse_draft("summary: s\n본문")

    def test_rejects_unclosed_frontmatter(self) -> None:
        with self.assertRaises(DraftFileError):
            parse_draft("---\nsummary: s\n본문")

    def test_rejects_scalar_labels(self) -> None:
        with self.assertRaises(DraftFileError) as ctx:
            parse_draft("---\nlabels: backend\n---\nb")
        self.assertIn("labels", str(ctx.exception))

    def test_scalar_link_becomes_list(self) -> None:
        fields, _ = parse_draft("---\nlinks: WL-100\n---\nb")
        self.assertEqual(fields["links"], ["WL-100"])


class ResolveEditorTest(unittest.TestCase):
    def test_config_wins_and_splits_args(self) -> None:
        with mock.patch.dict("os.environ", {"EDITOR": "nano", "VISUAL": "emacs"}):
            self.assertEqual(resolve_editor("code --wait"), ["code", "--wait"])

    def test_editor_env_before_visual(self) -> None:
        with mock.patch.dict("os.environ", {"EDITOR": "nano", "VISUAL": "emacs"}):
            self.assertEqual(resolve_editor(None), ["nano"])

    def test_visual_fallback_then_vi(self) -> None:
        with mock.patch.dict("os.environ", {"VISUAL": "emacs"}, clear=True):
            self.assertEqual(resolve_editor(None), ["emacs"])
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(resolve_editor(None), ["vi"])


class PayloadFromDraftFieldsTest(unittest.TestCase):
    def test_same_assignee_keeps_resolution(self) -> None:
        from tako.cmd_new import _payload_from_draft_fields

        prev = {"assignee": "5b1:acc", "assignee_label": "강민성 (a@b.c)", "issue_type": "기능변경"}
        payload = _payload_from_draft_fields(
            {"summary": "s", "assignee": "5b1:acc"}, "본문", _cfg(), issue_type="기능변경", prev=prev
        )
        self.assertEqual(payload["assignee"], "5b1:acc")
        self.assertEqual(payload["assignee_label"], "강민성 (a@b.c)")
        self.assertNotIn("assignee_pending", payload)

    def test_changed_assignee_goes_back_to_pending(self) -> None:
        from tako.cmd_new import _payload_from_draft_fields

        prev = {"assignee": "5b1:acc", "assignee_label": "강민성", "issue_type": "기능변경"}
        payload = _payload_from_draft_fields(
            {"summary": "s", "assignee": "me"}, "본문", _cfg(), issue_type="기능변경", prev=prev
        )
        self.assertEqual(payload["assignee_pending"], "me")
        self.assertNotIn("assignee", payload)

    def test_parent_alias_resolved(self) -> None:
        from tako.cmd_new import _payload_from_draft_fields

        cfg = _cfg(epic_aliases={"infra": "WL-9200"})
        payload = _payload_from_draft_fields(
            {"summary": "s", "parent": "infra"}, "본문", cfg, issue_type="기능변경"
        )
        self.assertEqual(payload["parent_epic"], "WL-9200")


if __name__ == "__main__":
    unittest.main()
