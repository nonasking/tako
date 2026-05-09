"""tako 진입점.

서브커맨드: init / new / preview / build / interactive.
new 가 일상 사용. preview/build 는 슬래시 커맨드 본문 또는 디버깅용.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import __version__
from .auth import CredentialsError, load_credentials, resolve_credentials_path
from .config import (
    ConfigError,
    TakoConfig,
    first_run_guide,
    interactive_init,
    load_config,
    resolve_config_path,
)
from .issue_draft import DraftError, IssueDraft, build_payload, render_preview
from .jira_client import JiraApiError, JiraSiteClient, markdown_to_adf
from .prompts import ask_choice, ask_multiline, ask_text, confirm, stdin_is_tty


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tako",
        description="Jira 티켓 생성 (Atlassian Cloud REST v3)",
    )
    parser.add_argument("--version", action="version", version=f"tako {__version__}")
    parser.add_argument("--config", help="설정 파일 경로")
    parser.add_argument("--credentials", help="인증 파일 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="대화형 마법사 (config + creds 작성)")
    init_parser.add_argument("--force", action="store_true", help="기존 파일 덮어쓰기")

    new_parser = sub.add_parser("new", help="이슈 생성 (REST 호출)")
    new_parser.add_argument("--project")
    new_parser.add_argument("--issue-type", dest="issue_type")
    new_parser.add_argument("--summary")
    new_parser.add_argument("--description")
    new_parser.add_argument("--parent")
    new_parser.add_argument("--label", action="append", default=[], help="라벨 (반복 가능)")
    new_parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 바로 생성")

    sub.add_parser("preview", help="stdin JSON → 미리보기 stdout")
    sub.add_parser("build", help="stdin JSON → 페이로드 JSON stdout")
    sub.add_parser("interactive", help="TTY 인터랙티브 → 페이로드 JSON stdout")
    return parser.parse_args(argv)


def _load_or_guide(path: str | None) -> TakoConfig:
    try:
        return load_config(path)
    except ConfigError as exc:
        msg = str(exc)
        if msg.startswith("설정 파일이 없습니다"):
            sys.stderr.write(first_run_guide(resolve_config_path(path)) + "\n")
            raise SystemExit(2)
        sys.stderr.write(f"[config] {msg}\n")
        raise SystemExit(2)


def _load_credentials_or_guide(path: str | None) -> Any:
    try:
        return load_credentials(path)
    except CredentialsError as exc:
        sys.stderr.write(f"[creds] {exc}\n")
        raise SystemExit(2)


def _read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("stdin 비었음. `... | tako preview` 형태로 호출.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"[input] JSON 파싱 실패: {exc}\n")
        raise SystemExit(2)
    if not isinstance(data, dict):
        sys.stderr.write("[input] JSON 최상위는 객체.\n")
        raise SystemExit(2)
    return data


def _draft_from_dict(data: dict[str, Any], cfg: TakoConfig) -> IssueDraft:
    payload = dict(data)
    payload.setdefault("project", cfg.jira.default_project)
    payload.setdefault("issue_type", cfg.jira.default_issue_type)
    parent = payload.get("parent_epic") or payload.get("parent")
    if parent:
        payload["parent_epic"] = cfg.resolve_epic(parent)

    if cfg.allowed_issue_types and payload["issue_type"] not in cfg.allowed_issue_types:
        sys.stderr.write(
            f"[input] 허용 안 된 이슈 타입: {payload['issue_type']!r}. "
            f"허용: {', '.join(cfg.allowed_issue_types)}\n"
        )
        raise SystemExit(2)

    try:
        return IssueDraft.from_payload(payload)
    except DraftError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        raise SystemExit(2)


def _collect_interactively(cfg: TakoConfig, *, prefilled: dict[str, Any] | None = None) -> IssueDraft:
    sys.stderr.write(f"tako 입력 모드 — 사이트 {cfg.jira.site}\n")
    pre = prefilled or {}

    project = pre.get("project") or ask_text("프로젝트 키", default=cfg.jira.default_project)
    if "issue_type" in pre and pre["issue_type"]:
        issue_type = pre["issue_type"]
    elif cfg.allowed_issue_types:
        issue_type = ask_choice("이슈 유형", cfg.allowed_issue_types, default=cfg.jira.default_issue_type)
    else:
        issue_type = ask_text("이슈 유형", default=cfg.jira.default_issue_type)

    summary = pre.get("summary") or ask_text("제목")
    description = pre.get("description")
    if not description:
        description = ask_multiline("본문(마크다운)")
    parent_input = pre.get("parent_epic")
    if parent_input is None:
        parent_input = ask_text("부모 (별칭/키, 없으면 Enter)", default="").strip()
    labels: list[str] = list(pre.get("labels") or [])

    payload: dict[str, Any] = {
        "project": project,
        "issue_type": issue_type,
        "summary": summary,
        "description": description,
        "labels": labels,
    }
    resolved_parent = cfg.resolve_epic(parent_input) if parent_input else None
    if resolved_parent:
        payload["parent_epic"] = resolved_parent

    try:
        return IssueDraft.from_payload(payload)
    except DraftError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        raise SystemExit(2)


def _cmd_preview(cfg: TakoConfig) -> int:
    draft = _draft_from_dict(_read_stdin_json(), cfg)
    print(render_preview(draft))
    return 0


def _cmd_build(cfg: TakoConfig) -> int:
    draft = _draft_from_dict(_read_stdin_json(), cfg)
    print(json.dumps(build_payload(draft), ensure_ascii=False, indent=2))
    return 0


def _cmd_interactive(cfg: TakoConfig) -> int:
    if not stdin_is_tty():
        sys.stderr.write("interactive 는 TTY 필요. preview/build 사용.\n")
        return 2
    draft = _collect_interactively(cfg)
    sys.stderr.write("\n")
    print(render_preview(draft))
    sys.stderr.write("\n")
    if not confirm("페이로드 출력?"):
        sys.stderr.write("취소.\n")
        return 1
    print(json.dumps(build_payload(draft), ensure_ascii=False, indent=2))
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    interactive_init(
        target=resolve_config_path(args.config),
        credentials_target=resolve_credentials_path(args.credentials),
        force=args.force,
    )
    return 0


def _cmd_new(args: argparse.Namespace, cfg: TakoConfig) -> int:
    creds = _load_credentials_or_guide(args.credentials)

    prefilled: dict[str, Any] = {}
    if args.project:
        prefilled["project"] = args.project
    if args.issue_type:
        prefilled["issue_type"] = args.issue_type
    if args.summary:
        prefilled["summary"] = args.summary
    if args.description is not None:
        prefilled["description"] = args.description
    if args.parent:
        prefilled["parent_epic"] = args.parent
    if args.label:
        prefilled["labels"] = list(args.label)

    needs_interactive = not (args.summary and args.description is not None)
    if needs_interactive and not stdin_is_tty():
        sys.stderr.write(
            "--summary / --description 없고 TTY 도 아님. 인자 모두 명시 또는 셸에서 직접 호출.\n"
        )
        return 2

    draft = _collect_interactively(cfg, prefilled=prefilled)
    sys.stderr.write("\n")
    print(render_preview(draft), file=sys.stderr)
    sys.stderr.write("\n")

    if not args.yes:
        if not confirm("Jira 에 생성?"):
            sys.stderr.write("취소.\n")
            return 1

    fields = build_payload(draft)["payload"]["fields"]
    fields["description"] = markdown_to_adf(draft.description)

    client = JiraSiteClient(site=cfg.jira.site, creds=creds)
    try:
        result = client.create_issue(fields)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    sys.stderr.write(f"\n생성 완료\n  키:   {result.key}\n  링크: {result.url}\n")
    print(result.key)  # stdout 에는 키만
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "init":
        return _cmd_init(args)
    cfg = _load_or_guide(args.config)
    match args.command:
        case "new":
            return _cmd_new(args, cfg)
        case "preview":
            return _cmd_preview(cfg)
        case "build":
            return _cmd_build(cfg)
        case "interactive":
            return _cmd_interactive(cfg)
        case _:
            sys.stderr.write(f"unknown command: {args.command}\n")
            return 2


if __name__ == "__main__":
    raise SystemExit(run())
