# Batch Processing Workflow

When clipping multiple articles (10+) in a single session, specific pitfalls and patterns apply.

## Pitfall: Dirty Vault Worktree Between Clips

The vault worktree can become dirty between sequential clips, blocking subsequent clips with:
```
ClipError: Git protection requires an entirely clean worktree.
```

**Root cause**: Failed local-images clips leave deleted image files on disk. When a clip with `--save-images yes` partially completes (downloads images but then fails during git commit), the images are deleted from the working tree but not staged. The next clip's `GitSync.preflight()` sees the dirty state and refuses to proceed.

**Fix**: Clean the worktree between clips:
```bash
cd ~/obsidian/shijistar && git checkout -- .
# Verify clean:
git status --short  # should be empty
```

Check `git status --short` — if output is non-empty, run `git checkout -- .` before the next clip.

## Pitfall: Rate Limiting on 163.com

Clipping many 163.com articles in rapid succession triggers HTTP 429 / NETWORK_ERROR. The Node.js extractor and the plugin's run() both hit this.

**Fix**: Add a 2-second delay between clips:
```python
import time
time.sleep(2)
result = service.run(url_flags)
```

The delay is per-clip, not per-site. Apply uniformly when processing mixed-site batches.

## Proven Batch Workflow

### 1. Pre-extract all URLs (parallel, read-only)

Verify every URL extracts successfully before starting clips. Batch in groups of 5-6:
```python
import json, subprocess
for idx, url in urls:
    r = subprocess.run(
        ["node", "extractor/src/cli.mjs", url, "--no-browser"],
        capture_output=True, text=True, timeout=60
    )
    data = json.loads(r.stdout) if r.stdout else {}
    print(f"{idx}. ok={data.get('ok')} title={data.get('title','?')[:50]} wc={data.get('wordCount',0)}")
```

Report failed URLs to user before proceeding. Decide: retry, skip, or use fallback.

### 2. Clip sequentially with cleanup

```python
import sys, time, subprocess
sys.path.insert(0, '.')
from pathlib import Path
from web_to_obsidian import ClipService

service = ClipService(Path('.'))
vault = Path.home() / 'obsidian' / 'shijistar'

def clean_vault():
    subprocess.run(['git', 'checkout', '--', '.'], cwd=str(vault), capture_output=True)

for idx, url, imgs in articles:  # imgs = "yes" or "no"
    clean_vault()  # ensure clean before each clip
    time.sleep(2)  # rate limit protection
    try:
        result = service.run(f'{url} --save-images {imgs} --no-browser')
        print(f"{idx}. OK: {result.user_message()[:100]}")
    except Exception as e:
        # Retry with --refresh in case of hash mismatch
        clean_vault()
        time.sleep(3)
        try:
            result = service.run(f'{url} --save-images {imgs} --refresh --no-browser')
            print(f"{idx}. OK (retry): {result.user_message()[:100]}")
        except Exception as e2:
            print(f"{idx}. FAIL: {e2}")
```

### 3. Post-batch summary

Collect all `ClipResult.github_url` values and present as a table:
```markdown
| # | Title | Images | Preview |
|---|-------|--------|---------|
| 1 | ... | 远程 | [预览](url) |
| 2 | ... | **本地** | [预览](url) |
```

## WeChat Batch Notes

- WeChat (`mp.weixin.qq.com`) clips use the plugin's curl fallback when Node.js extractor fails
- The curl fallback works reliably in batch — no special handling needed
- WeChat images are typically HTML `<img>` tags (not Markdown `![]()`), so they bypass the `--save-images` flow entirely

## GitHub Gist / Repo Notes

- Gist URLs (e.g., `gist.github.com/user/id`) and GitHub repo URLs extract cleanly
- Use `--save-images no` (default) — these rarely have downloadable images
- Gist content is typically README-style markdown

## Version History

- 2026-09-03: Created from batch session of 20 articles (163.com, Juejin, GitHub, WeChat)
  - Discovered dirty-worktree pitfall from failed local-images clips
  - Discovered 163.com rate limiting requiring 2s inter-clip delay
  - Verified WeChat curl fallback works in batch mode