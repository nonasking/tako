#!/usr/bin/env bash
# tako installer — https://github.com/nonasking/tako
#
# What this does, in order:
#   1. Checks for uv (an isolated Python tool installer). Installs it if absent.
#   2. Installs the `tako-shell` package from PyPI via `uv tool install`.
#   3. Reports whether the `tako` command landed on your PATH.
#
# It does NOT touch your system Python, and it never runs `sudo`.
# Nothing is written outside ~/.local and uv's own cache directory.
#
# Prefer not to pipe a script into your shell? Both of these work just as well:
#   uv tool install tako-shell
#   pipx install tako-shell

set -euo pipefail

PACKAGE="tako-shell"
COMMAND="tako"
UV_INSTALLER="https://astral.sh/uv/install.sh"
BIN_DIR="${UV_TOOL_BIN_DIR:-$HOME/.local/bin}"

if [ -t 1 ]; then
  B=$(printf '\033[1m'); DIM=$(printf '\033[2m'); R=$(printf '\033[0m')
  GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m'); RED=$(printf '\033[31m')
else
  B=""; DIM=""; R=""; GREEN=""; YELLOW=""; RED=""
fi

step() { printf "%s==>%s %s\n" "$B" "$R" "$*"; }
note() { printf "    %s%s%s\n" "$DIM" "$*" "$R"; }
ok()   { printf "    %s%s%s\n" "$GREEN" "$*" "$R"; }
warn() { printf "    %s%s%s\n" "$YELLOW" "$*" "$R" >&2; }
die()  { printf "%serror:%s %s\n" "$RED" "$R" "$*" >&2; exit 1; }

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "unsupported platform: $(uname -s). tako supports macOS and Linux." ;;
esac

printf "\n%stako installer%s\n" "$B" "$R"
note "installs: $PACKAGE (from PyPI) -> $BIN_DIR/$COMMAND"
printf "\n"

# ---------------------------------------------------------------- uv

if command -v uv >/dev/null 2>&1; then
  UV="$(command -v uv)"
  step "uv found — $($UV --version 2>/dev/null || echo unknown)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV="$HOME/.local/bin/uv"
  step "uv found — $($UV --version 2>/dev/null || echo unknown)"
else
  step "uv not found — installing it first"
  note "source: $UV_INSTALLER"
  command -v curl >/dev/null 2>&1 || die "curl is required to install uv."
  curl -LsSf "$UV_INSTALLER" | sh >/dev/null 2>&1 \
    || die "uv installation failed. Try manually: curl -LsSf $UV_INSTALLER | sh"

  if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
  elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
  else
    die "uv installed but could not be located. Open a new terminal and re-run this script."
  fi
  ok "uv installed"
fi

# ---------------------------------------------------------------- tako

step "installing $PACKAGE"
note "uv downloads a private Python 3.10+ if your system lacks one — this may take a moment"

# --force makes the script idempotent: re-running it upgrades an existing install.
"$UV" tool install --force "$PACKAGE" \
  || die "install failed. Re-run with more detail: $UV tool install --force $PACKAGE"

ok "$PACKAGE installed"

# ---------------------------------------------------------------- PATH

printf "\n"
if command -v "$COMMAND" >/dev/null 2>&1; then
  step "done — $COMMAND is on your PATH"
  ON_PATH=1
elif [ -x "$BIN_DIR/$COMMAND" ]; then
  step "done — but $BIN_DIR is not on your PATH yet"
  warn "run this once, then open a new terminal:"
  printf "\n      %s tool update-shell\n\n" "$UV"
  note "or add it yourself:  export PATH=\"\$PATH:$BIN_DIR\""
  ON_PATH=0
else
  die "installed, but $COMMAND was not found in $BIN_DIR. Please open an issue."
fi

# ---------------------------------------------------------------- next steps

printf "\n%snext step%s — set up your Jira connection:\n\n" "$B" "$R"
if [ "$ON_PATH" = "1" ]; then
  printf "      %s init\n" "$COMMAND"
else
  printf "      %s/%s init\n" "$BIN_DIR" "$COMMAND"
fi
printf "\n"
note "You'll need an Atlassian API token — tako init links you straight to the page."
note "docs: https://github.com/nonasking/tako"
printf "\n"
