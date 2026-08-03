"""tako list 결과 출력 포맷터.

- text: 사람 친화 표 (render_list_table — 한글 전각 폭 기준 열 정렬)
- csv:  Excel 친화 CSV (UTF-8 BOM, 한국어 깨짐 방지)
- json: 원본 응답 (main 쪽에서 그대로 dump)

행 추출(issue_cells)은 표·CSV 공용 — 값 없는 칸은 빈 문자열로 두고,
"(미할당)" 같은 표시용 대체 문구는 각 렌더러가 얹는다.
"""

from __future__ import annotations

import csv
import io
import unicodedata
from typing import Any


# SP 매핑 유무에 따라 동적으로 결정 (issues_to_csv 에서 처리)
_BASE_COLUMNS = ("key", "status", "type", "assignee", "created", "updated", "duedate", "summary", "parent", "url")


def issue_cells(issue: dict[str, Any], *, sp_field_id: str | None = None) -> dict[str, str]:
    """이슈 dict → 컬럼 이름별 문자열. 없는 값은 빈 문자열."""
    f = issue.get("fields") or {}
    sp = ""
    if sp_field_id:
        sp_val = f.get(sp_field_id)
        if sp_val is not None:
            sp = str(int(sp_val)) if isinstance(sp_val, (int, float)) else str(sp_val)
    return {
        "key": issue.get("key", ""),
        "status": (f.get("status") or {}).get("name", ""),
        "type": (f.get("issuetype") or {}).get("name", ""),
        "story_points": sp,
        "assignee": ((f.get("assignee") or {}).get("displayName")) or "",
        "created": (f.get("created") or "")[:10],
        "updated": (f.get("updated") or "")[:10],
        "duedate": f.get("duedate") or "",
        "summary": f.get("summary", ""),
        "parent": (f.get("parent") or {}).get("key", ""),
    }


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
    buf.write("\ufeff")  # UTF-8 BOM — Excel 이 UTF-8 로 인식하게. 이스케이프 표기 (소스에 안 보이는 리터럴 금지)
    writer = csv.writer(buf, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(columns)
    for it in issues:
        cells = issue_cells(it, sp_field_id=sp_field_id)
        key = cells["key"]
        cells["url"] = f"https://{site}/browse/{key}" if key else ""
        writer.writerow(tuple(cells[c] for c in columns))
    return buf.getvalue()


def display_width(text: str) -> int:
    """터미널 표시 폭 — 한글 등 동아시아 전각(W/F)은 2칸."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _fit(cell: str, width: int) -> str:
    """표시 폭 기준으로 자르고 오른쪽 공백 패딩. 코드포인트가 아니라 칸 수로 맞춘다."""
    out: list[str] = []
    used = 0
    for ch in cell:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out) + " " * (width - used)


def render_list_table(issues: list[dict[str, Any]], *, sp_field_id: str | None = None) -> str:
    """사람 친화 표. SP 매핑 있으면 SP 컬럼 추가."""
    has_sp = bool(sp_field_id)
    if has_sp:
        header = ("KEY", "상태", "유형", "SP", "담당자", "생성", "업데이트", "기한", "제목")
        widths = (12, 10, 12, 5, 14, 11, 11, 11, 50)
    else:
        header = ("KEY", "상태", "유형", "담당자", "생성", "업데이트", "기한", "제목")
        widths = (12, 10, 12, 14, 11, 11, 11, 55)

    rows: list[tuple[str, ...]] = []
    for it in issues:
        c = issue_cells(it, sp_field_id=sp_field_id)
        key = c["key"] or "?"
        status = c["status"] or "?"
        itype = c["type"] or "?"
        assignee = c["assignee"] or "(미할당)"
        if has_sp:
            rows.append((key, status, itype, c["story_points"], assignee, c["created"], c["updated"], c["duedate"], c["summary"]))
        else:
            rows.append((key, status, itype, assignee, c["created"], c["updated"], c["duedate"], c["summary"]))

    def line(row: tuple[str, ...]) -> str:
        return "  ".join(_fit(cell, widths[i] if i < len(widths) else 10) for i, cell in enumerate(row)).rstrip()

    divider = tuple("-" * (w - 2) for w in widths)
    return "\n".join([line(header), line(divider)] + [line(r) for r in rows])
