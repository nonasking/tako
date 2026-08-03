"""tako list — 필터 → JQL → 페이지네이션 → text/csv/json 출력.

limit 의미론:
  --limit N   결과 수 상한. 100 을 넘으면 필요한 만큼 페이지를 자동으로 넘겨 채운다.
  --all       상한 없이 끝까지 (--limit 무시, 페이지당 100).
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from .cmd_common import build_client, stamp_kst_str
from .config import TakoConfig
from .jira_client import JiraApiError, JiraSiteClient
from .list_output import issues_to_csv, render_list_table
from .list_query import DEFAULT_LIST_LIMIT, ListFilters, ListOutputOpts, QueryError, build_jql
from .prompts import ask_text, confirm, stdin_is_tty


_ALL_KEYWORDS = {"전체", "all", "*"}


def _is_all_keyword(value: str | None) -> bool:
    return bool(value) and value.strip().lower() in _ALL_KEYWORDS


def _normalize_multi_filter(values: tuple[str, ...]) -> tuple[str, ...]:
    """반복 필드 정규화 — '전체'/'all'/'*' 가 들어 있으면 빈 튜플 (조건 안 걸림)."""
    if any(_is_all_keyword(v) for v in values):
        return ()
    return tuple(v for v in values if v and v.strip())


def _ask_optional(prompt_text: str) -> str:
    """빈 입력 = 스킵, 값 입력하면 strip 후 반환."""
    return ask_text(prompt_text, default="").strip()


def _ask_csv_list(prompt_text: str) -> tuple[str, ...]:
    """쉼표로 구분된 입력 → 튜플. 빈 입력이면 ()."""
    raw = _ask_optional(prompt_text)
    if not raw:
        return ()
    return tuple(s.strip() for s in raw.split(",") if s.strip())


def _build_filters(
    *,
    assignee: str | None,
    projects: tuple[str, ...],
    statuses: tuple[str, ...],
    types: tuple[str, ...],
    parent: str | None,
    labels: tuple[str, ...],
    updated: str | None,
    created: str | None,
    due: str | None,
    sp: str | None,
    query: str | None,
    raw_jql: str | None,
) -> ListFilters:
    """원시 입력 → ListFilters. '전체' 키워드 해석과 정규화를 한곳에서.

    CLI 직접 경로와 wizard 경로가 이 함수 하나를 공유한다 — 새 필터를 추가할 때
    조립 규칙은 여기만 고치면 된다.
    """
    all_projects = any(_is_all_keyword(p) for p in projects)
    return ListFilters(
        assignee=None if _is_all_keyword(assignee) else (assignee or None),
        projects=() if all_projects else tuple(p for p in projects if p and p.strip()),
        statuses=_normalize_multi_filter(statuses),
        types=_normalize_multi_filter(types),
        parent=parent or None,
        labels=_normalize_multi_filter(labels),
        updated=updated or None,
        created=created or None,
        due=due or None,
        sp=sp or None,
        query=query or None,
        raw_jql=raw_jql,
        all_projects=all_projects,
    )


def _collect_list_filters_interactively(
    args: Any, cfg: TakoConfig
) -> tuple[ListFilters, ListOutputOpts]:
    """CLI 인자에 비어 있는 항목만 단계별로 묻고 (filters, opts) 반환.

    의도된 차이 — `cmd_new._collect_interactively` 는 dict 를 받고 dict 를 반환한다.
    new 쪽은 stdin JSON 진입점(`tako preview` / `tako build`)을 같이 쓰므로 dict 가 외부
    인터페이스. list 는 그 진입점이 없고 CLI args 와 1:1 매핑이라 args 를 직접 본다.
    """
    if not stdin_is_tty():
        sys.stderr.write("--wizard 는 TTY 필요. 인자로 직접 지정 또는 TTY 환경에서 실행.\n")
        raise SystemExit(2)

    sys.stderr.write(f"tako list 필터 입력 — 사이트 {cfg.jira.site}\n")
    sys.stderr.write(
        "빈 입력 = 해당 조건 스킵. 쉼표로 여러 값 가능한 항목 표시.\n"
        "각 항목에 '전체' 또는 'all' 입력 시: 그 조건 무시 (프로젝트는 default_project 도 무시).\n\n"
    )

    filters = _build_filters(
        assignee=args.assignee or _ask_optional("담당자 (me / 이메일 / accountId, 없으면 Enter)"),
        projects=tuple(args.project) or _ask_csv_list(
            f"프로젝트 (쉼표로 여러 개, 없으면 Enter — 기본 {cfg.jira.default_project}, '전체' = 사이트 전체)"
        ),
        statuses=tuple(args.status) or _ask_csv_list("상태 (쉼표로 여러 개, 예: 진행중,검토대기)"),
        types=tuple(args.types) or _ask_csv_list("이슈 유형 (쉼표로 여러 개, 예: 에픽,기능변경)"),
        parent=args.parent or _ask_optional("부모 이슈 키 (예: WL-9200)"),
        labels=tuple(args.label) or _ask_csv_list("라벨 (쉼표로 여러 개)"),
        updated=args.updated or _ask_optional("업데이트 (예: 7d / 2026-05-15 / 2026-05-01..2026-05-15)"),
        created=args.created or _ask_optional("생성 (예: 7d / 2026-05-15 / 2026-05-01..2026-05-15)"),
        due=args.due or _ask_optional("기한 (예: overdue / 2026-06-15 / <=2026-06-15 / 2026-06-01..2026-06-30)"),
        sp=args.sp or _ask_optional("스토리포인트 (정수 / >=N / <=N / none / set)"),
        query=args.query or _ask_optional("텍스트 검색 (제목/본문)"),
        raw_jql=args.raw_jql,
    )

    # 출력 옵션
    sys.stderr.write("\n[출력]\n")
    limit = args.limit
    fetch_all = args.fetch_all
    if not fetch_all:
        limit_raw = _ask_optional(
            f"최대 결과 수 (정수 / '전체' / 'all', 기본 {args.limit})"
        )
        if _is_all_keyword(limit_raw):
            fetch_all = True
            sys.stderr.write("→ 자동 페이지네이션 모드 (페이지당 100개씩 끝까지)\n")
        elif limit_raw:
            try:
                parsed = int(limit_raw)
                if parsed < 1:
                    raise ValueError
                limit = parsed
            except ValueError:
                sys.stderr.write(f"1 이상 정수가 아님: {limit_raw!r} — 기본 {args.limit} 사용\n")
        # fetch_all 단계는 '전체' 키워드로 처리했으면 스킵. 아니면 한 번 확인.
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
    parts = ["tako list"]
    if filters.raw_jql:
        parts += ["--jql", shlex.quote(filters.raw_jql)]
        return " ".join(parts)
    if filters.assignee:
        parts += ["--assignee", shlex.quote(filters.assignee)]
    if filters.all_projects:
        parts += ["--project", "전체"]
    else:
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
    if opts.fetch_all:
        parts.append("--all")  # --all 이면 limit 은 무시되므로 힌트에서도 뺀다
    elif opts.limit != DEFAULT_LIST_LIMIT:
        parts += ["--limit", str(opts.limit)]
    if opts.as_csv:
        parts.append("--csv")
    elif opts.as_json:
        parts.append("--json")
    if opts.output:
        parts += ["-o", shlex.quote(opts.output)]
    return " ".join(parts)


def _fetch_issues(
    client: JiraSiteClient,
    jql: str,
    *,
    fields: list[str],
    limit: int,
    fetch_all: bool,
) -> tuple[list[dict[str, Any]], bool]:
    """페이지네이션 실행 → (issues, has_more).

    fetch_all 이면 끝까지 (limit 무시). 아니면 limit 을 채우는 데 필요한
    만큼만 페이지를 넘기고 넘치면 잘라낸다. has_more 는 "상한 때문에 못 본
    결과가 남아 있음" — fetch_all 에선 항상 False.
    """
    issues: list[dict[str, Any]] = []
    token: str | None = None
    page_n = 0
    while True:
        page_n += 1
        if page_n > 1:
            sys.stderr.write(f"  …페이지 {page_n} 조회 중\n")
        page_size = 100 if fetch_all else min(limit - len(issues), 100)
        result = client.search_issues(
            jql, fields=fields, max_results=page_size, next_page_token=token
        )
        page_issues = result.get("issues") or []
        issues.extend(page_issues)
        token = result.get("nextPageToken")
        if not token:
            return issues, False
        if not page_issues:
            # 토큰은 있는데 빈 페이지 — 서버 이상. 무한 루프 대신 여기까지로 마감.
            return issues, True
        if not fetch_all and len(issues) >= limit:
            return issues[:limit], True


def cmd_list(args: Any, cfg: TakoConfig) -> int:
    if args.as_json and args.as_csv:
        sys.stderr.write("[input] --json 과 --csv 동시 사용 불가.\n")
        return 2
    if args.limit < 1:
        sys.stderr.write(f"[input] --limit 은 1 이상이어야 함: {args.limit}\n")
        return 2

    if args.wizard:
        try:
            filters, opts = _collect_list_filters_interactively(args, cfg)
        except QueryError as exc:
            sys.stderr.write(f"[input] {exc}\n")
            return 2
    else:
        filters = _build_filters(
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

    client = build_client(cfg, args.credentials)
    sys.stderr.write(f"JQL: {jql}\n")

    fields_req = ["summary", "status", "issuetype", "assignee", "created", "updated", "parent", "duedate"]
    if sp_field_id:
        fields_req.append(sp_field_id)

    try:
        issues, has_more = _fetch_issues(
            client, jql, fields=fields_req, limit=opts.limit, fetch_all=opts.fetch_all
        )
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    rc = _output_results(issues, has_more, jql=jql, site=cfg.jira.site, sp_field_id=sp_field_id, opts=opts)
    if args.wizard:
        sys.stderr.write(f"\n[힌트] 같은 조회 다시 쓰려면:\n  {_filters_to_shell_hint(filters, opts)}\n")
    return rc


def _output_results(
    issues: list[dict[str, Any]],
    has_more: bool,
    *,
    jql: str,
    site: str,
    sp_field_id: str | None,
    opts: ListOutputOpts,
) -> int:
    if opts.as_json:
        payload = json.dumps(
            {"jql": jql, "issues": issues, "has_more": has_more},
            ensure_ascii=False, indent=2,
        )
        saved = _emit(payload, opts.output)
        if saved:
            sys.stderr.write(f"저장: {saved}  ({len(issues)} 건)\n")
        return 0

    if opts.as_csv:
        if not issues:
            sys.stderr.write("(결과 없음 — CSV 헤더만 출력)\n")
        csv_text = issues_to_csv(issues, site=site, sp_field_id=sp_field_id)
        saved = _emit(csv_text, opts.output)
        if saved:
            sys.stderr.write(f"저장: {saved}  ({len(issues)} 행)\n")
        return 0

    # 기본: 사람 친화 표
    if not issues:
        sys.stderr.write("(결과 없음)\n")
        return 0
    print(render_list_table(issues, sp_field_id=sp_field_id))
    sys.stderr.write(f"\n({len(issues)} 건{', 더 있음 — --all 로 전체 / --limit 늘리기' if has_more else ''})\n")
    return 0


def _reserve_output_path(output_path: str) -> Path:
    """저장할 실제 경로를 확정한다. 기존 파일은 절대 덮어쓰지 않는다.

    이미 있으면 확장자 *앞*에 KST 타임스탬프를 끼워 비켜간다 (`.csv` 가 끝에 남아야
    엑셀 연결이 유지됨). 같은 초에 또 겹치면 `-2`, `-3` … 을 덧붙인다.
    부모 디렉터리가 없으면 만든다.
    """
    target = Path(output_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        return target

    stamp = stamp_kst_str()
    stamped = target.with_name(f"{target.stem}-{stamp}{target.suffix}")
    dup = 2
    while stamped.exists():
        stamped = target.with_name(f"{target.stem}-{stamp}-{dup}{target.suffix}")
        dup += 1
    sys.stderr.write(f"[출력] {target} 이미 있음 → 덮어쓰지 않고 새 이름으로 저장\n")
    return stamped


def _emit(text: str, output_path: str | None) -> Path | None:
    """output_path 가 있으면 파일로, 없으면 stdout 으로. 실제 저장 경로를 돌려준다."""
    if output_path:
        try:
            saved = _reserve_output_path(output_path)
            saved.write_text(text, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"[출력] 저장 실패 ({output_path}): {exc}\n")
            raise SystemExit(2)
        return saved
    # stdout 으로 — BOM 포함된 CSV 도 그대로 통과
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    return None
