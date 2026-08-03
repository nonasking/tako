"""tako 진입점 — argparse 정의 + 서브커맨드 디스패치.

서브커맨드 구현은 cmd_*.py 로 분리:
  cmd_new   new / preview / build / interactive (생성 계열)
  cmd_list  list (JQL 검색 + 출력)
  cmd_edit  update / retype (수정 계열)
  cmd_show  show (조회)
여기 남는 것: 파서, init / guide / fields (config 만 만지는 가벼운 커맨드), run.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .cmd_common import build_client, load_config_or_guide
from .cmd_edit import cmd_retype, cmd_update
from .cmd_list import cmd_list
from .cmd_new import cmd_build, cmd_interactive, cmd_new, cmd_preview
from .cmd_show import cmd_show
from .config import interactive_init, resolve_config_path
from .auth import resolve_credentials_path
from .fields import (
    SEARCH_KEYWORDS,
    filter_candidates,
    warn_comment_loss,
    write_field_mapping,
)
from .guide import (
    GuideError,
    effective_guide,
    resolve_guide_path,
    write_default_guide,
)
from .jira_client import JiraApiError
from .list_query import DEFAULT_LIST_LIMIT
from .prompts import ask_text, stdin_is_tty


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
    new_parser.add_argument(
        "--reporter",
        help="보고자: 'me' / 이메일 / accountId. 미지정이면 인증 사용자. "
             "프로젝트에 Modify Reporter 권한이 있어야 함",
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
    list_parser.add_argument("--updated", help="업데이트 시점: '7d'/'24h'/'2w'/'1m' 단축, 'YYYY-MM-DD', 비교('<=2026-04-01' 등), 범위('2026-05-01..2026-05-15' 또는 '~')")
    list_parser.add_argument("--created", help="생성 시점: '7d'/'24h'/'2w'/'1m' 단축, 'YYYY-MM-DD', 비교('<=2026-04-01' 등), 범위('2026-05-01..2026-05-15' 또는 '~')")
    list_parser.add_argument("--due", help="기한: 'overdue'/'none'/'set'/'YYYY-MM-DD'/'<=YYYY-MM-DD' 등, 범위('2026-05-01..2026-05-15' 또는 '~')")
    list_parser.add_argument("--sp", help="스토리포인트: 정수(3) / 비교('>=3','<=8','>0','<13') / 'none' / 'set'")
    list_parser.add_argument("--query", help="제목/본문 텍스트 검색")
    list_parser.add_argument("--jql", dest="raw_jql", help="JQL 직접 작성 (다른 필터 무시)")
    list_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIST_LIMIT,
        help=f"결과 수 상한 (기본 {DEFAULT_LIST_LIMIT}). 100 초과 시 필요한 만큼 페이지 자동 조회",
    )
    list_parser.add_argument("--all", dest="fetch_all", action="store_true", help="상한 없이 모든 페이지 자동 조회 (--limit 무시, 페이지당 100)")
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

    update_parser = sub.add_parser("update", help="기존 이슈 제목/본문 업데이트")
    update_parser.add_argument("key", help="이슈 키 또는 browse URL")
    update_parser.add_argument(
        "--summary",
        help="새 제목으로 교체. --body 와 함께 쓰면 둘 다 변경. 단독으로 쓰면 제목만 변경 (본문 안 건드림)",
    )
    update_parser.add_argument(
        "--body",
        help="추가/덮어쓸 마크다운 본문. --summary 가 있으면 생략 가능, 없으면 stdin 또는 TTY 입력으로 받음",
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

    retype_parser = sub.add_parser("retype", help="기존 이슈의 유형(issue type) 변경")
    retype_parser.add_argument("key", help="이슈 키 또는 browse URL")
    retype_parser.add_argument(
        "to",
        nargs="?",
        help="새 유형 이름 (예: Story / 기능변경). --to 로도 줄 수 있음",
    )
    retype_parser.add_argument(
        "--to",
        dest="to_opt",
        help="새 유형 이름. 위치 인자 대신 쓸 때.",
    )
    retype_parser.add_argument("--yes", "-y", action="store_true", help="확인 없이 바로 적용")

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

    guide_parser = sub.add_parser("guide", help="본문 작성 가이드 관리 (개인 커스텀)")
    guide_sub = guide_parser.add_subparsers(dest="guide_command", required=True)
    guide_sub.add_parser("show", help="적용 중인 가이드 출력 (개인 없으면 기본값)")
    guide_init = guide_sub.add_parser("init", help="기본 가이드를 개인 파일로 생성")
    guide_init.add_argument("--force", action="store_true", help="기존 개인 가이드 덮어쓰기")
    guide_sub.add_parser("reset", help="개인 가이드를 기본값으로 되돌림")
    guide_sub.add_parser("path", help="개인 가이드 경로 출력")

    return parser.parse_args(argv)


def _cmd_init(args: argparse.Namespace) -> int:
    interactive_init(
        target=resolve_config_path(args.config),
        credentials_target=resolve_credentials_path(args.credentials),
        force=args.force,
    )
    return 0


def _cmd_guide(args: argparse.Namespace) -> int:
    # 가이드 경로는 TAKO_GUIDE_PATH 환경변수 또는 기본 경로로 해석 (config 와 무관).
    try:
        if args.guide_command == "show":
            text, source = effective_guide()
            sys.stdout.write(text if text.endswith("\n") else text + "\n")
            sys.stderr.write(
                "출처: 개인 가이드\n" if source == "personal" else "출처: 기본값 (개인 가이드 없음)\n"
            )
            return 0
        if args.guide_command == "path":
            sys.stdout.write(f"{resolve_guide_path()}\n")
            return 0
        if args.guide_command in ("init", "reset"):
            force = args.guide_command == "reset" or getattr(args, "force", False)
            target = write_default_guide(force=force)
            sys.stderr.write(f"가이드 생성: {target}\n에디터로 직접 편집하거나 /tako-guide 로 수정.\n")
            return 0
    except GuideError as exc:
        sys.stderr.write(f"[guide] {exc}\n")
        return 2
    sys.stderr.write(f"unknown guide command: {args.guide_command}\n")
    return 2


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


def _cmd_fields_detect(args: argparse.Namespace, cfg) -> int:
    if args.name not in SEARCH_KEYWORDS:
        sys.stderr.write(
            f"[fields] 지원하지 않는 이름: {args.name!r}. 지원: {', '.join(SEARCH_KEYWORDS)}\n"
        )
        return 2
    client = build_client(cfg, args.credentials)
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
    if args.command == "guide":
        # config 로드 불필요 — 가이드는 별도 파일.
        return _cmd_guide(args)
    if args.command == "fields" and args.fields_command == "set":
        # set 은 config 만 건드림 — 로드는 안 함 (없으면 에러 메시지 자체 처리)
        return _cmd_fields_set(args)
    cfg = load_config_or_guide(args.config)
    match args.command:
        case "new":
            return cmd_new(args, cfg)
        case "show":
            return cmd_show(args, cfg)
        case "list":
            return cmd_list(args, cfg)
        case "update":
            return cmd_update(args, cfg)
        case "retype":
            return cmd_retype(args, cfg)
        case "preview":
            return cmd_preview(cfg)
        case "build":
            return cmd_build(cfg)
        case "interactive":
            return cmd_interactive(cfg)
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
