"""list_output 의 행 추출·CSV·표 렌더링 단위 테스트 (네트워크 없음).

실행: python -m unittest tests.test_list_output
"""

from __future__ import annotations

import unittest

from tako.list_output import (
    _fit,
    display_width,
    issue_cells,
    issues_to_csv,
    render_list_table,
)


SP_FIELD = "customfield_10016"


def _issue(**over) -> dict:
    base = {
        "key": "WL-1",
        "fields": {
            "status": {"name": "진행중"},
            "issuetype": {"name": "기능변경"},
            "assignee": {"displayName": "강민성"},
            "created": "2026-08-01T09:00:00.000+0900",
            "updated": "2026-08-02T10:00:00.000+0900",
            "duedate": "2026-08-10",
            "summary": "정렬 로직 개선",
            "parent": {"key": "WL-9200"},
        },
    }
    base["fields"].update(over)
    return base


class IssueCellsTest(unittest.TestCase):
    def test_extracts_all_columns(self) -> None:
        c = issue_cells(_issue())
        self.assertEqual(c["key"], "WL-1")
        self.assertEqual(c["status"], "진행중")
        self.assertEqual(c["created"], "2026-08-01")  # 날짜만
        self.assertEqual(c["parent"], "WL-9200")

    def test_missing_values_are_empty_strings(self) -> None:
        c = issue_cells({"key": "WL-2", "fields": {}})
        for name in ("status", "type", "assignee", "created", "duedate", "summary", "parent"):
            self.assertEqual(c[name], "", name)

    def test_sp_int_formatting(self) -> None:
        c = issue_cells(_issue(**{SP_FIELD: 3.0}), sp_field_id=SP_FIELD)
        self.assertEqual(c["story_points"], "3")  # 3.0 → "3"
        c = issue_cells(_issue(), sp_field_id=SP_FIELD)
        self.assertEqual(c["story_points"], "")


class CsvTest(unittest.TestCase):
    def test_bom_and_columns(self) -> None:
        text = issues_to_csv([_issue()], site="x.atlassian.net")
        self.assertTrue(text.startswith("﻿"))
        header, row = text.lstrip("﻿").splitlines()[:2]
        self.assertEqual(header, "key,status,type,assignee,created,updated,duedate,summary,parent,url")
        self.assertIn("https://x.atlassian.net/browse/WL-1", row)

    def test_sp_column_inserted_after_type(self) -> None:
        text = issues_to_csv([_issue(**{SP_FIELD: 5})], site="x.a.net", sp_field_id=SP_FIELD)
        header = text.lstrip("﻿").splitlines()[0].split(",")
        self.assertEqual(header.index("story_points"), header.index("type") + 1)


class WidthTest(unittest.TestCase):
    def test_display_width_counts_hangul_as_two(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width("한글"), 4)
        self.assertEqual(display_width("a한b"), 4)

    def test_fit_pads_to_display_width(self) -> None:
        for cell in ("ascii", "진행중", "혼합mix"):
            self.assertEqual(display_width(_fit(cell, 10)), 10, cell)

    def test_fit_truncates_by_display_width(self) -> None:
        # 폭 5 에 전각 3자는 2자(4칸)+공백 1칸 — 절대 5칸을 넘지 않는다
        self.assertEqual(_fit("가나다", 5), "가나 ")


class RenderTableTest(unittest.TestCase):
    def test_columns_align_across_hangul_and_ascii_rows(self) -> None:
        issues = [
            _issue(),
            {"key": "WL-22", "fields": {"status": {"name": "Done"}, "issuetype": {"name": "Task"},
                                        "assignee": None, "created": "2026-07-01T00:00:00",
                                        "updated": "2026-07-02T00:00:00", "summary": "ascii row"}},
        ]
        lines = render_list_table(issues).splitlines()
        # 한글 행과 ASCII 행에서 '생성' 날짜 컬럼의 *표시 폭* 위치가 같아야 한다
        starts = [display_width(l[: l.index("2026-0")]) for l in lines[2:]]
        self.assertEqual(starts[0], starts[1])

    def test_placeholders_for_missing(self) -> None:
        table = render_list_table([{"fields": {}}])
        self.assertIn("(미할당)", table)
        self.assertIn("?", table)


if __name__ == "__main__":
    unittest.main()
