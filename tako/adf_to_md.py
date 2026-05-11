"""ADF (Atlassian Document Format) JSON → 마크다운 텍스트 변환.

자체 구현. 외부 의존성 0. 지원 노드:
  doc / paragraph / heading / bulletList / orderedList / listItem
  text (marks: strong, em, code, strike, link) / hardBreak / codeBlock
  blockquote / rule / mention / inlineCard
미지원 노드는 안의 text 만 평면화 (fallback).
"""

from __future__ import annotations

from typing import Any


def adf_to_markdown(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""

    ntype = node.get("type")
    children = node.get("content") or []

    if ntype == "doc":
        return _join_blocks(children)

    if ntype == "paragraph":
        return _inline(children)

    if ntype == "heading":
        level = max(1, min(6, int(node.get("attrs", {}).get("level", 1))))
        return "#" * level + " " + _inline(children)

    if ntype == "bulletList":
        return "\n".join(f"- {_list_item(c)}" for c in children)

    if ntype == "orderedList":
        return "\n".join(f"{i + 1}. {_list_item(c)}" for i, c in enumerate(children))

    if ntype == "blockquote":
        body = _join_blocks(children)
        return "\n".join(f"> {ln}" if ln else ">" for ln in body.split("\n"))

    if ntype == "rule":
        return "---"

    if ntype == "codeBlock":
        lang = node.get("attrs", {}).get("language") or ""
        body = "".join(_text_only(c) for c in children)
        return f"```{lang}\n{body}\n```"

    if ntype == "text":
        return _apply_marks(node.get("text", ""), node.get("marks") or [])

    if ntype == "hardBreak":
        return "\n"

    if ntype == "mention":
        attrs = node.get("attrs", {}) or {}
        return f"@{attrs.get('text') or attrs.get('displayName') or attrs.get('id') or ''}"

    if ntype == "inlineCard":
        url = (node.get("attrs") or {}).get("url", "")
        return url

    # 미지원: 자식 텍스트만 평면화
    return _inline(children) if children else ""


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
