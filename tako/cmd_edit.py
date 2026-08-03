"""tako update / retype — 기존 이슈 수정 계열 서브커맨드.

수정 전 현재 상태를 조회해 미리보기 → Y/N 확인. --yes 로만 생략.
"""

from __future__ import annotations

import sys
from typing import Any

from .adf_to_md import adf_to_markdown
from .cmd_common import build_client, today_kst_str
from .config import TakoConfig
from .jira_client import JiraApiError, markdown_to_adf
from .patterns import extract_issue_key
from .prompts import confirm


def _read_body_input(args: Any) -> str:
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


def cmd_update(args: Any, cfg: TakoConfig) -> int:
    key = extract_issue_key(args.key)

    new_summary: str | None = None
    if args.summary is not None:
        stripped = args.summary.strip()
        if not stripped:
            sys.stderr.write("[update] --summary 가 빈 문자열. 제목은 비울 수 없음.\n")
            return 2
        new_summary = stripped

    # body 입력 결정:
    # - --body 인자 있음 → 그 값
    # - --body 없고 --summary 있음 → 본문 안 바꿈 (None)
    # - 둘 다 없음 → stdin/TTY 로 본문 받기 (기존 흐름)
    body_md: str | None
    if args.body is not None:
        body_md = args.body
    elif new_summary is not None:
        body_md = None  # 제목만 변경
    else:
        body_md = _read_body_input(args)

    if new_summary is None and body_md is None:
        sys.stderr.write("[update] --summary 또는 --body 중 최소 하나는 필요.\n")
        return 2

    client = build_client(cfg, args.credentials)

    # 현재 상태 조회 — 미리보기·append 머지에 필요한 두 필드만 받는다.
    try:
        issue = client.get_issue(key, fields=["summary", "description"])
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2
    current_summary = (issue.get("fields") or {}).get("summary", "")
    current_md = adf_to_markdown((issue.get("fields") or {}).get("description"))

    merged_md: str | None = None
    new_section: str | None = None
    if body_md is not None:
        if args.mode == "append":
            section_header = f"## {args.section} ({today_kst_str()})"
            new_section = f"{section_header}\n{body_md}"
            merged_md = (current_md.rstrip() + "\n\n" + new_section) if current_md.strip() else new_section
        else:  # overwrite
            new_section = body_md
            merged_md = body_md

    # 미리보기
    sys.stderr.write(f"\n[{key}] {current_summary}\n")
    sys.stderr.write(f"링크: https://{cfg.jira.site}/browse/{key}\n")

    if new_summary is not None:
        sys.stderr.write("\n--- 제목 변경 ---\n")
        sys.stderr.write(f"  현재: {current_summary}\n")
        sys.stderr.write(f"  변경: {new_summary}\n")

    if body_md is not None:
        sys.stderr.write(f"\n--- 본문 모드: {args.mode} ---\n")
        if args.mode == "append":
            sys.stderr.write("\n[추가될 섹션]\n")
            sys.stderr.write((new_section or "") + "\n")
        else:
            sys.stderr.write("\n[현재 본문 → 교체될 것]\n")
            sys.stderr.write((current_md or "(비어 있음)") + "\n")
            sys.stderr.write("\n[새 본문]\n")
            sys.stderr.write(body_md + "\n")
    sys.stderr.write("\n")

    if not args.yes:
        scope = []
        if new_summary is not None:
            scope.append("제목")
        if body_md is not None:
            scope.append("본문")
        if not confirm(f"이대로 {' + '.join(scope)} 을 업데이트할까요?"):
            sys.stderr.write("취소.\n")
            return 1

    fields: dict[str, Any] = {}
    if new_summary is not None:
        fields["summary"] = new_summary
    if merged_md is not None:
        fields["description"] = markdown_to_adf(merged_md)
    try:
        client.update_issue_fields(key, fields)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    sys.stderr.write(f"\n업데이트 완료: {key}\n  링크: https://{cfg.jira.site}/browse/{key}\n")
    print(key)
    return 0


def _match_issue_type(target: str, allowed: list[dict[str, Any]]) -> dict[str, Any] | None:
    """사용자가 준 유형 이름(또는 id)을 editmeta allowedValues 에서 찾아 항목 반환.

    이름 대소문자 무시 매칭 우선, 그래도 없으면 id 정확 일치. 못 찾으면 None.
    매칭 성공 자체가 "그 이슈에 허용되는 변환" 을 보증한다 (allowedValues 가
    같은 계층 타입만 담으므로 하위작업↔표준 경계 변환은 여기서 자연히 걸러짐).
    """
    t = target.strip()
    for item in allowed:
        if (item.get("name") or "").strip().lower() == t.lower():
            return item
    for item in allowed:
        if str(item.get("id") or "") == t:
            return item
    return None


def cmd_retype(args: Any, cfg: TakoConfig) -> int:
    key = extract_issue_key(args.key)
    target = (args.to or args.to_opt or "").strip()
    if not target:
        sys.stderr.write("[retype] 새 유형을 지정하세요. 예: tako retype WL-1234 Story\n")
        return 2

    client = build_client(cfg, args.credentials)

    # 현재 상태 조회 (미리보기·사후 비교용 두 필드만) + 편집 가능 유형 목록.
    try:
        issue = client.get_issue(key, fields=["summary", "issuetype"])
        meta = client.get_editmeta(key)
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    current_fields = issue.get("fields") or {}
    current_summary = current_fields.get("summary", "")
    current_type = ((current_fields.get("issuetype") or {}).get("name")) or "?"

    issuetype_meta = (meta.get("fields") or {}).get("issuetype")
    if not issuetype_meta:
        # editmeta 에 issuetype 자체가 없음 = 이 이슈는 편집 화면에서 유형 변경 불가.
        sys.stderr.write(
            f"[retype] 이 이슈는 유형을 변경할 수 없습니다 ({key}, 현재 '{current_type}').\n"
            "  편집 화면에 유형 필드가 없습니다. 계층을 바꾸는 변환(하위작업↔표준)이거나\n"
            "  사이트 설정상 막혀 있을 수 있어요. 그 경우 Jira UI 의 '이동(Move)' 으로 처리하세요.\n"
        )
        return 2

    allowed = issuetype_meta.get("allowedValues") or []
    chosen = _match_issue_type(target, allowed)
    if chosen is None:
        names = [str(v.get("name") or "?") for v in allowed]
        sys.stderr.write(
            f"[retype] '{target}' 으로는 바꿀 수 없습니다 ({key}, 현재 '{current_type}').\n"
            f"  바꿀 수 있는 유형: {', '.join(names) if names else '(없음)'}\n"
            "  하위작업↔표준 같은 계층 경계 변환은 목록에 없으며 UI 의 '이동(Move)' 이 필요합니다.\n"
        )
        return 2

    chosen_name = str(chosen.get("name") or target)
    chosen_id = str(chosen.get("id") or "")
    if not chosen_id:
        sys.stderr.write(f"[retype] 유형 '{chosen_name}' 의 id 를 찾지 못했습니다.\n")
        return 2

    if chosen_name.strip().lower() == current_type.strip().lower():
        sys.stderr.write(f"[retype] 이미 '{current_type}' 유형입니다. 변경할 게 없습니다: {key}\n")
        return 0

    # 미리보기
    sys.stderr.write(f"\n[{key}] {current_summary}\n")
    sys.stderr.write(f"링크: https://{cfg.jira.site}/browse/{key}\n")
    sys.stderr.write("\n--- 유형 변경 ---\n")
    sys.stderr.write(f"  현재: {current_type}\n")
    sys.stderr.write(f"  변경: {chosen_name}\n")
    sys.stderr.write(
        "  (되돌리려면 다시 retype 해야 합니다. 새 유형의 워크플로·필수 필드 차이로\n"
        "   거부될 수 있으며, 그 경우 아래 Jira 메시지를 그대로 보여드립니다.)\n\n"
    )

    if not args.yes:
        if not confirm(f"{key} 유형을 '{current_type}' → '{chosen_name}' 으로 바꿀까요?"):
            sys.stderr.write("취소.\n")
            return 1

    try:
        client.update_issue_fields(key, {"issuetype": {"id": chosen_id}})
    except JiraApiError as exc:
        sys.stderr.write(f"[jira] {exc}\n")
        return 2

    # 사후 확인 — 워크플로/스크린 차이로 200 이 떠도 결과가 어긋날 수 있어 재조회.
    try:
        after = client.get_issue(key, fields=["issuetype"])
        after_type = ((after.get("fields") or {}).get("issuetype") or {}).get("name") or "?"
    except JiraApiError:
        after_type = chosen_name  # 재조회 실패 시 요청값으로 보고

    if after_type.strip().lower() != chosen_name.strip().lower():
        sys.stderr.write(
            f"\n[경고] 적용 후 유형이 '{after_type}' 입니다 (요청: '{chosen_name}').\n"
            "  사이트 워크플로/화면 설정이 변경을 일부만 반영했을 수 있어요. Jira 에서 확인하세요.\n"
        )
        sys.stderr.write(f"링크: https://{cfg.jira.site}/browse/{key}\n")
        print(key)
        return 2

    sys.stderr.write(
        f"\n유형 변경 완료: {key}\n  {current_type} → {after_type}\n"
        f"  링크: https://{cfg.jira.site}/browse/{key}\n"
    )
    print(key)
    return 0
