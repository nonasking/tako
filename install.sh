#!/usr/bin/env bash
# 슬래시 커맨드 등록 (저장소를 받아 쓰는 개발용) —
#   tako/commands/*.md → ~/.claude/commands/ 심볼릭 링크.
#
# 링크라서 저장소를 수정하면 즉시 반영된다. 커맨드 자체를 고칠 때 쓰는 방식.
#
# 저장소 없이 tako 를 설치했다면(PyPI/원라인 설치) 이 스크립트가 아니라 이걸 쓴다:
#   tako slash install
# 그쪽은 패키지에 동봉된 사본을 복사한다 — 업그레이드 후엔 --force 로 갱신.
#
# 어느 쪽이든 셸 직접 사용(tako new 등)에는 필요 없다.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$REPO_ROOT/tako/commands"
TARGET_DIR="$HOME/.claude/commands"

# 등록할 슬래시 커맨드 파일 목록.
SOURCES=("tako.md" "tako-read.md" "tako-check.md" "tako-update.md" "tako-retype.md" "tako-list.md" "tako-guide.md")

say() { printf "  %s\n" "$*"; }
warn() { printf "  [!] %s\n" "$*" >&2; }
fail() { printf "  [x] %s\n" "$*" >&2; exit 1; }

link_one() {
  local name="$1"
  local src="$SOURCE_DIR/$name"
  local tgt="$TARGET_DIR/$name"

  [[ -f "$src" ]] || fail "원본 없음: $src"

  if [[ -L "$tgt" ]]; then
    local current_link
    current_link="$(readlink "$tgt")"
    if [[ "$current_link" == "$src" ]]; then
      say "이미 연결됨: $tgt"
    else
      warn "다른 위치 가리키는 중: $current_link → 재연결"
      ln -sfn "$src" "$tgt"
      say "재연결: $tgt"
    fi
  elif [[ -e "$tgt" ]]; then
    fail "같은 이름의 일반 파일이 이미 있음: $tgt (옮기거나 삭제 후 재실행)"
  else
    ln -s "$src" "$tgt"
    say "링크 생성: $tgt"
  fi
}

printf "tako 슬래시 커맨드 등록\n"
mkdir -p "$TARGET_DIR"
for name in "${SOURCES[@]}"; do
  link_one "$name"
done

printf "\n다음 단계:\n"
say "1) 셸 인터랙티브:    tako new"
say "2) 셸 자동화:        tako new --project ... --issue-type ... --summary ... --description ... --yes"
say "3) 티켓 조회:        tako show <KEY>   /   tako list --assignee me"
say "4) 본문 업데이트:    tako update <KEY>"
say "5) 유형 변경:        tako retype <KEY> <유형>"
say "6) 슬래시 커맨드:    /tako   /tako-read   /tako-check   /tako-update   /tako-retype   /tako-list   /tako-guide"
say "7) 본문 작성 가이드:  tako guide show   /   /tako-guide (개인 커스텀)"
say ""
say "패키지/설정 미설치면:"
say "   cd \"$REPO_ROOT\" && pip install -e . && tako init"
