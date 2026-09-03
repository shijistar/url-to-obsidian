# Netease (163.com) embedded-state author/published extraction

Session detail: 2026-08-31 — c.m.163.com clips had `author: ''` / `published: ''`
in frontmatter while Juejin/WeChat clips were fine. Fixed in extractor v0.4.0 (PR #10).

## Root cause

Netease mobile article pages expose author/published **only** inside the embedded
`window.__INITIAL_STATE__` JSON — no `<meta name="author">`, no JSON-LD, no byline
element, and the visible DOM only shows a generic disclaimer
("本文为网易自媒体平台'网易号'作者上传并发布").

Relevant embedded fields (网易号 = the "author"):
- `"source": "<网易号名>"` (e.g. `流苏晚晴`, `智东西`)
- `"sourceinfo": {"tname": "<网易号名>", ...}`
- `"ptime": "2026-08-27 12:32:05"` (publish time)
- `"articleType": "wemedia"`

The old extractor relied solely on Defuddle standard-tag parsing, so author/published
came back empty for every 163.com article.

## Diagnosis path (works, reuse this)

1. Inspect the saved file's frontmatter: `author: ''` + `extraction_method: static`.
2. Cross-check Inbox: are ALL empty-author articles from the same host? (Here: only
   c.m.163.com → site-specific, not a global regression.)
3. `curl -sL -A "<mobile UA>" <url> -o page.html` — HTTP 200; grep for
   `author` meta, `application/ld+json`, `class="*author*"` — none.
4. Grep raw HTML for `window.__INITIAL_STATE__` and `"source"/"ptime"` — found.
5. Confirm with a real extractor run: `node extractor/src/cli.mjs <url> --no-browser`
   returned `author: ''`.

## Fix (v0.4.0, extractor/src/extractor.mjs)

`extractNeteaseMeta(html)`:
- Locate `window.__INITIAL_STATE__=` then extract the **balanced JSON object**
  (helper `extractBalancedObjectAt` — tracks `{`/`}` depth, skips quoted strings).
  Do **not** wholesale `JSON.parse` the whole script block: trailing script payloads
  cause "Extra data: column N" failures.
- Cut the `"main"` sub-object and use field-level regexes only there:
  `"source"` → fallback `"sourceinfo":{"tname":...}` → `"ptime"`.
  Scoping to `main` avoids picking `source` from the recommendation list.
- **Scope to hostnames containing `163.com`**; standard Defuddle metadata always wins
  (`author: cleanString(parsed.author) || embeddedMeta?.author || ''`).
- Tests: fixture `extractor/test/fixtures/netease-article.html` (includes a
  recommend-list with its own `source` values to prove main-block priority) + 4 cases
  (fallback works / standard meta wins / broken state tolerated / non-163 unaffected).

## Metadata backfill is hash-safe

`webclip_id` = sha256(normalized URL); `source_content_hash`/`content_hash` =
sha256(markdown body). **Frontmatter author/published lines are NOT covered by any
hash** — a backfill that replaces only the `author:` line can leave all hash fields
untouched. Backfill pattern that worked: a script scanning Inbox for 163.com URLs
with empty author, dry-run via extractor CLI, `--apply` replacing only the exact
`author:` line (YAML-quoted), then `git diff` to prove 1-line-per-file changes.

## Unrecoverable case

If the source page already returns 404「内容不存在或已被删除」(and Netease API,
3g/m.163.com, Wayback snapshot, and web search all fail), the author cannot be
recovered — report it as not backfillable rather than inventing a value.
