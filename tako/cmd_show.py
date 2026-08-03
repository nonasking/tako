"""tako show — 단일 이슈 조회 + 텍스트/JSON 렌더링."""

from __future__ import annotations

import json
import sys
from typing import Any

from .adf_to_md import adf_to_markdown
from .cmd_common import build_client
from .config import TakoConfig
from .jira_client import JiraApiError
from .patterns import extract_issue_key


def cmd_show(args: Any, cfg: TakoConfig) -> int:
    key = extract_issue_key(args.key)
    client = build_client(cfg, args.credentials)
    try:
        # show 는 화면에 거의 모든 필드를 쓰므로 전체 수신 (fields 제한 없음).
        issue = client.get_issue(key)
        comments = client.list_comments(key, max_results=args.max_comments) if args.max_comments > 0 else []
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    if args.as_json:
        payload = {"issue": issue, "comments": comments, "url": f"https://{cfg.jira.site}/browse/{key}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(_render_issue_text(issue, comments, site=cfg.jira.site))
    return 0


def _render_issue_text(issue: dict[str, Any], comments: list[dict[str, Any]], *, site: str) -> str:
    fields = issue.get("fields") or {}
    key = issue.get("key", "?")
    summary = fields.get("summary", "")
    itype = (fields.get("issuetype") or {}).get("name", "?")
    status = (fields.get("status") or {}).get("name", "?")
    assignee = ((fields.get("assignee") or {}).get("displayName")) or "(미할당)"
    reporter = ((fields.get("reporter") or {}).get("displayName")) or "(없음)"
    priority = ((fields.get("priority") or {}).get("name")) or "(없음)"
    duedate = fields.get("duedate") or "(없음)"
    labels = fields.get("labels") or []
    parent = fields.get("parent") or {}
    parent_key = parent.get("key")
    parent_summary = (parent.get("fields") or {}).get("summary", "")

    description_md = adf_to_markdown(fields.get("description"))

    lines: list[str] = []
    lines.append(f"[{key}] {itype} — {summary}")
    if parent_key:
        lines.append(f"부모:     {parent_key} {parent_summary}".rstrip())
    lines.append(f"상태:     {status}")
    lines.append(f"담당자:   {assignee}")
    lines.append(f"보고자:   {reporter}")
    lines.append(f"우선순위: {priority}")
    lines.append(f"기한:     {duedate}")
    if labels:
        lines.append(f"라벨:     {', '.join(labels)}")
    lines.append(f"링크:     https://{site}/browse/{key}")

    children = _child_lines(fields.get("subtasks"))
    if children:
        lines.append("")
        lines.append(f"--- 하위 이슈 ({len(children)}) ---")
        lines.extend(children)
    related = _related_lines(fields.get("issuelinks"))
    if related:
        lines.append("")
        lines.append(f"--- 연결된 이슈 ({len(related)}) ---")
        lines.extend(related)

    lines.append("")
    lines.append("--- 설명 ---")
    lines.append(description_md or "(비어 있음)")
    if comments:
        lines.append("")
        lines.append(f"--- 코멘트 ({len(comments)}, 최신순) ---")
        for c in comments:
            author = ((c.get("author") or {}).get("displayName")) or "?"
            created = (c.get("created") or "")[:10]
            body_md = adf_to_markdown(c.get("body"))
            lines.append(f"\n[{created}] {author}")
            lines.append(body_md or "(비어 있음)")
    return "\n".join(lines)


def _brief(item: dict[str, Any]) -> str:
    """{key, fields:{summary, status}} → 'WL-101 [진행중] 제목' 한 줄."""
    key = item.get("key", "?")
    f = item.get("fields") or {}
    status = (f.get("status") or {}).get("name")
    summary = f.get("summary") or ""
    head = f"{key} [{status}]" if status else key
    return f"{head} {summary}".rstrip()


def _child_lines(subtasks: Any) -> list[str]:
    if not isinstance(subtasks, list):
        return []
    return [f"  {_brief(t)}" for t in subtasks if isinstance(t, dict)]


def _related_lines(issuelinks: Any) -> list[str]:
    """issuelinks → '  blocks → WL-200 [완료] 제목'.

    방향에 따라 표기어가 갈린다. outwardIssue 가 실려 오면 *이 이슈가* type.outward
    관계를 거는 쪽(예: blocks), inwardIssue 면 받는 쪽(예: is blocked by).
    한 항목에 둘 중 하나만 들어온다.
    """
    if not isinstance(issuelinks, list):
        return []
    out: list[str] = []
    for link in issuelinks:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        target = link.get("outwardIssue")
        phrase = link_type.get("outward")
        if not isinstance(target, dict):
            target = link.get("inwardIssue")
            phrase = link_type.get("inward")
        if not isinstance(target, dict):
            continue
        phrase = phrase or link_type.get("name") or "관련"
        out.append(f"  {phrase} → {_brief(target)}")
    return out
