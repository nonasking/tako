"""adf_to_md — Jira 본문(ADF)을 /tako-read·/tako-check가 읽을 마크다운으로."""
from __future__ import annotations

import unittest

from tako.adf_to_md import adf_to_markdown


def p(*inline):
    return {"type": "paragraph", "content": list(inline)}


def t(text, *marks):
    node = {"type": "text", "text": text}
    if marks:
        node["marks"] = [{"type": m} for m in marks]
    return node


def doc(*blocks):
    return {"type": "doc", "version": 1, "content": list(blocks)}


class BasicNodesTest(unittest.TestCase):
    def test_paragraphs_headings_and_marks(self) -> None:
        md = adf_to_markdown(doc(
            {"type": "heading", "attrs": {"level": 2}, "content": [t("배경")]},
            p(t("굵게", "strong"), t(" 그리고 "), t("코드", "code")),
        ))
        self.assertEqual(md, "## 배경\n\n**굵게** 그리고 `코드`")

    def test_ordered_list_respects_start(self) -> None:
        md = adf_to_markdown({"type": "orderedList", "attrs": {"order": 3}, "content": [
            {"type": "listItem", "content": [p(t("셋"))]},
            {"type": "listItem", "content": [p(t("넷"))]},
        ]})
        self.assertEqual(md, "3. 셋\n4. 넷")


class PanelExpandTest(unittest.TestCase):
    def test_panel_becomes_labeled_quote(self) -> None:
        md = adf_to_markdown({"type": "panel", "attrs": {"panelType": "warning"}, "content": [p(t("운영 배포 전 확인"))]})
        self.assertEqual(md, "> **[WARNING]**\n> 운영 배포 전 확인")

    def test_expand_keeps_title_and_body(self) -> None:
        md = adf_to_markdown({"type": "expand", "attrs": {"title": "상세"}, "content": [p(t("숨겨진 내용"))]})
        self.assertEqual(md, "**▸ 상세**\n\n숨겨진 내용")


class TableTest(unittest.TestCase):
    def _cell(self, kind, *blocks):
        return {"type": kind, "content": list(blocks)}

    def test_table_renders_gfm_with_header_row(self) -> None:
        md = adf_to_markdown({"type": "table", "content": [
            {"type": "tableRow", "content": [self._cell("tableHeader", p(t("항목"))), self._cell("tableHeader", p(t("값")))]},
            {"type": "tableRow", "content": [self._cell("tableCell", p(t("a|b"))), self._cell("tableCell", p(t("1")), p(t("2")))]},
        ]})
        self.assertEqual(md, "| 항목 | 값 |\n|---|---|\n| a\\|b | 1<br>2 |")

    def test_ragged_rows_are_padded(self) -> None:
        md = adf_to_markdown({"type": "table", "content": [
            {"type": "tableRow", "content": [self._cell("tableHeader", p(t("a"))), self._cell("tableHeader", p(t("b")))]},
            {"type": "tableRow", "content": [self._cell("tableCell", p(t("only")))]},
        ]})
        self.assertEqual(md.splitlines()[-1], "| only |  |")


class TaskDecisionTest(unittest.TestCase):
    def test_task_list_with_states_and_nesting(self) -> None:
        md = adf_to_markdown({"type": "taskList", "content": [
            {"type": "taskItem", "attrs": {"state": "DONE"}, "content": [t("설계")]},
            {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [t("구현")]},
            {"type": "taskList", "content": [
                {"type": "taskItem", "attrs": {"state": "TODO"}, "content": [t("테스트")]},
            ]},
        ]})
        self.assertEqual(md, "- [x] 설계\n- [ ] 구현\n  - [ ] 테스트")

    def test_decision_list(self) -> None:
        md = adf_to_markdown({"type": "decisionList", "content": [
            {"type": "decisionItem", "attrs": {"state": "DECIDED"}, "content": [t("A안으로 간다")]},
        ]})
        self.assertEqual(md, "- 결정: A안으로 간다")


class InlineNodesTest(unittest.TestCase):
    def test_emoji_status_date_mention(self) -> None:
        md = adf_to_markdown(p(
            {"type": "emoji", "attrs": {"shortName": ":warning:", "text": "⚠️"}},
            t(" "),
            {"type": "status", "attrs": {"text": "IN PROGRESS", "color": "blue"}},
            t(" 마감 "),
            {"type": "date", "attrs": {"timestamp": "1767225600000"}},  # 2026-01-01T00:00:00Z
            t(" 담당 "),
            {"type": "mention", "attrs": {"id": "abc", "text": "@홍길동"}},
        ))
        self.assertEqual(md, "⚠️ [IN PROGRESS] 마감 2026-01-01 담당 @@홍길동")

    def test_bad_date_is_empty(self) -> None:
        self.assertEqual(adf_to_markdown({"type": "date", "attrs": {"timestamp": "soon"}}), "")

    def test_media_is_named_placeholder(self) -> None:
        md = adf_to_markdown({"type": "mediaSingle", "content": [
            {"type": "media", "attrs": {"type": "file", "id": "x", "alt": "screenshot.png"}},
        ]})
        self.assertEqual(md, "[첨부: screenshot.png]")


class FallbackTest(unittest.TestCase):
    def test_unknown_block_container_keeps_paragraph_breaks(self) -> None:
        # 예전에는 자식 블록을 구분자 없이 이어 붙여 "첫 문단둘째 문단"이 됐다.
        md = adf_to_markdown(doc({"type": "someFutureContainer", "content": [p(t("첫 문단")), p(t("둘째 문단"))]}))
        self.assertEqual(md, "첫 문단\n\n둘째 문단")

    def test_unknown_inline_container_concatenates(self) -> None:
        md = adf_to_markdown(p({"type": "someFutureInline", "content": [t("a"), t("b")]}))
        self.assertEqual(md, "ab")

    def test_layout_columns_become_blocks(self) -> None:
        md = adf_to_markdown(doc({"type": "layoutSection", "content": [
            {"type": "layoutColumn", "attrs": {"width": 50}, "content": [p(t("왼쪽"))]},
            {"type": "layoutColumn", "attrs": {"width": 50}, "content": [p(t("오른쪽"))]},
        ]}))
        self.assertEqual(md, "왼쪽\n\n오른쪽")


if __name__ == "__main__":
    unittest.main()
