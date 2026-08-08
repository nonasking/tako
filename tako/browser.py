"""OS 별 URL 열기 — 종속성 없는 베스트-에포트 헬퍼.

macOS: open / Linux: xdg-open. 없으면 False 반환.
실패해도 메인 흐름은 진행 — 사용자는 URL 을 직접 복사해 열면 된다.
"""

from __future__ import annotations

import platform
import shutil
import subprocess


def open_url(url: str) -> bool:
    """기본 브라우저로 url 을 연다. 성공 True / 도구 없음·실패 False."""
    if not url.startswith(("http://", "https://")):
        # 로컬 파일·커스텀 스킴은 이 헬퍼의 책임 밖 — 조용히 거절한다.
        return False
    system = platform.system()
    if system == "Darwin":
        return _run(["open", url])
    if system == "Linux":
        if shutil.which("xdg-open"):
            return _run(["xdg-open", url])
        return False
    # Windows / 기타 — clipboard.py 와 같은 범위를 따른다.
    return False


def _run(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=5)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
