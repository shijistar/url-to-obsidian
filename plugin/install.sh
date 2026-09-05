#!/usr/bin/env bash
#
# install.sh — one-shot installer for the web-to-obsidian plugin stack.
#
# Lives INSIDE the plugin package so it is copied along by
# `hermes plugins install` and runnable right from the installed plugin dir.
#
# Steps performed:
#   0. Locate the source repo (for the skill symlink + config example):
#      --repo, else detect parent (source checkout) or $REPO_ROOT env.
#   1. `hermes plugins install <repo>/plugin --enable` (skipped with --skip-plugin-install)
#   2. `npm install` + `npx playwright install chromium` in the plugin dir
#   3. Symlink `skill/` into the target profile's skills dir (auto-discovery)
#   4. Copy `config.example.toml` → `config.toml` if absent
#   5. Print restart instructions
#
# Usage:
#   ./install.sh [--hermes-home DIR] [--profile NAME] [--repo DIR] [--skip-plugin-install]
#
# Defaults:
#   HERMES_HOME = $HERMES_HOME if set (not already a profile), else ~/.hermes
#   profile     = default (root ~/.hermes)
#   source repo = detected from script location when this is a source checkout;
#                 otherwise pass --repo /path/to/url-to-obsidian (required for
#                 the skill symlink step).
#
set -euo pipefail

# ---------------------------------------------------------------- defaults
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

HERMES_HOME_ARG=""
PROFILE_ARG=""
REPO_ARG=""
SKIP_PLUGIN_INSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hermes-home)
      HERMES_HOME_ARG="$2"; shift 2 ;;
    --profile)
      PROFILE_ARG="$2"; shift 2 ;;
    --repo)
      REPO_ARG="$2"; shift 2 ;;
    --skip-plugin-install)
      SKIP_PLUGIN_INSTALL=1; shift ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Resolve HERMES_HOME (profile base dir).
if [[ -n "$HERMES_HOME_ARG" ]]; then
  HERMES_HOME="$HERMES_HOME_ARG"
elif [[ -z "${HERMES_HOME:-}" || "$HERMES_HOME" == *"/profiles/"* ]]; then
  # When HERMES_HOME is unset or already points at a profile (gateway injects
  # the active profile), fall back to the user default so `--profile` keeps
  # working instead of nesting under the running profile.
  HERMES_HOME="$HOME/.hermes"
fi

# A named profile lives under <HERMES_HOME>/profiles/<name>.
if [[ -n "$PROFILE_ARG" ]]; then
  PROFILE_DIR="$HERMES_HOME/profiles/$PROFILE_ARG"
  PLUGIN_DIR="$PROFILE_DIR/plugins/web-to-obsidian"
  SKILLS_DIR="$PROFILE_DIR/skills/productivity"
else
  PROFILE_DIR="$HERMES_HOME"
  PLUGIN_DIR="$HERMES_HOME/plugins/web-to-obsidian"
  SKILLS_DIR="$HERMES_HOME/skills/productivity"
fi

# The plugin package this script ships with is SCRIPT_DIR itself.
PLUGIN_SRC="$SCRIPT_DIR"

# Locate the source repo (parent checkout can provide skill/ + config.example).
# Priority: --repo > $REPO_ROOT env > parent-dir has skill/ (source checkout).
if [[ -n "$REPO_ARG" ]]; then
  REPO_ROOT="$(cd "$REPO_ARG" && pwd)"
elif [[ -n "${REPO_ROOT:-}" ]]; then
  REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
elif [[ -d "$SCRIPT_DIR/../skill" ]]; then
  REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
  # Installed copy (hermes plugins install) — skill not shipped with the plugin.
  REPO_ROOT=""
fi

if [[ -n "$REPO_ROOT" ]]; then
  SKILL_SRC="$REPO_ROOT/skill"
else
  SKILL_SRC=""
fi

info()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

info "HERMES_HOME = $HERMES_HOME"
info "Profile dir = $PROFILE_DIR"
info "Plugin src  = $PLUGIN_SRC"
[[ -n "$REPO_ROOT" ]] && info "Repo root   = $REPO_ROOT"

# ------------------------------------------------------- 1. install plugin
if [[ "$SKIP_PLUGIN_INSTALL" -eq 1 ]]; then
  info "Skipping \`hermes plugins install\` (--skip-plugin-install)"
else
  if [[ -d "$PLUGIN_DIR" ]]; then
    ok "Plugin already installed at $PLUGIN_DIR (reinstall with \`hermes plugins install --force\`)"
  else
    info "Installing plugin from $PLUGIN_SRC ..."
    HERMES_HOME="$HERMES_HOME" \
      hermes plugins install "file://$PLUGIN_SRC" --enable
  fi
fi

test -d "$PLUGIN_DIR" || die "Plugin dir not found at $PLUGIN_DIR (run hermes plugins install first)"

# We operate on the INSTALLED plugin dir; if this script is already running
# from the installed dir, PLUGIN_DIR == SCRIPT_DIR and nothing extra is needed.
INSTALLED_PLUGIN_DIR="$PLUGIN_DIR"
if [[ "$(cd "$SCRIPT_DIR" && pwd)" != "$(cd "$INSTALLED_PLUGIN_DIR" && pwd)" ]]; then
  info "Script runs from $SCRIPT_DIR; installing dependencies into $INSTALLED_PLUGIN_DIR"
fi

# ----------------------------------------- 2. extractor npm + playwright
if [[ -f "$INSTALLED_PLUGIN_DIR/package.json" ]]; then
  info "Installing extractor npm package in $INSTALLED_PLUGIN_DIR ..."
  (cd "$INSTALLED_PLUGIN_DIR" && npm install)
  info "Installing Playwright Chromium ..."
  (cd "$INSTALLED_PLUGIN_DIR" && npx playwright install chromium)
  ok "Extractor installed"
else
  warn "No package.json in $INSTALLED_PLUGIN_DIR — extractor npm install skipped"
fi

# ------------------------------------------------------- 3. skill symlink
if [[ -z "$SKILL_SRC" ]]; then
  warn "Source repo not found; skipping skill symlink."
  warn "Re-run with --repo /path/to/url-to-obsidian to link the skill."
elif [[ ! -d "$SKILL_SRC" ]]; then
  warn "Skill dir not found at $SKILL_SRC; skipping skill symlink."
else
  mkdir -p "$SKILLS_DIR"
  if [[ -e "$SKILLS_DIR/web-clip-to-obsidian" || -L "$SKILLS_DIR/web-clip-to-obsidian" ]]; then
    ok "Skill already linked at $SKILLS_DIR/web-clip-to-obsidian"
  else
    info "Symlinking skill $SKILL_SRC → $SKILLS_DIR/web-clip-to-obsidian"
    ln -s "$SKILL_SRC" "$SKILLS_DIR/web-clip-to-obsidian"
  fi
fi

# ------------------------------------------------ 4. config.toml bootstrap
if [[ ! -f "$INSTALLED_PLUGIN_DIR/config.toml" ]]; then
  if [[ -f "$SCRIPT_DIR/config.example.toml" ]]; then
    info "Bootstrapping config.toml from config.example.toml"
    cp "$SCRIPT_DIR/config.example.toml" "$INSTALLED_PLUGIN_DIR/config.toml"
  elif [[ -n "$REPO_ROOT" && -f "$REPO_ROOT/plugin/config.example.toml" ]]; then
    info "Bootstrapping config.toml from $REPO_ROOT/plugin/config.example.toml"
    cp "$REPO_ROOT/plugin/config.example.toml" "$INSTALLED_PLUGIN_DIR/config.toml"
  fi
fi
if [[ -f "$INSTALLED_PLUGIN_DIR/config.toml" ]]; then
  ok "config.toml at $INSTALLED_PLUGIN_DIR/config.toml — review vault/destination/sync_branch"
else
  warn "No config.toml present; create it manually"
fi

# ------------------------------------------------------------ 5. summary
cat <<EOF

\033[1;32mInstall summary\033[0m
  Plugin:     $INSTALLED_PLUGIN_DIR
  Extractor:  $INSTALLED_PLUGIN_DIR/node_modules/@tiny-codes/web-clip-extractor
  Skill:      $SKILLS_DIR/web-clip-to-obsidian
  Config:     $INSTALLED_PLUGIN_DIR/config.toml

Next steps:
  1. Review config.toml (vault, destination, sync_branch).
  2. Restart your Hermes gateway service from a separate shell so the
     installed plugin is picked up by the running gateway process.
  3. Clip:  /webclip https://example.com/article
EOF