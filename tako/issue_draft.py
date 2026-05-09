"""페이로드 빌더 + 미리보기.

REST 호출은 jira_client 가 함. 이 모듈은 dict 만 짜서 넘겨준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

        return IssueDraft(
            project=data["project"].strip(),
            issue_type=data["issue_type"].strip(),
            summary=data["summary"].strip(),
            description=data["description"],
            parent_epic=(parent.strip() if parent else None) or None,
            labels=tuple(label.strip() for label in labels_raw if label.strip()),
            assignee=(assignee.strip() if assignee else None) or None,
        )


def build_payload(draft: IssueDraft) -> dict[str, Any]:
    # 반환: {"payload": {"fields": {...}}, "meta": {...}}
    # meta.description_format == "markdown" → jira_client 가 ADF 변환 후 전송.
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

    return {
        "payload": {"fields": fields_block},
        "meta": {"description_format": "markdown", "source": "tako"},
    }


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
    lines.append("본문:")
    lines.extend(f"  {body_line}" for body_line in draft.description.splitlines() or [""])
    lines.append("-" * 60)
    lines.append("이슈 유형은 생성 후 변경 불가.")
    return "\n".join(lines)
