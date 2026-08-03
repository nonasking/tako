"""서브커맨드 공용 헬퍼.

- config/credentials 로드 실패를 사용자 안내 메시지로 바꿔 종료
- 인증 + 사이트 클라이언트 조립
- KST 시각 문자열 (미리보기 섹션 헤더 / 저장 파일명 충돌 회피)
"""

from __future__ import annotations

import sys

from .auth import Credentials, CredentialsError, load_credentials
from .config import ConfigError, TakoConfig, first_run_guide, load_config, resolve_config_path
from .jira_client import JiraSiteClient


def load_config_or_guide(path: str | None) -> TakoConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        msg = str(exc)
        if msg.startswith("설정 파일이 없습니다"):
            sys.stderr.write(first_run_guide(resolve_config_path(path)) + "\n")
            raise SystemExit(2)
        sys.stderr.write(f"[config] {msg}\n")
        raise SystemExit(2)


def load_credentials_or_guide(path: str | None) -> Credentials:
    try:
        return load_credentials(path)
    except CredentialsError as exc:
        sys.stderr.write(f"[creds] {exc}\n")
        raise SystemExit(2)


def build_client(cfg: TakoConfig, credentials_path: str | None) -> JiraSiteClient:
    """creds 로드(실패 시 안내 후 종료) + 사이트 클라이언트 생성."""
    return JiraSiteClient(site=cfg.jira.site, creds=load_credentials_or_guide(credentials_path))


def today_kst_str() -> str:
    """KST(UTC+9) 기준 YYYY-MM-DD."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).strftime("%Y-%m-%d")


def stamp_kst_str() -> str:
    """KST(UTC+9) 기준 YYYY-MM-DD_HHMMSS. 저장 파일명 충돌 회피용."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).strftime("%Y-%m-%d_%H%M%S")
