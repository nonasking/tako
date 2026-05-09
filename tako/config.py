"""config.yaml 로드/검증 + init 마법사."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "tako" / "config.yaml"
ENV_OVERRIDE_VAR = "TAKO_CONFIG_PATH"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class JiraSite:
    site: str
    default_project: str
    default_issue_type: str


@dataclass(frozen=True)
class AutoFillRules:
    summary: bool = False
    description: bool = False
    labels: bool = False


@dataclass(frozen=True)
class TakoConfig:
    jira: JiraSite
    allowed_issue_types: tuple[str, ...]
    auto_fill: AutoFillRules
    epic_aliases: dict[str, str] = field(default_factory=dict)

    def resolve_epic(self, alias_or_key: str | None) -> str | None:
        if not alias_or_key:
            return None
        return self.epic_aliases.get(alias_or_key, alias_or_key)


def resolve_config_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env_value = os.environ.get(ENV_OVERRIDE_VAR)
    if env_value:
        return Path(env_value).expanduser()
    return DEFAULT_CONFIG_PATH


def load_config(path: str | os.PathLike[str] | None = None) -> TakoConfig:
    target = resolve_config_path(path)
    if not target.exists():
        raise ConfigError(f"설정 파일이 없습니다: {target}\n`tako init` 또는 수동 작성 필요.")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML 파싱 실패: {target} — {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"최상위가 매핑이 아님: {target}")
    return _build_config(raw)


def _build_config(raw: dict[str, Any]) -> TakoConfig:
    jira_raw = raw.get("jira")
    if not isinstance(jira_raw, dict):
        raise ConfigError("jira 블록 누락 또는 형식 틀림.")

    site = _require_str(jira_raw, "jira.site")
    default_project = _require_str(jira_raw, "jira.default_project")
    default_issue_type = _require_str(jira_raw, "jira.default_issue_type")

    issue_types_raw = raw.get("issue_types") or {}
    if not isinstance(issue_types_raw, dict):
        raise ConfigError("issue_types 가 매핑이 아님.")
    allowed = tuple(issue_types_raw.keys())

    if allowed and default_issue_type not in allowed:
        raise ConfigError(
            f"default_issue_type({default_issue_type!r}) 가 issue_types 에 없음. "
            f"정의된 타입: {', '.join(allowed) or '(없음)'}"
        )

    auto_fill_raw = raw.get("auto_fill_from_session") or {}
    if not isinstance(auto_fill_raw, dict):
        raise ConfigError("auto_fill_from_session 이 매핑이 아님.")
    auto_fill = AutoFillRules(
        summary=bool(auto_fill_raw.get("summary", False)),
        description=bool(auto_fill_raw.get("description", False)),
        labels=bool(auto_fill_raw.get("labels", False)),
    )

    epic_aliases_raw = raw.get("epic_aliases") or {}
    if not isinstance(epic_aliases_raw, dict):
        raise ConfigError("epic_aliases 가 매핑이 아님.")
    epic_aliases: dict[str, str] = {}
    for alias, value in epic_aliases_raw.items():
        if not isinstance(value, str):
            raise ConfigError(f"epic_aliases.{alias} 값이 문자열이 아님.")
        epic_aliases[str(alias)] = value

    return TakoConfig(
        jira=JiraSite(
            site=site,
            default_project=default_project,
            default_issue_type=default_issue_type,
        ),
        allowed_issue_types=allowed,
        auto_fill=auto_fill,
        epic_aliases=epic_aliases,
    )


def _require_str(d: dict[str, Any], dotted_key: str) -> str:
    leaf = dotted_key.split(".")[-1]
    val = d.get(leaf)
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"{dotted_key} 비었음.")
    return val.strip()


def first_run_guide(target_path: Path | None = None) -> str:
    target = target_path or DEFAULT_CONFIG_PATH
    return (
        "tako 처음 사용 — 설정 파일이 없음.\n"
        "\n"
        "  방법 1) 자동 (추천):\n"
        "     tako init\n"
        "\n"
        "  방법 2) 수동:\n"
        f"     mkdir -p {target.parent} && cp config.example.yaml {target}\n"
        "     그 후 jira.site / default_project / default_issue_type 값을 본인 환경에 맞게 수정.\n"
        "\n"
        "issue_types 는 키 이름만 정의해도 v1 에서 동작.\n"
        "다른 경로 쓰려면 TAKO_CONFIG_PATH 환경변수로 지정 가능."
    )


def interactive_init(
    target: Path | None = None,
    *,
    force: bool = False,
    credentials_target: Path | None = None,
) -> tuple[Path, Path]:
    # 지연 import — config 가 평소엔 prompts/auth 에 의존하지 않음
    from .auth import Credentials, resolve_credentials_path, write_credentials
    from .prompts import ask_secret, ask_text, confirm, stdin_is_tty

    path = (target or resolve_config_path()).expanduser()
    creds_path = (credentials_target or resolve_credentials_path()).expanduser()

    if not stdin_is_tty():
        sys.stderr.write("init 은 TTY 필요.\n")
        raise SystemExit(2)

    if path.exists() and not force:
        sys.stderr.write(f"이미 있음: {path}\n")
        if not confirm("덮어쓸까?", default=False):
            sys.stderr.write("취소.\n")
            raise SystemExit(1)
    if creds_path.exists() and not force:
        sys.stderr.write(f"이미 있음: {creds_path}\n")
        if not confirm("덮어쓸까?", default=False):
            sys.stderr.write("취소.\n")
            raise SystemExit(1)

    sys.stderr.write("tako init — 5개 항목 입력.\n\n")

    site = ask_text("Atlassian 도메인 (예: mycompany.atlassian.net)")
    project = ask_text("기본 프로젝트 키 (예: WL)")
    issue_type = ask_text("기본 이슈 타입 (예: Task, 기능변경)")
    email = ask_text("Atlassian 이메일")
    token = ask_secret(
        "Atlassian API 토큰 "
        "(https://id.atlassian.com/manage-profile/security/api-tokens)"
    )

    cfg_dict = {
        "jira": {
            "site": site,
            "default_project": project,
            "default_issue_type": issue_type,
        },
        "issue_types": {issue_type: {}},
        "auto_fill_from_session": {
            "summary": True,
            "description": True,
            "labels": False,
        },
        "epic_aliases": {},
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cfg_dict, allow_unicode=True, sort_keys=False, indent=2),
        encoding="utf-8",
    )
    write_credentials(Credentials(email=email, api_token=token), creds_path)
    sys.stderr.write(
        f"\nconfig: {path}\n"
        f"creds:  {creds_path} (chmod 0600)\n"
        "이슈 타입 추가 / epic 별칭 등록은 config 직접 편집.\n"
    )
    return path, creds_path
