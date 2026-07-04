#!/usr/bin/env bash
# scoperoute installer — one command sets up BOTH faces of the tool:
#   • terminal command   `scoperoute`   (interactive wizard)  →  ~/.local/bin/scoperoute
#   • Claude Code skill  `/scoperoute`   (personal skill)      →  ~/.claude/skills/scoperoute
#
# Idempotent. No build step, no dependencies (stdlib Python 3 only).
#   ./install.sh              install (symlinks that track this repo)
#   SKILL_COPY=1 ./install.sh copy the skill instead of symlinking (if /scoperoute won't load)
#   ./install.sh --uninstall  remove both links (the repo itself is untouched)
set -euo pipefail

REPO="$(cd -P "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
BIN_DIR="$HOME/.local/bin";      BIN_LINK="$BIN_DIR/scoperoute"
SKILL_DIR="$HOME/.claude/skills"; SKILL_LINK="$SKILL_DIR/scoperoute"
SKILL_SRC="$REPO/skills/scoperoute"

uninstall() {
  local removed=0
  for l in "$BIN_LINK" "$SKILL_LINK"; do
    if [ -L "$l" ] || [ -e "$l" ]; then rm -rf "$l"; echo "removed $l"; removed=1; fi
  done
  [ "$removed" = 0 ] && echo "nothing to remove."
  echo "Uninstalled scoperoute (this repo is untouched)."
}

case "${1:-}" in
  --uninstall|-u) uninstall; exit 0 ;;
esac

command -v python3 >/dev/null 2>&1 || { echo "error: python3 not found on PATH." >&2; exit 1; }

# 1) terminal command --------------------------------------------------------------
mkdir -p "$BIN_DIR"
chmod +x "$REPO/bin/scoperoute"
ln -snf "$REPO/bin/scoperoute" "$BIN_LINK"
echo "✓ terminal command : $BIN_LINK  →  bin/scoperoute"

# 2) Claude Code personal skill (bare /scoperoute) ---------------------------------
mkdir -p "$SKILL_DIR"
if [ -e "$SKILL_LINK" ] && [ ! -L "$SKILL_LINK" ]; then
  mv "$SKILL_LINK" "$SKILL_LINK.bak.$$"
  echo "! backed up existing $SKILL_LINK → $SKILL_LINK.bak.$$"
fi
if [ "${SKILL_COPY:-0}" = "1" ]; then
  rm -rf "$SKILL_LINK"; cp -R "$SKILL_SRC" "$SKILL_LINK"
  echo "✓ Claude Code skill: $SKILL_LINK  (copied — re-run install.sh after repo updates)"
else
  ln -snf "$SKILL_SRC" "$SKILL_LINK"
  echo "✓ Claude Code skill: $SKILL_LINK  →  skills/scoperoute   (invoke: /scoperoute)"
fi

# PATH hint ------------------------------------------------------------------------
case ":$PATH:" in
  *":$BIN_DIR:"*) : ;;
  *) printf '\n→ Add ~/.local/bin to PATH:  export PATH="$HOME/.local/bin:$PATH"\n' ;;
esac

cat <<'EOF'

Done. Two ways to use it:
  • Terminal    :  scoperoute            (guided wizard; or `scoperoute --help`)
  • Claude Code :  /scoperoute           (restart Claude Code once if it doesn't appear yet)

Nothing spends your Fable quota until you approve at the gate.
Fable 5 is free on Claude Code only until July 7 — point it at the right projects while it lasts.
EOF
