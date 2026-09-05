# Changelog

All notable changes to the url-to-obsidian plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## 2026-09-05

### Fixed
- **`pending_root` from config now actually applies** — `ClipConfig` carries `pending_root` (read from `config.toml` via `WEB_TO_OBSIDIAN_PENDING_ROOT`), and `_run_locked()` / `_resume_locked()` use `config.pending_root` instead of the hard-coded default. Removed the now-unused `_pending_root()` helper. Covered by new unit tests (env, TOML, default-outside-vault).

### Changed
- **Skill guidance de-risked for data safety and injection** — removed the blanket `git checkout -- .` batch-cleanup advice in favor of inspect-dirty-worktree + targeted cleanup of generated paths only (`inbox/`, `images/`); batch scripts now pass URL/image flags as arguments instead of interpolating them into Python source; Chrome cleanup stops only the debug instance by PID/`--user-data-dir` instead of all `chrome` processes.
- **Docs aligned with the restructured repo** — `skill/SKILL.md` config path now points at `plugin/config.toml` (tracked, not `.gitignored`) and the deploy symlink targets `plugin/`; `extractor/README.md` documents `npx playwright install chromium`; anti-bot fallback docs mark `author`/`published`/`description`/`site`/`markdown` as required (may be empty).
- **CHANGELOG merged the two `2026-07-26` headings** into one.
- **Plugin slash command renamed `/clip` → `/webclip`** — avoids colliding with the `clip` skill's auto-generated `/clip` command in Hermes (skills scan only skips built-in commands, not plugin commands). Plugin version bumped 0.5.1 → 0.6.0; skill 1.4.0 → 1.4.1; docs, error strings, and the plugin registration test updated. The `[clip]` config section name and `webclip_id` field are unchanged.
- **Plugin-local npm extractor install** — `plugin/` now ships a `package.json` declaring `@tiny-codes/web-clip-extractor` (the published npm package), so a plugin install can pull the matching Node.js extractor into `plugin/node_modules` and upgrade it in lockstep with the plugin. `_extractor_dir()` prefers the plugin-local npm package, falling back to the source-repo sibling and then the legacy subdirectory. `.gitignore` ignores `plugin/node_modules/` and `plugin/package-lock.json`. Plugin version bumped 0.6.0 → 0.7.0; covered by 3 new resolution-order unit tests (npm-preferred / sibling fallback / legacy fallback).

## 2026-09-04

### Added
- **AGENTS.md project working guide** — repo-level guide for humans and AI agents: module map, directory layout, build/test commands, versioning rules, Git workflow, safety invariants, and two mandatory rules (unit tests required for features/fixes; version bumps + CHANGELOG updates required for behavior changes).
- **`web-clip-to-obsidian` skill descriptions & reference keywords clarified** — `skill/SKILL.md` and `skill/README.md` updated for clarity.
- **`config.toml` `pending_root` support** — `ClipConfig.from_file()` now accepts `pending_root`; `config.toml`/`config.example.toml` enable `lock_file` and add `pending_root`. All 6 env vars (`VAULT`, `DEST`, `IMAGES`, `SYNC_BRANCH`, `LOCK_FILE`, `PENDING_ROOT`) now have `config.toml` equivalents — single source of truth.
- **`markdown` field restored as validated payload input** — `source_markdown` is again taken from the validated `markdown` field (previously extracted separately in `render_note`, which excluded it from frontmatter).

### Changed
- **Plugin files restructured into `plugin/` subdirectory** — `__init__.py`, `web_to_obsidian.py`, `plugin.yaml`, `config.toml`, `config.example.toml` moved from the repo root into `plugin/`. `tests/conftest.py` injects `plugin/` into `sys.path`; `tests/test_plugin.py` points at `plugin/__init__.py`.
- **Extractor located as sibling of the plugin package** — `run_extractor()` now resolves `extractor/` via `plugin_root.parent / "extractor"` (with a legacy fallback for `plugin_root/extractor`), so the Hermes plugin works after the restructure.
- **READMEs split by submodule** — added `plugin/README.md`, `extractor/README.md`, `skill/README.md`; rewrote root `README.md` as a project overview with the new repository layout and quick start.
- **CHANGELOG switched to date headings** — entries now use `## YYYY-MM-DD` instead of `## [<version>] - <date>`; module versions (plugin.yaml, package.json, SKILL.md) are independent of the CHANGELOG.

## 2026-09-03

### Added
- **Web Clip skill fully migrated to repo** — the `web-clip-to-obsidian` skill (SKILL.md v1.2.0 + 10 reference documents) is now version-controlled in `skill/` for git-based history and cross-profile sharing.
- 10 skill reference documents covering: anti-bot fallback (web_extract), batch processing, git sync testing, GitHub private blob URLs & image mode, Netease embedded-state author extraction, Netease placeholder & video links, pending-resume fallback, profile skill vs source-repo sync, repo-linked maintenance, Zhihu cookie & scraper diagnostics.

### Changed
- Skill version bumped from 1.1.0 to 1.2.0.
- Profile skill directory can now be a symlink to `skill/` instead of a standalone copy, ensuring single source of truth.

## 2026-09-02

### Added
- Bumped `web-to-obsidian-extractor` subpackage from `0.1.0` to `0.2.0`.

### Fixed
- **Lazy-loaded image extraction** — Netease (`c.m.163.com`) and similar sites render images with a placeholder `src` (`empty.png`) and the real URL in `data-echo` / `data-src` / `data-original`. The extractor now resolves the real lazy-loaded image URL (preferring `data-echo` → `data-src` → `data-original` → `data-lazy-src`, falling back to the original `src`) so clipped articles reference the actual image instead of the placeholder. Covered by `extractor/test/fixtures/netease-lazy-image.html` and a new unit test (full suite 23/23 passing).

## 2026-08-31

### Fixed
- **Netease author/published fallback** — `c.m.163.com` article pages do not expose author/published in standard meta tags or JSON-LD; the metadata lives only in the embedded `window.__INITIAL_STATE__` JSON (`main.source` / `main.sourceinfo.tname` / `main.ptime`). The extractor now falls back to that embedded state (scoped to `163.com` hostnames) when Defuddle returns no author or published value.

## 2026-07-26

### Added
- **WeChat article curl fallback** — when Node.js extractor fails for `mp.weixin.qq.com` URLs, automatically falls back to curl-based extraction: fetches raw HTML, parses metadata (title, author, publish time) and body (`js_content` div) via regex, converts HTML to Markdown. No manual intervention needed.
- `_is_wechat_url()`, `_fetch_wechat_html()`, `_parse_wechat_html()`, `_wechat_html_to_markdown()`, `_count_words()`, `run_extractor_with_fallback()` functions
- 11 unit tests for WeChat extraction pipeline
- `commit_message` parameter to `GitSync.finalize()` — commit messages now include the article title (e.g. `clip: <title>`) instead of a generic `clip: save web article`
- `skill/` directory with Hermes skill documentation for web-clip-to-obsidian workflow

### Changed
- `_run_locked()` now calls `run_extractor_with_fallback()` instead of `run_extractor()` directly

## 2026-07-25

### Added
- **Two-phase image confirmation workflow** — `/clip` with remote images now prompts user to confirm download via `web_to_obsidian_resume_pending` tool (`--save-images yes|no|ask`, default: `ask`)
- **Pending state management** — stores intermediate state in `~/.hermes/workspace/cache/url-to-obsidian/` with 1-hour TTL, single-active constraint, and vault/config binding
- **SSRF protection** for image downloads — full RFC reserved IP range blocking, redirect pinning (max 6 hops), non-default port rejection, IDNA hostname validation, Content-Type `image/*` enforcement
- **Code-aware markdown sanitization** — `sanitize_markdown` and `find_remote_images` skip fenced code blocks, inline code spans, and indented code lines
- **Managed note rendering** with YAML frontmatter (title, url, author, site, description, keywords, tags, extraction_method, word_count, content_hash, image_mode, etc.)
- **Git synchronization** — auto commit + push with post-commit verification, branch safety checks, and note preservation on Git failures
- **Vault lock** — cross-process non-blocking lock to prevent concurrent writes
- **CLI flags** — `--refresh`, `--no-browser`, `--no-git`, `--save-images`
- **Image localization** — downloads remote images to vault `images/` directory, rewrites markdown references, cleans up on failure
- Secure web clipper extraction pipeline with Node.js extractor, Exa fallback, and browser fallback

### Fixed
- Sanitize injected heading title to prevent XSS via article titles
- Refresh managed note semantics — re-clip updates existing notes correctly
- Allow prevalidated extractor payloads to pass through validation

### Security
- SSRF protection blocks private/internal network addresses on image downloads
- HTML dangerous tag sanitization (script, iframe, object, embed, form, etc.)
- Credential-like marker detection in extracted content
- Dangerous URL scheme blocking (javascript, vbscript, file, obsidian, data)
- Knowledge base content hash verification against known secret patterns
