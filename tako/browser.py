"""OS 별 URL 열기 — 종속성 없는 베스트-에포트 헬퍼.

macOS: open / Linux: xdg-open. 없으면 False (여러 개 열 땐 0) 반환.
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


def open_urls(urls: list[str]) -> int:
    """여러 URL 을 기본 브라우저 탭으로 연다. 실제로 연 개수를 돌려준다.

    macOS `open` 은 URL 을 여러 개 받으므로 한 번에 넘긴다 (탭 순서가 목록 순서와
    같아진다). Linux `xdg-open` 은 하나씩. 지원 안 되는 OS 면 0.
    """
    valid = [u for u in urls if u.startswith(("http://", "https://"))]
    if not valid:
        return 0
    if platform.system() == "Darwin":
        return len(valid) if _run(["open", *valid]) else 0
    # Linux 는 xdg-open 이 URL 을 하나씩만 받는다. 판정·Windows 거절은 open_url 에 맡긴다.
    return sum(1 for u in valid if open_url(u))


def _run(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=5)
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
