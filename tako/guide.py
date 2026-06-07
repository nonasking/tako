"""본문 작성 가이드 로드/생성.

개인 가이드 파일(`~/.config/tako/body_guide.md`)이 본문 규칙의 단일 원본.
없으면 패키지에 동봉된 기본 가이드(templates/body_guide.md)를 사용한다.
가이드는 슬래시 커맨드(/tako, /tako-update)가 본문 초안을 만들 때 읽어 따른다.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path


DEFAULT_GUIDE_PATH = Path.home() / ".config" / "tako" / "body_guide.md"
ENV_OVERRIDE_VAR = "TAKO_GUIDE_PATH"
_PACKAGED_DEFAULT = "templates/body_guide.md"


class GuideError(Exception):
    pass


def resolve_guide_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get(ENV_OVERRIDE_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_GUIDE_PATH


def default_guide_text() -> str:
    """패키지 동봉 기본 가이드 텍스트."""
    try:
        return (
            resources.files("tako")
            .joinpath(_PACKAGED_DEFAULT)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise GuideError(f"기본 가이드를 찾을 수 없음: {_PACKAGED_DEFAULT} — {exc}") from exc


def effective_guide(path: str | os.PathLike[str] | None = None) -> tuple[str, str]:
    """실제 적용될 가이드. (텍스트, 출처) 반환. 출처는 'personal' 또는 'default'."""
    target = resolve_guide_path(path)
    if target.exists():
        try:
            return target.read_text(encoding="utf-8"), "personal"
        except OSError as exc:
            raise GuideError(f"가이드 읽기 실패: {target} — {exc}") from exc
    return default_guide_text(), "default"


def write_default_guide(
    path: str | os.PathLike[str] | None = None, *, force: bool = False
) -> Path:
    """기본 가이드를 개인 가이드 경로에 쓴다. init / reset 공용."""
    target = resolve_guide_path(path)
    if target.exists() and not force:
        raise GuideError(f"이미 있음: {target} (덮어쓰려면 --force / reset)")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(default_guide_text(), encoding="utf-8")
    return target
