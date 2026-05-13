"""tako 진입점.

서브커맨드: init / new / preview / build / interactive.
new 가 일상 사용. preview/build 는 슬래시 커맨드 본문 또는 디버깅용.
"""

from __future__ import annotations

import argparse
import json
import re
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
from .adf_to_md import adf_to_markdown
from .fields import (
    SEARCH_KEYWORDS,
    filter_candidates,
    warn_comment_loss,
    write_field_mapping,
)
from .issue_draft import DraftError, IssueDraft, build_payload, render_preview
from .jira_client import JiraApiError, JiraSiteClient, markdown_to_adf
from .list_output import issues_to_csv
from .list_query import DEFAULT_LIST_LIMIT, ListFilters, ListOutputOpts, QueryError, build_jql
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
    new_parser.add_argument(
        "--assignee",
        help="담당자: 'me' / 이메일 / accountId. 생략 시 인터랙티브 단계에서 묻고, 빈 입력은 미할당 (또는 config.default_assignee).",
    )
    new_parser.add_argument("--label", action="append", default=[], help="라벨 (반복 가능)")
    new_parser.add_argument("--story-points", dest="story_points", type=int, help="스토리포인트 (정수)")
    new_parser.add_argument("--duedate", help="기한 YYYY-MM-DD")
    new_parser.add_argument(
        "--link",
        action="append",
        default=[],
        help="연결할 티켓 KEY[:TYPE] (반복 가능, TYPE 생략 시 'Relates'). 예: --link WL-100 --link 'WL-200:Blocks'",
    )
    new_parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 바로 생성")

    sub.add_parser("preview", help="stdin JSON → 미리보기 stdout")
    sub.add_parser("build", help="stdin JSON → 페이로드 JSON stdout")
    sub.add_parser("interactive", help="TTY 인터랙티브 → 페이로드 JSON stdout")

    show_parser = sub.add_parser("show", help="기존 이슈 조회 (조회용)")
    show_parser.add_argument("key", help="이슈 키 또는 browse URL (예: WL-8876)")
    show_parser.add_argument("--json", dest="as_json", action="store_true", help="원본 JSON 출력 (LLM/자동화용)")
    show_parser.add_argument("--max-comments", type=int, default=5, help="포함할 최근 코멘트 수 (기본 5, 0 이면 제외)")

    list_parser = sub.add_parser("list", help="JQL 기반 이슈 검색 + 필터링")
    list_parser.add_argument("--assignee", help="담당자: 'me' / 이메일 / accountId")
    list_parser.add_argument(
        "--project",
        action="append",
        default=[],
        help="프로젝트 키 (반복 가능, 예: --project WL --project ABC). 생략 시 config.default_project",
    )
    list_parser.add_argument("--status", action="append", default=[], help="상태 이름 (반복 가능, 예: --status 진행중)")
    list_parser.add_argument("--type", dest="types", action="append", default=[], help="이슈 유형 (반복 가능, 예: --type 에픽)")
    list_parser.add_argument("--parent", help="부모 이슈 키 (예: WL-9200)")
    list_parser.add_argument("--label", action="append", default=[], help="라벨 (반복 가능)")
    list_parser.add_argument("--updated", help="업데이트 시점: '7d'/'24h'/'2w'/'1m' 또는 'YYYY-MM-DD'")
    list_parser.add_argument("--created", help="생성 시점: '7d'/'24h'/'2w'/'1m' 또는 'YYYY-MM-DD'")
    list_parser.add_argument("--due", help="기한: 'overdue'/'none'/'set'/YYYY-MM-DD/'<=YYYY-MM-DD' 등")
    list_parser.add_argument("--sp", help="스토리포인트: 정수(3) / 비교('>=3','<=8','>0','<13') / 'none' / 'set'")
    list_parser.add_argument("--query", help="제목/본문 텍스트 검색")
    list_parser.add_argument("--jql", dest="raw_jql", help="JQL 직접 작성 (다른 필터 무시)")
    list_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIST_LIMIT,
        help=f"결과 수 상한 (기본 {DEFAULT_LIST_LIMIT}). --all 과 함께면 페이지당 크기",
    )
    list_parser.add_argument("--all", dest="fetch_all", action="store_true", help="모든 페이지 자동 조회 (페이지당 100 max)")
    list_parser.add_argument("--json", dest="as_json", action="store_true", help="원본 JSON 출력")
    list_parser.add_argument("--csv", dest="as_csv", action="store_true", help="CSV 출력 (UTF-8 BOM, Excel 호환)")
    list_parser.add_argument(
        "-o",
        "--output",
        help="파일에 저장 (생략 시 stdout). 예: --csv -o tako-list.csv",
    )
    list_parser.add_argument(
        "-i",
        "--wizard",
        action="store_true",
        help="필터 인터랙티브 입력 (옵트인). CLI 인자도 같이 주면 그 항목은 묻지 않음.",
    )

    update_parser = sub.add_parser("update", help="기존 이슈 본문(description) 업데이트")
    update_parser.add_argument("key", help="이슈 키 또는 browse URL")
    update_parser.add_argument(
        "--body",
        help="추가/덮어쓸 마크다운 본문. 생략하면 stdin 또는 TTY 입력으로 받음",
    )
    update_parser.add_argument(
        "--section",
        default="업데이트",
        help="append 모드에서 추가될 섹션 헤더 이름 (기본 '업데이트' → '## 업데이트 (YYYY-MM-DD)')",
    )
    update_parser.add_argument(
        "--mode",
        choices=["append", "overwrite"],
        default="append",
        help="append (기본): 기존 본문 끝에 새 섹션. overwrite: 통째 교체",
    )
    update_parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 바로 적용")

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


def _collect_interactively(
    cfg: TakoConfig,
    *,
    prefilled: dict[str, Any] | None = None,
    ask_assignee: bool = True,
) -> dict[str, Any]:
    """인터랙티브로 입력값 dict 수집. 호출자가 후처리(assignee resolve / IssueDraft 빌드) 책임."""
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
    assignee_input: str | None = None
    if ask_assignee:
        if "assignee_pending" in pre:
            assignee_input = pre["assignee_pending"]
        else:
            default_a = cfg.jira.default_assignee
            suffix = f" [{default_a}]" if default_a else ""
            raw = ask_text(
                f"담당자 (me / 이메일 / accountId, 없으면 Enter{suffix})",
                default="",
            ).strip()
            assignee_input = raw or default_a or None

    # optional 영역
    story_points = pre.get("story_points")
    if story_points is None:
        sp_raw = ask_text("스토리포인트 (정수, 없으면 Enter)", default="").strip()
        story_points = sp_raw if sp_raw else None
    duedate = pre.get("duedate")
    if duedate is None:
        due_raw = ask_text("기한 YYYY-MM-DD (없으면 Enter)", default="").strip()
        duedate = due_raw if due_raw else None
    links = pre.get("links")
    if links is None:
        link_raw = ask_text(
            "연결할 티켓 (KEY[:TYPE], 쉼표로 여러 개, 없으면 Enter)",
            default="",
        ).strip()
        links = [s.strip() for s in link_raw.split(",") if s.strip()] if link_raw else []

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
    if assignee_input:
        # 미해결 입력값만 실어둠. _cmd_new 가 REST 로 해석 후 accountId 로 교체.
        payload["assignee_pending"] = assignee_input
    if story_points is not None:
        payload["story_points"] = story_points
    if duedate is not None:
        payload["duedate"] = duedate
    if links:
        payload["links"] = links

    return payload  # type: ignore[return-value]


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
    # interactive 모드는 REST 호출 없이 페이로드만 뽑는 디버깅 경로 — 담당자 단계 생략.
    payload = _collect_interactively(cfg, ask_assignee=False)
    try:
        draft = IssueDraft.from_payload(payload)
    except DraftError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        return 2
    sys.stderr.write("\n")
    print(render_preview(draft))
    sys.stderr.write("\n")
    if not confirm("페이로드 출력?"):
        sys.stderr.write("취소.\n")
        return 1
    print(json.dumps(build_payload(draft, cfg.jira.custom_fields), ensure_ascii=False, indent=2))
    return 0


_ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9:\-]+$")


def _resolve_assignee(value: str, client: JiraSiteClient) -> tuple[str, str]:
    """사용자 입력 (me / 이메일 / accountId) → (accountId, 표시 라벨).

    실패 시 SystemExit(2). 메시지는 호출 전후 맥락이 있는 가정.

    list_query._assignee_clause 와 의도적 분리:
    저쪽은 JQL 문자열만 만들어 currentUser()/이메일 그대로 두면 Jira 가 해석.
    여기는 issue 생성 페이로드 fields.assignee 에 *accountId 가 필수* 라서
    REST 두 번(/myself, /user/search) 으로 직접 해석한다.
    """
    v = value.strip()
    if v.lower() in {"me", "current", "self"}:
        try:
            data = client.get_myself()
        except JiraApiError as exc:
            sys.stderr.write(f"[담당자] 본인 정보 조회 실패: {exc}\n")
            raise SystemExit(2)
        acc = data.get("accountId")
        if not acc:
            sys.stderr.write("[담당자] myself 응답에 accountId 없음.\n")
            raise SystemExit(2)
        name = data.get("displayName") or "me"
        email = data.get("emailAddress")
        label = f"{name}" + (f" ({email})" if email else "")
        return acc, label

    if "@" in v:
        try:
            users = client.search_users(v)
        except JiraApiError as exc:
            sys.stderr.write(f"[담당자] 사용자 검색 실패: {exc}\n")
            raise SystemExit(2)
        # 이메일 정확 일치 우선
        matches = [u for u in users if (u.get("emailAddress") or "").lower() == v.lower()]
        if not matches:
            matches = users
        if not matches:
            sys.stderr.write(
                f"[담당자] 일치하는 사용자 없음: {v!r}\n"
                "  사이트 GDPR 설정에 따라 이메일 검색이 제한될 수 있음 — accountId 직접 입력 권장.\n"
            )
            raise SystemExit(2)
        if len(matches) > 1:
            sys.stderr.write(
                f"[담당자] 이메일 검색 결과가 {len(matches)}건. 정확히 1건만 허용.\n"
                "  accountId 직접 입력 또는 다른 검색어 사용.\n"
            )
            raise SystemExit(2)
        u = matches[0]
        acc = u.get("accountId")
        if not acc:
            sys.stderr.write("[담당자] 검색 결과에 accountId 없음.\n")
            raise SystemExit(2)
        name = u.get("displayName") or v
        return acc, f"{name} ({v})"

    if _ACCOUNT_ID_RE.match(v) and len(v) >= 12:
        # accountId 패턴 — 그대로 사용
        return v, v

    sys.stderr.write(
        f"[담당자] 형식 미지원: {value!r}\n"
        "  지원: 'me' / 이메일 / accountId\n"
        "  한국어 이름·닉네임은 v1.x 미지원.\n"
    )
    raise SystemExit(2)


def _cmd_init(args: argparse.Namespace) -> int:
    interactive_init(
        target=resolve_config_path(args.config),
        credentials_target=resolve_credentials_path(args.credentials),
        force=args.force,
    )
    return 0


def _cmd_new(args: argparse.Namespace, cfg: TakoConfig) -> int:
    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)

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
    if args.assignee:
        prefilled["assignee_pending"] = args.assignee
    if args.story_points is not None:
        prefilled["story_points"] = args.story_points
    if args.duedate:
        prefilled["duedate"] = args.duedate
    if args.link:
        prefilled["links"] = list(args.link)

    needs_interactive = not (args.summary and args.description is not None)
    if needs_interactive and not stdin_is_tty():
        sys.stderr.write(
            "--summary / --description 없고 TTY 도 아님. 인자 모두 명시 또는 셸에서 직접 호출.\n"
        )
        return 2

    if needs_interactive:
        payload = _collect_interactively(cfg, prefilled=prefilled)
    else:
        # 완전 자동 모드: prefilled 만으로 draft. optional 항목 묻지 않음.
        payload = dict(prefilled)
        payload.setdefault("project", cfg.jira.default_project)
        payload.setdefault("issue_type", cfg.jira.default_issue_type)
        if "parent_epic" in payload:
            payload["parent_epic"] = cfg.resolve_epic(payload["parent_epic"])
        # --assignee 미지정 시 config.default_assignee 적용
        if "assignee_pending" not in payload and cfg.jira.default_assignee:
            payload["assignee_pending"] = cfg.jira.default_assignee

    # 담당자 해석 — me/이메일 → accountId. _collect_interactively / args.assignee /
    # cfg.default_assignee 어느 경로든 미해결 입력은 'assignee_pending' 키로 들어옴.
    pending = payload.pop("assignee_pending", None)
    if pending:
        acc, label = _resolve_assignee(pending, client)
        payload["assignee"] = acc
        payload["assignee_label"] = label
        sys.stderr.write(f"[담당자] {pending} → {label}\n")

    try:
        draft = IssueDraft.from_payload(payload)
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

    try:
        result = client.create_issue(fields)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    sys.stderr.write(f"\n생성 완료\n  키:   {result.key}\n  링크: {result.url}\n")

    # 연결 처리 — 이슈는 이미 만들어졌으므로 실패해도 rollback 안 함. 보고만.
    link_failures: list[tuple[str, str, str]] = []
    if draft.links:
        sys.stderr.write("\n연결:\n")
        for target, type_name in draft.links:
            try:
                client.create_issue_link(
                    type_name=type_name, inward_key=result.key, outward_key=target
                )
                sys.stderr.write(f"  [OK]   {type_name} → {target}\n")
            except JiraApiError as exc:
                link_failures.append((target, type_name, str(exc)))
                sys.stderr.write(f"  [실패] {type_name} → {target}  ({exc})\n")

    print(result.key)  # stdout 에는 키만
    return 1 if link_failures else 0


_KEY_FROM_URL = re.compile(r"/browse/([A-Z][A-Z0-9_]*-\d+)", re.IGNORECASE)


def _extract_key(raw: str) -> str:
    raw = raw.strip()
    if "/" in raw:
        m = _KEY_FROM_URL.search(raw)
        if m:
            return m.group(1).upper()
    return raw.upper()


def _read_body_input(args: argparse.Namespace) -> str:
    """body 텍스트를 우선순위로 받음: --body 인자 > stdin > TTY 입력."""
    if args.body is not None:
        return args.body
    if not sys.stdin.isatty():
        raw = sys.stdin.read()
        if not raw.strip():
            sys.stderr.write("[update] body 가 비었음. --body 또는 stdin 으로 마크다운 전달.\n")
            raise SystemExit(2)
        return raw.rstrip("\n")
    sys.stderr.write("추가할 본문(마크다운) 을 입력 (Ctrl+D 로 종료)\n")
    sys.stderr.flush()
    raw = sys.stdin.read()
    if not raw.strip():
        sys.stderr.write("[update] body 가 비었음.\n")
        raise SystemExit(2)
    return raw.rstrip("\n")


def _today_kst_str() -> str:
    """KST(UTC+9) 기준 YYYY-MM-DD."""
    from datetime import datetime, timedelta, timezone
    kst = timezone(timedelta(hours=9))
    return datetime.now(tz=kst).strftime("%Y-%m-%d")


def _cmd_update(args: argparse.Namespace, cfg: TakoConfig) -> int:
    key = _extract_key(args.key)
    body_md = _read_body_input(args)

    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)

    # 현재 본문 조회 (append 모드에서 필요. overwrite 도 미리보기 위해 받아둠)
    try:
        issue = client.get_issue(key)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2
    current_md = adf_to_markdown((issue.get("fields") or {}).get("description"))
    summary = (issue.get("fields") or {}).get("summary", "")

    if args.mode == "append":
        section_header = f"## {args.section} ({_today_kst_str()})"
        new_section = f"{section_header}\n{body_md}"
        merged_md = (current_md.rstrip() + "\n\n" + new_section) if current_md.strip() else new_section
    else:  # overwrite
        new_section = body_md
        merged_md = body_md

    # 미리보기
    sys.stderr.write(f"\n[{key}] {summary}\n")
    sys.stderr.write(f"링크: https://{cfg.jira.site}/browse/{key}\n")
    sys.stderr.write(f"\n--- 모드: {args.mode} ---\n")
    if args.mode == "append":
        sys.stderr.write("\n[추가될 섹션]\n")
        sys.stderr.write(new_section + "\n")
    else:
        sys.stderr.write("\n[현재 본문 → 교체될 것]\n")
        sys.stderr.write((current_md or "(비어 있음)") + "\n")
        sys.stderr.write("\n[새 본문]\n")
        sys.stderr.write(body_md + "\n")
    sys.stderr.write("\n")

    if not args.yes:
        if not confirm("이대로 본문을 업데이트할까요?"):
            sys.stderr.write("취소.\n")
            return 1

    adf = markdown_to_adf(merged_md)
    try:
        client.update_issue_fields(key, {"description": adf})
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    sys.stderr.write(f"\n업데이트 완료: {key}\n  링크: https://{cfg.jira.site}/browse/{key}\n")
    print(key)
    return 0


def _ask_optional(prompt_text: str) -> str:
    """빈 입력 = 스킵, 값 입력하면 strip 후 반환."""
    return ask_text(prompt_text, default="").strip()


def _ask_csv_list(prompt_text: str) -> tuple[str, ...]:
    """쉼표로 구분된 입력 → 튜플. 빈 입력이면 ()."""
    raw = _ask_optional(prompt_text)
    if not raw:
        return ()
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _collect_list_filters_interactively(
    args: argparse.Namespace, cfg: TakoConfig
) -> tuple[ListFilters, ListOutputOpts]:
    """CLI 인자에 비어 있는 항목만 단계별로 묻고 (filters, opts) 반환.

    의도된 차이 — `_collect_interactively` (new) 는 dict 를 받고 dict 를 반환한다.
    new 쪽은 stdin JSON 진입점(`tako preview` / `tako build`)을 같이 쓰므로 dict 가 외부
    인터페이스. list 는 그 진입점이 없고 CLI args 와 1:1 매핑이라 args 를 직접 본다.
    """
    if not stdin_is_tty():
        sys.stderr.write("--wizard 는 TTY 필요. 인자로 직접 지정 또는 TTY 환경에서 실행.\n")
        raise SystemExit(2)

    sys.stderr.write(f"tako list 필터 입력 — 사이트 {cfg.jira.site}\n")
    sys.stderr.write("빈 입력 = 해당 조건 스킵. 쉼표로 여러 값 가능한 항목 표시.\n\n")

    assignee = args.assignee or _ask_optional(
        "담당자 (me / 이메일 / accountId, 없으면 Enter)"
    ) or None
    projects = tuple(args.project) or _ask_csv_list(
        f"프로젝트 (쉼표로 여러 개, 없으면 Enter — 기본 {cfg.jira.default_project})"
    )
    statuses = tuple(args.status) or _ask_csv_list("상태 (쉼표로 여러 개, 예: 진행중,검토대기)")
    types = tuple(args.types) or _ask_csv_list("이슈 유형 (쉼표로 여러 개, 예: 에픽,기능변경)")
    parent = args.parent or _ask_optional("부모 이슈 키 (예: WL-9200)") or None
    labels = tuple(args.label) or _ask_csv_list("라벨 (쉼표로 여러 개)")
    updated = args.updated or _ask_optional(
        "업데이트 (7d / 24h / 2w / 1m / YYYY-MM-DD)"
    ) or None
    created = args.created or _ask_optional(
        "생성 (7d / 24h / 2w / 1m / YYYY-MM-DD)"
    ) or None
    due = args.due or _ask_optional(
        "기한 (overdue / none / set / YYYY-MM-DD / '<=YYYY-MM-DD')"
    ) or None
    sp = args.sp or _ask_optional(
        "스토리포인트 (정수 / >=N / <=N / none / set)"
    ) or None
    query = args.query or _ask_optional("텍스트 검색 (제목/본문)") or None

    # 출력 옵션
    sys.stderr.write("\n[출력]\n")
    limit_raw = _ask_optional(f"최대 결과 수 (기본 {args.limit})")
    try:
        limit = int(limit_raw) if limit_raw else args.limit
    except ValueError:
        sys.stderr.write(f"숫자가 아님: {limit_raw!r} — 기본 {args.limit} 사용\n")
        limit = args.limit

    fetch_all = args.fetch_all
    if not fetch_all:
        fetch_all = confirm("모든 페이지 자동 조회 (--all)?", default=False)

    if args.as_csv or args.as_json:
        as_csv = args.as_csv
        as_json = args.as_json
    else:
        fmt = _ask_optional("출력 형식 (text / csv / json) [text]") or "text"
        as_csv = fmt == "csv"
        as_json = fmt == "json"

    output = args.output
    if not output and (as_csv or as_json):
        path_raw = _ask_optional("파일로 저장 (없으면 stdout)")
        output = path_raw or None

    filters = ListFilters(
        assignee=assignee,
        projects=projects,
        statuses=statuses,
        types=types,
        parent=parent,
        labels=labels,
        updated=updated,
        created=created,
        due=due,
        sp=sp,
        query=query,
        raw_jql=args.raw_jql,
    )
    opts = ListOutputOpts(
        limit=limit,
        fetch_all=fetch_all,
        as_csv=as_csv,
        as_json=as_json,
        output=output,
    )
    return filters, opts


def _filters_to_shell_hint(filters: ListFilters, opts: ListOutputOpts) -> str:
    """filters + opts → 'tako list ...' 셸 명령 한 줄. 사용자가 alias 로 저장하라고 보여줌."""
    import shlex
    parts = ["tako list"]
    if filters.raw_jql:
        parts += ["--jql", shlex.quote(filters.raw_jql)]
        return " ".join(parts)
    if filters.assignee:
        parts += ["--assignee", shlex.quote(filters.assignee)]
    for p in filters.projects:
        parts += ["--project", shlex.quote(p)]
    for s in filters.statuses:
        parts += ["--status", shlex.quote(s)]
    for t in filters.types:
        parts += ["--type", shlex.quote(t)]
    if filters.parent:
        parts += ["--parent", shlex.quote(filters.parent)]
    for lb in filters.labels:
        parts += ["--label", shlex.quote(lb)]
    if filters.updated:
        parts += ["--updated", shlex.quote(filters.updated)]
    if filters.created:
        parts += ["--created", shlex.quote(filters.created)]
    if filters.due:
        parts += ["--due", shlex.quote(filters.due)]
    if filters.sp:
        parts += ["--sp", shlex.quote(filters.sp)]
    if filters.query:
        parts += ["--query", shlex.quote(filters.query)]
    if opts.limit != DEFAULT_LIST_LIMIT:
        parts += ["--limit", str(opts.limit)]
    if opts.fetch_all:
        parts.append("--all")
    if opts.as_csv:
        parts.append("--csv")
    elif opts.as_json:
        parts.append("--json")
    if opts.output:
        parts += ["-o", shlex.quote(opts.output)]
    return " ".join(parts)


def _cmd_list(args: argparse.Namespace, cfg: TakoConfig) -> int:
    if args.as_json and args.as_csv:
        sys.stderr.write("[input] --json 과 --csv 동시 사용 불가.\n")
        return 2

    if args.wizard:
        try:
            filters, opts = _collect_list_filters_interactively(args, cfg)
        except QueryError as exc:
            sys.stderr.write(f"[input] {exc}\n")
            return 2
    else:
        filters = ListFilters(
            assignee=args.assignee,
            projects=tuple(args.project),
            statuses=tuple(args.status),
            types=tuple(args.types),
            parent=args.parent,
            labels=tuple(args.label),
            updated=args.updated,
            created=args.created,
            due=args.due,
            sp=args.sp,
            query=args.query,
            raw_jql=args.raw_jql,
        )
        opts = ListOutputOpts(
            limit=args.limit,
            fetch_all=args.fetch_all,
            as_csv=args.as_csv,
            as_json=args.as_json,
            output=args.output,
        )

    sp_field_id = cfg.jira.custom_fields.get("story_points")

    try:
        jql = build_jql(
            filters,
            default_project=cfg.jira.default_project,
            sp_field_id=sp_field_id,
        )
    except QueryError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        return 2

    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)
    sys.stderr.write(f"JQL: {jql}\n")

    fields_req = ["summary", "status", "issuetype", "assignee", "created", "updated", "parent", "duedate"]
    if sp_field_id:
        fields_req.append(sp_field_id)

    limit = opts.limit
    fetch_all = opts.fetch_all
    as_csv = opts.as_csv
    as_json = opts.as_json
    output_path = opts.output

    # 페이지네이션
    page_size = min(limit, 100) if not fetch_all else 100
    issues: list[dict[str, Any]] = []
    token: str | None = None
    page_n = 0
    try:
        while True:
            page_n += 1
            if fetch_all and page_n > 1:
                sys.stderr.write(f"  …페이지 {page_n} 조회 중\n")
            result = client.search_issues(
                jql, fields=fields_req, max_results=page_size, next_page_token=token
            )
            page_issues = result.get("issues") or []
            issues.extend(page_issues)
            token = result.get("nextPageToken")
            if not fetch_all:
                break
            if not token:
                break
            if len(issues) >= limit and not fetch_all:
                break
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    has_more = bool(token) and not fetch_all

    # 출력 분기
    if as_json:
        payload = json.dumps(
            {"jql": jql, "issues": issues, "has_more": has_more},
            ensure_ascii=False, indent=2,
        )
        _emit(payload, output_path)
        if args.wizard:
            sys.stderr.write(f"\n[힌트] 같은 조회 다시 쓰려면:\n  {_filters_to_shell_hint(filters, opts)}\n")
        return 0

    if as_csv:
        if not issues:
            sys.stderr.write("(결과 없음 — CSV 헤더만 출력)\n")
        csv_text = issues_to_csv(issues, site=cfg.jira.site, sp_field_id=sp_field_id)
        _emit(csv_text, output_path)
        if output_path:
            sys.stderr.write(f"저장: {output_path}  ({len(issues)} 행)\n")
        if args.wizard:
            sys.stderr.write(f"\n[힌트] 같은 조회 다시 쓰려면:\n  {_filters_to_shell_hint(filters, opts)}\n")
        return 0

    # 기본: 사람 친화 표
    if not issues:
        sys.stderr.write("(결과 없음)\n")
        if args.wizard:
            sys.stderr.write(f"\n[힌트] 같은 조회 다시 쓰려면:\n  {_filters_to_shell_hint(filters, opts)}\n")
        return 0
    print(_render_list_table(issues, sp_field_id=sp_field_id))
    sys.stderr.write(f"\n({len(issues)} 건{', 더 있음 — --all 로 전체 / --limit 늘리기' if has_more else ''})\n")
    if args.wizard:
        sys.stderr.write(f"\n[힌트] 같은 조회 다시 쓰려면:\n  {_filters_to_shell_hint(filters, opts)}\n")
    return 0


def _emit(text: str, output_path: str | None) -> None:
    if output_path:
        from pathlib import Path
        Path(output_path).expanduser().write_text(text, encoding="utf-8")
    else:
        # stdout 으로 — BOM 포함된 CSV 도 그대로 통과
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def _render_list_table(issues: list[dict[str, Any]], *, sp_field_id: str | None = None) -> str:
    # 컬럼 정의: SP 매핑 있으면 SP 컬럼 추가
    has_sp = bool(sp_field_id)
    if has_sp:
        header = ("KEY", "상태", "유형", "SP", "담당자", "생성", "업데이트", "기한", "제목")
        widths = (12, 10, 12, 5, 14, 11, 11, 11, 50)
    else:
        header = ("KEY", "상태", "유형", "담당자", "생성", "업데이트", "기한", "제목")
        widths = (12, 10, 12, 14, 11, 11, 11, 55)

    rows: list[tuple[str, ...]] = []
    for it in issues:
        f = it.get("fields") or {}
        key = it.get("key", "?")
        status = (f.get("status") or {}).get("name", "?")
        itype = (f.get("issuetype") or {}).get("name", "?")
        assignee = ((f.get("assignee") or {}).get("displayName")) or "(미할당)"
        created = (f.get("created") or "")[:10]
        updated = (f.get("updated") or "")[:10]
        duedate = f.get("duedate") or ""
        summary = f.get("summary", "")
        if has_sp:
            sp_val = f.get(sp_field_id)
            sp_str = "" if sp_val is None else (str(int(sp_val)) if isinstance(sp_val, (int, float)) else str(sp_val))
            rows.append((key, status, itype, sp_str, assignee, created, updated, duedate, summary))
        else:
            rows.append((key, status, itype, assignee, created, updated, duedate, summary))

    def line(row: tuple[str, ...]) -> str:
        out = []
        for i, cell in enumerate(row):
            w = widths[i] if i < len(widths) else 10
            out.append(cell[: w].ljust(w))
        return "  ".join(out).rstrip()

    divider = tuple("-" * (w - 2) for w in widths)
    return "\n".join([line(header), line(divider)] + [line(r) for r in rows])


def _cmd_show(args: argparse.Namespace, cfg: TakoConfig) -> int:
    key = _extract_key(args.key)
    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)
    try:
        issue = client.get_issue(key)
        comments = client.list_comments(key, max_results=args.max_comments) if args.max_comments > 0 else []
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    if args.as_json:
        payload = {"issue": issue, "comments": comments, "url": f"https://{cfg.jira.site}/browse/{key}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(_render_issue_text(issue, comments, site=cfg.jira.site))
    return 0


def _render_issue_text(issue: dict[str, Any], comments: list[dict[str, Any]], *, site: str) -> str:
    fields = issue.get("fields") or {}
    key = issue.get("key", "?")
    summary = fields.get("summary", "")
    itype = (fields.get("issuetype") or {}).get("name", "?")
    status = (fields.get("status") or {}).get("name", "?")
    assignee = ((fields.get("assignee") or {}).get("displayName")) or "(미할당)"
    reporter = ((fields.get("reporter") or {}).get("displayName")) or "(없음)"
    priority = ((fields.get("priority") or {}).get("name")) or "(없음)"
    duedate = fields.get("duedate") or "(없음)"
    labels = fields.get("labels") or []
    parent = fields.get("parent") or {}
    parent_key = parent.get("key")
    parent_summary = (parent.get("fields") or {}).get("summary", "")

    description_md = adf_to_markdown(fields.get("description"))

    lines: list[str] = []
    lines.append(f"[{key}] {itype} — {summary}")
    if parent_key:
        lines.append(f"부모:     {parent_key} {parent_summary}".rstrip())
    lines.append(f"상태:     {status}")
    lines.append(f"담당자:   {assignee}")
    lines.append(f"보고자:   {reporter}")
    lines.append(f"우선순위: {priority}")
    lines.append(f"기한:     {duedate}")
    if labels:
        lines.append(f"라벨:     {', '.join(labels)}")
    lines.append(f"링크:     https://{site}/browse/{key}")
    lines.append("")
    lines.append("--- 설명 ---")
    lines.append(description_md or "(비어 있음)")
    if comments:
        lines.append("")
        lines.append(f"--- 코멘트 ({len(comments)}, 최신순) ---")
        for c in comments:
            author = ((c.get("author") or {}).get("displayName")) or "?"
            created = (c.get("created") or "")[:10]
            body_md = adf_to_markdown(c.get("body"))
            lines.append(f"\n[{created}] {author}")
            lines.append(body_md or "(비어 있음)")
    return "\n".join(lines)


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
        case "show":
            return _cmd_show(args, cfg)
        case "list":
            return _cmd_list(args, cfg)
        case "update":
            return _cmd_update(args, cfg)
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
