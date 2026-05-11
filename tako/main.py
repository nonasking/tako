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
from .fields import (
    SEARCH_KEYWORDS,
    filter_candidates,
    warn_comment_loss,
    write_field_mapping,
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
    new_parser.add_argument("--story-points", dest="story_points", type=int, help="스토리포인트 (정수)")
    new_parser.add_argument("--duedate", help="기한 YYYY-MM-DD")
    new_parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 바로 생성")

    sub.add_parser("preview", help="stdin JSON → 미리보기 stdout")
    sub.add_parser("build", help="stdin JSON → 페이로드 JSON stdout")
    sub.add_parser("interactive", help="TTY 인터랙티브 → 페이로드 JSON stdout")

    fields_parser = sub.add_parser("fields", help="custom field 매핑 관리")
    fields_sub = fields_parser.add_subparsers(dest="fields_command", required=True)

    set_parser = fields_sub.add_parser("set", help="필드 매핑 등록 (config 자동 쓰기)")
    set_parser.add_argument("name", help=f"필드 이름. 지원: {', '.join(SEARCH_KEYWORDS)}")
    set_parser.add_argument("field_id", help="customfield ID (예: customfield_10016)")

    detect_parser = fields_sub.add_parser(
        "detect", help="Jira API 자동 조회로 필드 후보 찾기"
    )
    detect_parser.add_argument("name", help=f"필드 이름. 지원: {', '.join(SEARCH_KEYWORDS)}")
    detect_parser.add_argument(
        "--save", action="store_true", help="찾은 결과를 config 에 자동 쓰기 (주석 손실 주의)"
    )

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
    # 별칭: --due 가 들어와도 받기 (사용자 친화)
    if "due" in payload and "duedate" not in payload:
        payload["duedate"] = payload["due"]

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

    # optional 영역
    story_points = pre.get("story_points")
    if story_points is None:
        sp_raw = ask_text("스토리포인트 (정수, 없으면 Enter)", default="").strip()
        story_points = sp_raw if sp_raw else None
    duedate = pre.get("duedate")
    if duedate is None:
        due_raw = ask_text("기한 YYYY-MM-DD (없으면 Enter)", default="").strip()
        duedate = due_raw if due_raw else None

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
    if story_points is not None:
        payload["story_points"] = story_points
    if duedate is not None:
        payload["duedate"] = duedate

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
    print(json.dumps(build_payload(draft, cfg.jira.custom_fields), ensure_ascii=False, indent=2))
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
    print(json.dumps(build_payload(draft, cfg.jira.custom_fields), ensure_ascii=False, indent=2))
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
    if args.story_points is not None:
        prefilled["story_points"] = args.story_points
    if args.duedate:
        prefilled["duedate"] = args.duedate

    needs_interactive = not (args.summary and args.description is not None)
    if needs_interactive and not stdin_is_tty():
        sys.stderr.write(
            "--summary / --description 없고 TTY 도 아님. 인자 모두 명시 또는 셸에서 직접 호출.\n"
        )
        return 2

    if needs_interactive:
        draft = _collect_interactively(cfg, prefilled=prefilled)
    else:
        # 완전 자동 모드: prefilled 만으로 draft. optional 항목 묻지 않음.
        prefilled.setdefault("project", cfg.jira.default_project)
        prefilled.setdefault("issue_type", cfg.jira.default_issue_type)
        if "parent_epic" in prefilled:
            prefilled["parent_epic"] = cfg.resolve_epic(prefilled["parent_epic"])
        try:
            draft = IssueDraft.from_payload(prefilled)
        except DraftError as exc:
            sys.stderr.write(f"[input] {exc}\n")
            return 2

    sys.stderr.write("\n")
    print(render_preview(draft), file=sys.stderr)
    sys.stderr.write("\n")

    if not args.yes:
        if not confirm("Jira 에 생성?"):
            sys.stderr.write("취소.\n")
            return 1

    built = build_payload(draft, cfg.jira.custom_fields)
    for w in built["meta"].get("warnings", []):
        sys.stderr.write(f"[경고] {w}\n")
    fields = built["payload"]["fields"]
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


def _cmd_fields_set(args: argparse.Namespace) -> int:
    if args.name not in SEARCH_KEYWORDS:
        sys.stderr.write(
            f"[fields] 지원하지 않는 이름: {args.name!r}. 지원: {', '.join(SEARCH_KEYWORDS)}\n"
        )
        return 2
    if not args.field_id.startswith("customfield_"):
        sys.stderr.write(
            f"[경고] field_id 가 'customfield_' 로 시작하지 않습니다: {args.field_id!r}\n"
        )
    config_path = resolve_config_path(args.config)
    warn_comment_loss()
    try:
        write_field_mapping(args.name, args.field_id, config_path)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"[fields] {exc}\n")
        return 2
    sys.stderr.write(f"등록 완료: jira.fields.{args.name} = {args.field_id}\n")
    return 0


def _cmd_fields_detect(args: argparse.Namespace, cfg: TakoConfig) -> int:
    if args.name not in SEARCH_KEYWORDS:
        sys.stderr.write(
            f"[fields] 지원하지 않는 이름: {args.name!r}. 지원: {', '.join(SEARCH_KEYWORDS)}\n"
        )
        return 2
    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)
    sys.stderr.write(f"{cfg.jira.site} 에서 필드 목록 조회 중...\n")
    try:
        all_fields = client.list_fields()
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    candidates = filter_candidates(args.name, all_fields)
    if not candidates:
        sys.stderr.write(f"매칭되는 필드 없음 ({args.name}).\n")
        return 1

    sys.stderr.write("\n후보:\n")
    for i, (fid, fname) in enumerate(candidates, 1):
        sys.stderr.write(f"  {i}) {fid} — {fname!r}\n")

    if len(candidates) == 1:
        chosen_id, chosen_name = candidates[0]
        sys.stderr.write(f"\n자동 선택: {chosen_id} ({chosen_name!r})\n")
    else:
        if not stdin_is_tty():
            sys.stderr.write(
                "\n후보가 여럿이고 TTY 가 아님. tako fields set 으로 직접 등록하세요.\n"
            )
            return 1
        idx_raw = ask_text(f"\n어느 것을 {args.name} 로?", default="1").strip()
        try:
            idx = int(idx_raw) - 1
            if not 0 <= idx < len(candidates):
                raise ValueError
        except ValueError:
            sys.stderr.write("유효하지 않은 번호.\n")
            return 2
        chosen_id, chosen_name = candidates[idx]

    if args.save:
        config_path = resolve_config_path(args.config)
        warn_comment_loss()
        try:
            write_field_mapping(args.name, chosen_id, config_path)
        except (FileNotFoundError, ValueError) as exc:
            sys.stderr.write(f"[fields] {exc}\n")
            return 2
        sys.stderr.write(f"\n등록 완료: jira.fields.{args.name} = {chosen_id}\n")
    else:
        sys.stderr.write(
            f"\n다음을 ~/.config/tako/config.yaml 의 jira 블록에 추가하세요:\n\n"
            f"  fields:\n    {args.name}: {chosen_id}\n\n"
            f"또는 자동 등록: tako fields set {args.name} {chosen_id}\n"
            f"또는 한 번에:   tako fields detect {args.name} --save\n"
        )

    print(chosen_id)
    return 0


def run(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "init":
        return _cmd_init(args)
    if args.command == "fields" and args.fields_command == "set":
        # set 은 config 만 건드림 — 로드는 안 함 (없으면 에러 메시지 자체 처리)
        return _cmd_fields_set(args)
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
        case "fields":
            if args.fields_command == "detect":
                return _cmd_fields_detect(args, cfg)
            sys.stderr.write(f"unknown fields command: {args.fields_command}\n")
            return 2
        case _:
            sys.stderr.write(f"unknown command: {args.command}\n")
            return 2


if __name__ == "__main__":
    raise SystemExit(run())
