"""Jira REST v3 클라이언트.

422/400 같은 의미 있는 실패는 즉시 보고. 재시도는 _request 의 정책 참고 —
서버에 닿기 전 실패·429 는 항상 재시도, 전송 후 네트워크 오류·5xx 는 멱등 호출만
(POST /issue 중복 생성 방지).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import ssl

import requests
from urllib3.exceptions import ConnectTimeoutError, LocationParseError

from .auth import Credentials
from .patterns import browse_url


class JiraApiError(Exception):
    pass


@dataclass(frozen=True)
class CreatedIssue:
    key: str
    url: str
    raw: dict[str, Any]


# 429 Retry-After 가 없거나 비상식적으로 클 때의 대기 상한 (초)
_MAX_RETRY_AFTER = 15.0


# 서버 상태를 바꾸는 호출이 서버에 닿았을 수 있는 채로 실패했을 때 붙이는 안내
_MAYBE_APPLIED_HINT = " (요청이 처리됐을 수 있으니 Jira에서 확인 후 다시 시도)"

# _request 가 네트워크 실패로 다루는 예외. ChunkedEncodingError·ContentDecodingError 는
# 응답 본문을 읽다 끊긴 경우로, ConnectionError 의 하위 클래스가 아니라 따로 잡는다.
_NETWORK_ERRORS = (
    requests.ConnectionError,
    requests.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


def _never_reached_server(exc: requests.RequestException) -> bool:
    """요청이 서버에 전달되기 전에 실패했다고 확신할 수 있는지.

    requests.ConnectionError는 연결 거부처럼 보내기 전 실패와, 요청을 보낸 뒤 응답을
    기다리다 끊긴 경우("Connection aborted", 본문 읽기 타임아웃)를 함께 담는다.
    뒤쪽은 서버가 처리를 마쳤을 수 있으므로, 확실히 보내기 전인 경우만 True.
    urllib3 2.x에서 NewConnectionError·NameResolutionError는 ConnectTimeoutError의 하위 클래스다.

    SSLError 전체를 여기 넣지는 않는다. 핸드셰이크 실패(보내기 전)와 요청을 보낸 뒤
    응답을 읽다 난 TLS 오류가 같은 예외로 오기 때문이다. 인증서 검증 실패만은
    핸드셰이크 단계라는 것이 확실하므로 보내기 전으로 본다.

    ProxyError 도 통째로 믿지 않는다. urllib3 는 터널을 지나 요청을 보낸 뒤 끊긴 연결도
    ProxyError("Unable to connect to proxy", RemoteDisconnected(...)) 로 감싼다. 감싼 원래
    오류가 연결 실패일 때만 보내기 전이다.
    """
    if isinstance(exc, requests.ConnectTimeout):
        return True
    if any(isinstance(e, ssl.SSLCertVerificationError) for e in _error_chain(exc)):
        return True
    cause = exc.args[0] if exc.args else None
    reason = getattr(cause, "reason", cause)  # urllib3 MaxRetryError.reason
    reason = getattr(reason, "original_error", reason)  # urllib3 ProxyError 가 감싼 원래 오류
    return isinstance(reason, ConnectTimeoutError)


def _error_chain(exc: BaseException, limit: int = 12) -> list[BaseException]:
    """requests → urllib3 → ssl/socket 으로 감싸인 예외들을 펼친다."""
    seen: list[BaseException] = []
    todo: list[Any] = [exc]
    while todo and len(seen) < limit:
        e = todo.pop(0)
        if not isinstance(e, BaseException) or any(e is s for s in seen):
            continue
        seen.append(e)
        todo.extend([
            e.args[0] if e.args else None,
            getattr(e, "reason", None),
            getattr(e, "original_error", None),
            e.__cause__,
            e.__context__,
        ])
    return seen


# 재시도해도 결과가 같고, 요청이 나가기 전에 실패한 것이 확실한 예외
_NEVER_SENT_ERRORS = (
    requests.exceptions.InvalidURL,
    requests.exceptions.InvalidSchema,
    requests.exceptions.MissingSchema,
    requests.exceptions.InvalidHeader,
    LocationParseError,
    UnicodeError,
)


def _json(resp: requests.Response) -> Any:
    """성공 응답의 본문. JSON 이 아니면(SSO 로그인 페이지, 캡티브 포털 등) 안내와 함께 실패."""
    try:
        return resp.json()
    except ValueError as exc:
        raise JiraApiError(
            f"{resp.status_code} 응답이 JSON 이 아님. 로그인 페이지나 프록시가 대신 응답했을 수 있음 "
            "(jira.site 설정과 네트워크 확인)."
        ) from exc


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

        - 서버에 닿기 전 실패(연결 거부·DNS 실패·연결 타임아웃·프록시에 연결하지 못함·
          인증서 검증 실패): 항상 재시도.
        - 그 밖의 네트워크 오류(응답 대기 타임아웃, 응답 도중 끊김 "Connection aborted",
          본문 읽다 끊김·타임아웃, TLS 오류): 요청은 이미 서버에 닿았을 수 있어 5xx와
          같이 *멱등 호출만* 재시도. POST /issue 를 재시도하면 티켓이 중복된다.
          상태를 바꾸는 호출이 이렇게 실패하면 "처리됐을 수 있음" 안내를 붙인다.
          여러 번 시도한 경우 마지막 실패가 아니라 *어느 한 번이라도* 서버에 닿았을 수
          있었는지를 본다 (첫 PUT 이 타임아웃, 재시도가 연결 실패로 끝나는 경우).
        - 429: 항상 재시도 — 처리 전에 거절된 요청이라 중복 위험 없음. Retry-After 존중.
        - 5xx: *멱등 호출만* 재시도. POST /issue 는 서버가 처리를 마쳤을 수도 있어
          재시도하면 티켓이 중복 생성될 수 있다. 기본값은 메서드로 추정
          (GET/PUT/DELETE = 재시도) — 검색처럼 POST 여도 안전한 곳은 호출자가
          retry_responses=True 로 명시한다.
        """
        # 재시도를 명시한 POST(검색)는 조회이므로 상태를 바꾸는 호출로 보지 않는다.
        mutating = method.upper() not in ("GET", "HEAD") and retry_responses is not True
        if retry_responses is None:
            retry_responses = method.upper() in ("GET", "PUT", "DELETE")
        url = self._endpoint(path)
        # 상태를 바꾸는 요청이 지금까지 한 번이라도 서버에 닿았을 수 있는지
        maybe_applied = False
        for attempt in range(1, self._max_attempts + 1):
            last_try = attempt == self._max_attempts
            try:
                resp = self._session.request(
                    method, url, json=json, auth=self._auth, timeout=self._timeout
                )
            except _NETWORK_ERRORS as exc:
                reached = not _never_reached_server(exc)
                maybe_applied = maybe_applied or (mutating and reached)
                safe_to_retry = retry_responses or not reached
                if last_try or not safe_to_retry:
                    label = "응답 시간 초과" if isinstance(exc, requests.ReadTimeout) else "네트워크 오류"
                    hint = _MAYBE_APPLIED_HINT if maybe_applied else ""
                    raise JiraApiError(f"{label}: {exc}{hint}") from exc
                self._sleep(float(attempt))
                continue
            except (requests.RequestException, LocationParseError, UnicodeError) as exc:
                # 재시도해도 같은 결과인 실패: 잘못된 site 주소, 리다이렉트 루프,
                # 헤더에 넣을 수 없는 문자가 든 이메일·토큰 등.
                if isinstance(exc, UnicodeError):
                    detail = "이메일이나 API 토큰에 쓸 수 없는 문자가 있음 (tako init --force 로 다시 입력)"
                else:
                    detail = str(exc)
                if mutating and not isinstance(exc, _NEVER_SENT_ERRORS):
                    maybe_applied = True
                hint = _MAYBE_APPLIED_HINT if maybe_applied else ""
                raise JiraApiError(f"요청 실패: {detail}{hint}") from exc
            if resp.status_code == 429 and not last_try:
                self._sleep(min(_retry_after_seconds(resp) or attempt * 2.0, _MAX_RETRY_AFTER))
                continue
            if 500 <= resp.status_code < 600:
                maybe_applied = maybe_applied or mutating
                if retry_responses and not last_try:
                    self._sleep(float(attempt))
                    continue
            if maybe_applied and not 200 <= resp.status_code < 300:
                # 앞선 시도(또는 이번 5xx)에서 요청이 반영됐을 수 있는데 최종 응답은 실패다.
                raise JiraApiError(_format_error(resp) + _MAYBE_APPLIED_HINT)
            return resp
        raise AssertionError("unreachable")  # 루프가 항상 return/raise 로 끝남

    def create_issue(self, fields: dict[str, Any]) -> CreatedIssue:
        # fields = build_payload()['payload']['fields']
        resp = self._request("POST", "issue", json={"fields": fields})
        if resp.status_code == 201:
            # 201 이면 티켓은 이미 만들어졌다. 본문을 못 읽어도 다시 만들면 중복된다.
            try:
                data = resp.json()
            except ValueError as exc:
                raise JiraApiError("201 응답 본문을 읽지 못함." + _MAYBE_APPLIED_HINT) from exc
            key = data.get("key") if isinstance(data, dict) else None
            if not key:
                raise JiraApiError(f"응답에 키 없음: {data}" + _MAYBE_APPLIED_HINT)
            return CreatedIssue(key=key, url=browse_url(self._site, key), raw=data)
        raise JiraApiError(_format_error(resp))

    def list_fields(self) -> list[dict[str, Any]]:
        """GET /rest/api/3/field — 사이트의 모든 필드 목록."""
        resp = self._request("GET", "field")
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = _json(resp)
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
            return _json(resp)
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
            return _json(resp)
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
        data = _json(resp)
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
            return _json(resp)
        raise JiraApiError(_format_error(resp))

    def search_users(self, query: str) -> list[dict[str, Any]]:
        """GET /rest/api/3/user/search?query=... — 이메일/이름으로 사용자 검색.

        사이트 GDPR 설정에 따라 이메일 기반 검색이 제한될 수 있음. v1 은 이메일만 안내.
        """
        from urllib.parse import quote
        resp = self._request("GET", f"user/search?query={quote(query)}")
        if resp.status_code != 200:
            raise JiraApiError(_format_error(resp))
        data = _json(resp)
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
        data = _json(resp)
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
            return _json(resp)
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
