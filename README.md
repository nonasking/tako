# tako

셸 한 줄로 Jira 티켓을 만든다. Claude Code 슬래시 커맨드 위에서 쓸 때는 세션 컨텍스트로 제목·본문 초안을 자동 생성한다. 백엔드는 Atlassian Cloud REST API v3.

## 사전 요구사항

- macOS / Linux
- Python ≥ 3.10
- Atlassian Cloud 계정 + API 토큰 ([id.atlassian.com/manage-profile/security/api-tokens](https://id.atlassian.com/manage-profile/security/api-tokens))
- 선택: [Claude Code](https://docs.anthropic.com/claude-code) — 세션 컨텍스트 모드 쓸 때만

## 설치

```bash
git clone <repo-url> tako && cd tako
pip install -e .
./install.sh        # 슬래시 커맨드 등록 (선택)
```

## 첫 실행

```bash
tako init
```

5개 항목(사이트 도메인 / 기본 프로젝트 / 기본 이슈 타입 / 이메일 / API 토큰) 입력하면 두 파일 생성:

- `~/.config/tako/config.yaml` — 사이트·프로젝트·이슈 타입
- `~/.config/tako/credentials.json` — 이메일·토큰 (chmod 0600)

기존 파일 있으면 덮어쓸지 묻는다. `--force` 로 건너뜀. 수동 작성하려면 [`config.example.yaml`](./config.example.yaml) 복사해서 편집.

## 사용 — 두 모드

### A) 셸에서 직접

```bash
# 인터랙티브 (권장) — 아래처럼 차례로 묻는다
tako new
#  프로젝트 키 [WL]:
#  이슈 유형 (Task / 기능변경 / 버그수정) [기능변경]:
#  제목: ...
#  본문(마크다운) (Ctrl+D 로 종료): ...
#  부모 (별칭/키, 없으면 Enter):
#  스토리포인트 (정수, 없으면 Enter):
#  기한 YYYY-MM-DD (없으면 Enter):
#  → 미리보기 → Jira 에 생성? (Y/n)

# 일부만 미리 지정
tako new --project WL --issue-type 기능변경

# 모든 인자 + 확인 단계 건너뛰기
tako new \
  --project WL \
  --issue-type 기능변경 \
  --summary "스프린트 보드 정렬 깨짐" \
  --description "## 재현
1. 보드 진입
2. 정렬 클릭

## 기대
정렬 적용" \
  --story-points 3 \
  --duedate 2026-06-15 \
  --yes
```

`--story-points` / `--duedate` 는 선택. 인터랙티브 모드에서도 빈 입력으로 두면 스킵된다. 스토리포인트를 *실제로 페이로드에 실으려면* config 의 `jira.fields.story_points` 에 사용자 환경의 customfield ID 가 있어야 한다 (없으면 경고 출력 후 SP 만 제외하고 생성). 두 가지 방법:

```bash
# 방법 1) 자동 — Jira 에서 후보 찾아서 한 줄로 등록
tako fields detect story_points --save

# 방법 2) ID 를 이미 알고 있으면 직접 등록
tako fields set story_points customfield_10016
```

`tako fields detect <name>` 는 `--save` 없이 쓰면 결과만 출력하고 config 는 안 건드림 (config 자동 쓰기 시 주석 손실 가능성 때문). 지원하는 이름: `story_points` (v1.x).

흐름: 입력 → 미리보기 → Y/n → REST → 키 + 링크. Claude Code 불필요.

### B) Claude Code 안에서 (세션 컨텍스트 활용)

```
/tako 방금 발견한 정렬 버그 티켓 끊어. WL 프로젝트, 부모는 infra
```

LLM 이 세션 내용을 요약 → 미리보기 → 확인 후 `tako new` 호출. 세션 컨텍스트 없을 때는 모드 A 가 더 가볍다.

### C) 기존 티켓 ↔ 세션 작업 검토 (`/tako-check`)

```
/tako-check WL-8876
```

세션에서 한 작업이 그 티켓의 명세를 얼마나 충족하는지 LLM 이 대조해서 보고. 셸에서 직접 조회만 하려면:

```bash
tako show WL-8876                  # 사람 친화 텍스트
tako show WL-8876 --json           # 원본 JSON (자동화·LLM 용)
tako show https://<site>/browse/WL-8876   # URL 도 OK
tako show WL-8876 --max-comments 0 # 코멘트 제외
```

`tako show` 가 ADF→마크다운 변환·인증·REST 호출을 모두 처리한다.

> 민감 정보 주의: 티켓 본문이 세션에 노출되므로 토큰·비밀번호 포함 티켓에는 신중히 사용 (v1.x 자동 필터 없음).

## 부분 호출 (디버깅·자동화)

```bash
# 미리보기만
echo '{"summary":"x","description":"y"}' | tako preview

# 페이로드 JSON 만
echo '{"summary":"x","description":"y"}' | tako build

# TTY 인터랙티브 → 페이로드 JSON
tako interactive
```

`new` / `fields detect` 만 실제 REST 호출. 나머지는 로컬 처리.

## 적용 환경 가정

- 대상 Jira 프로젝트는 team-managed (일반 이슈가 Epic 을 부모로 가지면 `parent` 필드 하나). classic 프로젝트는 v1 미검증.
- description 은 마크다운으로 받아 [`md-to-adf`](https://pypi.org/project/md-to-adf/) 로 ADF 변환 후 전송.
- v1 은 1인 사용 가정. 팀 공용 설정 오버라이드 / 멀티 사이트 / 사용자별 필드 커스터마이징은 v1.1+ 확장 포인트.

## 디렉토리

```
tako/
├── commands/
│   ├── tako.md             /tako 슬래시 커맨드
│   └── tako-check.md       /tako-check 슬래시 커맨드
├── tako/                   Python 패키지
│   ├── auth.py              credentials 로드
│   ├── jira_client.py       REST + ADF 변환 진입점
│   ├── adf_to_md.py         ADF → 마크다운
│   ├── issue_draft.py       페이로드 빌더 + 미리보기
│   ├── fields.py            custom field 매핑 헬퍼
│   ├── prompts.py           인터랙티브 입력
│   ├── config.py            설정 + init 마법사
│   └── main.py              진입점
├── config.example.yaml     설정 예시
└── install.sh              슬래시 커맨드 등록 (선택)
```

## 문제 해결

- `tako: command not found` — PATH 에 없음. `pip install -e .` 안 끝났거나 다른 venv. 폴백으로 `python -m tako ...` 도 동일 동작.
- `설정 파일이 없습니다` — `tako init`. 다른 경로면 `TAKO_CONFIG_PATH` 환경변수.
- `creds 없음` — 위와 동일.
- `허용 안 된 이슈 타입` — `~/.config/tako/config.yaml` 의 `issue_types` 에 추가.
- `401 인증 실패` — 토큰 만료. `tako init --force` 로 재입력.
- `403 권한 없음` — 해당 프로젝트에 이슈 생성 권한 있는지 확인.
- `400/422 입력 거부` — 응답 body 확인. 흔한 원인: 이슈 타입 이름이 그 프로젝트에 정의되지 않음.
- `story_points 값을 받았지만 ... 페이로드에서 제외함` — `tako fields detect story_points --save` 한 줄로 자동 등록 가능.
