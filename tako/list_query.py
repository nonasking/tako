"""tako list 의 *명시 인자 → JQL* 변환.

각 필터를 안전하게 JQL 절로 만들어 AND 로 합친다.
사용자가 `--jql` 로 직접 줄 때는 변환 안 하고 그대로 사용 (덮어쓰기).
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass

from .patterns import ISSUE_KEY_RE, is_self_alias, looks_like_account_id


_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_UPDATED_SHORTHAND = re.compile(r"^(\d+)([dhwm])$")  # 7d, 24h, 2w, 1m
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_WITH_OP = re.compile(r"^(<=|>=|<|>)\s*(\d{4}-\d{2}-\d{2})$")


DEFAULT_LIST_LIMIT = 20
OPEN_EACH_CAP = 20  # --open-each 가 확인 없이 여는 탭 수 상한. DEFAULT_LIST_LIMIT 과 값만 같을 뿐 무관


@dataclass
class ListFilters:
    """사용자 인자를 그대로 보존한 필터 묶음."""
    assignee: str | None = None
    reporter: str | None = None
    projects: tuple[str, ...] = ()
    statuses: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    parent: str | None = None
    labels: tuple[str, ...] = ()
    updated: str | None = None
    created: str | None = None
    due: str | None = None        # overdue / none / <=YYYY-MM-DD / >=YYYY-MM-DD / YYYY-MM-DD
    sp: str | None = None         # N / >=N / <=N / >N / <N / none
    query: str | None = None
    raw_jql: str | None = None
    # 사용자가 '전체'/'all' 명시 시 True — build_jql 이 default_project fallback 도 건너뜀.
    # 결과: JQL 에 project 절 자체가 빠져 사이트의 모든 프로젝트가 매칭됨.
    all_projects: bool = False


@dataclass(frozen=True)
class ListOutputOpts:
    """tako list 의 결과 받는 방식 (필터와 별도). limit/페이지/형식/저장경로."""
    limit: int = DEFAULT_LIST_LIMIT
    fetch_all: bool = False
    as_csv: bool = False
    as_json: bool = False
    output: str | None = None
    open_search: bool = False  # --open: JQL 검색 페이지 한 탭
    open_each: bool = False  # --open-each: 티켓마다 탭 하나


class QueryError(Exception):
    pass


def _esc(value: str) -> str:
    """JQL 문자열 리터럴 escape — backslash → \\, double-quote → \\\""""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _project_clause(values: tuple[str, ...]) -> str:
    """프로젝트 키 검증 후 단일이면 `=`, 여러 개면 `in (...)`."""
    keys: list[str] = []
    for v in values:
        key = v.strip().upper()
        if not _PROJECT_KEY.match(key):
            raise QueryError(
                f"project 키 형식 아님: {v!r} (예: WL, ABC, PROJ_X)"
            )
        if key not in keys:
            keys.append(key)
    if len(keys) == 1:
        return f"project = {keys[0]}"
    items = ", ".join(keys)
    return f"project in ({items})"


def _user_clause(field: str, value: str) -> str:
    """담당자/보고자 공용 — 'me' 는 currentUser(), 이메일·accountId 는 따옴표 리터럴.

    필드 이름만 다르고 해석 규칙은 같다. 이메일 해석은 Jira 에 맡긴다.
    """
    value = value.strip()
    if is_self_alias(value):
        return f"{field} = currentUser()"
    if "@" in value:  # 이메일
        return f'{field} = "{_esc(value)}"'
    if looks_like_account_id(value):
        return f'{field} = "{_esc(value)}"'
    raise QueryError(
        f"{field} 값 형식 미지원: {value!r}\n"
        "  지원: 'me' / 이메일 / accountId\n"
        "  한국어 이름·닉네임은 v1.x 미지원 — accountId 로 변환 후 사용."
    )


def _date_clause(field: str, value: str) -> str:
    """단순 표현 → JQL 절.

    지원:
      7d / 24h / 2w                 → <field> >= -<단축>   (지난 N 단위)
      1m                            → <field> >= startOfDay("-1M")  (지난 N개월, 그 날 00:00부터)
      YYYY-MM-DD                    → <field> >= "..."     (그 날짜 이후)
      <=YYYY-MM-DD                  → <field> < "다음 날"   (그 날 하루 포함)
      <YYYY-MM-DD                   → <field> < "..."
      >=YYYY-MM-DD                  → <field> >= "..."
      >YYYY-MM-DD                   → <field> >= "다음 날"  (그 날 하루 제외)
      YYYY-MM-DD..YYYY-MM-DD        → <field> >= A AND <field> < B+1일  (양 끝 포함)
      YYYY-MM-DD~YYYY-MM-DD         → 동상 (alias)

    created/updated는 datetime 필드라 날짜만 쓰면 그 날 00:00을 뜻한다. 그래서 끝 날짜를
    포함하려면 다음 날 00:00 미만으로 써야 한다. 달력에 없는 날짜는 QueryError.

    비교 연산자 형식은 `--due` 와 동일 — stale 티켓 쿼리(`updated <= "..."`) 가능.
    범위는 절대 날짜끼리만. 단축형('7d') 과 섞을 수 없음.
    """
    value = value.strip()
    range_clause = _date_range_clause(field, value, datetime_field=True)
    if range_clause is not None:
        return range_clause
    short = _UPDATED_SHORTHAND.match(value)
    if short:
        amount, unit = short.groups()
        if unit == "m":
            # JQL 날짜 리터럴의 상대 단위는 w/d/h/m(분)뿐이라 '-1m'은 1분 전이다.
            # 도움말의 '1m'은 한 달 뜻이므로 월 단위(M)를 받는 함수 인자로 쓴다.
            # Atlassian 문서의 인자 예시는 모두 따옴표가 있는 startOfDay("-1M") 형태다.
            return f'{field} >= startOfDay("-{amount}M")'
        return f"{field} >= -{value}"
    if _DATE_ONLY.match(value):
        _parse_day(value)
        return f'{field} >= "{value}"'
    m = _DATE_WITH_OP.match(value)
    if m:
        op, day = m.groups()
        _parse_day(day)
        # datetime 필드라 날짜 하루 전체를 포함/제외하도록 경계를 다음 날 00:00으로 옮긴다.
        if op == "<=":
            return f'{field} < "{_next_day(day)}"'
        if op == ">":
            return f'{field} >= "{_next_day(day)}"'
        return f'{field} {op} "{day}"'
    raise QueryError(
        f"{field} 값 형식 미지원: {value!r}\n"
        "  지원: '7d'/'24h'/'2w'/'1m' 단축, 'YYYY-MM-DD', '<=YYYY-MM-DD'/'>=YYYY-MM-DD'/'<YYYY-MM-DD'/'>YYYY-MM-DD',\n"
        "       'YYYY-MM-DD..YYYY-MM-DD' 또는 'YYYY-MM-DD~YYYY-MM-DD' (범위, 양 끝 포함)"
    )


_SP_INT = re.compile(r"^-?\d+$")
_SP_WITH_OP = re.compile(r"^(<=|>=|<|>)\s*(-?\d+)$")

# 범위 — '..' 또는 '~' 구분자, 양 끝 inclusive. 단축형(7d) 과의 조합은 비지원.
_DATE_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*(?:\.\.|~)\s*(\d{4}-\d{2}-\d{2})$")


def _parse_day(day: str) -> _dt.date:
    """정규식은 통과하지만 달력에 없는 날짜(2026-02-30, 2026-13-01)를 QueryError로."""
    try:
        return _dt.date.fromisoformat(day)
    except ValueError:
        raise QueryError(f"없는 날짜: {day!r} (YYYY-MM-DD, 실제 달력 날짜만)") from None


def _next_day(day: str) -> str:
    try:
        return (_parse_day(day) + _dt.timedelta(days=1)).isoformat()
    except OverflowError:
        # 9999-12-31 의 다음 날은 표현할 수 없다.
        raise QueryError(f"날짜가 범위를 벗어남: {day!r}") from None


def _date_range_clause(field: str, value: str, *, datetime_field: bool = False) -> str | None:
    """범위 표현이면 inclusive JQL 절, 아니면 None.

    created/updated 같은 datetime 필드에서 `<= "2026-03-31"`은 31일 00:00까지만
    잡아 끝 날짜 하루가 빠진다. 그래서 datetime 필드는 `< 끝날짜+1일`로 쓴다.
    """
    m = _DATE_RANGE.match(value)
    if not m:
        return None
    start, end = m.group(1), m.group(2)
    _parse_day(start)
    _parse_day(end)
    if start > end:
        raise QueryError(
            f"{field} 범위의 시작이 끝보다 늦음: {start} > {end}"
        )
    if datetime_field:
        return f'{field} >= "{start}" AND {field} < "{_next_day(end)}"'
    return f'{field} >= "{start}" AND {field} <= "{end}"'


def _due_clause(value: str) -> str:
    """기한 통합 표현 → JQL 절.

    지원:
      overdue                       → duedate < now()
      none / empty                  → duedate is EMPTY
      set                           → duedate is not EMPTY
      YYYY-MM-DD                    → duedate = "..." (정확 일치)
      <=YYYY-MM-DD                  → duedate <= "..."
      <YYYY-MM-DD                   → duedate < "..."
      >=YYYY-MM-DD                  → duedate >= "..."
      >YYYY-MM-DD                   → duedate > "..."
      YYYY-MM-DD..YYYY-MM-DD        → duedate >= A AND duedate <= B  (inclusive)
      YYYY-MM-DD~YYYY-MM-DD         → 동상 (alias)
    """
    v = value.strip()
    low = v.lower()
    if low == "overdue":
        return "duedate < now()"
    if low in ("none", "empty"):
        return "duedate is EMPTY"
    if low == "set":
        return "duedate is not EMPTY"
    range_clause = _date_range_clause("duedate", v)
    if range_clause is not None:
        return range_clause
    if _DATE_ONLY.match(v):
        _parse_day(v)
        return f'duedate = "{v}"'
    m = _DATE_WITH_OP.match(v)
    if m:
        _parse_day(m.group(2))
        return f'duedate {m.group(1)} "{m.group(2)}"'
    raise QueryError(
        f"due 값 형식 미지원: {value!r}\n"
        "  지원: 'overdue' / 'none' / 'set' / 'YYYY-MM-DD' / '<=YYYY-MM-DD' / '>YYYY-MM-DD' /\n"
        "       'YYYY-MM-DD..YYYY-MM-DD' 또는 'YYYY-MM-DD~YYYY-MM-DD' (범위, 양 끝 포함)"
    )


def _sp_clause(value: str, sp_field_id: str | None) -> str:
    """스토리포인트 통합 표현 → JQL 절.

    지원:
      N                → cf[<id>] = N
      >=N / <=N / >N / <N → 비교
      none / empty     → cf[<id>] is EMPTY
      set              → cf[<id>] is not EMPTY

    sp_field_id 가 None 이면 QueryError (사용자에게 안내).
    """
    if not sp_field_id:
        raise QueryError(
            "story_points custom field ID 가 config 에 없음.\n"
            "  `tako fields detect story_points --save` 로 자동 등록하거나\n"
            "  `tako fields set story_points customfield_XXXXX` 로 수동 등록."
        )
    v = value.strip()
    low = v.lower()

    # customfield ID 에서 숫자만 추출 → cf[N] 형태
    m_id = re.search(r"(\d+)$", sp_field_id)
    if not m_id:
        raise QueryError(f"story_points field ID 형식이 이상함: {sp_field_id!r}")
    cf = f"cf[{m_id.group(1)}]"

    if low in ("none", "empty"):
        return f"{cf} is EMPTY"
    if low == "set":
        return f"{cf} is not EMPTY"
    if _SP_INT.match(v):
        return f"{cf} = {v}"
    m = _SP_WITH_OP.match(v)
    if m:
        return f"{cf} {m.group(1)} {m.group(2)}"
    raise QueryError(
        f"sp 값 형식 미지원: {value!r}\n"
        "  지원: 정수(3) / 비교(>=3, <=8, >0, <13) / 'none' / 'set'"
    )


def build_jql(
    filters: ListFilters,
    *,
    default_project: str | None = None,
    sp_field_id: str | None = None,
) -> str:
    """필터들을 AND 로 합친 JQL 문자열. raw_jql 있으면 그것만 사용."""
    if filters.raw_jql:
        return filters.raw_jql.strip()

    clauses: list[str] = []

    if filters.all_projects:
        projects: tuple[str, ...] = ()  # default_project fallback 도 건너뜀
    else:
        projects = filters.projects or ((default_project,) if default_project else ())
    if projects:
        clauses.append(_project_clause(projects))

    if filters.assignee:
        clauses.append(_user_clause("assignee", filters.assignee))

    if filters.reporter:
        clauses.append(_user_clause("reporter", filters.reporter))

    if filters.statuses:
        items = ", ".join(f'"{_esc(s)}"' for s in filters.statuses)
        clauses.append(f"status in ({items})")

    if filters.types:
        items = ", ".join(f'"{_esc(t)}"' for t in filters.types)
        clauses.append(f"issuetype in ({items})")

    if filters.parent:
        parent = filters.parent.strip().upper()
        if not ISSUE_KEY_RE.match(parent):
            raise QueryError(f"parent 키 형식 아님: {parent!r} (예: WL-1234)")
        clauses.append(f"parent = {parent}")

    if filters.labels:
        items = ", ".join(f'"{_esc(l)}"' for l in filters.labels)
        clauses.append(f"labels in ({items})")

    if filters.updated:
        clauses.append(_date_clause("updated", filters.updated))

    if filters.created:
        clauses.append(_date_clause("created", filters.created))

    if filters.due:
        clauses.append(_due_clause(filters.due))

    if filters.sp:
        clauses.append(_sp_clause(filters.sp, sp_field_id))

    if filters.query:
        clauses.append(f'text ~ "{_esc(filters.query)}"')

    if not clauses:
        raise QueryError("필터가 비었음. 최소 하나 이상 지정하거나 --jql 사용.")

    return " AND ".join(clauses) + " ORDER BY updated DESC"
