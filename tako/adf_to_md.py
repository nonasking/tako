"""ADF (Atlassian Document Format) JSON → 마크다운 텍스트 변환.

자체 구현. 외부 의존성 0. 지원 노드:
  doc / paragraph / heading / bulletList / orderedList / listItem
  text (marks: strong, em, code, strike, link) / hardBreak / codeBlock
  blockquote / rule / mention / inlineCard / blockCard / embedCard
  panel / expand / nestedExpand / table / tableRow / tableHeader / tableCell
  taskList / taskItem / decisionList / decisionItem
  emoji / status / date / placeholder / media / mediaInline / mediaSingle / mediaGroup
  layoutSection / layoutColumn / extension / inlineExtension / bodiedExtension
미지원 노드는 자식이 블록이면 문단 단위로, 인라인이면 이어 붙여 평면화한다 (fallback).
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

_PANEL_LABELS = {
    "info": "INFO",
    "note": "NOTE",
    "warning": "WARNING",
    "error": "ERROR",
    "success": "SUCCESS",
    "tip": "TIP",
    "custom": "NOTE",
}

# 자식으로 오면 블록 단위(빈 줄)로 이어야 하는 노드
_BLOCK_TYPES = {
    "paragraph", "heading", "bulletList", "orderedList", "blockquote", "rule",
    "codeBlock", "panel", "expand", "nestedExpand", "table", "taskList",
    "decisionList", "mediaSingle", "mediaGroup", "layoutSection", "layoutColumn",
    "blockCard", "embedCard", "extension", "bodiedExtension",
}


def adf_to_markdown(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    ntype = node.get("type")
    attrs = node.get("attrs") or {}
    children = node.get("content") or []

    if ntype == "doc":
        return _join_blocks(children)

    if ntype == "paragraph":
        return _inline(children)

    if ntype == "heading":
        level = max(1, min(6, int(attrs.get("level", 1))))
        return "#" * level + " " + _inline(children)

    if ntype == "bulletList":
        return "\n".join(f"- {_list_item(c)}" for c in children)

    if ntype == "orderedList":
        start = int(attrs.get("order") or 1)
        return "\n".join(f"{start + i}. {_list_item(c)}" for i, c in enumerate(children))

    if ntype == "blockquote":
        return _quote(_join_blocks(children))

    if ntype == "rule":
        return "---"

    if ntype == "codeBlock":
        lang = attrs.get("language") or ""
        body = "".join(_text_only(c) for c in children)
        return f"```{lang}\n{body}\n```"

    if ntype == "text":
        return _apply_marks(node.get("text", ""), node.get("marks") or [])

    if ntype == "hardBreak":
        return "\n"

    if ntype == "mention":
        return f"@{attrs.get('text') or attrs.get('displayName') or attrs.get('id') or ''}"

    if ntype in ("inlineCard", "blockCard", "embedCard"):
        return attrs.get("url", "")

    if ntype == "panel":
        kind = attrs.get("panelType") or "info"
        label = f"**[{_PANEL_LABELS.get(kind, str(kind).upper())}]**"
        body = _join_blocks(children)
        return _quote(f"{label}\n{body}" if body else label)

    if ntype in ("expand", "nestedExpand"):
        title = attrs.get("title") or "펼치기"
        body = _join_blocks(children)
        return f"**▸ {title}**\n\n{body}" if body else f"**▸ {title}**"

    if ntype == "table":
        return _table(children)

    if ntype in ("tableRow", "tableHeader", "tableCell"):
        # 표 밖에서 단독으로 오는 경우 (정상 ADF에서는 table이 처리한다)
        return _join_blocks(children)

    if ntype == "taskList":
        return "\n".join(s for s in (_task_line(c) for c in children) if s)

    if ntype == "taskItem":
        return _task_line(node)

    if ntype == "decisionList":
        return "\n".join(s for s in (_decision_line(c) for c in children) if s)

    if ntype == "decisionItem":
        return _decision_line(node)

    if ntype == "emoji":
        return attrs.get("text") or attrs.get("shortName") or ""

    if ntype == "status":
        text = attrs.get("text") or ""
        return f"[{text}]" if text else ""

    if ntype == "date":
        return _date(attrs.get("timestamp"))

    if ntype == "placeholder":
        return attrs.get("text") or ""

    if ntype in ("mediaSingle", "mediaGroup"):
        return "\n".join(s for s in (adf_to_markdown(c) for c in children) if s)

    if ntype in ("media", "mediaInline"):
        name = attrs.get("alt") or attrs.get("__fileName") or attrs.get("filename") or "file"
        return f"[첨부: {name}]"

    if ntype in ("layoutSection", "layoutColumn"):
        return _join_blocks(children)

    if ntype in ("extension", "inlineExtension", "bodiedExtension"):
        key = attrs.get("extensionKey") or "extension"
        body = _join_blocks(children) if children else ""
        return f"[확장: {key}]\n\n{body}" if body else f"[확장: {key}]"

    # 미지원: 자식이 블록이면 문단 단위로, 인라인이면 이어 붙인다.
    # (블록을 구분자 없이 이으면 단락·목록이 한 줄로 붙어 읽을 수 없다)
    if not children:
        return ""
    if any(isinstance(c, dict) and c.get("type") in _BLOCK_TYPES for c in children):
        return _join_blocks(children)
    return _inline(children)


def _quote(body: str) -> str:
    return "\n".join(f"> {ln}" if ln else ">" for ln in body.split("\n"))


def _cell_text(cell: dict[str, Any]) -> str:
    """표 칸 안의 블록을 한 줄로. 칸 안 줄바꿈은 <br>, 파이프는 이스케이프."""
    body = _join_blocks(cell.get("content") or [])
    return body.replace("|", "\\|").replace("\n\n", "<br>").replace("\n", "<br>").strip()


def _table(rows: list[Any]) -> str:
    grid = [
        [_cell_text(c) for c in (r.get("content") or []) if isinstance(c, dict)]
        for r in rows
        if isinstance(r, dict)
    ]
    grid = [r for r in grid if r]
    if not grid:
        return ""
    width = max(len(r) for r in grid)
    grid = [r + [""] * (width - len(r)) for r in grid]
    # GFM 표는 머리 행이 필요하다. 첫 행을 머리로 쓴다 (ADF tableHeader 여부와 무관).
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in grid[1:]]
    return "\n".join(lines)


def _task_line(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    if item.get("type") == "taskList":
        # 중첩 체크리스트는 한 단계 들여쓴다
        return "\n".join("  " + ln for ln in adf_to_markdown(item).split("\n"))
    done = (item.get("attrs") or {}).get("state") == "DONE"
    return f"- [{'x' if done else ' '}] {_inline(item.get('content') or [])}"


def _decision_line(item: Any) -> str:
    if not isinstance(item, dict):
        return ""
    return f"- 결정: {_inline(item.get('content') or [])}"


def _date(timestamp: Any) -> str:
    """ADF date 노드의 timestamp(ms 문자열, UTC 자정)를 YYYY-MM-DD로."""
    try:
        ms = int(timestamp)
    except (TypeError, ValueError):
        return ""
    return _dt.datetime.fromtimestamp(ms / 1000, tz=_dt.timezone.utc).date().isoformat()


def _list_item(item: dict[str, Any]) -> str:
    body = _join_blocks(item.get("content") or [])
    # 다단 리스트는 줄바꿈 + 들여쓰기로 표현
    lines = body.split("\n")
    if len(lines) <= 1:
        return body
    return lines[0] + "\n" + "\n".join("  " + ln for ln in lines[1:])


def _inline(children: list[Any]) -> str:
    return "".join(adf_to_markdown(c) for c in children)


def _join_blocks(children: list[Any]) -> str:
    return "\n\n".join(s for s in (adf_to_markdown(c) for c in children) if s)


def _text_only(node: Any) -> str:
    if isinstance(node, dict) and node.get("type") == "text":
        return node.get("text", "")
    if isinstance(node, dict):
        return "".join(_text_only(c) for c in node.get("content") or [])
    return ""


def _apply_marks(text: str, marks: list[dict[str, Any]]) -> str:
    out = text
    for mark in marks:
        mtype = mark.get("type") if isinstance(mark, dict) else None
        if mtype == "strong":
            out = f"**{out}**"
        elif mtype == "em":
            out = f"*{out}*"
        elif mtype == "code":
            out = f"`{out}`"
        elif mtype == "strike":
            out = f"~~{out}~~"
        elif mtype == "link":
            href = (mark.get("attrs") or {}).get("href", "")
            out = f"[{out}]({href})"
    return out
