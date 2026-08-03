"""이슈 키 / 사용자 식별자 공용 패턴.

같은 도메인 개념(이슈 키, accountId)이 main / list_query / issue_draft 에
각각 정의돼 "변경 시 같이 맞출 것" 주석으로 손동기화하던 것을 한곳으로 모음.
JQL 빌더 같은 순수 모듈도 import 하므로 여기는 CLI/네트워크 의존이 없어야 한다.
"""

from __future__ import annotations

import re


# 'WL-1234' — 프로젝트 키(대문자 시작) + 번호
ISSUE_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*-\d+$")

# browse URL 안의 이슈 키 — 'https://.../browse/WL-1234' 형태에서 추출
KEY_IN_URL_RE = re.compile(r"/browse/([A-Z][A-Z0-9_]*-\d+)", re.IGNORECASE)

# accountId — '557058:<uuid>' 또는 24자 hex 등 형태가 다양해 문자 집합만 본다
ACCOUNT_ID_RE = re.compile(r"^[a-zA-Z0-9:\-]+$")

# accountId 로 보기 위한 최소 길이 — 이메일/키워드와의 오인 방지 휴리스틱
_ACCOUNT_ID_MIN_LEN = 12


def extract_issue_key(raw: str) -> str:
    """'wl-123' / browse URL → 대문자 이슈 키. URL 이 아니면 대문자화만."""
    raw = raw.strip()
    if "/" in raw:
        m = KEY_IN_URL_RE.search(raw)
        if m:
            return m.group(1).upper()
    return raw.upper()


def looks_like_account_id(value: str) -> bool:
    """me/이메일 판별을 통과한 입력이 accountId 로 볼 만한 형태인지."""
    return bool(ACCOUNT_ID_RE.match(value)) and len(value) >= _ACCOUNT_ID_MIN_LEN
