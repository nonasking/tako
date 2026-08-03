"""콘솔 입력 / Y/N 확인."""

from __future__ import annotations

import sys
from typing import Iterable


def _prompt(text: str) -> str:
    sys.stderr.write(text)
    sys.stderr.flush()
    line = sys.stdin.readline()
    if line == "":
        # EOF (Ctrl+D) — 빈 줄과 구분해 즉시 중단. 안 그러면 default 없는
        # ask_text 의 재시도 루프가 "(빈 입력 안 됨)" 을 무한 출력한다.
        sys.stderr.write("\n입력 종료(EOF) — 취소.\n")
        raise SystemExit(1)
    return line.rstrip("\n")


def ask_text(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = _prompt(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        sys.stderr.write("(빈 입력 안 됨)\n")


def ask_secret(prompt: str) -> str:
    import getpass
    while True:
        try:
            value = getpass.getpass(f"{prompt}: ").strip()
        except EOFError:
            # getpass 는 EOF 를 예외로 알린다 — _prompt 와 같은 방식으로 중단.
            sys.stderr.write("\n입력 종료(EOF) — 취소.\n")
            raise SystemExit(1)
        if value:
            return value
        sys.stderr.write("(빈 입력 안 됨)\n")


def ask_choice(prompt: str, choices: Iterable[str], default: str | None = None) -> str:
    options = list(choices)
    if not options:
        raise ValueError("선택지 없음.")
    listing = " / ".join(options)
    while True:
        value = ask_text(f"{prompt} ({listing})", default=default)
        if value in options:
            return value
        sys.stderr.write(f"'{value}' 은 선택지에 없음.\n")


def ask_multiline(prompt: str) -> str:
    sys.stderr.write(f"{prompt} (Ctrl+D 로 종료)\n")
    sys.stderr.flush()
    return sys.stdin.read().rstrip("\n")


def confirm(prompt: str, default: bool = True) -> bool:
    suffix = "(Y/n)" if default else "(y/N)"
    raw = _prompt(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "예", "ㅇ"}


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()
