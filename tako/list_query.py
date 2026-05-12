"""tako list 의 *명시 인자 → JQL* 변환.

각 필터를 안전하게 JQL 절로 만들어 AND 로 합친다.
사용자가 `--jql` 로 직접 줄 때는 변환 안 하고 그대로 사용 (덮어쓰기).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_PROJECT_KEY = re.compile(r"^[A-Z][A-Z0-9_]*$")
_UPDATED_SHORTHAND = re.compile(r"^(\d+)([dhwm])$")  # 7d, 24h, 2w, 1m


@dataclass
class ListFilters:
    """사용자 인자를 그대로 보존한 필터 묶음."""
    assignee: str | None = None
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


def _assignee_clause(value: str) -> str:
    value = value.strip()
    if value.lower() in {"me", "current", "self"}:
        return "assignee = currentUser()"
    if "@" in value:  # 이메일
        return f'assignee = "{_esc(value)}"'
    if re.match(r"^[a-f0-9:\-]+$", value, re.IGNORECASE) and len(value) >= 12:
        # accountId 패턴 (UUID-ish)
        return f'assignee = "{_esc(value)}"'
    raise QueryError(
        f"assignee 값 형식 미지원: {value!r}\n"
        "  지원: 'me' / 이메일 / accountId\n"
        "  한국어 이름·닉네임은 v1.x 미지원 — accountId 로 변환 후 사용."
    )


def _date_clause(field: str, value: str) -> str:
    """단순 표현 → JQL 절. '<field> >= -<단축>' 또는 '<field> >= "YYYY-MM-DD"'.

    지원: '7d'/'24h'/'2w'/'1m' 단축, 또는 'YYYY-MM-DD'.
    """
    value = value.strip()
    if _UPDATED_SHORTHAND.match(value):
        return f"{field} >= -{value}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return f'{field} >= "{value}"'
    raise QueryError(
        f"{field} 값 형식 미지원: {value!r}\n"
        "  지원: '7d'/'24h'/'2w'/'1m' 단축, 또는 'YYYY-MM-DD'"
    )


_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_WITH_OP = re.compile(r"^(<=|>=|<|>)\s*(\d{4}-\d{2}-\d{2})$")
_SP_INT = re.compile(r"^-?\d+$")
_SP_WITH_OP = re.compile(r"^(<=|>=|<|>)\s*(-?\d+)$")


def _due_clause(value: str) -> str:
    """기한 통합 표현 → JQL 절.

    지원:
      overdue          → duedate < now()
      none / empty     → duedate is EMPTY
      set              → duedate is not EMPTY
      YYYY-MM-DD       → duedate = "..." (정확 일치)
      <=YYYY-MM-DD     → duedate <= "..."
      <YYYY-MM-DD      → duedate < "..."
      >=YYYY-MM-DD     → duedate >= "..."
      >YYYY-MM-DD      → duedate > "..."
    """
    v = value.strip()
    low = v.lower()
    if low == "overdue":
        return "duedate < now()"
    if low in ("none", "empty"):
        return "duedate is EMPTY"
    if low == "set":
        return "duedate is not EMPTY"
    if _DATE_ONLY.match(v):
        return f'duedate = "{v}"'
    m = _DATE_WITH_OP.match(v)
    if m:
        return f'duedate {m.group(1)} "{m.group(2)}"'
    raise QueryError(
        f"due 값 형식 미지원: {value!r}\n"
        "  지원: 'overdue' / 'none' / 'set' / 'YYYY-MM-DD' / '<=YYYY-MM-DD' / '>YYYY-MM-DD' 등"
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

    projects = filters.projects or ((default_project,) if default_project else ())
    if projects:
        clauses.append(_project_clause(projects))

    if filters.assignee:
        clauses.append(_assignee_clause(filters.assignee))

    if filters.statuses:
        items = ", ".join(f'"{_esc(s)}"' for s in filters.statuses)
        clauses.append(f"status in ({items})")

    if filters.types:
        items = ", ".join(f'"{_esc(t)}"' for t in filters.types)
        clauses.append(f"issuetype in ({items})")

    if filters.parent:
        parent = filters.parent.strip().upper()
        if not _ISSUE_KEY.match(parent):
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
