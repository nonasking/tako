"""cmd_list 의 페이지네이션·필터 조립·셸 힌트 단위 테스트 (네트워크 없음).

실행: python -m unittest tests.test_cmd_list
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from tako.cmd_list import _build_filters, _fetch_issues, _filters_to_shell_hint
from tako.list_query import DEFAULT_LIST_LIMIT, ListOutputOpts


def _issue(n: int) -> dict:
    return {"key": f"WL-{n}", "fields": {"summary": f"이슈 {n}"}}


class FakeSearchClient:
    """search_issues 만 흉내 — 준비된 전체 목록을 max_results 단위로 잘라 내준다."""

    def __init__(self, total: int):
        self._all = [_issue(i) for i in range(1, total + 1)]
        self.page_sizes: list[int] = []

    def search_issues(self, jql, *, fields=None, max_results=20, next_page_token=None):
        self.page_sizes.append(max_results)
        start = int(next_page_token) if next_page_token else 0
        end = start + max_results
        page = self._all[start:end]
        result = {"issues": page}
        if end < len(self._all):
            result["nextPageToken"] = str(end)
        return result


class FetchIssuesTest(unittest.TestCase):
    def _fetch(self, client, **kw):
        with redirect_stderr(io.StringIO()):
            return _fetch_issues(client, "project = WL", fields=["summary"], **kw)

    def test_limit_within_one_page(self) -> None:
        client = FakeSearchClient(total=50)
        issues, has_more = self._fetch(client, limit=20, fetch_all=False)
        self.assertEqual(len(issues), 20)
        self.assertTrue(has_more)
        self.assertEqual(client.page_sizes, [20])

    def test_limit_over_100_spans_pages(self) -> None:
        # 예전엔 100 에서 조용히 끊겼다 — 이제 상한을 채울 때까지 페이지를 넘긴다.
        client = FakeSearchClient(total=500)
        issues, has_more = self._fetch(client, limit=250, fetch_all=False)
        self.assertEqual(len(issues), 250)
        self.assertTrue(has_more)
        self.assertEqual(client.page_sizes, [100, 100, 50])

    def test_limit_exact_boundary_no_more(self) -> None:
        client = FakeSearchClient(total=40)
        issues, has_more = self._fetch(client, limit=40, fetch_all=False)
        self.assertEqual(len(issues), 40)
        self.assertFalse(has_more)  # 마지막 페이지에서 토큰이 없으면 더 없음

    def test_fetch_all_ignores_limit(self) -> None:
        client = FakeSearchClient(total=230)
        issues, has_more = self._fetch(client, limit=20, fetch_all=True)
        self.assertEqual(len(issues), 230)
        self.assertFalse(has_more)
        self.assertEqual(client.page_sizes, [100, 100, 100])

    def test_empty_result(self) -> None:
        client = FakeSearchClient(total=0)
        issues, has_more = self._fetch(client, limit=20, fetch_all=False)
        self.assertEqual(issues, [])
        self.assertFalse(has_more)


class BuildFiltersTest(unittest.TestCase):
    def _base(self, **kw):
        defaults = dict(
            assignee=None, projects=(), statuses=(), types=(), parent=None,
            labels=(), updated=None, created=None, due=None, sp=None,
            query=None, raw_jql=None,
        )
        defaults.update(kw)
        return _build_filters(**defaults)

    def test_all_keyword_on_projects_sets_all_projects(self) -> None:
        f = self._base(projects=("전체",))
        self.assertTrue(f.all_projects)
        self.assertEqual(f.projects, ())

    def test_all_keyword_on_assignee_clears_it(self) -> None:
        f = self._base(assignee="all")
        self.assertIsNone(f.assignee)

    def test_all_keyword_on_multi_filter_clears_it(self) -> None:
        f = self._base(statuses=("진행중", "전체"))
        self.assertEqual(f.statuses, ())

    def test_blank_values_dropped(self) -> None:
        f = self._base(projects=("WL", " "), labels=("", "backend"))
        self.assertEqual(f.projects, ("WL",))
        self.assertEqual(f.labels, ("backend",))

    def test_empty_strings_become_none(self) -> None:
        # wizard 의 빈 입력("") 이 그대로 실려 와도 None 으로 정규화
        f = self._base(assignee="", updated="", query="")
        self.assertIsNone(f.assignee)
        self.assertIsNone(f.updated)
        self.assertIsNone(f.query)


class ShellHintTest(unittest.TestCase):
    def test_fetch_all_hides_limit(self) -> None:
        f = _build_filters(
            assignee="me", projects=(), statuses=(), types=(), parent=None,
            labels=(), updated=None, created=None, due=None, sp=None,
            query=None, raw_jql=None,
        )
        opts = ListOutputOpts(limit=DEFAULT_LIST_LIMIT, fetch_all=True)
        hint = _filters_to_shell_hint(f, opts)
        self.assertIn("--all", hint)
        self.assertNotIn("--limit", hint)

    def test_custom_limit_shown_without_all(self) -> None:
        f = _build_filters(
            assignee="me", projects=(), statuses=(), types=(), parent=None,
            labels=(), updated=None, created=None, due=None, sp=None,
            query=None, raw_jql=None,
        )
        opts = ListOutputOpts(limit=50, fetch_all=False)
        hint = _filters_to_shell_hint(f, opts)
        self.assertIn("--limit 50", hint)


if __name__ == "__main__":
    unittest.main()
