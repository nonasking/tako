"""페이로드 빌더 + 미리보기.

REST 호출은 jira_client 가 함. 이 모듈은 dict 만 짜서 넘겨준다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISSUE_KEY = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")
DEFAULT_LINK_TYPE = "Relates"


class DraftError(Exception):
    pass


def _parse_link_item(item: Any) -> tuple[str, str]:
    """문자열 'KEY' 또는 'KEY:TYPE' 또는 dict {target, type} 을 (key, type) 으로."""
    if isinstance(item, dict):
        target = item.get("target") or item.get("key")
        type_name = item.get("type") or DEFAULT_LINK_TYPE
    elif isinstance(item, str):
        if ":" in item:
            target, _, type_name = item.partition(":")
            type_name = type_name.strip() or DEFAULT_LINK_TYPE
        else:
            target = item
            type_name = DEFAULT_LINK_TYPE
    else:
        raise DraftError(f"link 항목은 문자열 또는 객체여야 함: {item!r}")

    if not isinstance(target, str) or not target.strip():
        raise DraftError(f"link target 이 비었음: {item!r}")
    target = target.strip().upper()
    if not _ISSUE_KEY.match(target):
        raise DraftError(f"link target 키 형식 아님: {target!r} (예: WL-1234)")
    if not isinstance(type_name, str) or not type_name.strip():
        raise DraftError(f"link type 이 비었음: {item!r}")
    return target, type_name.strip()


@dataclass(frozen=True)
class IssueDraft:
    project: str
    issue_type: str
    summary: str
    description: str
    parent_epic: str | None = None
    labels: tuple[str, ...] = ()
    assignee: str | None = None
    # 미리보기·로그 전용 표시 문자열 ("강민성 (foo@bar.com)" 형태).
    # 페이로드에는 안 들어감. assignee 가 accountId 라 사용자가 못 알아보는 문제 완화.
    assignee_label: str | None = None
    story_points: int | None = None
    duedate: str | None = None  # YYYY-MM-DD
    # ((target_key, type_name), ...) — 새 티켓 기준 outward 관계.
    # 예: (("WL-100", "Relates"), ("WL-200", "Blocks"))
    links: tuple[tuple[str, str], ...] = ()

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
        assignee_label = data.get("assignee_label")
        if assignee_label is not None and not isinstance(assignee_label, str):
            raise DraftError("assignee_label 는 문자열이어야 함.")

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

        links_raw = data.get("links") or []
        if not isinstance(links_raw, list):
            raise DraftError("links 는 리스트여야 함.")
        links: list[tuple[str, str]] = []
        for item in links_raw:
            target, type_name = _parse_link_item(item)
            links.append((target, type_name))

        return IssueDraft(
            project=data["project"].strip(),
            issue_type=data["issue_type"].strip(),
            summary=data["summary"].strip(),
            description=data["description"],
            parent_epic=(parent.strip() if parent else None) or None,
            labels=tuple(label.strip() for label in labels_raw if label.strip()),
            assignee=(assignee.strip() if assignee else None) or None,
            assignee_label=(assignee_label.strip() if assignee_label else None) or None,
            story_points=story_points,
            duedate=duedate,
            links=tuple(links),
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
        # draft.assignee_label 은 표시 전용 — 페이로드에는 accountId 만 실음.
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
    if draft.links:
        # links 는 fields 에 안 들어감 (Jira REST 의 issueLink 는 별도 API).
        # 호출자(tako new 또는 슬래시 커맨드)가 본체 생성 후 link 별도 호출.
        meta["links"] = [
            {"target": target, "type": type_name} for target, type_name in draft.links
        ]

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
        lines.append(f"담당자:   {draft.assignee_label or draft.assignee}")
    if draft.story_points is not None:
        lines.append(f"SP:      {draft.story_points}")
    if draft.duedate:
        lines.append(f"기한:    {draft.duedate}")
    if draft.links:
        lines.append("연결:")
        for target, type_name in draft.links:
            lines.append(f"  {type_name} → {target}")
    lines.append("본문:")
    lines.extend(f"  {body_line}" for body_line in draft.description.splitlines() or [""])
    lines.append("-" * 60)
    lines.append("이슈 유형은 생성 후 변경 불가.")
    return "\n".join(lines)
