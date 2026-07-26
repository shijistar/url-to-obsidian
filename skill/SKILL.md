---
name: web-clip-to-obsidian
description: Clip web articles to the Obsidian vault via the url-to-obsidian plugin — extract, confirm images, save as dated markdown, commit & push to Git. Use when the user shares a URL wanting to save/read-later in Obsidian, or says "抓取到obsidian"/"clip to obsidian"/"save to vault".
version: 1.1.0
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
```

## Invocation Pattern

The plugin exposes a Python API via `build_handler`. Invoke from the plugin directory:

```python
import sys
sys.path.insert(0, '.')
from web_to_obsidian import build_handler
from pathlib import Path

plugin_root = Path('.')
handler = build_handler(plugin_root)
result = handler('<URL>')
print(result)
```

CLI shortcut (same effect):
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

1. **Extract**: Call `handler(url)` — runs Node.js extractor, returns `ClipResult` or `PendingClipResult`
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
- **WeChat articles fail silently**: Plugin returns `BROWSER_FAILED`, `web_extract` returns empty body. Both are expected for `mp.weixin.qq.com` — see WeChat fallback extraction reference.
- **Browser CAPTCHA wall**: WeChat triggers slider CAPTCHA in headless browser; no automated workaround exists.
- **Branch protection**: Repos with branch protection rules reject direct pushes to master. Always use feature branch + PR workflow. If you accidentally merge locally to master, `git reset --hard HEAD~1` before pushing, then create a PR.
- **Git sync tests need upstream**: `GitSync.preflight()` requires the branch to have an upstream. In unit tests, always call `git push -u origin <branch>` after the initial commit, before calling `preflight()`.

## Commit Message

Commits use the article title: `clip: <title>` (truncated to 60 chars). The `finalize()` method accepts an optional `commit_message` parameter; when `None`, falls back to `clip: save web article`. The `_persist_article()` method auto-generates the message from the article title.

## Post-Clip

After article is saved and pushed:
1. Show the GitHub vault URL from `ClipResult.github_url`
2. Update any tracking (e.g., reading list) if user maintains one
