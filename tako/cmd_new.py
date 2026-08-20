"""tako new / preview / build / interactive — 이슈 생성 계열 서브커맨드.

stdin JSON 진입점(preview/build)과 인터랙티브 수집이 같은 draft 빌드 경로를 탄다.
REST 호출 직전 미리보기 → Y/N 확인이 원칙 (--yes 로만 생략).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .cmd_common import build_client
from .config import TakoConfig
from .draft_file import (
    DraftFileError,
    new_draft_path,
    open_in_editor,
    parse_draft,
    render_draft,
    resolve_editor,
)
from .issue_draft import DraftError, IssueDraft, build_payload, render_preview
from .jira_client import JiraApiError, JiraSiteClient, markdown_to_adf
from .patterns import looks_like_account_id
from .prompts import ask_choice, ask_multiline, ask_text, confirm, confirm_or_edit, stdin_is_tty


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

    _ensure_issue_type_allowed(cfg, payload["issue_type"])

    try:
        return IssueDraft.from_payload(payload)
    except DraftError as exc:
        sys.stderr.write(f"[input] {exc}\n")
        raise SystemExit(2)


def _ensure_issue_type_allowed(cfg: TakoConfig, issue_type: str) -> None:
    if cfg.allowed_issue_types and issue_type not in cfg.allowed_issue_types:
        sys.stderr.write(
            f"[input] 허용 안 된 이슈 타입: {issue_type!r}. "
            f"허용: {', '.join(cfg.allowed_issue_types)}\n"
        )
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
        # 미해결 입력값만 실어둠. cmd_new 가 REST 로 해석 후 accountId 로 교체.
        payload["assignee_pending"] = assignee_input
    if pre.get("reporter_pending"):
        # 보고자는 인터랙티브에서 *묻지 않는다*. 대부분 본인이고, 지정하려면 별도 권한이
        # 필요해 매번 묻는 값이 아니다. --reporter 로 들어온 값만 그대로 흘려보낸다.
        payload["reporter_pending"] = pre["reporter_pending"]
    if story_points is not None:
        payload["story_points"] = story_points
    if duedate is not None:
        payload["duedate"] = duedate
    if links:
        payload["links"] = links

    return payload


def cmd_preview(args: Any, cfg: TakoConfig) -> int:
    if args.draft_file:
        payload = _payload_from_draft_path(args.draft_file, cfg, args.issue_type)
        # REST 없이 도는 커맨드라 me/이메일 해석은 못 한다. 해석된 값처럼 보이면
        # 확인의 의미가 깨지므로 미해석 표시를 달아서 보여준다.
        for role in ("assignee", "reporter"):
            pending = payload.pop(f"{role}_pending", None)
            if pending:
                payload[role] = pending
                payload[f"{role}_label"] = f"{pending} (미해석 — 생성 시 조회)"
        try:
            draft = IssueDraft.from_payload(payload)
        except DraftError as exc:
            sys.stderr.write(f"[input] {exc}\n")
            return 2
        print(render_preview(draft))
        return 0
    draft = _draft_from_dict(_read_stdin_json(), cfg)
    print(render_preview(draft))
    return 0


def cmd_build(cfg: TakoConfig) -> int:
    draft = _draft_from_dict(_read_stdin_json(), cfg)
    print(json.dumps(build_payload(draft, cfg.jira.custom_fields), ensure_ascii=False, indent=2))
    return 0


def cmd_interactive(cfg: TakoConfig) -> int:
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


class _ResolveError(Exception):
    """담당자/보고자 입력 해석 실패. 메시지는 사용자에게 그대로 보여줄 한국어."""


def _resolve_user(value: str, client: JiraSiteClient, *, role: str = "담당자") -> tuple[str, str]:
    """사용자 입력 (me / 이메일 / accountId) → (accountId, 표시 라벨).

    role 은 오류 문구 앞에 붙는 역할 이름 — 담당자(assignee) / 보고자(reporter)
    양쪽이 같은 해석 규칙을 쓰므로 문구만 갈아 끼운다.
    실패 시 _ResolveError — 호출자가 종료할지, 편집기로 되돌릴지 정한다.

    list_query._assignee_clause 와 의도적 분리:
    저쪽은 JQL 문자열만 만들어 currentUser()/이메일 그대로 두면 Jira 가 해석.
    여기는 issue 생성 페이로드의 사용자 필드에 *accountId 가 필수* 라서
    REST 두 번(/myself, /user/search) 으로 직접 해석한다.
    """
    v = value.strip()
    if v.lower() in {"me", "current", "self"}:
        try:
            data = client.get_myself()
        except JiraApiError as exc:
            raise _ResolveError(f"[{role}] 본인 정보 조회 실패: {exc}")
        acc = data.get("accountId")
        if not acc:
            raise _ResolveError(f"[{role}] myself 응답에 accountId 없음.")
        name = data.get("displayName") or "me"
        email = data.get("emailAddress")
        label = f"{name}" + (f" ({email})" if email else "")
        return acc, label

    if "@" in v:
        try:
            users = client.search_users(v)
        except JiraApiError as exc:
            raise _ResolveError(f"[{role}] 사용자 검색 실패: {exc}")
        # 이메일 정확 일치 우선
        matches = [u for u in users if (u.get("emailAddress") or "").lower() == v.lower()]
        if not matches:
            matches = users
        if not matches:
            raise _ResolveError(
                f"[{role}] 일치하는 사용자 없음: {v!r}\n"
                "  사이트 GDPR 설정에 따라 이메일 검색이 제한될 수 있음 — accountId 직접 입력 권장."
            )
        if len(matches) > 1:
            raise _ResolveError(
                f"[{role}] 이메일 검색 결과가 {len(matches)}건. 정확히 1건만 허용.\n"
                "  accountId 직접 입력 또는 다른 검색어 사용."
            )
        u = matches[0]
        acc = u.get("accountId")
        if not acc:
            raise _ResolveError(f"[{role}] 검색 결과에 accountId 없음.")
        name = u.get("displayName") or v
        if (u.get("emailAddress") or "").lower() == v.lower():
            return acc, f"{name} ({v})"
        # GDPR 설정으로 응답에 이메일이 빠지면 대조를 못 한 채 채택된 것 —
        # 입력한 이메일을 확인된 것처럼 라벨에 박으면 안 된다.
        return acc, f"{name} ({v}, 이메일 미확인)"

    if looks_like_account_id(v):
        # accountId 패턴 — 그대로 사용
        return v, v

    raise _ResolveError(
        f"[{role}] 형식 미지원: {value!r}\n"
        "  지원: 'me' / 이메일 / accountId\n"
        "  한국어 이름·닉네임은 v1.x 미지원."
    )


def _can_modify_reporter(client: JiraSiteClient, project_key: str) -> bool:
    """보고자를 지정해도 되는 프로젝트인지 미리 확인.

    Jira 는 MODIFY_REPORTER(팀 관리형 이름은 'Edit reporters') 를 기본적으로
    프로젝트 관리자에게만 준다. 권한 없이 fields.reporter 를 실어 보내면
    POST /issue 가 통째로 400 이라 *티켓 자체가 안 만들어진다*. 링크 실패처럼
    부분 실패로 끝나지 않으니, 미리보기·확인 단계로 사용자 시간을 쓰기 전에
    한 번 물어보고 접는다.

    판정이 안 될 때(조회 실패·응답 모양 이상)는 막지 않는다. 확인용 호출이
    본 작업을 가로막는 쪽이 더 나쁘다.
    """
    try:
        allowed = client.check_project_permission("MODIFY_REPORTER", project_key=project_key)
    except JiraApiError as exc:
        sys.stderr.write(f"[보고자] 권한 확인 실패, 확인 없이 진행: {exc}\n")
        return True
    if allowed is None:
        sys.stderr.write("[보고자] 권한 판정 불가, 확인 없이 진행.\n")
        return True
    if allowed:
        return True
    sys.stderr.write(
        f"[보고자] {project_key} 프로젝트에서 보고자를 지정할 권한이 없음.\n"
        "  Jira 는 'Modify Reporter' (팀 관리형은 'Edit reporters') 권한을\n"
        "  기본적으로 프로젝트 관리자에게만 부여한다.\n"
        "  --reporter 를 빼면 본인이 보고자로 생성됨.\n"
    )
    return False


def _apply_pendings(payload: dict[str, Any], client: JiraSiteClient, cfg: TakoConfig) -> int | None:
    """assignee_pending / reporter_pending 을 accountId 로 해석해 payload 에 반영.

    성공 시 None, 실패 시 stderr 에 사유를 쓰고 종료 코드 반환.
    실패해도 pending 키는 남겨 둔다 — 편집기 라운드가 사용자가 넣은
    원래 입력을 그대로 다시 보여줄 수 있어야 한다.
    """
    pending = payload.get("assignee_pending")
    if pending:
        try:
            acc, label = _resolve_user(pending, client, role="담당자")
        except _ResolveError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        del payload["assignee_pending"]
        payload["assignee"] = acc
        payload["assignee_label"] = label
        sys.stderr.write(f"[담당자] {pending} → {label}\n")

    reporter_pending = payload.get("reporter_pending")
    if reporter_pending:
        if not _can_modify_reporter(client, payload.get("project") or cfg.jira.default_project):
            return 2
        try:
            acc, label = _resolve_user(reporter_pending, client, role="보고자")
        except _ResolveError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        del payload["reporter_pending"]
        payload["reporter"] = acc
        payload["reporter_label"] = label
        sys.stderr.write(f"[보고자] {reporter_pending} → {label}\n")
    return None


def _payload_from_draft_fields(
    fields: dict[str, Any],
    body: str,
    cfg: TakoConfig,
    *,
    issue_type: str,
    prev: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """초안 파일 파싱 결과 → cmd_new 루프가 쓰는 payload dict.

    assignee/reporter 는 이전 라운드에서 해석한 accountId 와 같으면 그대로 두고,
    달라졌으면 pending 으로 되돌려 다음 라운드에서 재해석한다.
    """
    prev = prev or {}
    payload: dict[str, Any] = {
        "project": str(fields.get("project") or cfg.jira.default_project),
        "issue_type": issue_type,
        "summary": str(fields.get("summary") or ""),
        "description": body,
        "labels": fields.get("labels") or [],
    }
    parent = fields.get("parent")
    if parent:
        payload["parent_epic"] = cfg.resolve_epic(str(parent).strip())
    for role in ("assignee", "reporter"):
        raw = fields.get(role)
        if not raw:
            continue
        raw = str(raw).strip()
        if raw == prev.get(role):
            payload[role] = raw
            if prev.get(f"{role}_label"):
                payload[f"{role}_label"] = prev[f"{role}_label"]
        else:
            payload[f"{role}_pending"] = raw
    if fields.get("story_points") is not None:
        payload["story_points"] = fields["story_points"]
    if fields.get("duedate"):
        payload["duedate"] = str(fields["duedate"])
    if fields.get("links"):
        payload["links"] = fields["links"]
    return payload


def _payload_from_draft_path(path_str: str, cfg: TakoConfig, issue_type_arg: str | None) -> dict[str, Any]:
    """--draft-file 경로 → payload. 실패는 stderr + SystemExit(2)."""
    path = Path(path_str).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"[초안] 파일 못 읽음: {exc}\n")
        raise SystemExit(2)
    try:
        fields, body = parse_draft(text)
    except DraftFileError as exc:
        sys.stderr.write(f"[초안] {exc}\n")
        raise SystemExit(2)
    issue_type = issue_type_arg or cfg.jira.default_issue_type
    _ensure_issue_type_allowed(cfg, issue_type)
    return _payload_from_draft_fields(fields, body, cfg, issue_type=issue_type)


def _edit_round(payload: dict[str, Any], cfg: TakoConfig) -> dict[str, Any] | None:
    """초안 파일을 에디터로 열어 payload 를 고친다. 포기하면 None.

    파싱/검증 실패 시 같은 파일을 다시 연다 — 사용자가 쓴 내용을
    잃지 않는 게 이 왕복의 존재 이유다.
    """
    editor = resolve_editor(cfg.editor)
    path = new_draft_path()
    path.write_text(render_draft(payload), encoding="utf-8")
    sys.stderr.write(f"에디터 열기: {' '.join(editor)} {path}\n")
    while True:
        try:
            open_in_editor(path, editor)
            fields, body = parse_draft(path.read_text(encoding="utf-8"))
            candidate = _payload_from_draft_fields(
                fields, body, cfg, issue_type=payload["issue_type"], prev=payload
            )
            # 구조 검증만 미리 태운다. pending 해석은 루프 상단에서.
            IssueDraft.from_payload(candidate)
        except (DraftFileError, DraftError, OSError) as exc:
            sys.stderr.write(f"[초안] {exc}\n")
            if confirm("다시 열어서 고칠까?"):
                continue
            # 포기해도 파일은 지우지 않는다 — 쓴 내용을 잃지 않는 게 이 왕복의 존재 이유.
            # tako new --draft-file 로 이어서 쓸 수 있다.
            sys.stderr.write(f"초안 파일 유지: {path}\n")
            return None
        path.unlink(missing_ok=True)
        return candidate


def cmd_new(args: Any, cfg: TakoConfig) -> int:
    client = build_client(cfg, args.credentials)

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
    if args.reporter:
        prefilled["reporter_pending"] = args.reporter
    if args.story_points is not None:
        prefilled["story_points"] = args.story_points
    if args.duedate:
        prefilled["duedate"] = args.duedate
    if args.link:
        prefilled["links"] = list(args.link)

    if args.draft_file:
        # 파일이 내용 전체를 가진다 — 내용 인자와 섞이면 어느 쪽이 이기는지
        # 모호해지므로 금지. 파일에 못 싣는 --issue-type 만 예외.
        conflicting = (
            args.project, args.summary, args.parent,
            args.assignee, args.reporter, args.duedate,
        )
        if (
            any(conflicting)
            or args.description is not None
            or args.label
            or args.link
            or args.story_points is not None
        ):
            sys.stderr.write("--draft-file 은 내용 인자(--summary 등)와 같이 못 씀. --issue-type 만 예외.\n")
            return 2
        if not stdin_is_tty() and not args.yes:
            # 비대화형에서 확인 응답을 stdin 아무 내용에서 읽게 두면 빈 줄 하나로
            # 생성될 수 있다. 확인 주체를 명시하게 강제한다.
            sys.stderr.write("비대화형 --draft-file 은 --yes 필요 (확인은 호출 쪽 책임).\n")
            return 2
        payload = _payload_from_draft_path(args.draft_file, cfg, args.issue_type)
        if args.edit:
            if not stdin_is_tty():
                sys.stderr.write("--edit 는 TTY 필요.\n")
                return 2
            edited = _edit_round(payload, cfg)
            if edited is None:
                sys.stderr.write("취소.\n")
                return 1
            payload = edited
    elif args.edit:
        if not stdin_is_tty():
            sys.stderr.write("--edit 는 TTY 필요. 비대화형은 --draft-file 사용.\n")
            return 2
        # 질문 릴레이 대신 처음부터 에디터로. 빈 필수 항목은 검증이 잡아 다시 연다.
        payload = dict(prefilled)
        payload.setdefault("project", cfg.jira.default_project)
        payload.setdefault("issue_type", cfg.jira.default_issue_type)
        payload.setdefault("summary", "")
        payload.setdefault("description", "")
        if "parent_epic" in payload:
            payload["parent_epic"] = cfg.resolve_epic(payload["parent_epic"])
        if "assignee_pending" not in payload and cfg.jira.default_assignee:
            payload["assignee_pending"] = cfg.jira.default_assignee
        edited = _edit_round(payload, cfg)
        if edited is None:
            sys.stderr.write("취소.\n")
            return 1
        payload = edited
    else:
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

    # 어느 입력 경로로 왔든 로컬에서 걸러야 Jira 400 왕복을 아낀다.
    _ensure_issue_type_allowed(cfg, payload["issue_type"])

    # 해석 → 검증 → 미리보기 → Y/n/e 루프. 편집(e)을 고르면 초안 파일을
    # 에디터로 열고, 저장분을 다시 이 루프에 태운다.
    can_edit = stdin_is_tty() and not args.yes
    while True:
        # 담당자/보고자 해석 — me/이메일 → accountId. 어느 입력 경로든
        # 미해결 값은 'assignee_pending'/'reporter_pending' 키로 들어옴.
        rc = _apply_pendings(payload, client, cfg)
        if rc is not None:
            if not can_edit:
                return rc
            if not confirm("에디터로 열어서 고칠까?", default=False):
                sys.stderr.write("취소.\n")
                return rc
            edited = _edit_round(payload, cfg)
            if edited is None:
                sys.stderr.write("취소.\n")
                return rc
            payload = edited
            continue

        try:
            draft = IssueDraft.from_payload(payload)
        except DraftError as exc:
            sys.stderr.write(f"[input] {exc}\n")
            return 2

        sys.stderr.write("\n")
        print(render_preview(draft), file=sys.stderr)
        sys.stderr.write("\n")

        if args.yes:
            break
        decision = confirm_or_edit("Jira 에 생성?")
        if decision == "yes":
            break
        if decision == "no":
            sys.stderr.write("취소.\n")
            return 1
        edited = _edit_round(payload, cfg)
        if edited is not None:
            payload = edited
        # 편집을 포기(None)해도 미리보기로 되돌아간다 — 이미 있던 초안은 유효하다.

    built = build_payload(draft, cfg.jira.custom_fields)
    for w in built["meta"].get("warnings", []):
        sys.stderr.write(f"[경고] {w}\n")
    fields = built["payload"]["fields"]
    fields["description"] = markdown_to_adf(draft.description)

    try:
        result = client.create_issue(fields)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        if draft.reporter and "reporter" in str(exc):
            # 권한은 통과했는데도 거부된 경우 — 남는 원인은 화면 구성이다.
            sys.stderr.write(
                "  보고자 지정이 거부됨. 권한이 있어도 Reporter 필드가 프로젝트의\n"
                "  Edit / View 화면에 없으면 같은 오류가 난다.\n"
                "  --reporter 를 빼면 본인이 보고자로 생성됨.\n"
            )
        return 2

    sys.stderr.write(f"\n생성 완료\n  키:   {result.key}\n  링크: {result.url}\n")

    if cfg.jira.auto_copy_url:
        from .clipboard import copy_to_clipboard
        if copy_to_clipboard(result.url):
            sys.stderr.write("  (링크 클립보드 복사됨)\n")

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
