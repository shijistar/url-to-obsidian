# Changelog

All notable changes to the url-to-obsidian plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## v0.4.0

2026-08-31

### Fixed
- **Netease author/published fallback** — `c.m.163.com` article pages do not expose author/published in standard meta tags or JSON-LD; the metadata lives only in the embedded `window.__INITIAL_STATE__` JSON (`main.source` / `main.sourceinfo.tname` / `main.ptime`). The extractor now falls back to that embedded state (scoped to `163.com` hostnames) when Defuddle returns no author or published value.

## v0.3.0

2026-07-26

### Added
- **WeChat article curl fallback** — when Node.js extractor fails for `mp.weixin.qq.com` URLs, automatically falls back to curl-based extraction: fetches raw HTML, parses metadata (title, author, publish time) and body (`js_content` div) via regex, converts HTML to Markdown. No manual intervention needed.
- `_is_wechat_url()`, `_fetch_wechat_html()`, `_parse_wechat_html()`, `_wechat_html_to_markdown()`, `_count_words()`, `run_extractor_with_fallback()` functions
- 11 unit tests for WeChat extraction pipeline

### Changed
- `_run_locked()` now calls `run_extractor_with_fallback()` instead of `run_extractor()` directly

## v0.2.0

2026-07-26

### Added
- `commit_message` parameter to `GitSync.finalize()` — commit messages now include the article title (e.g. `clip: <title>`) instead of a generic `clip: save web article`
- `skill/` directory with Hermes skill documentation for web-clip-to-obsidian workflow

## v0.1.0

2026-07-25

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
