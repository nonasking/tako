"""tako list 의 *명시 인자 → JQL* 변환.

각 필터를 안전하게 JQL 절로 만들어 AND 로 합친다.
사용자가 `--jql` 로 직접 줄 때는 변환 안 하고 그대로 사용 (덮어쓰기).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
_UPDATED_SHORTHAND = re.compile(r"^(\d+)([dhwm])$")  # 7d, 24h, 2w, 1m


@dataclass
class ListFilters:
    """사용자 인자를 그대로 보존한 필터 묶음."""
    assignee: str | None = None
    project: str | None = None
    statuses: tuple[str, ...] = ()
    types: tuple[str, ...] = ()
    parent: str | None = None
    labels: tuple[str, ...] = ()
    updated: str | None = None
    query: str | None = None
    raw_jql: str | None = None


class QueryError(Exception):
    pass


def _esc(value: str) -> str:
    """JQL 문자열 리터럴 escape — backslash → \\, double-quote → \\\""""
    return value.replace("\\", "\\\\").replace('"', '\\"')


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


def _updated_clause(value: str) -> str:
    """단순 표현 → JQL 절. '7d' → 'updated >= -7d', '2026-05-01' → 'updated >= 2026-05-01'"""
    value = value.strip()
    if _UPDATED_SHORTHAND.match(value):
        return f"updated >= -{value}"
    # ISO 날짜 가정 (대략)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return f'updated >= "{value}"'
    raise QueryError(
        f"updated 값 형식 미지원: {value!r}\n"
        "  지원: '7d'/'24h'/'2w'/'1m' 단축, 또는 'YYYY-MM-DD'"
    )


def build_jql(filters: ListFilters, *, default_project: str | None = None) -> str:
    """필터들을 AND 로 합친 JQL 문자열. raw_jql 있으면 그것만 사용."""
    if filters.raw_jql:
        return filters.raw_jql.strip()

    clauses: list[str] = []

    project = filters.project or default_project
    if project:
        clauses.append(f"project = {project}")

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
        clauses.append(_updated_clause(filters.updated))

    if filters.query:
        clauses.append(f'text ~ "{_esc(filters.query)}"')

    if not clauses:
        raise QueryError("필터가 비었음. 최소 하나 이상 지정하거나 --jql 사용.")

    return " AND ".join(clauses) + " ORDER BY updated DESC"
