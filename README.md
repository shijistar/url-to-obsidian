# Web to Obsidian Hermes Plugin

A synchronous Hermes standalone plugin that clips a **public** web article into an
Obsidian Vault and optionally performs guarded Git synchronization.

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Project Structure

```
url-to-obsidian/
├── web_to_obsidian.py        # Core plugin logic
├── __init__.py               # Plugin entry point (slash command + resume tool)
├── plugin.yaml               # Plugin metadata
├── config.example.toml       # Configuration template
├── extractor/                # Node.js content extraction engine
│   ├── src/cli.mjs           # CLI entry point
│   └── package.json
├── skill/                    # Hermes agent skill documentation
│   └── SKILL.md              # Workflow instructions for the agent
├── tests/                    # Python unit tests
├── CHANGELOG.md              # Version history
└── README.md
```

## Current scope

- Static extraction with Defuddle.
- Playwright Chromium fallback for weak static pages.
- Remote HTTP(S) image references can either stay remote or be downloaded into the Vault.
- The default `/clip <url>` flow asks for a follow-up yes/no decision only when the
  final sanitized Markdown still contains remote images.
- Login-gated pages, cookies, credentials, and password-manager integration are
  intentionally unsupported.
- Linux/WSL only: the implementation uses `fcntl` locks and POSIX process groups.

## Requirements

- Hermes Agent with standalone plugin support.
- Python 3.11+ and PyYAML.
- Node.js 18+.
- Git for default synchronization.
- Locked Node packages from `extractor/package-lock.json`.
- Playwright Chromium for dynamic fallback.

## Install

Install from a local Git checkout using Hermes' plugin manager, then install the
locked Node dependency tree in the installed copy:

```bash
hermes plugins install file:///absolute/path/to/web-to-obsidian --enable
cd "$HERMES_HOME/plugins/web-to-obsidian/extractor"
npm ci --ignore-scripts
npx playwright install chromium
cp ../config.example.toml ../config.toml
hermes gateway restart
```

For a named profile, set `HERMES_HOME` to that profile before the commands.
Review `config.toml` before restarting the Gateway.

## Configuration

`config.toml` is non-secret and lives in the installed plugin directory:

```toml
[clip]
vault = "~/obsidian/shijistar"
destination = "Inbox"
images = "images"
sync_branch = "master"
# lock_file = "~/.local/state/web-to-obsidian/vault.lock"
```

The lock must be outside the Vault. When `config.toml` is absent, the legacy
`WEB_TO_OBSIDIAN_VAULT`, `WEB_TO_OBSIDIAN_DEST`,
`WEB_TO_OBSIDIAN_IMAGES`, `WEB_TO_OBSIDIAN_SYNC_BRANCH`,
`WEB_TO_OBSIDIAN_LOCK_FILE`, and `WEB_TO_OBSIDIAN_PENDING_ROOT` variables are
accepted as a fallback. The pending state defaults to
`~/.hermes/workspace/cache/url-to-obsidian/pending-state` and supports only one
active yes/no confirmation at a time. These values are never forwarded to the
untrusted extractor process.

## Usage

```text
/clip <url> [--refresh] [--no-browser] [--no-git] [--save-images yes|no|ask]
```

- Exactly one public `http://` or `https://` URL is accepted.
- `--refresh` explicitly updates changed managed content while preserving the
  manual section.
- `--no-browser` disables Playwright fallback.
- `--no-git` disables Git preflight, commit, and push for that invocation.
- `--save-images yes` downloads remote Markdown/HTML image references into
  `images/<article-slug>/...` before saving the note.
- `--save-images no` preserves remote image URLs and saves immediately.
- Omitting `--save-images` behaves as `ask`: if the final sanitized Markdown has
  remote `http/https` image references, `/clip` stores one pending confirmation
  and waits for a plain `yes` or `no` reply via the registered
  `web_to_obsidian_resume_pending` tool. If no remote images remain, the note is
  saved immediately.

## Network and content safety

- URLs reject credentials, fragments, unsafe ports, malformed hosts, and
  non-HTTP schemes.
- Every redirect is revalidated. DNS answers must all be public, and Node pins
  each request to an approved address while preserving TLS SNI/hostname checks.
- Static responses require an HTML media type and a bounded body.
- Chromium native DNS is disabled. Every HTTP(S) page resource is fetched by
  the pinned-IP Node layer and injected with `route.fulfill`; WebSockets,
  service workers, and downloads are blocked. Request count, per-resource bytes,
  total bytes, and wall time are bounded.
- The extractor child receives only an allowlisted environment and runs in a
  new POSIX process group. Timeout/output-limit cleanup terminates the complete
  group.
- Extracted Markdown removes injected management comments, active HTML,
  dangerous local/custom URL schemes, and Obsidian embeds. Ordinary HTTPS links
  and remote images remain.

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

With Git enabled, `/clip` requires:

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

```bash
cd extractor
npm test
npm run check

cd ..
python3 -W error::ResourceWarning -m unittest discover -s tests -v
python3 -m compileall -q __init__.py web_to_obsidian.py tests
```

The automated tests use fixtures, temporary directories, and temporary Git
repositories; they do not write the configured real Vault.

## Hermes Skill

The `skill/` directory contains a Hermes agent skill (`SKILL.md`) that teaches
the agent how to use this plugin. To deploy it to a Hermes profile:

```bash
# Symlink the skill into the profile's skills directory
ln -s /path/to/url-to-obsidian/skill \
      ~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian
```

The skill provides workflow instructions, invocation patterns, and pitfall
documentation for the agent when handling "clip to obsidian" requests.
