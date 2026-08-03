"""Jira REST v3 클라이언트.

422/400 같은 의미 있는 실패는 즉시 보고. 재시도는 _request 의 정책 참고 —
네트워크 끊김·429 는 재시도, 5xx 는 멱등 호출만 (POST /issue 중복 생성 방지).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

from .auth import Credentials


class JiraApiError(Exception):
    pass


@dataclass(frozen=True)
class CreatedIssue:
    key: str
    url: str
    raw: dict[str, Any]


# 429 Retry-After 가 없거나 비상식적으로 클 때의 대기 상한 (초)
_MAX_RETRY_AFTER = 15.0


class JiraSiteClient:
    def __init__(self, site: str, creds: Credentials, *, timeout: float = 10.0, max_attempts: int = 3):
        self._site = site.rstrip("/")
        self._auth = creds.as_basic_auth()
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        self._sleep = time.sleep  # 테스트에서 대기 없이 재시도 검증할 수 있게 주입점
        self._session = requests.Session()
        self._session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })

    @property
    def site(self) -> str:
        return self._site

    def _endpoint(self, path: str) -> str:
        return f"https://{self._site}/rest/api/3/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        retry_responses: bool | None = None,
    ) -> requests.Response:
        """재시도 정책:

        - 연결 오류/타임아웃: 항상 재시도 — 요청이 서버에 닿기 전 실패.
        - 429: 항상 재시도 — 처리 전에 거절된 요청이라 중복 위험 없음. Retry-After 존중.
        - 5xx: *멱등 호출만* 재시도. POST /issue 는 서버가 처리를 마쳤을 수도 있어
          재시도하면 티켓이 중복 생성될 수 있다. 기본값은 메서드로 추정
          (GET/PUT/DELETE = 재시도) — 검색처럼 POST 여도 안전한 곳은 호출자가
          retry_responses=True 로 명시한다.
        """
        if retry_responses is None:
            retry_responses = method.upper() in ("GET", "PUT", "DELETE")
        url = self._endpoint(path)
        for attempt in range(1, self._max_attempts + 1):
            last_try = attempt == self._max_attempts
            try:
                resp = self._session.request(
                    method, url, json=json, auth=self._auth, timeout=self._timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if last_try:
                    raise JiraApiError(f"네트워크 오류: {exc}") from exc
                self._sleep(float(attempt))
                continue
            if resp.status_code == 429 and not last_try:
                self._sleep(min(_retry_after_seconds(resp) or attempt * 2.0, _MAX_RETRY_AFTER))
                continue
            if 500 <= resp.status_code < 600 and retry_responses and not last_try:
                self._sleep(float(attempt))
                continue
            return resp
        raise AssertionError("unreachable")  # 루프가 항상 return/raise 로 끝남

    def create_issue(self, fields: dict[str, Any]) -> CreatedIssue:
        # fields = build_payload()['payload']['fields']
        resp = self._request("POST", "issue", json={"fields": fields})
        if resp.status_code == 201:
            data = resp.json()
            key = data.get("key")
            if not key:
                raise JiraApiError(f"응답에 키 없음: {data}")
            return CreatedIssue(key=key, url=f"https://{self._site}/browse/{key}", raw=data)
        raise JiraApiError(_format_error(resp))

    def list_fields(self) -> list[dict[str, Any]]:
        """GET /rest/api/3/field — 사이트의 모든 필드 목록."""
        resp = self._request("GET", "field")
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = resp.json()
        if not isinstance(data, list):
            raise JiraApiError(f"field 응답이 리스트가 아님: {type(data).__name__}")
        return data

    def get_issue(self, key: str, *, fields: list[str] | None = None) -> dict[str, Any]:
        """GET /rest/api/3/issue/<key> — 단일 이슈 상세.

        fields 를 주면 그 필드만 수신 (update/retype 처럼 일부만 쓰는 호출의
        전송량 절약). 생략하면 전체 — show 가 이 경로.
        """
        path = f"issue/{key}"
        if fields:
            path += "?fields=" + ",".join(fields)
        resp = self._request("GET", path)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            raise JiraApiError(f"이슈를 찾을 수 없음: {key}")
        raise JiraApiError(_format_error(resp))

    def get_editmeta(self, key: str) -> dict[str, Any]:
        """GET /rest/api/3/issue/<key>/editmeta — 이 이슈에서 *지금 편집 가능한* 필드 메타.

        유형 변경에 쓰는 핵심 소스. 응답 fields.issuetype.allowedValues 가
        "이 이슈를 어떤 유형으로 바꿀 수 있는가" 의 정답 목록(각 항목에 id/name).
        fields 에 issuetype 키가 아예 없으면 = 이 이슈는 유형 변경 불가(편집 화면에 없음).
        같은 계층(표준↔표준, 하위작업↔하위작업) 타입만 나열되므로 계층 경계 변환은
        목록에 안 나타나 자연히 걸러진다.
        """
        resp = self._request("GET", f"issue/{key}/editmeta")
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code == 404:
            raise JiraApiError(f"이슈를 찾을 수 없음: {key}")
        raise JiraApiError(_format_error(resp))

    def list_comments(self, key: str, *, max_results: int = 5) -> list[dict[str, Any]]:
        """최근 코멘트 N개. 응답에서 최신순으로 정렬해 반환."""
        resp = self._request(
            "GET", f"issue/{key}/comment?orderBy=-created&maxResults={int(max_results)}"
        )
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = resp.json()
        comments = data.get("comments") if isinstance(data, dict) else None
        return comments if isinstance(comments, list) else []

    def create_issue_link(self, *, type_name: str, inward_key: str, outward_key: str) -> None:
        """POST /rest/api/3/issueLink — 두 이슈 사이 link 생성.

        tako 의 일반 사용 시:
          inward_key  = 새로 만든 티켓
          outward_key = 사용자가 지정한 대상 (--link KEY)
        Jira 가 "<inward> <type> <outward>" 관계로 해석.
        성공 시 빈 응답(201). 실패면 JiraApiError.
        """
        body = {
            "type": {"name": type_name},
            "inwardIssue": {"key": inward_key},
            "outwardIssue": {"key": outward_key},
        }
        resp = self._request("POST", "issueLink", json=body)
        if resp.status_code in (200, 201):
            return
        raise JiraApiError(_format_error(resp))

    def get_myself(self) -> dict[str, Any]:
        """GET /rest/api/3/myself — 현재 사용자 정보 (accountId / displayName / emailAddress)."""
        resp = self._request("GET", "myself")
        if resp.status_code == 200:
            return resp.json()
        raise JiraApiError(_format_error(resp))

    def search_users(self, query: str) -> list[dict[str, Any]]:
        """GET /rest/api/3/user/search?query=... — 이메일/이름으로 사용자 검색.

        사이트 GDPR 설정에 따라 이메일 기반 검색이 제한될 수 있음. v1 은 이메일만 안내.
        """
        from urllib.parse import quote
        resp = self._request("GET", f"user/search?query={quote(query)}")
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = resp.json()
        return data if isinstance(data, list) else []

    def check_project_permission(self, permission: str, *, project_key: str) -> bool | None:
        """GET /rest/api/3/mypermissions — 이 프로젝트에서 해당 권한을 갖고 있는지.

        permissions 파라미터는 필수다 (빈 조회는 Atlassian 이 막았다). 권한 키는
        Jira 내장 이름 그대로 — 예: MODIFY_REPORTER.
        반환 True/False. 응답에 해당 키가 없거나 모양이 다르면 None(판정 불가) —
        호출자가 '막지 말고 진행' 을 택할 수 있게 예외 대신 None 으로 알린다.
        """
        from urllib.parse import quote
        resp = self._request(
            "GET",
            f"mypermissions?projectKey={quote(project_key)}&permissions={quote(permission)}",
        )
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = resp.json()
        block = data.get("permissions") if isinstance(data, dict) else None
        entry = block.get(permission) if isinstance(block, dict) else None
        if not isinstance(entry, dict) or "havePermission" not in entry:
            return None
        return bool(entry["havePermission"])

    def update_issue_fields(self, key: str, fields: dict[str, Any]) -> None:
        """PUT /rest/api/3/issue/<key> — 필드 업데이트.

        호출자가 fields dict 를 *완성된 형태*로 넘긴다 (description 은 ADF JSON 트리).
        성공 시 빈 응답(204).
        """
        resp = self._request("PUT", f"issue/{key}", json={"fields": fields})
        if resp.status_code in (200, 204):
            return
        if resp.status_code == 404:
            raise JiraApiError(f"이슈를 찾을 수 없음: {key}")
        raise JiraApiError(_format_error(resp))

    def search_issues(
        self,
        jql: str,
        *,
        fields: list[str] | None = None,
        max_results: int = 20,
        next_page_token: str | None = None,
    ) -> dict[str, Any]:
        """POST /rest/api/3/search/jql — JQL 검색.

        반환: {"issues": [...], "nextPageToken"?: str, ...}
        nextPageToken 이 있으면 더 가져올 수 있음 — 호출자가 반복 호출하며 결과 누적.
        """
        body: dict[str, Any] = {"jql": jql, "maxResults": int(max_results)}
        if fields:
            body["fields"] = list(fields)
        if next_page_token:
            body["nextPageToken"] = next_page_token
        # POST 지만 조회라 5xx 재시도 안전 — 기본 추정(POST=재시도 안 함)을 명시로 덮음.
        resp = self._request("POST", "search/jql", json=body, retry_responses=True)
        if resp.status_code == 200:
            return resp.json()
        raise JiraApiError(_format_error(resp))


def _retry_after_seconds(resp: requests.Response) -> float | None:
    """429 응답의 Retry-After 헤더 (초 단위 정수형만 지원, HTTP-date 는 무시)."""
    raw = resp.headers.get("Retry-After") if getattr(resp, "headers", None) else None
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _format_error(resp: requests.Response) -> str:
    code = resp.status_code
    text = (resp.text or "").strip()
    if code == 401:
        return "401 인증 실패. 이메일/토큰 확인."
    if code == 403:
        return "403 권한 없음. 프로젝트에 생성 권한 있는지 확인."
    if code in (400, 422):
        return f"{code} 입력 거부. body: {text[:500]}"
    if code == 429:
        return f"429 한도 초과. body: {text[:300]}"
    if 500 <= code < 600:
        return f"{code} 서버 오류. body: {text[:300]}"
    return f"{code} 예상치 못한 응답. body: {text[:500]}"


def markdown_to_adf(text: str) -> dict[str, Any]:
    # md-to-adf 라이브러리 lazy import
    if not text or not text.strip():
        return {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": []}]}
    try:
        from md_to_adf import convert  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise JiraApiError("md-to-adf 미설치. pip install -e . 로 의존성 설치 필요.") from exc
    result = convert(text)
    if isinstance(result, str):
        import json
        result = json.loads(result)
    return result
