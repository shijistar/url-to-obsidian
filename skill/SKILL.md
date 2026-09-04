---
name: web-clip-to-obsidian
description: Clip web articles to the Obsidian vault via the url-to-obsidian plugin — extract, confirm images, save as dated markdown, commit & push to Git. Use when the user shares a URL wanting to save/read-later in Obsidian, or says "抓取到obsidian"/"clip to obsidian"/"save to vault".
version: 1.3.0
author: Hermes Agent
metadata:
  hermes:
    tags: [obsidian, web-clip, productivity, content-management, juejin, reading]
---

# Web Clip to Obsidian

Clip web articles to the user's Obsidian vault using the `url-to-obsidian` plugin.

## When to Use

- User shares a URL with intent to save it to Obsidian
- Keywords: "抓取到obsidian", "clip to obsidian", "save to vault", "web clip", "剪藏"
- User pastes a link from Juejin, WeChat, Zhihu, or any web source wanting to archive it

## Plugin Location & Config

The **source repo** is the single source of truth. The installed plugin is a symlink:

```
Source repo:     ~/.hermes/workspace/repository/url-to-obsidian/
Plugin symlink:  ~/.hermes/profiles/<profile>/plugins/web-to-obsidian/ → source repo
Skill symlink:   ~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian/ → source repo/skill/
Config:          <source repo>/config.toml
```

Deploy via symlink (not copies):
```bash
ln -s ~/.hermes/workspace/repository/url-to-obsidian \
      ~/.hermes/profiles/<profile>/plugins/web-to-obsidian
ln -s ~/.hermes/workspace/repository/url-to-obsidian/skill \
      ~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian
```

`config.toml` lives in the source repo root but is `.gitignored` — each profile keeps its own local config. After symlink setup, copy or create `config.toml` in the source repo root.

Default config:
```toml
[clip]
vault = "~/obsidian/shijistar"
destination = "Inbox"
images = "images"
sync_branch = "master"
lock_file = "~/.hermes/workspace/cache/url-to-obsidian/vault.lock"
pending_root = "~/.hermes/workspace/cache/url-to-obsidian/pending-state"
```

## Invocation Pattern

There are **two** Python entry points, both invoked from the plugin directory:

1. **Hermes command boundary** via `build_handler()` — returns a formatted `str` safe to show directly to the user.
2. **Structured API** via `ClipService.run()` — returns `ClipResult` or `PendingClipResult` for programmatic branching.

Hermes-style string handler:

```python
import sys
sys.path.insert(0, '.')
from web_to_obsidian import build_handler
from pathlib import Path

plugin_root = Path('.')
handler = build_handler(plugin_root)
message = handler('<URL>')
print(message)
```

Structured API for automation:

```python
import sys
sys.path.insert(0, '.')
from pathlib import Path
from web_to_obsidian import ClipService

plugin_root = Path('.')
service = ClipService(plugin_root)
result = service.run('<URL>')
print(type(result).__name__)
print(result.user_message())
```

CLI shortcut (string handler):
```bash
cd ~/.hermes/profiles/<profile>/plugins/web-to-obsidian && \
python3 -c "
import sys; sys.path.insert(0, '.')
from web_to_obsidian import build_handler; from pathlib import Path
print(build_handler(Path('.'))('<URL>'))
"
```

Flags: `--no-browser`, `--no-git`, `--refresh`, `--save-images yes|no|ask`

## Workflow

1. **Extract**: Call `service.run(url)` for structured control flow, or `handler(url)` when a final user-facing string is sufficient.
2. **PendingClipResult** (has remote images): Prompt user `yes`/`no`
   - `yes` → download and localize images into vault `images/` dir
   - `no` → keep remote image URLs
3. **Resume**: Call `service.resume_pending('yes')` or `service.resume_pending('no')` (requires `ClipService` + pending state)
4. **ClipResult**: Contains `path`, `commit_state`, `push_state`, `github_url`
5. **Return**: Show the GitHub vault URL to user for preview

## Pending State

When images need confirmation, pending state is stored at:
`~/.hermes/workspace/cache/url-to-obsidian/pending-state`

This path is **mandatory** — never use system/state directories for pending state.

## Result Handling

```python
# For PendingClipResult
from web_to_obsidian import ClipService
from pathlib import Path
service = ClipService(Path('.'))
result = service.resume_pending('yes')  # or 'no'
print(result.user_message())
```

`ClipResult.user_message()` produces human-readable output with:
- File path relative to vault
- Git state (committed/pushed/unchanged/failed)
- GitHub URL for preview (when available)

## Pitfalls

- **CWD matters**: Must run from plugin directory
- **Node.js required**: Extractor uses `node` CLI — ensure it's available
- **Vault lock**: Plugin acquires a file lock during writes; concurrent clips will block
- **Image confirmation flow**: `PendingClipResult` requires a second call with `resume_pending()` — the first call does NOT save the article
- **GitHub URL**: After successful save, always return the vault file URL so user can preview
- **Pre-clip repo hygiene (both repos!)**: before clipping, confirm the vault (`~/obsidian/shijistar`) AND the plugin source repo (`~/.hermes/workspace/repository/url-to-obsidian`) are both on clean `master` — prior tasks often leave them on feature branches, and the clip commit would land on the wrong branch. Fix: `git switch master && git pull --ff-only` in each. Verify with `git status --short`.
- **Dry-run preflight before the real clip**: run the extractor once with `node extractor/src/cli.mjs <url> --no-browser` and confirm `ok:true`, non-empty `author`, `method:static`, sane markdown length BEFORE `service.run()`. Catches 404s / anti-bot walls early instead of failing mid-clip.
- **Structured API sequence (proven)**: `service = ClipService(Path('.'))` → `result = service.run(url)` → if `PendingClipResult`, ask the user for image handling (localize vs keep remote) → `service.resume_pending('yes'|'no')` → `ClipResult` with `.github_url`. `run()` alone does NOT save the article.
- **Site-specific extraction quirks**: netease (163.com) pages hide author/published in `window.__INITIAL_STATE__` embedded JSON, not in standard meta/JSON-LD. See `references/netease-embedded-state-author.md` for root cause, diagnosis path, fix (v0.4.0), and backfill hash-safety notes.
- **163 `news/v/` video short links are anti-scrape dead ends**: A `c.m.163.com/news/v/<id>.html` link (especially with `?spss/spsnuid/spsvid` tracking) frequently returns only a "网络不给力 / 重新加载" placeholder plus unrelated search-recommendation links. Extractor reports `method:static`, tiny `wordCount` (≈123), empty `author`/`published`. The real content is a VIDEO and is NOT captured. Do NOT save this as a note — abort and ask the user; contrast with `news/a/` article links which clip cleanly. See `references/netease-placeholder-and-video-links.md`.
- **163 `empty.png` is a placeholder, not a figure — BUT the extractor now resolves the real image**: Netease lazy-loads images via `src=empty.png` + `data-echo=<real url>`; the pre-fix extractor only read `src` and clipped `empty.png`. Since the `fix/extractor-lazy-image` fix (`extractLazyImageSrc()` in `extractor/src/extractor.mjs`), clipped `news/a/` articles now emit the REAL `dingyue.ws.126.net/...` URL instead. So a freshly-clipped netease article shows a real remote image, not `empty.png`. `empty.png` only remains if the source has no lazy-load URL (rare) — it is a 1×1 transparent pixel; localizing it only stores identical pixels — confirm before localizing, default keep-remote. See `references/netease-placeholder-and-video-links.md`.
- **Stale `pending-state` directory lock**: If a previous clip left a `PendingClipResult` un-resumed, the NEXT `service.run()` fails with `ClipError: The pending image confirmation expired; please run /clip again.` The `pending-state` path is a DIRECTORY (not a file) — `cat` shows empty but `_load_pending_state` treats its presence as expired. Fix: `rm -rf ~/.hermes/workspace/cache/url-to-obsidian/pending-state` (cache dir — safe to remove), then re-run. To avoid the two-step entirely, pass flags (see below).
- **Hash-protection on re-clip of an existing note**: If the same URL was already clipped, `write_managed_note` compares semantic state and raises `The saved page changed; rerun with --refresh to update it.` unless `refresh=True`. Common trigger: re-clipping with a different `image_mode` (remote vs local). Before re-clipping, check whether the note already exists in the vault; ask the user keep-vs-refresh rather than auto-overwriting. To refresh AND localize images, pass `--refresh --save-images yes`.
- **One-step non-interactive clip via flags**: `service.run('<url> --refresh --save-images yes|no --no-browser')` performs the full clip (including the image decision) in a single call and returns `ClipResult` directly — no `PendingClipResult`, no second `resume_pending()` step. Use this when the image decision is already known (e.g. user confirmed, or dry-run shows only `empty.png` placeholders). This also sidesteps the expired-pending-state error.
- **Single-URL: always ask about images, never skip confirmation**: When clipping a single URL (or a small handful), ALWAYS use the normal `service.run(url)` flow without `--save-images` flag. If the page has remote images, it returns `PendingClipResult` — prompt the user to choose localize vs remote. Do NOT preemptively pass `--save-images no` just because the user's general preference is remote; the user expects to be consulted on each clip unless they explicitly said 'all remote' or 'all local'. Skipping the confirmation step is a workflow violation that frustrates users. Exception: batch mode (10+ articles) with pre-declared per-article image preferences is acceptable to use flags directly.
- **163 tracking params are harmless to dedup**: `?spss=...&spsnuid=...&spsvid=...&spstoken=...` are share-source trackers; `canonicalUrl` strips them and `webclip_id` keys off `canonicalUrl`, so tracking params do NOT cause duplicate notes. The `original_url`/`fetched_url` frontmatter retains the full tracked URL. Safe to clip either form.
- **Profile skill vs source-repo drift**: before proposing or creating a PR for `url-to-obsidian`, verify whether the loaded profile skill file and `~/.hermes/workspace/repository/url-to-obsidian/skill/SKILL.md` are actually the same file or symlink. If they are not, patching the loaded profile skill alone will not create a source-repo diff; sync the source repo file explicitly first.
- **`image_mode` is conditional metadata**: treat `image_mode` as metadata for the remote-Markdown-image workflow only. When no remote Markdown images are present, omit the field entirely. Use `image_mode: remote` only when remote Markdown images were detected and intentionally kept remote; use `image_mode: local` only when those images were localized into the vault.
- **HTML `<img>` is outside `image_mode` flow**: plain HTML image tags in article content do not trigger the yes/no remote-image confirmation path and should not be used as evidence that `image_mode` must be present.
- **WeChat articles**: Plugin now auto-falls back to curl-based extraction for `mp.weixin.qq.com` when Node.js extractor fails. The fallback parses HTML via regex (title, author, body from `js_content`), converts to Markdown, and returns the same format. No manual intervention needed.
- **Browser CAPTCHA wall**: WeChat triggers slider CAPTCHA in headless browser; the curl fallback bypasses this entirely.
- **Zhihu anti-bot / cookie caveat**: A user-supplied `d_c0` cookie alone may still return `403` / `40362` on Zhihu article pages. Do not claim the retry succeeded unless a real request returns article HTML. If direct retry is blocked but the user has already provided or cached the article HTML (for example under `~/.hermes/profiles/<profile>/cache/documents/`), rebuild the Markdown from that HTML as a fallback and report that the live retry remained blocked.
- **Zhihu login-valid ≠ fetch-success**: even after the user later supplies `z_c0` and an auxiliary scraper reports that `z_c0`/`d_c0` are configured and the Zhihu login status is valid, protocol fetch can still return `HTTP 403`, and browser fallback can fail separately (for example by not finding `articles:<id>` in page state). For Zhihu, verify and report these layers separately: cookie completeness from the actual text received, login validity, protocol fetch result, and browser fallback result. See `references/zhihu-cookie-and-scraper-diagnostics.md`.
- **Anti-bot fallback via web_extract**: For sites with strong anti-bot (Zhihu, Doubao, some paywalled content), the Node.js extractor may fail with `QUALITY_GATE` / `HTTP_STATUS` / 403 even when `web_extract` succeeds. Workflow: run `web_extract` on the URL → if it returns clean Markdown/HTML, manually construct the `article` dict (including `ok: true`, `title`, `author`, `published`, `description`, `site`, `canonicalUrl`, `keywords`, `url`, `wordCount`, `method: web_extract_fallback`, `markdown`) and call `ClipService._persist_article()` directly with the extracted `content_markdown`. Use `ClipConfig.from_file()` (reads `config.toml`, no hardcoded paths) and `GitSync(vault, repo_root, branch)` constructor. This bypasses the extractor entirely and avoids the PendingClipResult flow when no Markdown images are present (HTML `<img>` tags don't trigger it). See `references/anti-bot-fallback-web-extract.md`.
- **`_persist_article()` requires `markdown` field in article dict**: The article dict passed to `_persist_article()` must include a `"markdown"` key (string) in addition to `content_markdown`. Without it, `_validate_success_payload()` raises `ClipError: The extractor returned incomplete or invalid article data.` The `markdown` field is part of the extractor's output schema and is validated even though `content_markdown` is the parameter used by `render_note()`. Always include both.
- **Branch semantics (vault vs source repo)**: NEW article clips auto-commit + push directly to the vault's `sync_branch` (master by default) via the plugin's `finalize()` — that is the expected flow (user-confirmed direct-master exception for the Obsidian vault). Only MANUAL vault edits/backfills and changes to the `url-to-obsidian` source repo use feature branch + PR (backfill example: `backfill/netease-author` branch, user manually merged). Never force-push or rewrite history; if a repo rejects direct push due to branch protection, fall back to feature branch + PR.
- **Git sync tests need upstream**: `GitSync.preflight()` requires the branch to have an upstream. In unit tests, always call `git push -u origin <branch>` after the initial commit, before calling `preflight()`.
- **Batch processing pitfalls**: When clipping 10+ articles sequentially, the vault worktree can get dirty between clips (especially after failed local-images clips that leave deleted images on disk). Fix: run `git checkout -- .` in the vault before each clip. Also, 163.com and other sites rate-limit rapid sequential requests — add a 2-second delay between clips. See `references/batch-processing.md` for the full proven workflow (pre-extract → sequential clip with cleanup → summary table).
- **SPA / client-side rendered pages**: Some sites (e.g., kimi.com, certain modern SPAs) render content entirely via JavaScript — the HTML response contains no article content, only JS bundles. The Node.js extractor, `web_extract`, and curl all return empty/boilerplate. Without a real browser session, these cannot be extracted. Report to user as requiring manual browser access. Do NOT save the empty shell as a note.

## Commit Message

Commits use the article title: `clip: <title>` (truncated to 60 chars). The `finalize()` method accepts an optional `commit_message` parameter; when `None`, falls back to `clip: save web article`. The `_persist_article()` method auto-generates the message from the article title.


## Filename Date Prefix

New clips use the article's **published date** (from frontmatter `published` field) as the filename date prefix, falling back to the capture date when published date is unavailable or invalid. Implemented in `ClipService._publish_date()` (PR #15 on url-to-obsidian).

- `published: '2026-05-20T01:20:46+00:00'` → filename prefix `2026-05-20`
- `published: '2026-05-20'` → filename prefix `2026-05-20`
- Missing/invalid published → falls back to today's date

## Post-Clip

After article is saved and pushed:
1. Show the GitHub vault URL from `ClipResult.github_url`
2. Update any tracking (e.g., reading list) if user maintains one
