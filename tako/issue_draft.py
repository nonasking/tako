"""페이로드 빌더 + 미리보기.

REST 호출은 jira_client 가 함. 이 모듈은 dict 만 짜서 넘겨준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DraftError(Exception):
    pass


@dataclass(frozen=True)
class IssueDraft:
    project: str
    issue_type: str
    summary: str
    description: str
    parent_epic: str | None = None
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    story_points: int | None = None
    duedate: str | None = None  # YYYY-MM-DD

    @staticmethod
    def from_payload(data: dict[str, Any]) -> IssueDraft:
        for required in ("project", "issue_type", "summary", "description"):
            value = data.get(required)
            if not isinstance(value, str) or not value.strip():
                raise DraftError(f"{required} 비었음.")

        labels_raw = data.get("labels") or []
        if not isinstance(labels_raw, list) or not all(isinstance(x, str) for x in labels_raw):
            raise DraftError("labels 는 문자열 리스트여야 함.")

        parent = data.get("parent_epic")
        if parent is not None and not isinstance(parent, str):
            raise DraftError("parent_epic 는 문자열이어야 함.")

        assignee = data.get("assignee")
        if assignee is not None and not isinstance(assignee, str):
            raise DraftError("assignee 는 문자열이어야 함.")

        sp_raw = data.get("story_points")
        story_points: int | None = None
        if sp_raw is not None and sp_raw != "":
            try:
                story_points = int(sp_raw)
            except (TypeError, ValueError):
                raise DraftError(f"story_points 는 정수여야 함: {sp_raw!r}")
            if story_points < 0:
                raise DraftError(f"story_points 음수 불가: {story_points}")

        duedate_raw = data.get("duedate")
        duedate: str | None = None
        if duedate_raw:
            if not isinstance(duedate_raw, str) or not _ISO_DATE.match(duedate_raw.strip()):
                raise DraftError(f"duedate 는 YYYY-MM-DD 형식: {duedate_raw!r}")
            duedate = duedate_raw.strip()

        return IssueDraft(
            project=data["project"].strip(),
            issue_type=data["issue_type"].strip(),
            summary=data["summary"].strip(),
            description=data["description"],
            parent_epic=(parent.strip() if parent else None) or None,
            labels=tuple(label.strip() for label in labels_raw if label.strip()),
            assignee=(assignee.strip() if assignee else None) or None,
            story_points=story_points,
            duedate=duedate,
        )


def build_payload(
    draft: IssueDraft,
    custom_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """반환: {"payload": {"fields": {...}}, "meta": {...}}.

    custom_fields: {"story_points": "customfield_10016"} 처럼 사용자 환경의
    field ID 매핑. story_points 가 draft 에 있는데 매핑이 없으면 meta.warnings 로 보고.
    meta.description_format == "markdown" → jira_client 가 ADF 변환 후 전송.
    """
    custom_fields = custom_fields or {}
    fields_block: dict[str, Any] = {
        "project": {"key": draft.project},
        "issuetype": {"name": draft.issue_type},
        "summary": draft.summary,
        "description": draft.description,
    }
    if draft.parent_epic:
        fields_block["parent"] = {"key": draft.parent_epic}
    if draft.labels:
        fields_block["labels"] = list(draft.labels)
    if draft.assignee:
        fields_block["assignee"] = {"accountId": draft.assignee}
    if draft.duedate:
        fields_block["duedate"] = draft.duedate

    warnings: list[str] = []
    if draft.story_points is not None:
        sp_field_id = custom_fields.get("story_points")
        if sp_field_id:
            fields_block[sp_field_id] = draft.story_points
        else:
            warnings.append(
                "story_points 값을 받았지만 config.jira.fields.story_points 가 비어 있어 페이로드에서 제외함."
            )

    meta: dict[str, Any] = {"description_format": "markdown", "source": "tako"}
    if warnings:
        meta["warnings"] = warnings

    return {"payload": {"fields": fields_block}, "meta": meta}


def render_preview(draft: IssueDraft) -> str:
    lines = [
        "[미리보기]",
        "-" * 60,
        f"프로젝트: {draft.project}",
        f"유형:    {draft.issue_type}",
        f"제목:    {draft.summary}",
    ]
    if draft.parent_epic:
        lines.append(f"부모:    {draft.parent_epic}")
    if draft.labels:
        lines.append(f"라벨:    {', '.join(draft.labels)}")
    if draft.assignee:
        lines.append(f"담당자:   {draft.assignee}")
    if draft.story_points is not None:
        lines.append(f"SP:      {draft.story_points}")
    if draft.duedate:
        lines.append(f"기한:    {draft.duedate}")
    lines.append("본문:")
    lines.extend(f"  {body_line}" for body_line in draft.description.splitlines() or [""])
    lines.append("-" * 60)
    lines.append("이슈 유형은 생성 후 변경 불가.")
    return "\n".join(lines)
