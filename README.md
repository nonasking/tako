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
#  담당자 (me / 이메일 / accountId, 없으면 Enter):
#  스토리포인트 (정수, 없으면 Enter):
#  기한 YYYY-MM-DD (없으면 Enter):
#  연결할 티켓 (KEY[:TYPE], 쉼표로 여러 개, 없으면 Enter):
#  → 미리보기 → Jira 에 생성? (Y/n)

# 일부만 미리 지정
tako new --project WL --issue-type 기능변경 --assignee me

# 하위 작업(sub-task) 생성 — 부모 키 + 사이트의 sub-task 이슈 타입 이름
tako new --project WL --issue-type 하위작업 --parent WL-9058 \
  --summary "결제 환불 가상계좌 케이스 테스트 추가" --description "..."

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
  --assignee jy@example.com \
  --story-points 3 \
  --duedate 2026-06-15 \
  --link WL-100 \
  --link "WL-200:Blocks" \
  --yes
```

`--assignee` / `--story-points` / `--duedate` / `--link` 는 선택. 인터랙티브 모드에서도 빈 입력으로 두면 스킵된다.

`--assignee` 는 `me` (자기 자신, `/myself` 한 번 호출), 이메일 (`/user/search` 한 번 호출, 정확히 1건 매칭만 허용), 또는 accountId 직접 입력. 한국어 이름·닉네임은 v1.x 미지원. 이메일 검색은 사이트 GDPR 설정에 따라 막힐 수 있는데 그 때는 accountId 직접 입력으로 우회. config 의 `jira.default_assignee` 에 'me'/이메일/accountId 를 두면 인터랙티브 빈 입력 / `--assignee` 미지정 자동 모드에서 기본값으로 적용된다.

`--link KEY[:TYPE]` 는 반복 가능. TYPE 생략 시 `Relates` 적용. 흔한 TYPE: `Blocks` / `Relates` / `Duplicates` / `Causes` / `Clones` (사이트마다 다름). 본인 사이트 link types 확인:

```bash
curl -u "email:token" "https://<site>/rest/api/3/issueLinkType" | jq '.issueLinkTypes[].name'
```

연결 호출은 *이슈 생성 후 별도 REST*. 이슈 생성은 성공했는데 일부 링크가 실패하면 *티켓은 그대로*, 실패한 링크만 보고하고 종료 코드 1. 스토리포인트를 *실제로 페이로드에 실으려면* config 의 `jira.fields.story_points` 에 사용자 환경의 customfield ID 가 있어야 한다 (없으면 경고 출력 후 SP 만 제외하고 생성). 두 가지 방법:

```bash
# 방법 1) 자동 — Jira 에서 후보 찾아서 한 줄로 등록
tako fields detect story_points --save

# 방법 2) ID 를 이미 알고 있으면 직접 등록
tako fields set story_points customfield_10016
```

`tako fields detect <name>` 는 `--save` 없이 쓰면 결과만 출력하고 config 는 안 건드림 (config 자동 쓰기 시 주석 손실 가능성 때문). 지원하는 이름: `story_points` (v1.x).

흐름: 입력 → 미리보기 → Y/n → REST → 키 + 링크. Claude Code 불필요.

생성 직후 티켓 URL 이 시스템 클립보드에 자동 복사된다 (macOS `pbcopy` / Linux `xclip` 또는 `xsel`). 끄려면 config 에 `jira.auto_copy_url: false`. 도구가 없는 환경에서는 조용히 skip — 생성 자체는 영향 없음.

### B) Claude Code 안에서 (세션 컨텍스트 활용)

```
/tako 방금 발견한 정렬 버그 티켓 끊어. WL 프로젝트, 부모는 infra
/tako 이거 WL-9058 하위 작업으로 끊어줘    # → 이슈 유형 자동으로 사이트의 sub-task 타입, --parent WL-9058
```

LLM 이 세션 내용을 요약 → 미리보기 → 확인 후 `tako new` 호출. 세션 컨텍스트 없을 때는 모드 A 가 더 가볍다.

> 하위 작업 생성에는 사용자 환경 `~/.config/tako/config.yaml` 의 `issue_types` 에 *그 사이트의 sub-task 타입 이름이 등록* 되어 있어야 함 (예: `하위작업`, `Sub-task`, `서브태스크`). 없으면 `tako new` 가 "허용 안 된 이슈 타입" 으로 거부한다.

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

### D) 기존 티켓 제목/본문 업데이트 (`/tako-update`)

```
/tako-update WL-8876
```

세션에서 한 작업을 *티켓 본문에 append* (기본). LLM 이 세션 맥락 → 추가될 섹션 자동 작성 → 미리보기 → Y/n → REST. 셸에서 직접:

```bash
# 기본 append — 본문 끝에 '## 업데이트 (YYYY-MM-DD)' 섹션 추가
tako update WL-8876 --body "$(cat <<'BODY'
- 작업 내용 1
- 작업 내용 2
BODY
)" --yes

# 섹션 이름 지정
tako update WL-8876 --section "진행 상황" --body "..."

# 본문 통째 교체 (위험 — 미리보기에서 신중히)
tako update WL-8876 --mode overwrite --body "..."

# 제목만 변경 (본문 안 건드림)
tako update WL-8876 --summary "새 제목으로 교체"

# 제목 + 본문 동시 변경
tako update WL-8876 --summary "새 제목" --body "..." --mode overwrite
```

`--summary` 와 `--body` 중 *최소 하나는* 있어야 함. `--mode` 는 *본문에만* 영향 — 제목은 항상 교체.

> 본문·제목 모두 *영구 기록*되므로 민감 정보·실수 주의. 미리보기 단계에서 반드시 검토.

### E) 티켓 조회·필터링 (`tako list` / `/tako-list`)

```bash
# 내 티켓 (config.default_project + 자기 자신 자동)
tako list --assignee me

# 흔한 조합
tako list --assignee me --status 진행중 --updated 7d
tako list --type 에픽 --limit 50
tako list --parent WL-9200          # 자식 이슈들
tako list --label backend --query 정렬
tako list --project WL --project ABC --assignee me   # 여러 프로젝트 동시

# 고급 — JQL 직접 (다른 인자 무시)
tako list --jql "project = WL AND assignee = currentUser() AND duedate < now()"

# 자동화·LLM 용 JSON
tako list --assignee me --json

# Excel 로 (UTF-8 BOM 포함 CSV — 더블클릭으로 Excel 자동 열림)
tako list --assignee me --csv --output my-issues.csv
tako list --assignee me --csv > my-issues.csv   # stdout 리다이렉트도 가능
```

지원 인자: `--assignee` (me / 이메일 / accountId), `--project` (반복, 여러 프로젝트 동시 조회), `--status` (반복), `--type` (반복), `--parent`, `--label` (반복), `--updated` / `--created` (`7d`/`1w`/`YYYY-MM-DD` / `<=YYYY-MM-DD` 등 비교), `--due` (`overdue` / `none` / `set` / `YYYY-MM-DD` / `<=YYYY-MM-DD` 등), `--sp` (정수 / `>=N` / `<=N` / `none` / `set`), `--query`, `--jql`, `--limit` (기본 20), `--all` (페이지네이션 자동), `--json`, `--csv`, `--output / -o`, `--wizard / -i` (인터랙티브 입력).

필터가 길어 한 줄이 부담스러우면 `tako list --wizard` (또는 `-i`) — 항목별로 묻고 빈 입력은 스킵. CLI 인자와 병용 가능 (예: `tako list -i --assignee me` 하면 담당자는 묻지 않고 나머지만). 결과 출력 직후 같은 조회를 만드는 *셸 명령 한 줄* 을 stderr 에 힌트로 찍어줘서 마음에 들면 alias 로 저장 가능.

**`전체` / `all` / `*` 키워드** — 모든 필터에 사용 가능 (인터랙티브·CLI 양쪽):

- 상태 / 유형 / 라벨 / 담당자: `전체` = 빈 입력과 동일 (해당 조건 안 걸림).
- 프로젝트: `전체` = `default_project` 도 무시 + JQL 에 `project` 절 자체 빠짐 → 사이트의 모든 프로젝트.
- 최대 결과 수 (`--limit` / 인터랙티브 limit 단계): `전체` = `--all` 자동 + 페이지당 100개로 끝까지.

```bash
tako list --project 전체 --assignee me --updated 7d   # 사이트 전체 프로젝트 중 내 이번 주 티켓
tako list -i  # 인터랙티브에서 "프로젝트: 전체", "최대: 전체" 같이 답해도 동일
```

> 주의: `--project 전체` 만 명시하고 다른 조건이 없으면 *사이트 전체 모든 이슈* 가 되어 거부됨. 다른 조건 한 개 이상 같이 줘야 함.

`--all` 은 모든 페이지를 자동 반복 호출 (페이지당 100 max). 큰 결과집합에 주의 — 943건이 약 10페이지에 걸쳐 조회됨.

기본 컬럼: `key, status, type, assignee, created, updated, duedate, summary, parent, url`. 사용자 config 에 `jira.fields.story_points` 매핑이 있으면 `story_points` 컬럼이 `type` 직후에 자동 추가됨. 매핑 없으면 SP 필터·컬럼 비활성 + 안내.

```bash
# 기한 / SP 필터 예시
tako list --due overdue                       # 기한 지난 것
tako list --due "<=2026-06-15"                # 6월 15일까지
tako list --sp ">=3" --assignee me            # 내 SP 3 이상
tako list --sp none --status 진행중           # SP 미정 진행중

# 오래된 stale 티켓 찾기
tako list --assignee me --updated "<=2026-04-01"   # 4월 1일 이후 한 번도 안 건드린 내 티켓

# 전체 조회 + CSV
tako list --created 2026-03-01 --all --csv -o issues-since-march.csv
```

Claude Code 슬래시는 *자연어 → 인자 매핑*:
```
/tako-list 이번 주 내가 진행한 거
/tako-list WL-9200 자식 중 에픽
/tako-list 최근 한 달 진행중 + 라벨 backend
```

> 담당자 *한국어 이름*은 v1.x 미지원. `me` / 이메일 / accountId 만.

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

- 대상 Jira 프로젝트는 team-managed. 모든 부모-자식 관계가 `parent` 필드 하나로 표현됨 — 일반 이슈가 Epic 아래 (`parent` = Epic 키), *하위 작업(sub-task)* 이 일반 이슈 아래 (`parent` = 일반 이슈 키) 모두 동일 형태. classic 프로젝트는 v1 미검증.
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
