# web-clip-to-obsidian skill

The `skill/` directory is a Hermes agent skill that teaches an agent how to
use the web-to-obsidian plugin: extract an article, confirm image handling,
save a dated Markdown note, and commit & push it to the Obsidian vault Git.

- `SKILL.md` — frontmatter (name, description, version) plus the full workflow:
  plugin location/config, invocation patterns, pending-state flow, commit
  message, filename date prefix, and a large pitfalls section.
- `references/` — deep-dive documents for site quirks and fallback workflows
  (see index below).

## Version

`SKILL.md` frontmatter `version` is the skill version (currently `1.3.0`).
The plugin version is tracked separately in `../plugin/plugin.yaml` and
`../CHANGELOG.md`.

## Deploy to a Hermes profile

Symlink (not copy) the skill into the profile's skills directory so the repo
stays the single source of truth:

```bash
ln -s /path/to/url-to-obsidian/skill \
      ~/.hermes/profiles/<profile>/skills/productivity/web-clip-to-obsidian
```

The skill is designed to be used together with the plugin symlink
(`~/.hermes/profiles/<profile>/plugins/web-to-obsidian -> <repo>/plugin`).

## Reference index

| File | Covers |
|------|--------|
| `references/anti-bot-fallback-web-extract.md` | When the Node extractor is blocked (Zhihu, Doubao), fall back to `web_extract` + `_persist_article()` |
| `references/batch-processing.md` | Proven workflow for clipping 10+ articles sequentially |
| `references/git-sync-testing-pattern.md` | Git safety tests require an upstream branch |
| `references/github-private-blob-url-and-image-mode.md` | GitHub blob preview URLs and `image_mode` metadata semantics |
| `references/netease-embedded-state-author.md` | Netease author/published extraction from embedded JSON state |
| `references/netease-placeholder-and-video-links.md` | Netease `empty.png` placeholder images and anti-scrape video short links |
| `references/pending-resume-fallback.md` | Stale pending-state directory lock and fallbacks |
| `references/profile-skill-vs-source-repo-sync.md` | Keeping the profile skill in sync with the source repo |
| `references/repo-linked-maintenance.md` | Maintenance implications of symlinking the skill/plugin from a Git repo |
| `references/wsl-chrome-cdp-extraction.md` | WSL Chrome CDP extraction path |
| `references/zhihu-cookie-and-scraper-diagnostics.md` | Layered Zhihu cookie/fetch/browser diagnostics |

## Trigger phrases

The skill activates on intent like "抓取到obsidian" / "clip to obsidian" /
"save to vault" / "web clip" / "剪藏", or when a URL is shared with the
intent to archive it.