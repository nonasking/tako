"""Claude Code 슬래시 커맨드 설치.

패키지에 동봉된 `commands/*.md` 를 `~/.claude/commands/` 로 **복사**한다.

심볼릭 링크가 아니라 복사인 이유: PyPI 로 설치하면 원본이 uv 의 도구 환경 안에 있어
패키지를 올리거나 지우는 순간 링크가 끊긴다. 저장소를 받아 쓰는 개발용 링크 방식은
install.sh 가 그대로 담당한다.

업그레이드 후 최신 커맨드로 갱신하려면 `tako slash install --force`.
"""

from __future__ import annotations

import os
from importlib import resources
from pathlib import Path


DEFAULT_COMMANDS_DIR = Path.home() / ".claude" / "commands"
ENV_OVERRIDE_VAR = "TAKO_COMMANDS_DIR"
_PACKAGED_DIR = "commands"


class SlashError(Exception):
    pass


def resolve_commands_dir(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get(ENV_OVERRIDE_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_COMMANDS_DIR


def packaged_commands() -> list[tuple[str, str]]:
    """동봉된 슬래시 커맨드. [(파일명, 내용)] — 파일명 오름차순."""
    try:
        root = resources.files("tako").joinpath(_PACKAGED_DIR)
        found = [
            (entry.name, entry.read_text(encoding="utf-8"))
            for entry in root.iterdir()
            if entry.name.endswith(".md")
        ]
    except (FileNotFoundError, ModuleNotFoundError, NotADirectoryError) as exc:
        raise SlashError(f"동봉된 슬래시 커맨드를 찾을 수 없음: {_PACKAGED_DIR} — {exc}") from exc

    if not found:
        raise SlashError(f"동봉된 슬래시 커맨드가 비어 있음: {_PACKAGED_DIR}")
    return sorted(found)


def install_commands(
    target_dir: str | os.PathLike[str] | None = None, *, force: bool = False
) -> tuple[list[str], list[str]]:
    """커맨드 파일을 target_dir 에 쓴다. (쓴 것, 건너뛴 것) 반환.

    이미 있는 파일은 기본적으로 건드리지 않는다 — 사용자가 손봤을 수 있다.
    force=True 면 덮어쓴다.
    """
    target = resolve_commands_dir(target_dir)
    entries = packaged_commands()

    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SlashError(f"디렉터리를 만들 수 없음: {target} — {exc}") from exc

    written: list[str] = []
    skipped: list[str] = []
    for name, text in entries:
        dest = target / name
        # 심볼릭 링크는 예전 install.sh 가 만든 것 — 덮어쓰기 대상으로 본다.
        if dest.exists() and not force:
            skipped.append(name)
            continue
        try:
            if dest.is_symlink():
                dest.unlink()
            dest.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise SlashError(f"쓰기 실패: {dest} — {exc}") from exc
        written.append(name)

    return written, skipped
