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
from .list_query import ListFilters, QueryError, build_jql
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
    list_parser.add_argument("--project", help="프로젝트 키 (생략 시 config.default_project)")
    list_parser.add_argument("--status", action="append", default=[], help="상태 이름 (반복 가능, 예: --status 진행중)")
    list_parser.add_argument("--type", dest="types", action="append", default=[], help="이슈 유형 (반복 가능, 예: --type 에픽)")
    list_parser.add_argument("--parent", help="부모 이슈 키 (예: WL-9200)")
    list_parser.add_argument("--label", action="append", default=[], help="라벨 (반복 가능)")
    list_parser.add_argument("--updated", help="업데이트 시점: '7d'/'24h'/'2w'/'1m' 또는 'YYYY-MM-DD'")
    list_parser.add_argument("--query", help="제목/본문 텍스트 검색")
    list_parser.add_argument("--jql", dest="raw_jql", help="JQL 직접 작성 (다른 필터 무시)")
    list_parser.add_argument("--limit", type=int, default=20, help="결과 수 상한 (기본 20)")
    list_parser.add_argument("--json", dest="as_json", action="store_true", help="원본 JSON 출력")
    list_parser.add_argument("--csv", dest="as_csv", action="store_true", help="CSV 출력 (UTF-8 BOM, Excel 호환)")
    list_parser.add_argument(
        "-o",
        "--output",
        help="파일에 저장 (생략 시 stdout). 예: --csv -o tako-list.csv",
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
    if story_points is not None:
        payload["story_points"] = story_points
    if duedate is not None:
        payload["duedate"] = duedate
    if links:
        payload["links"] = links

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
    if args.link:
        prefilled["links"] = list(args.link)

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


def _cmd_list(args: argparse.Namespace, cfg: TakoConfig) -> int:
    if args.as_json and args.as_csv:
        sys.stderr.write("[input] --json 과 --csv 동시 사용 불가.\n")
        return 2

    filters = ListFilters(
        assignee=args.assignee,
        project=args.project,
        statuses=tuple(args.status),
        types=tuple(args.types),
        parent=args.parent,
        labels=tuple(args.label),
        updated=args.updated,
        query=args.query,
        raw_jql=args.raw_jql,
    )

    try:
        jql = build_jql(filters, default_project=cfg.jira.default_project)
    except QueryError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        return 2

    creds = _load_credentials_or_guide(args.credentials)
    client = JiraSiteClient(site=cfg.jira.site, creds=creds)
    sys.stderr.write(f"JQL: {jql}\n")
    try:
        result = client.search_issues(
            jql,
            fields=["summary", "status", "issuetype", "assignee", "updated", "parent"],
            max_results=args.limit,
        )
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    issues = result.get("issues") or []

    # 출력 분기
    if args.as_json:
        payload = json.dumps({"jql": jql, "issues": issues, "raw": result}, ensure_ascii=False, indent=2)
        _emit(payload, args.output)
        return 0

    if args.as_csv:
        if not issues:
            sys.stderr.write("(결과 없음 — CSV 헤더만 출력)\n")
        csv_text = issues_to_csv(issues, site=cfg.jira.site)
        _emit(csv_text, args.output)
        if args.output:
            sys.stderr.write(f"저장: {args.output}  ({len(issues)} 행)\n")
        return 0

    # 기본: 사람 친화 표
    if not issues:
        sys.stderr.write("(결과 없음)\n")
        return 0
    print(_render_list_table(issues))
    total = result.get("total")
    has_more = bool(result.get("nextPageToken"))
    if total is not None:
        sys.stderr.write(f"\n({len(issues)} / {total} 표시{', 더 있음' if has_more else ''})\n")
    elif has_more:
        sys.stderr.write(f"\n({len(issues)} 표시, 더 있음 — --limit 늘리거나 --jql 로 좁히기)\n")
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


def _render_list_table(issues: list[dict[str, Any]]) -> str:
    rows: list[tuple[str, str, str, str, str, str]] = []
    for it in issues:
        f = it.get("fields") or {}
        key = it.get("key", "?")
        status = (f.get("status") or {}).get("name", "?")
        itype = (f.get("issuetype") or {}).get("name", "?")
        assignee = ((f.get("assignee") or {}).get("displayName")) or "(미할당)"
        updated = (f.get("updated") or "")[:10]
        summary = f.get("summary", "")
        rows.append((key, status, itype, assignee, updated, summary))

    # 단순 컬럼 너비 — 한국어가 섞여 시각 폭이 다를 수 있음. v1.x 는 단순 처리.
    widths = (12, 10, 12, 14, 11, 60)
    header = ("KEY", "상태", "유형", "담당자", "업데이트", "제목")

    def line(row: tuple[str, ...]) -> str:
        out = []
        for i, cell in enumerate(row):
            w = widths[i] if i < len(widths) else 10
            out.append(cell[: w].ljust(w))
        return "  ".join(out).rstrip()

    return "\n".join([line(header), line(("-" * 12, "-" * 8, "-" * 8, "-" * 10, "-" * 10, "-" * 30))] + [line(r) for r in rows])


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
