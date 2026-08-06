"""서브커맨드 공용 헬퍼.

- config/credentials 로드 실패를 사용자 안내 메시지로 바꿔 종료
  (설정만 없는 첫 실행은 TTY 한정으로 init 을 그 자리에서 제안)
- 인증 + 사이트 클라이언트 조립
- KST 시각 문자열 (미리보기 섹션 헤더 / 저장 파일명 충돌 회피)
"""

from __future__ import annotations

import sys

from .auth import Credentials, CredentialsError, load_credentials, resolve_credentials_path
from .config import (
    ConfigError,
    TakoConfig,
    first_run_guide,
    interactive_init,
    load_config,
    resolve_config_path,
)
from .jira_client import JiraSiteClient


def load_config_or_guide(path: str | None, credentials_path: str | None = None) -> TakoConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        msg = str(exc)
        if not msg.startswith("설정 파일이 없습니다"):
            sys.stderr.write(f"[config] {msg}\n")
            raise SystemExit(2)
        return _offer_init(path, credentials_path)


def _offer_init(path: str | None, credentials_path: str | None = None) -> TakoConfig:
    """설정만 없는 첫 실행 — 안내로 끝내지 않고 그 자리에서 init 을 제안한다.

    비TTY(슬래시 커맨드 · CI · 파이프)에서는 종전대로 안내만 하고 종료한다.
    자동화 흐름이 입력을 기다리며 멈추면 안 되기 때문.
    """
    # 지연 import — 정상 경로에서는 prompts 가 필요 없다.
    from .prompts import confirm, stdin_is_tty

    target = resolve_config_path(path)

    if not stdin_is_tty():
        sys.stderr.write(first_run_guide(target) + "\n")
        raise SystemExit(2)

    sys.stderr.write(f"설정 파일이 없다: {target}\n")
    if not confirm("지금 설정할까? (약 1분, Atlassian 토큰 필요)", default=True):
        sys.stderr.write("\n" + first_run_guide(target) + "\n")
        raise SystemExit(2)

    sys.stderr.write("\n")
    # --credentials 로 준 경로를 그대로 넘긴다 — 안 그러면 인증 파일만 기본 경로에 생겨
    # 바로 뒤 creds 로드가 엉뚱한 곳을 본다.
    interactive_init(target, credentials_target=resolve_credentials_path(credentials_path))

    try:
        return load_config(path)
    except ConfigError as exc:
        # init 직후인데도 못 읽으면 사용자가 고칠 수 있는 상태가 아니다 — 원인을 그대로 보여준다.
        sys.stderr.write(f"[config] 설정을 만들었지만 읽지 못했다: {exc}\n")
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
