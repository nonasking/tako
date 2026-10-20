"""tako fields set / detect — custom field 매핑 등록.

set 은 config 를 로드하지 않고 파일만 다시 쓴다 (없으면 그 자리에서 오류 안내).
detect 는 사이트 필드 목록을 REST 로 받아 후보를 고르고, --save 면 set 과 같은 경로로 쓴다.
"""

from __future__ import annotations

import shlex
import sys
from typing import Any

from .cmd_common import build_client
from .config import TakoConfig, resolve_config_path
from .fields import SEARCH_KEYWORDS, filter_candidates, warn_comment_loss, write_field_mapping
from .jira_client import JiraApiError
from .prompts import ask_text, stdin_is_tty


def _check_supported(name: str) -> bool:
    if name in SEARCH_KEYWORDS:
        return True
    sys.stderr.write(f"[fields] 지원하지 않는 이름: {name!r}. 지원: {', '.join(SEARCH_KEYWORDS)}\n")
    return False


def _save_mapping(args: Any, field_id: str) -> bool:
    """config 에 jira.fields.<name> 기록. 실패 사유는 stderr 로."""
    config_path = resolve_config_path(args.config)
    warn_comment_loss()
    try:
        write_field_mapping(args.name, field_id, config_path)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"[fields] {exc}\n")
        return False
    return True


def cmd_fields_set(args: Any) -> int:
    if not _check_supported(args.name):
        return 2
    if not args.field_id.startswith("customfield_"):
        sys.stderr.write(
            f"[경고] field_id 가 'customfield_' 로 시작하지 않습니다: {args.field_id!r}\n"
        )
    if not _save_mapping(args, args.field_id):
        return 2
    sys.stderr.write(f"등록 완료: jira.fields.{args.name} = {args.field_id}\n")
    return 0


def cmd_fields_detect(args: Any, cfg: TakoConfig) -> int:
    if not _check_supported(args.name):
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
        if not _save_mapping(args, chosen_id):
            return 2
        sys.stderr.write(f"\n등록 완료: jira.fields.{args.name} = {chosen_id}\n")
    else:
        # --config 로 다른 파일을 쓰고 있으면 안내 경로와 재실행 명령도 그 파일을 가리켜야 한다.
        # 빠뜨리면 사용자가 힌트대로 쳤을 때 기본 경로 config 에 써진다.
        config_opt = f"--config {shlex.quote(args.config)} " if args.config else ""
        sys.stderr.write(
            f"\n다음을 {resolve_config_path(args.config)} 의 jira 블록에 추가하세요:\n\n"
            f"  fields:\n    {args.name}: {chosen_id}\n\n"
            f"또는 자동 등록: tako {config_opt}fields set {args.name} {chosen_id}\n"
            f"또는 한 번에:   tako {config_opt}fields detect {args.name} --save\n"
        )

    print(chosen_id)
    return 0
