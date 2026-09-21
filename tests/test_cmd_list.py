"""cmd_list 의 페이지네이션·필터 조립·셸 힌트·브라우저 열기·위저드 열기 프롬프트/선택 파서 단위 테스트 (네트워크 없음).

실행: python -m unittest tests.test_cmd_list
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from tako.cmd_list import (
    _build_filters,
    _fetch_issues,
    _filters_to_shell_hint,
    _open_in_browser,
    _pick_issues,
    _prompt_open_targets,
)
from tako.list_output import search_page_url
from tako.list_query import DEFAULT_LIST_LIMIT, OPEN_EACH_CAP, ListOutputOpts


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


    def test_shell_hint_includes_open_flags(self) -> None:
        hint = _filters_to_shell_hint(
            _build_filters(assignee="me", projects=(), statuses=(), types=(), parent=None, labels=(),
                           updated=None, created=None, due=None, sp=None, query=None, raw_jql=None),
            ListOutputOpts(open_search=True, open_each=True),
        )
        self.assertTrue(hint.endswith("--open --open-each"))


class BrowserOpenTest(unittest.TestCase):
    """--open / --open-each — URL 조립과 탭 상한. 실제 브라우저는 열지 않는다."""

    def _open(self, issues, **kw):
        opts = ListOutputOpts(**kw)
        self.opened: list[list[str]] = []
        buf = io.StringIO()
        with (
            patch("tako.cmd_list.open_urls", side_effect=lambda urls: (self.opened.append(list(urls)), len(urls))[1]),
            patch("tako.cmd_list.stdin_is_tty", return_value=False),
            redirect_stderr(buf),
        ):
            _open_in_browser(issues, jql="project = WL AND a = b", site="x.atlassian.net", opts=opts)
        return buf.getvalue()

    def test_search_page_url_encodes_jql(self) -> None:
        url = search_page_url("x.atlassian.net", 'project = WL AND text ~ "a&b"')
        self.assertTrue(url.startswith("https://x.atlassian.net/issues/?jql="))
        self.assertNotIn("&b", url.split("jql=")[1])
        self.assertNotIn(" ", url)

    def test_open_search_is_one_tab(self) -> None:
        self._open([_issue(i) for i in range(1, 6)], open_search=True)
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(len(self.opened[0]), 1)
        self.assertIn("/issues/?jql=", self.opened[0][0])

    def test_open_search_alone_ignores_empty_result(self) -> None:
        # 결과 0 건이어도 검색 페이지는 열 수 있다 (조건을 Jira 에서 다시 다듬으라고)
        self._open([], open_search=True)
        self.assertEqual(len(self.opened), 1)

    def test_open_each_within_cap(self) -> None:
        self._open([_issue(i) for i in range(1, 4)], open_each=True)
        self.assertEqual(self.opened, [[f"https://x.atlassian.net/browse/WL-{i}" for i in (1, 2, 3)]])

    def test_open_each_over_cap_refused_without_tty(self) -> None:
        err = self._open([_issue(i) for i in range(1, OPEN_EACH_CAP + 2)], open_each=True)
        self.assertEqual(self.opened, [])
        self.assertIn("상한", err)

    def test_open_each_empty_result_skips(self) -> None:
        err = self._open([], open_each=True)
        self.assertEqual(self.opened, [])
        self.assertIn("건너뜀", err)

    def test_both_flags_search_tab_first(self) -> None:
        self._open([_issue(1)], open_search=True, open_each=True)
        self.assertEqual(len(self.opened[0]), 2)
        self.assertIn("/issues/?jql=", self.opened[0][0])
        self.assertTrue(self.opened[0][1].endswith("/browse/WL-1"))


class PickIssuesTest(unittest.TestCase):
    """위저드 '브라우저로 열기' 입력 → 결과 이슈 선택. 번호·범위·키 혼용."""

    def setUp(self) -> None:
        self.issues = [_issue(i) for i in range(1, 8)]

    def _keys(self, raw: str) -> list[str]:
        return [it["key"] for it in _pick_issues(raw, self.issues)]

    def test_numbers_ranges_and_keys_mix(self) -> None:
        self.assertEqual(self._keys("1,3 5-6 WL-7"), ["WL-1", "WL-3", "WL-5", "WL-6", "WL-7"])

    def test_duplicates_collapse_and_order_is_input_order(self) -> None:
        self.assertEqual(self._keys("3 1 3 wl-1"), ["WL-3", "WL-1"])

    def test_out_of_range_number_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _pick_issues("8", self.issues)
        with self.assertRaises(ValueError):
            _pick_issues("0", self.issues)

    def test_bad_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _pick_issues("5-3", self.issues)
        with self.assertRaises(ValueError):
            _pick_issues("6-9", self.issues)

    def test_unknown_key_rejects_whole_input(self) -> None:
        # 일부만 열고 나머지를 조용히 버리지 않는다
        with self.assertRaises(ValueError):
            _pick_issues("1 WL-99", self.issues)


class WizardOpenPromptTest(unittest.TestCase):
    """표 출력 뒤 반복 프롬프트. Enter 로 끝, s/a 는 힌트용 opts 에 반영."""

    def _run(self, answers: list[str], issues=None):
        issues = issues if issues is not None else [_issue(i) for i in range(1, 4)]
        self.opened: list[list[str]] = []
        buf = io.StringIO()
        with (
            patch("tako.cmd_list.ask_text", side_effect=answers),
            patch("tako.cmd_list.open_urls", side_effect=lambda urls: (self.opened.append(list(urls)), len(urls))[1]),
            patch("tako.cmd_list.stdin_is_tty", return_value=True),
            redirect_stderr(buf),
        ):
            opts = _prompt_open_targets(issues, jql="project = WL", site="x.atlassian.net", opts=ListOutputOpts())
        return opts, buf.getvalue()

    def test_enter_ends_without_opening(self) -> None:
        opts, _ = self._run([""])
        self.assertEqual(self.opened, [])
        self.assertFalse(opts.open_each or opts.open_search)

    def test_partial_pick_opens_only_those_and_loops(self) -> None:
        opts, _ = self._run(["1 3", "2", ""])
        self.assertEqual(self.opened, [
            ["https://x.atlassian.net/browse/WL-1", "https://x.atlassian.net/browse/WL-3"],
            ["https://x.atlassian.net/browse/WL-2"],
        ])
        # 부분 선택은 셸 인자로 재현 불가 — 힌트에 반영하지 않는다
        self.assertFalse(opts.open_each or opts.open_search)

    def test_all_opens_every_result_and_marks_hint(self) -> None:
        opts, _ = self._run(["a", ""])
        self.assertEqual(len(self.opened[0]), 3)
        self.assertTrue(opts.open_each)

    def test_search_opens_one_tab_and_marks_hint(self) -> None:
        opts, _ = self._run(["S", ""])
        self.assertEqual(len(self.opened), 1)
        self.assertIn("/issues/?jql=", self.opened[0][0])
        self.assertTrue(opts.open_search)

    def test_bad_input_reprompts(self) -> None:
        _, err = self._run(["9", "WL-77", "1", ""])
        self.assertIn("벗어남", err)
        self.assertIn("결과에 없음", err)
        self.assertEqual(len(self.opened), 1)

    def test_all_over_cap_asks_first(self) -> None:
        issues = [_issue(i) for i in range(1, OPEN_EACH_CAP + 2)]
        with patch("tako.cmd_list.confirm", return_value=False):
            opts, err = self._run(["a", ""], issues=issues)
        self.assertEqual(self.opened, [])
        self.assertIn("취소", err)
        # 취소했으면 힌트에도 --open-each 를 권하지 않는다
        self.assertFalse(opts.open_each)


if __name__ == "__main__":
    unittest.main()
