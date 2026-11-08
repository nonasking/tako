"""초안 파일 — 미리보기 단계에서 에디터로 직접 고치는 왕복 통로.

포맷: YAML frontmatter(필드) + 마크다운 본문. git commit 편집과 같은 멘탈 모델.
파일을 저장하고 닫으면 다시 파싱 → 검증 → 미리보기로 돌아온다.

이슈 유형은 여기 안 실린다 — 생성 후에도 못 바꾸는 필드라 초안에서도 잠근다.
파일에 issue_type 키가 보이면 파싱 단계에서 거부한다.
"""

from __future__ import annotations

import datetime
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .issue_draft import DEFAULT_LINK_TYPE


FALLBACK_EDITOR = "vi"

# frontmatter 에 올 수 있는 키 전체. 이 밖의 키는 오타로 보고 거부한다 —
# 조용히 무시하면 사용자는 반영됐다고 믿는다.
EDITABLE_KEYS = (
    "project",
    "summary",
    "parent",
    "labels",
    "assignee",
    "reporter",
    "story_points",
    "duedate",
    "links",
)


class DraftFileError(Exception):
    pass


def resolve_editor(configured: str | None) -> list[str]:
    """에디터 명령 해석: config editor → $EDITOR → $VISUAL → vi.

    'code --wait' 처럼 인자 딸린 명령도 받는다.
    """
    for candidate in (configured, os.environ.get("EDITOR"), os.environ.get("VISUAL")):
        if candidate and candidate.strip():
            return shlex.split(candidate)
    return [FALLBACK_EDITOR]


def _dump_value(key: str, value: Any) -> str:
    # width 를 크게 — 긴 제목이 접혀 들어가면 사용자가 들여쓰기를 깨기 쉽다.
    text = yaml.safe_dump(
        {key: value}, allow_unicode=True, sort_keys=False, default_flow_style=True, width=10**6
    )
    return text.strip().removeprefix("{").removesuffix("}")


def render_draft(payload: dict[str, Any]) -> str:
    """payload dict → 초안 파일 텍스트.

    값이 있는 키만 싣는다. 없는 선택 키는 헤더 주석의 목록으로 안내 —
    빈 키를 줄줄이 깔면 정작 고칠 내용이 묻힌다.
    """
    keys_help = ", ".join(EDITABLE_KEYS)
    lines = [
        "---",
        "# tako 초안 — 저장하고 닫으면 미리보기로 돌아간다.",
        f"# 이슈 유형: {payload.get('issue_type', '?')} (여기서는 변경 불가)",
        f"# 쓸 수 있는 키: {keys_help}",
        '#   (예: links: [WL-100, "WL-200:Blocks"])',
    ]
    for key in EDITABLE_KEYS:
        source_key = "parent_epic" if key == "parent" else key
        value = payload.get(source_key)
        if key == "assignee" and not value:
            # 아직 해석 안 된 입력(me/이메일)이 남아 있으면 그걸 보여준다.
            value = payload.get("assignee_pending")
        if key == "reporter" and not value:
            value = payload.get("reporter_pending")
        if value is None or value == [] or value == "":
            continue
        if key == "links":
            value = [t if n == DEFAULT_LINK_TYPE else f"{t}:{n}" for t, n in _norm_links(value)]
        line = _dump_value(key, value)
        if key in ("assignee", "reporter") and payload.get(f"{key}_label"):
            # accountId 만 남으면 누군지 못 알아본다 — 표시 라벨을 주석으로 병기.
            line += f"  # {payload[f'{key}_label']}"
        lines.append(line)
    lines.append("---")
    body = payload.get("description") or ""
    return "\n".join(lines) + "\n" + body + ("\n" if body and not body.endswith("\n") else "")


def _norm_links(value: Any) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for item in value or []:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            out.append((str(item[0]), str(item[1])))
        elif isinstance(item, str):
            target, _, type_name = item.partition(":")
            out.append((target, type_name or DEFAULT_LINK_TYPE))
    return out


def parse_draft(text: str) -> tuple[dict[str, Any], str]:
    """초안 파일 텍스트 → (frontmatter dict, 본문).

    실패는 전부 DraftFileError — 호출자가 메시지 보여주고 같은 파일을 다시 연다.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DraftFileError("첫 줄이 '---' 가 아님. frontmatter(--- ... ---) 형태 유지 필요.")
    close_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            close_idx = i
            break
    if close_idx is None:
        raise DraftFileError("frontmatter 닫는 '---' 가 없음.")

    front_text = "\n".join(lines[1:close_idx])
    try:
        fields = yaml.safe_load(front_text) or {}
    except yaml.YAMLError as exc:
        raise DraftFileError(f"frontmatter YAML 파싱 실패: {exc}")
    if not isinstance(fields, dict):
        raise DraftFileError("frontmatter 는 '키: 값' 매핑이어야 함.")

    if "issue_type" in fields:
        raise DraftFileError("이슈 유형은 초안에서 못 바꾼다 (생성 후에도 변경 불가). issue_type 줄 삭제 필요.")
    unknown = [k for k in fields if k not in EDITABLE_KEYS]
    if unknown:
        raise DraftFileError(
            f"모르는 키: {', '.join(str(k) for k in unknown)}. "
            f"쓸 수 있는 키: {', '.join(EDITABLE_KEYS)}"
        )

    normalized: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, datetime.date):
            # YAML 은 따옴표 없는 2026-08-25 를 date 로 읽는다 — 문자열로 되돌림.
            value = value.isoformat()
        if key == "labels":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise DraftFileError("labels 는 문자열 리스트여야 함. 예: labels: [backend, urgent]")
        if key == "links" and isinstance(value, str):
            value = [value]
        normalized[key] = value

    body = "\n".join(lines[close_idx + 1 :])
    return normalized, body.strip("\n")


def open_in_editor(path: Path, editor_cmd: list[str]) -> None:
    """에디터를 열고 닫힐 때까지 기다린다. 비정상 종료면 DraftFileError."""
    try:
        result = subprocess.run(editor_cmd + [str(path)])
    except FileNotFoundError:
        raise DraftFileError(
            f"에디터 실행 실패: {' '.join(editor_cmd)!r} — "
            "config 의 editor 또는 $EDITOR 확인 필요."
        )
    if result.returncode != 0:
        raise DraftFileError(f"에디터가 오류로 종료됨 (exit {result.returncode}).")


def new_draft_path() -> Path:
    fd, name = tempfile.mkstemp(prefix="tako-draft-", suffix=".md")
    os.close(fd)
    return Path(name)
