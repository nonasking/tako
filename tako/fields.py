"""custom field 매핑 헬퍼.

v1.x 는 `story_points` 만 지원. 새 매핑 이름 추가는 SEARCH_KEYWORDS 에 등록만 하면 됨.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


# name → (필수 포함 키워드 (lowercase, 모두 매칭), 제외 키워드 (lowercase, 하나라도 매칭되면 거름))
SEARCH_KEYWORDS: dict[str, tuple[list[str], list[str]]] = {
    "story_points": (["story", "point"], ["ai", "original"]),
}


def filter_candidates(
    name: str,
    all_fields: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """name 에 매칭되는 (field_id, field_name) 후보 목록.

    매칭 규칙:
      - SEARCH_KEYWORDS[name] 의 모든 포함 키워드를 가짐 (lowercase)
      - 제외 키워드 하나라도 매칭되면 거름
    SEARCH_KEYWORDS 에 없는 name 은 ValueError.
    """
    if name not in SEARCH_KEYWORDS:
        raise ValueError(f"지원하지 않는 매핑 이름: {name!r}. 지원 목록: {', '.join(SEARCH_KEYWORDS)}")
    includes, excludes = SEARCH_KEYWORDS[name]

    out: list[tuple[str, str]] = []
    for f in all_fields:
        fname = (f.get("name") or "").lower()
        fid = f.get("id")
        if not isinstance(fid, str):
            continue
        if not all(kw in fname for kw in includes):
            continue
        if any(ex in fname for ex in excludes):
            continue
        out.append((fid, f.get("name") or fid))
    return out


def write_field_mapping(name: str, field_id: str, config_path: Path) -> None:
    """config.yaml 의 jira.fields.<name> = field_id 등록.

    yaml 자동 재생성이라 *기존 주석·들여쓰기·키 순서가 사라진다*. 호출 전 호출자가 사용자에게 경고할 책임.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"설정 파일 없음: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config 최상위가 매핑이 아님: {config_path}")

    jira_block = raw.setdefault("jira", {})
    if not isinstance(jira_block, dict):
        raise ValueError("jira 블록이 매핑이 아님.")
    fields_block = jira_block.setdefault("fields", {})
    if not isinstance(fields_block, dict):
        raise ValueError("jira.fields 가 매핑이 아님.")
    fields_block[name] = field_id

    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )


def warn_comment_loss() -> None:
    sys.stderr.write(
        "[주의] config.yaml 을 자동으로 다시 씁니다. 기존 주석·들여쓰기·키 순서가 사라질 수 있습니다.\n"
    )
