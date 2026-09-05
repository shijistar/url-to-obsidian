# AGENTS.md

Working guide for humans and AI agents contributing to **url-to-obsidian** — a
web clipping pipeline that extracts public articles as clean Markdown, saves
dated notes into an Obsidian vault, and synchronizes them via guarded Git.

## Modules

| Path            | What it is                                                                 | Key files                                                                                                         |
| --------------- | -------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `plugin/`       | Hermes plugin package: config, safe vault writes, image handling, Git sync | `__init__.py`, `web_to_obsidian.py`, `plugin.yaml`, `config.example.toml`, `config.toml`                          |
| `extractor/`    | Hardened Node.js extraction engine (Defuddle static + Playwright fallback) | `src/cli.mjs`, `src/extractor.mjs`, `src/network-policy.mjs`, `package.json`                                      |
| `skill/`        | Hermes agent skill teaching the clip-to-Obsidian workflow                  | `SKILL.md`, `references/*.md`                                                                                     |
| `plugin/tests/` | Python plugin test suites                                                  | `conftest.py`, `test_web_to_obsidian.py`, `test_integration.py`, `test_plugin.py`, `test_security_regressions.py` |

The extractor is a **sibling** of the plugin package, not a subdirectory:
`plugin/` and `extractor/` both live at the repo root. At runtime the plugin
locates the extractor via `plugin_root.parent / "extractor"` (with a legacy
fallback for `plugin_root/extractor`).

## Directory layout

```
url-to-obsidian/
├── AGENTS.md
├── CHANGELOG.md                # Version history
├── README.md                   # project overview
├── plugin/                     # Hermes plugin (install target)
│   ├── __init__.py             # entry point: /webclip + resume tool
│   ├── web_to_obsidian.py      # core logic
│   ├── plugin.yaml             # plugin metadata + version
│   ├── install.sh              # one-shot installer (ships with hermes plugins install)
│   ├── package.json            # npm deps for the extractor (installed into plugin/node_modules)
│   ├── config.toml             # local non-secret config (tracked; edit per install)
│   ├── config.example.toml     # Configuration template
│   ├── after-install.md        # Follow-up steps shown by `hermes plugins install`
│   ├── README.md               # Install / config / usage / safety
│   └── tests/                  # python plugin test suites (pytest)
├── extractor/                  # node.js content extraction engine
│   ├── src/cli.mjs             # CLI entry point
│   ├── src/extractor.mjs       # static + Playwright extraction
│   ├── src/network-policy.mjs
│   ├── test/                   # node --test suites + fixtures
│   ├── README.md               # extractor README
│   └── package.json            # extractor version
├── skill/                      # Hermes agent skill
│   ├── SKILL.md                # workflow instructions for the agent + version in
│   ├── README.md               # skill README
│   └── references/             # site quirks & fallback deep-dives
```

## Build & test

### Python plugin

```bash
python3 -m pytest plugin/tests/ -v
```

- `plugin/tests/conftest.py` injects the plugin directory (its parent) into
  `sys.path` so `import web_to_obsidian` resolves to `plugin/web_to_obsidian.py`.
- Test suites: core unit tests (`test_web_to_obsidian.py`), real Git/vault
  integration (`test_integration.py`), plugin registration
  (`test_plugin.py`), and security regressions (`test_security_regressions.py`).

### Node extractor

```bash
cd extractor
npm ci --ignore-scripts   # first time only (locked deps)
npm test                  # node --test
npm run check             # node --check on each src module
```

## Versioning rules

The project tracks module versions in **three** places. The CHANGELOG uses
**date headings** (`## YYYY-MM-DD`), not version numbers:

| Place                    | Field                    | Current           |
| ------------------------ | ------------------------ | ----------------- |
| `plugin/plugin.yaml`     | `version`                | 0.7.0             |
| `extractor/package.json` | `version`                | 0.2.0             |
| `skill/SKILL.md`         | frontmatter `version`    | 1.4.0             |
| `CHANGELOG.md`           | `## YYYY-MM-DD` headings | 2026-09-05 latest |

Module versions are independent of each other and of the CHANGELOG; there is
no requirement that they match a changelog heading.

## Mandatory rules

### 1. Unit tests are required

Every **new feature** or **bug fix** MUST ship with unit tests covering the
changed behavior:

- Python changes → add/update tests under `plugin/tests/` (choose the suite by
  concern: core logic, integration, plugin registration, or security
  regression).
- Node extractor changes → add/update tests under `extractor/test/`.
- Security-relevant changes → add a regression test in
  `plugin/tests/test_security_regressions.py` or
  `extractor/test/security-regressions.test.mjs`, even if the change looks
  like a pure refactor.

Do not merge a change whose tests do not pass in the local run.

### 2. Version bumps + CHANGELOG updates are required

Any **new feature or behavior change** MUST:

1. bump the `version` of every module the change touches
   (`plugin/plugin.yaml`, `extractor/package.json`, `skill/SKILL.md` —
   bump only what the change actually touches);
2. add a `## YYYY-MM-DD` entry in `CHANGELOG.md` (use today's date) describing
   the change, under the existing changelog conventions. If an entry for that
   date already exists, append the change to it (multiple changes on the same
   date share one heading).

Pure docs/refactor changes that do not alter behavior do not require a version
bump, but a CHANGELOG entry is encouraged when user-visible.

## Git workflow

- Branch convention: `feat/<slug>` or `fix/<slug>` from the latest `master`.
  The default branch is `master`; never commit directly to it.
- Commit style: conventional commits — `feat:`, `fix:`, `docs:`,
  `refactor:`, `chore:`, optionally scoped like `fix(plugin):`.
- Push the feature branch and let the maintainer review before opening a PR.
  PR descriptions are written in English.
- Never force-push, never rewrite public history. Prefer `revert` over
  `reset --hard`.

## Safety first

This plugin handles untrusted web content and writes into a user vault. Keep
these invariants on every change:

- **SSRF protection**: remote image fetches must keep blocking reserved/private
  IP ranges, pin redirects, reject non-default ports, and enforce
  `Content-Type: image/*`. Extractor network policy lives in
  `extractor/src/network-policy.mjs`.
- **Path containment**: all writes must resolve inside the configured vault;
  symlink escapes and traversal must be rejected (covered by
  `plugin/tests/test_web_to_obsidian.py` TargetAndAtomicWriteTests).
- **Secrets**: never log or forward credentials/tokens to the extractor child;
  the extractor receives only an allowlisted environment. Credential-like
  markers in extracted content must refuse to save.
- **Sandboxing**: the extractor child runs in a new POSIX process group;
  timeout/output-limit cleanup must terminate the whole group.
- **Atomicity**: note writes use same-directory temp file + `fsync` +
  `os.replace`; frontmatter and managed regions are fully plugin-managed.
