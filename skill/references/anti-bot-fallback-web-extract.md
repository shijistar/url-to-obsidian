# Anti-bot Fallback via web_extract

When the Node.js extractor fails due to anti-bot measures (Zhihu, Doubao, some paywalled sites), but `web_extract` succeeds in retrieving the article content, use this fallback workflow to save the article to the vault.

## When to Use

- `node extractor/src/cli.mjs <url> --no-browser` returns `ok: false`, `code: "QUALITY_GATE"` or `HTTP_STATUS"`
- `web_extract` on the same URL returns clean Markdown/HTML content
- User wants the article saved despite extractor failure
- Common triggers: Zhihu, Doubao (doubao.com), some paywalled SPA sites

## Workflow

1. **Extract via web_extract**
```python
from hermes_tools import web_extract
result = web_extract([url])
# result["results"][0]["content"] contains the Markdown/HTML
```

2. **Build article dict** — all fields in `_validate_success_payload` limits dict MUST be non-empty strings:
```python
article = {
    "ok": True,
    "title": "...",          # REQUIRED, non-empty string
    "author": "...",          # string, empty OK but must be present
    "published": "...",       # string, empty OK
    "description": "...",     # string, empty OK
    "site": "...",            # string, empty OK
    "canonicalUrl": url,        # REQUIRED, valid URL string
    "keywords": [],             # list of strings, max 128
    "url": url,                 # REQUIRED, valid URL string
    "wordCount": 1000,          # int >= 0
    "method": "web_extract_fallback",  # REQUIRED, non-empty string
    "markdown": "..."           # REQUIRED, non-empty string (actual content)
}
```

**⚠️ Critical**: The `markdown` field MUST be a non-empty string containing the article content. `_validate_success_payload()` checks all fields in the limits dict — an empty or missing `markdown` raises `ClipError("The extractor returned incomplete or invalid article data.")`. The `content_markdown` parameter to `_persist_article()` is a separate arg; both must be set.

3. **Call _persist_article directly** — use `ClipConfig.from_env()` and `GitSync.preflight()`:
```python
import sys; sys.path.insert(0, '.')
from pathlib import Path
from datetime import datetime, timezone
from web_to_obsidian import ClipService, ClipConfig, GitSync

config = ClipConfig.from_env()  # NOT from_file()
vault = Path.home() / 'obsidian' / 'shijistar'
git_sync = GitSync.preflight(vault, config.sync_branch)  # NOT GitSync(vault=..., repo_root=..., branch=...)

service = ClipService(Path('.'))
result = service._persist_article(
    config=config,
    article=article,
    captured_at=datetime.now(timezone.utc),
    refresh=False,
    git_sync=git_sync,
    content_markdown=article["markdown"],  # same as markdown field
    image_mode=None,  # HTML <img> tags don't trigger Markdown image flow
)
print(result.user_message())
```

**⚠️ API gotchas** (discovered 2026-09-03):
- `ClipConfig.from_env()` reads from env vars (WEB_TO_OBSIDIAN_VAULT etc.), NOT from config.toml
- `GitSync.__init__()` requires 3 positional args: `vault`, `repo_root`, `branch`. Use `GitSync.preflight(vault, branch)` classmethod which auto-detects `repo_root` via `git rev-parse --show-toplevel`
- `GitSync.preflight()` requires the branch to have an upstream — if not, push first with `git push -u origin <branch>`
- `generated_paths` is optional, defaults to `()`

## Key Points

- `image_mode=None` is correct when the extracted content has no Markdown `![](...)` images (only HTML `<img>`)
- HTML `<img>` tags are NOT processed by the remote-image confirmation flow
- This bypasses the `PendingClipResult` / `resume_pending()` flow entirely
- Requires: `ok: true`, `method: non-empty`, valid `canonicalUrl` and `url`, non-empty `title`

## Example: Doubao Article

```python
article = {
    "ok": True,
    "title": "古德哈特定律介绍",
    "author": "",
    "published": "2026-05-26",
    "description": "当一项指标被当作考核目标时，它就不再是有效的衡量指标",
    "site": "doubao.com",
    "canonicalUrl": "https://www.doubao.com/thread/aa1278a169ca5",
    "keywords": [],
    "url": "https://www.doubao.com/thread/aa1278a169ca5",
    "wordCount": 200,
    "method": "web_extract_fallback",
    "markdown": "...actual content from web_extract..."
}
```

## Related References

- `zhihu-cookie-and-scraper-diagnostics.md` — layered diagnostics for Zhihu cookie/fetch/browser
- `netease-embedded-state-author.md` — another anti-bot quirk (netease embedded JSON)

## Version History

- 2026-09-03-v2: Fixed API calls — `ClipConfig.from_env()` (not `from_file`), `GitSync.preflight()` (not constructor). Added `markdown` field non-empty requirement (was incorrectly documented as optional). Added doubao.com example.
- 2026-09-03-v1: Created from session where Zhihu article was clipped via web_extract fallback.
