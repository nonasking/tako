"""Jira REST v3 클라이언트.

POST /issue 만 처리. 422/400 같은 의미 있는 실패는 즉시 보고, 네트워크 끊김만 1회 재시도.
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


class JiraSiteClient:
    def __init__(self, site: str, creds: Credentials, *, timeout: float = 10.0):
        self._site = site.rstrip("/")
        self._auth = creds.as_basic_auth()
        self._timeout = timeout
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

    def _request(self, method: str, path: str, *, json: Any | None = None) -> requests.Response:
        url = self._endpoint(path)
        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                return self._session.request(
                    method, url, json=json, auth=self._auth, timeout=self._timeout
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt == 2:
                    break
                time.sleep(1.0)
        raise JiraApiError(f"네트워크 오류: {last_exc}") from last_exc

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

    def get_issue(self, key: str) -> dict[str, Any]:
        """GET /rest/api/3/issue/<key> — 단일 이슈 상세."""
        resp = self._request("GET", f"issue/{key}")
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
        resp = self._request("POST", "search/jql", json=body)
        if resp.status_code == 200:
            return resp.json()
        raise JiraApiError(_format_error(resp))


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
