"""tako list 결과 출력 포맷터.

- text: 사람 친화 표 (기본, _render_list_table 은 main.py 에 유지)
- csv:  Excel 친화 CSV (UTF-8 BOM, 한국어 깨짐 방지)
- json: 원본 응답
"""

from __future__ import annotations

import csv
import io
from typing import Any


CSV_COLUMNS = ("key", "status", "type", "assignee", "updated", "summary", "parent", "url")


def issues_to_csv(issues: list[dict[str, Any]], *, site: str) -> str:
    """이슈 목록 → CSV 문자열 (UTF-8 BOM 포함, Excel 한국어 호환).

    BOM 없는 CSV 는 Excel 이 ANSI 로 해석해 한국어가 깨진다.
    """
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM — Excel 이 UTF-8 로 인식하게
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    for it in issues:
        writer.writerow(_row(it, site=site))
    return buf.getvalue()


def _row(issue: dict[str, Any], *, site: str) -> tuple[str, ...]:
    f = issue.get("fields") or {}
    key = issue.get("key", "")
    status = (f.get("status") or {}).get("name", "")
    itype = (f.get("issuetype") or {}).get("name", "")
    assignee = ((f.get("assignee") or {}).get("displayName")) or ""
    updated = (f.get("updated") or "")[:10]
    summary = f.get("summary", "")
    parent = (f.get("parent") or {}).get("key", "")
    url = f"https://{site}/browse/{key}" if key else ""
    return (key, status, itype, assignee, updated, summary, parent, url)
