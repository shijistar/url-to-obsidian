# after-install.md

The plugin is installed. Finish setup with the bundled one-shot installer:

```bash
cd "$HERMES_HOME/plugins/web-to-obsidian"
./install.sh
```

What it does:
1. `npm install` — installs the `@tiny-codes/web-clip-extractor` npm package
   into this plugin directory (used at runtime as the Node extractor).
2. `npx playwright install chromium` — Chromium for dynamic-page fallback.
3. Symlinks the `web-clip-to-obsidian` skill into the profile's skills dir
   (needs `--repo /path/to/url-to-obsidian` if the source repo isn't next to
   this copy).
4. Bootstraps `config.toml` from `config.example.toml` if absent.

Then review `config.toml`, restart your Hermes gateway service from a separate
shell, and clip with `/webclip <url>`.