"""사용자 파일 경로 해석 공용 규칙.

config / credentials / 본문 가이드 / 슬래시 커맨드 디렉터리가 모두 같은 우선순위를 따른다:
명시 인자(--config 등) > 환경변수 > 기본 경로. 모듈마다 같은 함수를 손으로 복제하던 것을
한곳으로 모았다. 각 모듈은 자기 기본 경로와 환경변수 이름만 들고 이 함수를 부른다.
"""

from __future__ import annotations

import os
from pathlib import Path


# tako 전용 파일(config / credentials / body_guide)이 모이는 디렉터리
DEFAULT_TAKO_DIR = Path.home() / ".config" / "tako"


def resolve_override_path(
    explicit: str | os.PathLike[str] | None, *, env_var: str, default: Path
) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get(env_var)
    if env_value:
        return Path(env_value).expanduser()
    return default
