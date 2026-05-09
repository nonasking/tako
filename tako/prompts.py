"""콘솔 입력 / Y/N 확인."""

from __future__ import annotations

import sys
from typing import Iterable


def _prompt(text: str) -> str:
    sys.stderr.write(text)
    sys.stderr.flush()
    line = sys.stdin.readline()
    return line.rstrip("\n") if line else ""


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
        value = getpass.getpass(f"{prompt}: ").strip()
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
