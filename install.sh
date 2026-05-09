#!/usr/bin/env bash
# 슬래시 커맨드 등록 — commands/tako.md → ~/.claude/commands/tako.md 심볼릭 링크.
# Claude Code 에서 /tako 슬래시 쓸 때만 필요. 셸 직접 사용은 install.sh 안 돌려도 됨.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$REPO_ROOT/commands/tako.md"
TARGET_DIR="$HOME/.claude/commands"
TARGET="$TARGET_DIR/tako.md"

say() { printf "  %s\n" "$*"; }
warn() { printf "  [!] %s\n" "$*" >&2; }
fail() { printf "  [x] %s\n" "$*" >&2; exit 1; }

printf "tako 슬래시 커맨드 등록\n"

[[ -f "$SOURCE" ]] || fail "원본 없음: $SOURCE"
mkdir -p "$TARGET_DIR"

if [[ -L "$TARGET" ]]; then
  current_link="$(readlink "$TARGET")"
  if [[ "$current_link" == "$SOURCE" ]]; then
    say "이미 연결됨: $TARGET"
  else
    warn "다른 위치 가리키는 중: $current_link → 재연결"
    ln -sfn "$SOURCE" "$TARGET"
    say "재연결: $TARGET"
  fi
elif [[ -e "$TARGET" ]]; then
  fail "같은 이름의 일반 파일이 이미 있음: $TARGET (옮기거나 삭제 후 재실행)"
else
  ln -s "$SOURCE" "$TARGET"
  say "링크 생성: $TARGET"
fi

printf "\n다음 단계:\n"
say "1) 셸 인터랙티브:    tako new"
say "2) 셸 자동화:        tako new --project ... --issue-type ... --summary ... --description ... --yes"
say "3) 슬래시 커맨드:    /tako <의도>"
say ""
say "패키지/설정 미설치면:"
say "   cd \"$REPO_ROOT\" && pip install -e . && tako init"
