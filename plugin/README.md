# web-to-obsidian plugin

A synchronous Hermes standalone plugin that clips a **public** web article
into an Obsidian Vault and optionally performs guarded Git synchronization.

This directory is the Hermes plugin package: `plugin.yaml`, `__init__.py`
(registration entry point), `web_to_obsidian.py` (core logic), and the TOML
config. The Node.js extraction engine lives in its sibling `../extractor/`.

See `../CHANGELOG.md` for version history.

## Requirements

- Hermes Agent with standalone plugin support.
- Python 3.11+ and PyYAML.
- Node.js 18+ (`plugin/node_modules` must contain `@tiny-codes/web-clip-extractor`).
- Git for default synchronization.
- Playwright Chromium (via the extractor) for dynamic fallback.

## Install

Install from a Git checkout using Hermes' plugin manager, then install the
published extractor npm package into the plugin directory. Keeping the
extractor inside `plugin/node_modules` means that upgrading the plugin also
upgrades its extractor dependency:

```bash
REPO=/path/to/url-to-obsidian
hermes plugins install "file://$REPO/plugin" --enable
cd "$HERMES_HOME/plugins/web-to-obsidian"
npm install
npx playwright install chromium
cp "$REPO/plugin/config.example.toml" "$HERMES_HOME/plugins/web-to-obsidian/config.toml"
hermes gateway restart
```

For a named profile, set `HERMES_HOME` to that profile before the commands.
Review `config.toml` before restarting the Gateway.

> The installed plugin is a clone of `$REPO/plugin`. After `npm install`, the
> extractor lives at `<plugin_root>/node_modules/@tiny-codes/web-clip-extractor`
> and is resolved first at runtime. When developing from a source checkout
> (no `npm install`), the plugin falls back to the source-repo sibling
> `plugin_root.parent / "extractor"` (legacy layouts where the extractor sat
> directly under the plugin root are also tolerated).

## Configuration

`config.toml` is non-secret and lives in the installed plugin directory:

```toml
[clip]
vault = "~/obsidian/shijistar"
destination = "Inbox"
images = "images"
sync_branch = "master"
lock_file = "~/.hermes/workspace/cache/url-to-obsidian/vault.lock"
pending_root = "~/.hermes/workspace/cache/url-to-obsidian/pending-state"
```

- `vault` — the Obsidian vault root.
- `destination` — note destination subdirectory inside the vault.
- `images` — directory inside the vault for localized images.
- `sync_branch` — the Git branch the vault must be on for automatic sync.
- `lock_file` — cross-process lock path; **must be outside the vault**.
- `pending_root` — where pending image confirmations are stored; only one
  active yes/no confirmation is supported at a time.

When `config.toml` is absent, the legacy `WEB_TO_OBSIDIAN_VAULT`,
`WEB_TO_OBSIDIAN_DEST`, `WEB_TO_OBSIDIAN_IMAGES`, `WEB_TO_OBSIDIAN_SYNC_BRANCH`,
`WEB_TO_OBSIDIAN_LOCK_FILE`, and `WEB_TO_OBSIDIAN_PENDING_ROOT` environment
variables are accepted as a fallback. These values are never forwarded to the
untrusted extractor process.

## Usage

```text
/webclip <url> [--refresh] [--no-browser] [--no-git] [--save-images yes|no|ask]
```

- Exactly one public `http://` or `https://` URL is accepted.
- `--refresh` explicitly updates changed managed content while preserving the
  manual section.
- `--no-browser` disables the Playwright fallback in the extractor.
- `--no-git` disables Git preflight, commit, and push for that invocation.
- `--save-images yes` downloads remote Markdown/HTML image references into
  `images/<article-slug>/...` before saving the note.
- `--save-images no` preserves remote image URLs and saves immediately.
- Omitting `--save-images` behaves as `ask`: if the final sanitized Markdown
  has remote `http/https` image references, `/webclip` stores one pending
  confirmation and waits for a plain `yes` or `no` reply via the registered
  `web_to_obsidian_resume_pending` tool. If no remote images remain, the note
  is saved immediately.

### Programmatic entry points

```python
from web_to_obsidian import ClipService
from pathlib import Path

service = ClipService(Path("."))          # run from the plugin directory
result = service.run("<url> [flags]")     # ClipResult | PendingClipResult
# If PendingClipResult: service.resume_pending("yes" | "no")
print(result.user_message())
```

`build_handler(root)` returns the Hermes slash-command string handler;
`build_resume_tool(root)` returns the `web_to_obsidian_resume_pending` tool
handler.

## Filename date prefix

New clips use the article's `published` date as the filename date prefix,
falling back to the capture date when the published date is missing or
invalid.

- `published: '2026-05-20T01:20:46+00:00'` → `2026-05-20`
- `published: '2026-05-20'` → `2026-05-20`
- Missing/invalid → today's date

## Extraction fallback

The plugin drives the Node extractor via `run_extractor_with_fallback()`:

1. `run_extractor()` — Node extractor with hardened network policy.
2. On failure for **WeChat URLs** (`mp.weixin.qq.com`), a curl-based fallback
   fetches the raw HTML and parses title, author, publish time, and the
   `js_content` body via regex, converting HTML to Markdown. This bypasses the
   WeChat slider CAPTCHA that headless browsers trigger.
3. Other sites surface the Node extractor error directly.

## Network and content safety

- URLs reject credentials, fragments, unsafe ports, malformed hosts, and
  non-HTTP schemes.
- Every redirect is revalidated; see `../extractor/README.md` for the
  extractor-side network policy.
- The extractor child receives only an allowlisted environment and runs in a
  new POSIX process group. Timeout/output-limit cleanup terminates the
  complete group.
- Extracted Markdown removes injected management comments, active HTML,
  dangerous local/custom URL schemes, and Obsidian embeds. Ordinary HTTPS
  links and remote images remain.
- Remote **image** downloads add SSRF protection: full RFC reserved IP range
  blocking, redirect pinning (max 6 hops), non-default port rejection, IDNA
  hostname validation, and `Content-Type: image/*` enforcement.
- Code-aware sanitization: fenced code blocks, inline code spans, and indented
  code lines are skipped when scanning for remote images.
- Credential-like markers in extracted content are detected and refuse to
  save.

## Vault and idempotency safety

- A cross-process external lock covers Git synchronization, extraction, write,
  commit, and push.
- Paths are resolved and required to remain inside the configured Vault.
- Portable filenames use `YYYY-MM-DD-{article-title}.md` in the configured
  destination and reject traversal, control characters, device names, and
  excessive UTF-8 length.
- Writes use a same-directory temporary file, file `fsync`, `os.replace`, and
  parent-directory `fsync` where supported. Symlink targets are rejected.
- Frontmatter is serialized with `title`, `url`, `author`, `site`,
  `description`, `keywords`, `tags`, `original_url`, `original_host`,
  optional `fetched_url` (when the request URL differs from the canonical
  article URL), `extraction_method`, `status`, `category`, `word_count`,
  `webclip_id`, `source_content_hash`, `content_hash`, `image_mode`,
  `published`, and `created`.
- Managed note content preserves an extracted Markdown H1 when present and
  injects `# <title>` only when the extracted article has no Markdown H1.
- Existing notes are matched by normalized `url`, `original_url`, or legacy
  `source` frontmatter so older notes remain refreshable without duplication.
  That compatibility is recognition-only; metadata is rewritten to the new
  field names when a note is refreshed, not by a background migration.
- Same managed source + same managed content is a no-op. Changed content is
  rejected unless `--refresh` is explicit. Refresh preserves text inside the
  manual boundary.
- Frontmatter is fully managed output. User-added frontmatter keys are not part
  of the preserved manual region and may trigger a refresh requirement or be
  overwritten on refresh.
- Title collisions from different sources receive a deterministic URL hash.

## Git safety

With Git enabled, `/webclip` requires:

1. the configured sync branch (the shipped default is `master`);
2. no merge/rebase state;
3. a completely clean worktree, including untracked files;
4. an `origin` remote and successful `fetch --prune`;
5. a successful rebase of the current upstream before extraction.

After writing, it verifies the exact changed path, stages only that note,
verifies the staged set, commits with a message derived from the article title
(e.g. `clip: <title>`), then verifies the actual commit path set and clean
worktree before pushing `HEAD` normally. It never force-pushes or rewrites
public history.
If commit fails, the note remains untracked for recovery. If push fails, the
local commit remains for a later manual retry. When the `origin` remote points at
GitHub, successful saves include the `blob/<branch>/...` preview URL in the user
response.

## Tests

Python tests are colocated with the plugin in `tests/` (same directory). Run
pytest from the plugin directory:

```bash
python3 -m pytest tests/ -v
```

The tests use fixtures, temporary directories, and temporary Git
repositories; they do not write the configured real Vault. A `tests/conftest.py`
injects the plugin directory (`..`) into `sys.path` so `import web_to_obsidian`
resolves here.