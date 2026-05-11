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
