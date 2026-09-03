# Batch Processing Workflow

When clipping multiple articles (10+) in a single session, specific pitfalls and patterns apply.

## Pitfall: `execute_code` 300s Timeout

The `execute_code` tool has a hard 300-second timeout. Processing 10+ articles sequentially (each taking 10-30s extraction + 2s delay) will exceed this limit.

**Symptom**: `⏰ Cell timed out after 300s; the session kernel was killed and its state was lost.`

**Fix**: Use bash scripts via `terminal()` instead of `execute_code` for batch processing. Write a `.sh` file with a loop, then execute it:
```bash
cat > /tmp/batch_clip.sh << 'SCRIPT'
#!/bin/bash
cd ~/.hermes/workspace/repository/url-to-obsidian
VAULT=~/obsidian/shijistar

clip_one() {
    local url="$1" imgs="$2" label="$3"
    cd "$VAULT" && git checkout -- . 2>/dev/null
    cd ~/.hermes/workspace/repository/url-to-obsidian
    sleep 2
    result=$(python3 -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
from web_to_obsidian import ClipService
service = ClipService(Path('.'))
try:
    result = service.run('${url} --save-images ${imgs} --no-browser')
    print(type(result).__name__ + '|||' + result.user_message())
except Exception as e:
    print('ERROR|||' + str(e))
" 2>&1)
    status=$(echo "$result" | cut -d'|' -f1)
    msg=$(echo "$result" | cut -d'|' -f3-)
    echo "${label}. [${imgs}] ${status}: ${msg}"
}

clip_one "<url1>" "no" "1"
clip_one "<url2>" "yes" "2"
# ... more articles
SCRIPT
bash /tmp/batch_clip.sh
```

For 20+ articles, split into 2 bash scripts (10-12 each) to stay within the 600s terminal timeout.

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

## One-Step Non-Interactive Clip

When the image decision is already known (user specified "本地图片" or "远程图片"), use flags to skip the PendingClipResult two-step:
```python
result = service.run(f'{url} --save-images yes|no --no-browser')
# Returns ClipResult directly, no resume_pending() needed
```

This is the recommended approach for batch processing — it avoids stale pending-state errors.

## Handling Failed Extractions in Batch

For URLs where the extractor fails (QUALITY_GATE, HTTP_STATUS):
1. Try `web_extract` as fallback (see `anti-bot-fallback-web-extract.md`)
2. If web_extract also fails (SPA pages like kimi.com), report to user as requiring browser
3. Don't block the batch — log the failure and continue with remaining articles

## WeChat Batch Notes

- WeChat (`mp.weixin.qq.com`) clips use the plugin's curl fallback when Node.js extractor fails
- The curl fallback works reliably in batch — no special handling needed
- WeChat images are typically HTML `<img>` tags (not Markdown `![]()`), so they bypass the `--save-images` flow entirely

## GitHub Gist / Repo Notes

- Gist URLs (e.g., `gist.github.com/user/id`) and GitHub repo URLs extract cleanly
- Use `--save-images no` (default) — these rarely have downloadable images
- Gist content is typically README-style markdown

## Renaming a Clipped Article

If the user wants a different title than what was auto-extracted:
```bash
cd ~/obsidian/shijistar
git mv "Inbox/old-filename.md" "Inbox/new-filename.md"
sed -i 's/^title: .*$/title: New Title/' "Inbox/new-filename.md"
git add -A && git commit -m "clip: New Title" && git push
```

## Per-Article Image Preferences in Batch

When the user provides a list of URLs with mixed image preferences (some "本地图片", some not specified), process each article with the correct `--save-images` flag based on the user's per-article annotation.

**Example user input:**
```
抓取到obsidian，
https://example.com/article1    # no annotation → remote
https://example.com/article2，本地图片
https://example.com/article3
https://example.com/article4，本地图片
```

**Processing pattern:**
1. Parse the list into `(url, images_flag)` tuples
2. Use `--save-images no` for unannotated URLs, `--save-images yes` for "本地图片"
3. Pass flags directly (non-interactive) since the user pre-declared preferences

**Important**: This pre-declared batch mode is the ONLY acceptable scenario for skipping the image confirmation prompt. Single-URL clips MUST always ask.

## Anti-Bot Fallback for Failed Articles in Batch

When extractor fails for some articles in a batch (QUALITY_GATE, HTTP_STATUS, WeChat fetch failure):

1. **Don't block the batch** — log the failure, continue with remaining articles
2. **After batch completes**, use `web_extract` on failed URLs as fallback
3. If `web_extract` also fails (SPA sites like kimi.com, quark.cn), report to user
4. For successful `web_extract` results, save via `_persist_article()` with `article["markdown"]` field (see `anti-bot-fallback-web-extract.md`)
5. For local-image articles that failed extraction, re-clip with `--refresh --save-images yes` after the fallback save

## Version History

- 2026-09-03-v3: Added per-article image preference pattern. Added anti-bot fallback workflow for batch failures.
- 2026-09-03-v2: Added execute_code timeout pitfall with bash script workaround. Added one-step non-interactive clip pattern. Added failed-extraction handling. Added renaming workflow. Split into 2 bash scripts for 20+ articles.
- 2026-09-03-v1: Created from batch session of 20 articles (163.com, Jiean, GitHub, WeChat).
  - Discovered dirty-worktree pitfall from failed local-images clips
  - Discovered 163.com rate limiting requiring 2s inter-clip delay
  - Verified WeChat curl fallback works in batch mode
