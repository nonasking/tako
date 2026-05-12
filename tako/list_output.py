"""tako list 결과 출력 포맷터.

- text: 사람 친화 표 (기본, _render_list_table 은 main.py 에 유지)
- csv:  Excel 친화 CSV (UTF-8 BOM, 한국어 깨짐 방지)
- json: 원본 응답
"""

from __future__ import annotations

import csv
import io
from typing import Any


# SP 매핑 유무에 따라 동적으로 결정 (issues_to_csv 에서 처리)
_BASE_COLUMNS = ("key", "status", "type", "assignee", "created", "updated", "duedate", "summary", "parent", "url")


def issues_to_csv(
    issues: list[dict[str, Any]],
    *,
    site: str,
    sp_field_id: str | None = None,
) -> str:
    """이슈 목록 → CSV 문자열 (UTF-8 BOM 포함, Excel 한국어 호환).

    sp_field_id 가 있으면 'story_points' 컬럼이 'type' 직후에 추가됨.
    """
    columns = list(_BASE_COLUMNS)
    if sp_field_id:
        # type 다음에 story_points 삽입
        idx = columns.index("type") + 1
        columns.insert(idx, "story_points")

    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM — Excel 이 UTF-8 로 인식하게
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for it in issues:
        writer.writerow(_row(it, site=site, sp_field_id=sp_field_id))
    return buf.getvalue()


def _row(issue: dict[str, Any], *, site: str, sp_field_id: str | None) -> tuple[str, ...]:
    f = issue.get("fields") or {}
    key = issue.get("key", "")
    status = (f.get("status") or {}).get("name", "")
    itype = (f.get("issuetype") or {}).get("name", "")
    assignee = ((f.get("assignee") or {}).get("displayName")) or ""
    created = (f.get("created") or "")[:10]
    updated = (f.get("updated") or "")[:10]
    duedate = f.get("duedate") or ""
    summary = f.get("summary", "")
    parent = (f.get("parent") or {}).get("key", "")
    url = f"https://{site}/browse/{key}" if key else ""

    if sp_field_id:
        sp_val = f.get(sp_field_id)
        sp_str = "" if sp_val is None else (str(int(sp_val)) if isinstance(sp_val, (int, float)) else str(sp_val))
        return (key, status, itype, sp_str, assignee, created, updated, duedate, summary, parent, url)
    return (key, status, itype, assignee, created, updated, duedate, summary, parent, url)
