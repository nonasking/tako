"""tako list 의 JQL 빌더 단위 테스트 (stdlib unittest — 네트워크 없음).

실행: python -m unittest tests.test_list_query
"""

from __future__ import annotations

import unittest

from tako.list_query import (
    ListFilters,
    QueryError,
    _date_clause,
    _due_clause,
    _sp_clause,
    build_jql,
)


SP_FIELD = "customfield_10016"


class DateClauseTest(unittest.TestCase):
    def test_shorthand(self) -> None:
        # 7d / 24h / 2w / 1m → 상대 표현 그대로 JQL 로 (따옴표 없음)
        self.assertEqual(_date_clause("updated", "7d"), "updated >= -7d")
        self.assertEqual(_date_clause("updated", "24h"), "updated >= -24h")
        self.assertEqual(_date_clause("created", "2w"), "created >= -2w")
        self.assertEqual(_date_clause("created", "1m"), "created >= -1m")

    def test_plain_date_means_since(self) -> None:
        self.assertEqual(_date_clause("updated", "2026-05-15"), 'updated >= "2026-05-15"')

    def test_comparison_operators(self) -> None:
        self.assertEqual(_date_clause("updated", "<=2026-04-01"), 'updated <= "2026-04-01"')
        self.assertEqual(_date_clause("updated", "<2026-04-01"), 'updated < "2026-04-01"')
        self.assertEqual(_date_clause("created", ">=2026-04-01"), 'created >= "2026-04-01"')
        self.assertEqual(_date_clause("created", ">2026-04-01"), 'created > "2026-04-01"')
        # 연산자와 날짜 사이 공백 허용
        self.assertEqual(_date_clause("updated", "<= 2026-04-01"), 'updated <= "2026-04-01"')

    def test_range_both_separators(self) -> None:
        expected = 'updated >= "2026-05-01" AND updated <= "2026-05-15"'
        self.assertEqual(_date_clause("updated", "2026-05-01..2026-05-15"), expected)
        self.assertEqual(_date_clause("updated", "2026-05-01~2026-05-15"), expected)

    def test_range_start_after_end_rejected(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            _date_clause("updated", "2026-05-15..2026-05-01")
        self.assertIn("시작이 끝보다 늦음", str(ctx.exception))

    def test_unsupported_format(self) -> None:
        for bad in ("yesterday", "2026/05/15", "7일", "7d..2026-05-01"):
            with self.assertRaises(QueryError):
                _date_clause("updated", bad)


class DueClauseTest(unittest.TestCase):
    def test_keywords(self) -> None:
        self.assertEqual(_due_clause("overdue"), "duedate < now()")
        self.assertEqual(_due_clause("none"), "duedate is EMPTY")
        self.assertEqual(_due_clause("empty"), "duedate is EMPTY")
        self.assertEqual(_due_clause("set"), "duedate is not EMPTY")
        self.assertEqual(_due_clause("OVERDUE"), "duedate < now()")  # 대소문자 무시

    def test_exact_date_is_equality(self) -> None:
        # updated/created 와 의도적으로 다름 — 기한은 '그 날짜' 정확 일치.
        self.assertEqual(_due_clause("2026-06-15"), 'duedate = "2026-06-15"')

    def test_comparison_and_range(self) -> None:
        self.assertEqual(_due_clause("<=2026-06-15"), 'duedate <= "2026-06-15"')
        self.assertEqual(_due_clause(">2026-06-15"), 'duedate > "2026-06-15"')
        self.assertEqual(
            _due_clause("2026-06-01..2026-06-30"),
            'duedate >= "2026-06-01" AND duedate <= "2026-06-30"',
        )

    def test_unsupported_format(self) -> None:
        with self.assertRaises(QueryError):
            _due_clause("내일")


class SpClauseTest(unittest.TestCase):
    def test_int_and_comparisons(self) -> None:
        # customfield_10016 → cf[10016]
        self.assertEqual(_sp_clause("3", SP_FIELD), "cf[10016] = 3")
        self.assertEqual(_sp_clause(">=3", SP_FIELD), "cf[10016] >= 3")
        self.assertEqual(_sp_clause("<=8", SP_FIELD), "cf[10016] <= 8")
        self.assertEqual(_sp_clause(">0", SP_FIELD), "cf[10016] > 0")
        self.assertEqual(_sp_clause("<13", SP_FIELD), "cf[10016] < 13")

    def test_none_and_set(self) -> None:
        self.assertEqual(_sp_clause("none", SP_FIELD), "cf[10016] is EMPTY")
        self.assertEqual(_sp_clause("set", SP_FIELD), "cf[10016] is not EMPTY")

    def test_missing_field_mapping_guides_user(self) -> None:
        with self.assertRaises(QueryError) as ctx:
            _sp_clause("3", None)
        self.assertIn("tako fields detect story_points", str(ctx.exception))

    def test_unsupported_format(self) -> None:
        with self.assertRaises(QueryError):
            _sp_clause("높음", SP_FIELD)


class BuildJqlTest(unittest.TestCase):
    def test_default_project_and_order_by(self) -> None:
        jql = build_jql(ListFilters(assignee="me"), default_project="WL")
        self.assertEqual(
            jql, "project = WL AND assignee = currentUser() ORDER BY updated DESC"
        )

    def test_multiple_projects_use_in(self) -> None:
        jql = build_jql(ListFilters(projects=("wl", "ABC")), default_project="ZZ")
        self.assertIn("project in (WL, ABC)", jql)  # 대문자 정규화

    def test_all_projects_skips_default(self) -> None:
        jql = build_jql(
            ListFilters(all_projects=True, assignee="me"), default_project="WL"
        )
        self.assertNotIn("project", jql)
        self.assertTrue(jql.startswith("assignee = currentUser()"))

    def test_invalid_project_and_parent_keys(self) -> None:
        with self.assertRaises(QueryError):
            build_jql(ListFilters(projects=("wl-1",)))
        with self.assertRaises(QueryError):
            build_jql(ListFilters(parent="WL"), default_project="WL")

    def test_quotes_escaped_in_text_filters(self) -> None:
        jql = build_jql(ListFilters(query='say "hi"'), default_project="WL")
        self.assertIn(r'text ~ "say \"hi\""', jql)

    def test_raw_jql_bypasses_everything(self) -> None:
        raw = "project = WL AND duedate < now()"
        jql = build_jql(
            ListFilters(raw_jql=f"  {raw}  ", assignee="nonsense"), default_project="ZZ"
        )
        self.assertEqual(jql, raw)  # ORDER BY 도 안 붙음

    def test_empty_filters_rejected(self) -> None:
        with self.assertRaises(QueryError):
            build_jql(ListFilters())  # default_project 도 없음

    def test_full_combination(self) -> None:
        jql = build_jql(
            ListFilters(
                assignee="me",
                statuses=("진행중",),
                types=("에픽",),
                labels=("backend",),
                updated="7d",
                due="overdue",
                sp=">=3",
            ),
            default_project="WL",
            sp_field_id=SP_FIELD,
        )
        self.assertEqual(
            jql,
            'project = WL AND assignee = currentUser() AND status in ("진행중") '
            'AND issuetype in ("에픽") AND labels in ("backend") AND updated >= -7d '
            "AND duedate < now() AND cf[10016] >= 3 ORDER BY updated DESC",
        )


if __name__ == "__main__":
    unittest.main()
