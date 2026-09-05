# url-to-obsidian

A **web clipping pipeline for Obsidian**: extract any public web article as
clean Markdown, save it as a dated note in your Obsidian vault, and — when the
vault is a Git repository — synchronize the note with guarded automatic commit
& push.

The project is a small monorepo of three parts:

- [`plugin/`](plugin/README.md) — the Hermes plugin (`/webclip` command +
  `web_to_obsidian_resume_pending` tool) implementing config, safe vault
  writes, image handling, and Git synchronization.
- [`extractor/`](extractor/README.md) — a hardened Node.js extraction engine
  (Defuddle static parsing + isolated Playwright fallback) with a CLI and
  strict network policy.
- [`skill/`](skill/README.md) — a Hermes agent skill teaching the full
  clip-to-Obsidian workflow, including anti-bot fallbacks and site quirks.

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Project structure

```
url-to-obsidian/
├── README.md                # This overview
├── CHANGELOG.md             # Version history
├── plugin/                  # Hermes plugin package (install target)
│   ├── __init__.py          # Plugin entry point (slash command + resume tool)
│   ├── web_to_obsidian.py   # Core plugin logic
│   ├── plugin.yaml          # Plugin metadata
│   ├── config.toml          # Local config (tracked; edit per install)
│   ├── config.example.toml  # Configuration template
│   └── README.md            # Install / config / usage / safety
├── extractor/               # Node.js content extraction engine
│   ├── src/cli.mjs          # CLI entry point
│   ├── src/extractor.mjs    # Static + Playwright extraction
│   ├── src/network-policy.mjs
│   ├── test/                # node --test suites
│   └── README.md            # CLI / error codes / network safety
├── skill/                   # Hermes agent skill
│   ├── SKILL.md             # Workflow instructions for the agent
│   ├── references/          # Site quirks & fallback deep-dives
│   └── README.md            # Skill overview / deployment / reference index
└── tests/                   # Python plugin test suites (pytest)
```

## Current scope

- Static extraction with Defuddle; Playwright Chromium fallback for weak
  static pages.
- Remote HTTP(S) image references can either stay remote or be downloaded
  into the Vault.
- The default `/webclip <url>` flow asks for a follow-up yes/no decision only
  when the final sanitized Markdown still contains remote images.
- WeChat article URLs automatically fall back to curl-based extraction when
  the Node extractor is blocked.
- Login-gated pages, cookies, credentials, and password-manager integration
  are intentionally unsupported.
- Linux/WSL only: the implementation uses `fcntl` locks and POSIX process
  groups.

## Quick start

Requirements: Hermes Agent, Python 3.11+, Node.js 18+, Git, PyYAML.

```bash
REPO=/path/to/url-to-obsidian

# 1. Install the plugin
hermes plugins install "file://$REPO/plugin" --enable

# 2. Install the extractor's locked Node deps + Chromium
cd "$REPO/extractor"
npm ci --ignore-scripts
npx playwright install chromium

# 3. Configure
cd "$REPO"
cp plugin/config.example.toml "$HERMES_HOME/plugins/web-to-obsidian/config.toml"
# edit $HERMES_HOME/plugins/web-to-obsidian/config.toml => vault, destination, ...
hermes gateway restart
```

Then clip articles:

```text
/webclip https://example.com/article
/webclip https://example.com/article --save-images yes
/webclip https://example.com/article --refresh
```

Full usage, flags, and safety documentation live in
[`plugin/README.md`](plugin/README.md).

## Configuration

All configuration lives in `plugin/config.toml` (vault, destination, images
directory, sync branch, lock file, pending root). See
[`plugin/README.md`](plugin/README.md#configuration) for the full field list,
and the legacy environment-variable fallback.

## Tests

```bash
# Node extractor
cd extractor
npm test
npm run check

# Python plugin (from repo root)
cd ..
python3 -m pytest tests/ -v
```

The automated tests use fixtures, temporary directories, and temporary Git
repositories; they do not write the configured real Vault.

## Documentation map

| Topic                    | Where                                        |
|--------------------------|----------------------------------------------|
| Install / config / usage | [`plugin/README.md`](plugin/README.md)       |
| Plugin network/Vault/Git safety | [`plugin/README.md`](plugin/README.md) |
| Extractor CLI & error codes | [`extractor/README.md`](extractor/README.md) |
| Extractor network policy | [`extractor/README.md`](extractor/README.md#network-safety) |
| Agent skill deployment   | [`skill/README.md`](skill/README.md)         |
| Anti-bot fallbacks       | `skill/references/` (index in skill/README.md) |
| Version history          | [`CHANGELOG.md`](CHANGELOG.md)               |