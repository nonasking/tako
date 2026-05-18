"""OS 별 클립보드 복사 — 종속성 없는 베스트-에포트 헬퍼.

macOS: pbcopy / Linux: xclip → xsel. 둘 다 없으면 False 반환.
실패해도 메인 흐름은 진행 — 어디까지나 편의 기능.
"""

from __future__ import annotations

import platform
import shutil
import subprocess


def copy_to_clipboard(text: str) -> bool:
    """text 를 시스템 클립보드에 복사. 성공 True / 도구 없음·실패 False."""
    if not text:
        return False
    system = platform.system()
    if system == "Darwin":
        return _run(["pbcopy"], text)
    if system == "Linux":
        if shutil.which("xclip"):
            return _run(["xclip", "-selection", "clipboard"], text)
        if shutil.which("xsel"):
            return _run(["xsel", "--clipboard", "--input"], text)
        return False
    # Windows / 기타 — v1.x 미지원
    return False


def _run(cmd: list[str], text: str) -> bool:
    try:
        proc = subprocess.run(
            cmd, input=text, text=True, capture_output=True, timeout=2
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
