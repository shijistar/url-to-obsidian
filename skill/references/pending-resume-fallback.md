# Pending resume fallback for web-to-obsidian

Use this when the first clip call returns a pending image-confirmation state and the dedicated Hermes resume tool cannot proceed.

## Symptom

The article extracts successfully and reports remote images, but the Hermes `web_to_obsidian_resume_pending` wrapper blocks progress — for example because it requires a `decision=yes|no` argument that is unavailable in the current wrapper surface.

## Verified fallback

Run the plugin's native Python API from the plugin directory and call `ClipService.resume_pending('yes')` or `ClipService.resume_pending('no')` directly.

```bash
cd ~/.hermes/profiles/<profile>/plugins/web-to-obsidian && \
python3 - <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, '.')
from web_to_obsidian import ClipService
service = ClipService(Path('.'))
result = service.resume_pending('no')  # or 'yes'
print(result.user_message())
print('\n---PATH---')
print(getattr(result, 'path', ''))
print('\n---GITHUB-URL---')
print(getattr(result, 'github_url', ''))
PY
```

## Why this is useful

- Bypasses wrapper-layer parameter mismatches.
- Preserves the normal plugin workflow and pending-state storage.
- Returns the same final `ClipResult` fields needed for user reporting: vault path, git state, and GitHub preview URL.

## Verification checklist

1. `result.user_message()` says the clip was saved.
2. `path` is non-empty.
3. `github_url` is present when git push succeeds.
4. The generated markdown frontmatter reflects the user's image choice, e.g. `image_mode: remote` when the user declined image download.
