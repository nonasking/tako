"""credentials.json (이메일 + API 토큰) 로드/저장.

인증과 설정을 분리해서 보관 — 토큰 파일만 chmod 0600.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import DEFAULT_TAKO_DIR, resolve_override_path


DEFAULT_CREDENTIALS_PATH = DEFAULT_TAKO_DIR / "credentials.json"
ENV_OVERRIDE_VAR = "TAKO_CREDENTIALS_PATH"


class CredentialsError(Exception):
    pass


@dataclass(frozen=True)
class Credentials:
    email: str
    api_token: str

    def as_basic_auth(self) -> tuple[str, str]:
        return (self.email, self.api_token)


def resolve_credentials_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    return resolve_override_path(explicit, env_var=ENV_OVERRIDE_VAR, default=DEFAULT_CREDENTIALS_PATH)


def load_credentials(path: str | os.PathLike[str] | None = None) -> Credentials:
    target = resolve_credentials_path(path)
    if not target.exists():
        raise CredentialsError(f"creds 없음: {target}\n`tako init` 먼저 돌려.")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"creds JSON 파싱 실패: {target} — {exc}") from exc
    if not isinstance(raw, dict):
        raise CredentialsError(f"creds 최상위가 객체가 아님: {target}")

    email = raw.get("email")
    token = raw.get("api_token")
    if not isinstance(email, str) or "@" not in email:
        raise CredentialsError("email 비었거나 형식 틀림.")
    if not isinstance(token, str) or not token.strip():
        raise CredentialsError("api_token 비었음.")
    return Credentials(email=email.strip(), api_token=token.strip())


def write_credentials(creds: Credentials, path: str | os.PathLike[str] | None = None) -> Path:
    target = resolve_credentials_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"email": creds.email, "api_token": creds.api_token}
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    # 처음부터 0600으로 만든다. write 후 chmod 하면 잠깐 0644(umask 기준)로 존재한다.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(data)
    try:
        # 이미 있던 파일은 O_CREAT 모드가 적용되지 않으므로 한 번 더 맞춘다.
        target.chmod(0o600)
    except OSError as exc:
        sys.stderr.write(f"[경고] {target} chmod 실패: {exc}\n")
    return target
