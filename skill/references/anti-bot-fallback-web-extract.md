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

2. **Build article dict** (must pass `_validate_success_payload`):
```python
article = {
    "ok": True,
    "title": "...",          # required, non-empty
    "author": "...",          # optional but recommended
    "published": "...",       # ISO 8601, optional
    "description": "...",     # optional
    "site": "...",            # optional
    "canonicalUrl": url,        # required, valid URL
    "keywords": ["..."],       # list of strings, max 128
    "url": url,                 # required, valid URL
    "wordCount": 1000,          # int >= 0
    "method": "static",         # required
    "markdown": ""              # empty string is fine
}
```

3. **Call _persist_article directly**
```python
from pathlib import Path
from datetime import datetime
from web_to_obsidian import ClipService, ClipConfig, GitSync

plugin_root = Path('.')
config = ClipConfig.from_file(plugin_root / 'config.toml')
git_sync = GitSync(vault=config.vault, repo_root=config.vault, branch=config.sync_branch)

service = ClipService(plugin_root)
result = service._persist_article(
    config=config,
    article=article,
    captured_at=datetime.now(),
    refresh=False,
    git_sync=git_sync,
    content_markdown=extracted_markdown,  # from web_extract
    image_mode=None,  # HTML <img> tags don't trigger Markdown image flow
    generated_paths=[],
)
print(result.user_message())
```

## Key Points

- `image_mode=None` is correct when the extracted content has no Markdown `![](...)` images (only HTML `<img>`)
- HTML `<img>` tags are NOT processed by the remote-image confirmation flow
- This bypasses the `PendingClipResult` / `resume_pending()` flow entirely
- The `markdown` field in article dict can be empty — the real content goes in `content_markdown` param
- Requires: `ok: true`, `method: static`, valid `canonicalUrl` and `url`, non-empty `title`

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
    "method": "web_extract_fallback"
}
```

## Related References

- `zhihu-cookie-and-scraper-diagnostics.md` — layered diagnostics for Zhihu cookie/fetch/browser
- `netease-embedded-state-author.md` — another anti-bot quirk (netease embedded JSON)

## Version History

- 2026-09-04: Reverted article dict to master comment style. Reverted to `ClipConfig.from_file()` + `GitSync()` constructor (config.toml). Removed `markdown` field from Doubao example.
- 2026-09-03-v2: Fixed API calls — `ClipConfig.from_env()` (not `from_file`), `GitSync.preflight()` (not constructor). Added `markdown` field non-empty requirement (was incorrectly documented as optional). Added doubao.com example.
- 2026-09-03-v1: Created from session where Zhihu article was clipped via web_extract fallback.
