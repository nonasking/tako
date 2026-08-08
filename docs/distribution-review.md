# tako 배포 방식 검토

**전제** — macOS 위주 · 공개 배포(불특정 다수) · 비개발자 포함 · **기능은 현행 유지**

목표는 "받는 사람이 깔아야 하는 별도 프로그램을 최소화한다"이다. 결론부터: **언어 재작성은 하지 않는다.** 배포 채널만 바꾼다.

> 상태: §5·§6 은 확정되어 구현 완료. §4 는 재작성을 하지 않기로 한 근거이며, 재평가 트리거를 함께 남겨 둔다.

---

## 1. 지금 설치가 깨지는 지점

현재 안내는 `git clone` → `pip install -e .` → `./install.sh` → `tako init` 네 단계다. 각 단계의 실패 조건을 실제로 확인했다.

| 단계 | 실패 조건 | 확인 결과 |
|---|---|---|
| `git clone` | git 미설치 | macOS가 Xcode CLT(~1GB) 설치 프롬프트를 띄움 |
| `pip install -e .` | Python < 3.10 | **macOS 기본 python은 3.9.6** — `requires-python >=3.10`이라 즉시 실패 |
| 〃 | PEP 668 | Homebrew·최신 Linux python에서 `externally-managed-environment` 에러 |
| 〃 | PATH | 설치돼도 `tako` 명령을 못 찾는 케이스 다수 |
| `./install.sh` | bash 전용 | Claude Code 쓸 때만 필요한데 설치 안내에 섞여 있어 필수처럼 보임 |
| `tako init` | Atlassian API 토큰 | **불가피** — 어떤 배포 방식으로도 없앨 수 없음 |

핵심 병목은 **Python 런타임**이다. 나머지는 파생 문제다.

## 2. "공개 배포"가 바꾸는 전제

팀 내부 배포였다면 고려하지 않아도 될 것들이 생긴다.

- **브라우저 다운로드 경로가 반드시 생긴다.** README의 curl 라인을 무시하고 Releases 페이지에서 zip을 받는 사람이 나온다. `curl`/`wget`은 `com.apple.quarantine` 속성을 붙이지 않지만 **브라우저는 붙인다.** 즉 미서명 단일 바이너리는 그 경로에서 "Apple이 확인할 수 없음"으로 차단된다. → **공개 배포는 단일 바이너리에 불리하게 작용한다.**
- **낯선 사람에게 `curl | bash`를 실행시켜야 한다.** 원라인은 편하지만 신뢰를 요구한다. 검증 가능한 대안(PyPI, Homebrew tap)을 반드시 병행해야 한다.
- **이름이 이미 선점돼 있다.** PyPI `tako`는 타인이 점유 중(분산 데이터 스토어). → **`takopy` 로 확정.** §5 참조.
- **릴리스 자동화가 없으면 유지가 안 된다.** 현재 `.github/workflows` 없음.
- **슬래시 커맨드가 도달 불가능해진다.** `install.sh` 는 저장소의 `commands/*.md` 를 심볼릭 링크한다 — PyPI 설치자는 저장소가 없다. 설치 방식만 바꾸면 기능이 하나 사라진다. §5 참조.

## 3. 후보 비교

| 방식 | 사용자가 깔 것 | 브라우저 경로 안전 | 작업량 | 판정 |
|---|---|---|---|---|
| **PyPI + uv/pipx** | uv 또는 pipx | ✅ 해당 없음 | 낮음 | **정본 채널** |
| **원라인 설치 스크립트** | 없음 (스크립트가 uv → Python 자동) | ✅ 해당 없음 | 낮음 | **비개발자용 주력** |
| Homebrew **개인 tap** | Homebrew | ✅ | 중 | mac 개발자 보조 |
| Homebrew **core 등록** | Homebrew | ✅ | — | **초기 불가**(아래 참조) |
| PyInstaller 단일 바이너리 | 없음 | ❌ Gatekeeper 차단 | 중 | 공개 배포에선 역효과 |
| Go/Rust 재작성 | 없음 | ❌ 동일 | **매우 높음** | 불필요 |

homebrew-core는 **≥75 stars 또는 ≥30 forks/watchers + 30일 이상**을 요구하고, 본인 프로젝트 셀프 제출은 권장하지 않는다. 초기에는 개인 tap만 가능하다.

## 4. 언어 재작성을 하지 않는 이유

코드 규모: **Python 3,228줄 + 테스트 1,321줄.** 실질적 외부 의존성은 `md-to-adf` 하나뿐이고(`requests`/`pyyaml`은 어느 언어든 대체 가능), 역방향 변환기 `adf_to_md.py`(112줄)는 이미 의존성 0으로 자작돼 있다.

1. **사용자 체감 차이가 거의 없다.** 어느 쪽이든 최종 설치 경험은 "curl 한 줄"이다. 설치 시간 20초 → 2초는 비개발자의 진입장벽이 아니다.
2. **진짜 장벽은 언어와 무관하다.** Atlassian 토큰 발급과 "터미널을 연다"는 행위는 Go로 바꿔도 그대로 남는다.
3. **ADF 변환기를 자작해야 한다.** Go 생태계의 markdown→ADF 라이브러리는 [`summonio/markdown-to-adf`](https://github.com/summonio/markdown-to-adf) 하나뿐인데 **별 1개, 마지막 커밋 2023-03.** 사실상 직접 구현이고, 여기가 Jira 400 에러가 터지는 최고 위험 부위다.
4. **테스트 1,321줄을 버린다.** 최근 리팩터링으로 정리한 자산이 통째로 날아간다.
5. **공개 배포에서는 오히려 손해다.** 재작성의 주된 이득인 "단일 바이너리"가 §2의 Gatekeeper 문제를 새로 만든다.

### 재평가 트리거

아래가 생기면 §4를 다시 연다.

- Windows가 실제 타깃이 될 때 (현재 `clipboard.py`는 Windows 미지원, `install.sh`는 bash 전용)
- Apple Developer 계정($99/년)을 확보해 공증이 가능해질 때 — 그러면 단일 바이너리의 Gatekeeper 문제가 사라진다
- "zip 받아서 압축 풀면 끝"이 요구사항이 될 때

## 5. 결정 — 채널 3층

**배포명은 `takopy`.** Python 패키지임을 이름에서 드러낸다. 실행 명령어는 `tako` 로 유지된다(`[project.scripts]` 가 패키지명과 독립). 이름이 기능을 말해주지 않는 대가는 메타데이터로 갚는다 — `keywords = ["jira", "atlassian", "cli", ...]`.

### PyPI 이름 선정에서 걸린 함정

처음 고른 이름은 `tako-shell` 이었다 — "taco shell" 말장난이자, 속(본문)은 LLM 이 채우고 껍데기(형식·인증·페이로드·REST)는 결정론적 코드가 잡는다는 아키텍처와도 맞았다. **PyPI 가 등록을 거부했다.**

`GET /pypi/<name>/json` 이 404 를 주는 것은 "그 이름이 존재하지 않는다"는 뜻일 뿐, **"등록할 수 있다"는 뜻이 아니다.** PyPI 는 그 위에 [ultranormalization](https://github.com/pypi/warehouse/issues/11139) 을 적용해 혼동 가능한 이름을 막는다. 핵심은 **구분자(`-` `_` `.`)를 아예 제거한 뒤 비교**한다는 것:

```
tako-shell  →  takoshell  ←  이미 존재 (Pythonic shell, 2020-06) → 차단
tako-yaki   →  takoyaki   ←  이미 존재 (burner mail, 2022-03)    → 차단
```

하이픈은 방패가 아니다. 후보를 검증할 때는 **하이픈을 제거한 형태**가 비어 있는지 봐야 한다. 다만 ultranormalization 은 혼동 문자(`l`/`1`, `o`/`0` 등)도 접을 수 있으므로, 최종 판정은 PyPI UI 가 한다.

**정본 (PyPI).** `uv tool install takopy` / `pipx install takopy`. git·URL 불필요, 검증 가능, 업데이트 경로 명확.

**비개발자용 (원라인).**
```bash
curl -fsSL https://github.com/nonasking/tako/raw/develop/get-tako.sh | bash
```
스크립트가 하는 일: uv 탐지 → 없으면 설치 → PyPI 에서 `uv tool install --force` → PATH 확인 → `tako init` 안내. Python·git·pip 를 사용자가 인지할 필요가 없어진다. `curl | bash` 를 꺼리는 사람을 위해 스크립트 헤더와 README 양쪽에 동등한 PyPI 경로를 병기했다.

**URL 은 `raw.githubusercontent.com` 이 아니라 `github.com/.../raw/...` 를 쓴다.** 더 짧다는 이유로 되돌리지 말 것 — 저장소 이름을 바꾸면 [raw 도메인은 리다이렉트되지 않는다](https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository). 일단 퍼진 설치 명령은 회수할 수 없으므로, 그 경우 원라인이 영구히 깨진다. `github.com` 경로는 저장소 리다이렉트를 타고 raw 로 넘어가므로 이름 변경을 견딘다(이름이 바뀐 저장소로 실측 확인).

저장소 이름 자체는 `tako` 로 유지한다. GitHub 저장소 이름은 소유자별 네임스페이스라 전역 충돌이 없고, 명령어·설정 디렉터리(`~/.config/tako/`)·환경변수·슬래시 커맨드가 모두 `tako` 다. `takopy` 는 PyPI 네임스페이스 제약에서 나온 유통상의 이름이지 프로젝트의 정체성이 아니다.

**mac 개발자 (개인 tap).** `brew install nonasking/tako/tako`. 여유 될 때. homebrew-core 정식 등록은 §3 의 요건 때문에 초기 불가.

**슬래시 커맨드는 패키지에 동봉한다.** `commands/` → `tako/commands/` 로 옮기고 package-data 에 포함, `tako slash install` 이 `~/.claude/commands/` 로 **복사**한다. 링크가 아니라 복사인 이유: PyPI 설치 시 원본이 uv 도구 환경 안에 있어 업그레이드·제거로 링크가 끊긴다. 저장소를 받아 커맨드를 편집하는 개발 흐름은 `install.sh` 의 심볼릭 링크가 계속 담당한다.

## 6. 확정된 결정 사항

| # | 사항 | 결정 |
|---|---|---|
| 1 | PyPI 패키지명 | **`takopy`** — 실행 명령어는 `tako` 유지 |
| 2 | `curl \| bash` 제공 | **제공** — PyPI/pipx 경로를 나란히 병기 |
| 3 | Claude Code 플러그인 전환 | **보류** — 기존 `/tako` 사용자에게 커맨드명이 깨지는 변경. 설치 문제와 직교하므로 분리 |
| 4 | 비개발자 첫 실행 UX | **적용** — TTY 한정 자동 init 제안, 토큰 페이지 자동 열기, 안내문에서 저장소 전제 제거 |
| 5 | 터미널이 장벽인 사용자 | **범위 밖** — GUI/웹 UI 는 별도 과제 |

## 6-1. 남은 후속 과제

- **Claude Code 플러그인 마켓플레이스** (#3) — `.claude-plugin/marketplace.json` 을 두면 `/plugin install` 한 번으로 끝난다. 커맨드명 네임스페이스 변경을 언제 감수할지가 관건.
- **Homebrew 개인 tap** — mac 개발자용 보조 채널.
- **문서 언어 불일치** — README 는 영어, CLI 출력과 슬래시 커맨드는 한국어다. 공개 배포에서는 한쪽으로 정리하거나 의도를 명시하는 편이 낫다.
- **Windows** — `clipboard.py` · `browser.py` 가 미지원이고 두 설치 스크립트가 bash 전용. 요구가 생기면 §4 재평가 트리거와 함께 검토.

## 7. 미검증 항목

- `uv tool install`이 wheel 직접 URL(PEP 508 direct reference)을 받는지는 공식 문서에서 확인하지 못했다. **PyPI를 정본 채널로 하면 이 불확실성 자체가 사라지므로** 권고안에는 영향 없음. 원라인 스크립트를 PyPI 없이 만들 경우에만 실측 필요.

---

## 참고

- [macOS Gatekeeper / Quarantine — HackTricks](https://hacktricks.wiki/en/macos-hardening/macos-security-and-privilege-escalation/macos-security-protections/macos-gatekeeper.html)
- [uv — Python versions (자동 다운로드)](https://docs.astral.sh/uv/concepts/python-versions/)
- [uv — Tools](https://docs.astral.sh/uv/concepts/tools/)
- [Homebrew — Acceptable Formulae](https://docs.brew.sh/Acceptable-Formulae)
- [PyPI — Trusted Publishers](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [pypa/gh-action-pypi-publish](https://github.com/pypa/gh-action-pypi-publish)
- [Claude Code plugins](https://claude.com/blog/claude-code-plugins)
